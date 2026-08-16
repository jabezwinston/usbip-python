# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
The device core - build a virtual USB device from descriptors and requests.

The core knows only descriptors and requests: append descriptor content through
:class:`DescriptorGroup` (raw bytes or lazy blocks), route :class:`Endpoint`
pipes, and register per-interface control / SET_INTERFACE handlers plus
per-endpoint data callbacks. Standard requests, Microsoft OS/BOS/WebUSB descriptors
and the URB engine are answered here. It has no class concept - the
object-oriented authoring layer (`Interface` / `Function`) lives in
:mod:`usbip.function` and is built entirely on this module's public API.
"""

from __future__ import annotations

import heapq
import itertools
import os
import sys
import threading
import time
from collections import deque

from . import core
from .core import (
    ENDPOINT,
    FEATURE_ENDPOINT_HALT,
    IN,
    OUT,
    STANDARD,
    VENDOR,
    Setup,
    Stall,
)

# opt-in (USBIP_DEBUG=1): one line per EP0 control request, with whether it was
# answered or STALLed, and one per data transfer. The twin of the C core's
# USBIP_DEBUG: catches what a host sends that we reject, without a PCAP capture.
_DEBUG = bool(os.environ.get("USBIP_DEBUG"))

STALL_STATUS = -32  # the URB status a STALLed transfer reports (-EPIPE)


def _dbg_xfer(direction, epnum, type, length, note=None):
    """One USBIP_DEBUG line per data transfer: direction, endpoint address,
    transfer type and the byte count that actually moved."""
    if not _DEBUG:
        return
    addr = epnum | (0x80 if direction == IN else 0)
    print(
        f"[usbip_device] {'IN ' if direction == IN else 'OUT'} ep=0x{addr:02x} "
        f"{type or '?':<9} len={length}{' ' + note if note else ''}",
        flush=True,
    )


# ---- endpoint declaration ------------------------------------------------
class _EPSpec:
    def __init__(self, addr, type="bulk", mps=64, interval=0):
        self.addr = addr
        self.type = type
        self.mps = mps
        self.interval = interval


def In(addr, type="bulk", mps=64, interval=0):
    """Declare a device->host endpoint, e.g. In(0x81, 'interrupt', mps=8)."""
    return _EPSpec(addr | 0x80, type, mps, interval)


def Out(addr, type="bulk", mps=64, interval=0):
    """Declare a host->device endpoint, e.g. Out(0x01, 'bulk')."""
    return _EPSpec(addr & 0x7F, type, mps, interval)


class Endpoint:
    """A byte pipe. Device writes IN data and reads OUT data; the device's URB
    handler is the other end. Backed by a queue so reads/writes decouple."""

    def __init__(self, spec: _EPSpec):
        self.addr = spec.addr
        self.number = spec.addr & 0x0F
        self.dir = IN if spec.addr & 0x80 else OUT
        self.type = spec.type
        self.mps = spec.mps
        self.interval = spec.interval
        self.owner = None  # opaque tag set by USBDevice.route_endpoint()
        # data-path callbacks; the device consults these instead of knowing who
        # built the endpoint. Bound methods/closures carry any state they need.
        self.on_out = None  # fn(ep, data);      None -> queue for ep.read()
        # iso callback, contract by endpoint direction:
        #   IN:  fn(ep, lengths) -> list[bytes]  (requested maxima in); None -> no data
        #   OUT: fn(ep, packets)                 (received bytes in);   None -> discard
        self.on_iso = None
        self.halted = False  # STALL until CLEAR_FEATURE(ENDPOINT_HALT)
        self._q: deque = deque()
        self._cv = threading.Condition()
        self._pending: deque = deque()  # IN URBs parked waiting for data
        self.iso_deadline = 0.0  # iso pacing: monotonic time the next frame is due

    def write(self, data: bytes):  # device -> host (IN)
        """Queue IN data and complete as many parked URBs as it covers, in FIFO order.

        Draining a WHILE loop here (not a single pop) matters once a client keeps
        more than one IN URB outstanding: completing only one URB per write left
        the remainder in _q with further URBs still parked - nothing ever
        re-matched them, so the stream stalled and the NEXT write (e.g. an MSC
        CSW) was handed to the wrong URB, completing them out of order. Each
        parked URB takes up to its length from the FRONT chunk only (_pop_locked),
        preserving write/short-packet boundaries."""
        done = []
        with self._cv:
            self._q.append(bytes(data))
            while self._pending and self._q:
                length, cb, _seq = self._pending.popleft()
                done.append((cb, self._pop_locked(length)))
            if self._q:
                self._cv.notify_all()
        for cb, chunk in done:
            cb(chunk)  # complete parked URBs outside the lock

    def read(self, timeout=1.0) -> bytes:  # device reads host OUT data
        with self._cv:
            if not self._q and not self._cv.wait_for(lambda: bool(self._q), timeout):
                return b""
            return self._q.popleft()

    def _pop_locked(self, length):
        chunk = self._q.popleft()
        if length and len(chunk) > length:  # keep the unread tail for the next URB
            self._q.appendleft(chunk[length:])  # (a written blob may span several reads)
            return chunk[:length]
        return chunk

    # used by the device's URB handler (async): take queued data, else park the URB
    def take_or_park(self, length, complete, seqnum=None):
        """Return queued IN data if any; otherwise register `complete(data)` to
        run when data arrives (NAK-until-data) and return None. `seqnum` lets the
        server cancel this parked URB if the host later unlinks it."""
        with self._cv:
            if self._q:
                return self._pop_locked(length)
            self._pending.append((length, complete, seqnum))
            return None

    def cancel(self, seqnum):
        """Drop a parked IN URB the host has UNLINKed, so it is never completed
        (completing an unlinked seqnum desyncs the kernel's vhci). Returns True if
        one was removed."""
        with self._cv:
            for i, entry in enumerate(self._pending):
                if entry[2] == seqnum:
                    del self._pending[i]
                    return True
        return False

    # synchronous fallback (used when no async `respond` is available)
    def _take_in(self, length, timeout) -> bytes:
        with self._cv:
            if not self._q and not self._cv.wait_for(lambda: bool(self._q), timeout):
                return b""
            return self._pop_locked(length)

    def _put_out(self, data: bytes):
        with self._cv:
            self._q.append(bytes(data))
            self._cv.notify_all()

    def stall(self):
        """Halt the endpoint: every URB STALLs until the host clears the halt.

        Any IN URB the host already has outstanding is STALLed immediately -
        leaving it parked is the hang this exists to avoid, since the host would
        otherwise sit on the read until its own timeout fires. Queued data
        survives the halt and is delivered once it is cleared: mass storage halts
        bulk-IN to abandon a failed data phase, then queues the CSW."""
        with self._cv:
            self.halted = True
            parked, self._pending = list(self._pending), deque()
        for _length, cb, _seq in parked:
            cb(b"", STALL_STATUS)  # complete outside the lock

    def clear_halt(self):
        """Clear a halt, as CLEAR_FEATURE(ENDPOINT_HALT) does. Queued data is kept."""
        with self._cv:
            self.halted = False

    def reset(self):
        """Drop buffered data and any parked URBs - called when a host (re)attaches
        so leftovers from a previous session can't corrupt the new one."""
        with self._cv:
            self._q.clear()
            self._pending.clear()
            self.halted = False


# ---- a run of configuration-descriptor content ---------------------------
class DescriptorGroup:
    """One *function's worth* of configuration-descriptor content: an ordered run
    of descriptor blocks plus the interface numbers claimed through it. Created
    with :meth:`USBDevice.add_group`; the groups render in creation order to form
    the configuration descriptor.

    A block is ``bytes``, or a zero-argument callable returning ``bytes`` -
    callables are rendered on every GET_DESCRIPTOR, so content that depends on
    late-bound state (relocated endpoint addresses, speed-sized wMaxPacketSize,
    alternate settings) is always current.

    The group is also the scoping key for the Microsoft OS descriptors
    (:meth:`enable_msos`): on a composite each group is advertised as its own
    Windows function. A device that never creates a group is treated as one
    implicit device-wide group."""

    def __init__(self, dev, owner=None):
        self._dev = dev
        self.owner = owner  # opaque back-pointer for the layer above
        self.interface_numbers = []  # bInterfaceNumbers claimed through this group
        self._blocks = []

    def add(self, block):
        """Append a descriptor block: ``bytes`` or ``callable() -> bytes``."""
        self._blocks.append(block)

    def claim_interface(self, owner=None) -> int:
        """Claim the next free bInterfaceNumber for this group and return it."""
        ifnum = self._dev._claim_interface(owner)
        self.interface_numbers.append(ifnum)
        return ifnum

    def add_endpoint(self, spec: _EPSpec) -> Endpoint:
        """Create an :class:`Endpoint` from an :func:`In`/:func:`Out` spec, route it
        (relocating the address if taken), and append its endpoint descriptor as a
        lazy block - the descriptor always reflects the final address and size."""
        ep = Endpoint(spec)
        self._dev.route_endpoint(ep, owner=self.owner or self)

        def render():
            return core.EndpointDescriptor(
                bEndpointAddress=ep.addr,
                bmAttributes=core.XFER_BY_NAME[ep.type],
                wMaxPacketSize=ep.mps,
                bInterval=ep.interval,
            ).pack()

        self._blocks.append(render)
        return ep

    def enable_msos(self, compatible=b"WINUSB", guid=None):
        """Advertise Microsoft OS descriptors scoped to this group's interfaces (see
        :meth:`USBDevice.enable_msos`)."""
        self._dev.enable_msos(compatible, guid, function=self)

    def enable_winusb(self, guid=None):
        """:meth:`enable_msos` with the "WINUSB" Compatible ID."""
        self.enable_msos(b"WINUSB", guid or core.DEFAULT_WINUSB_GUID)

    def _render(self) -> bytes:
        return b"".join(block() if callable(block) else block for block in self._blocks)


# ---- the virtual device --------------------------------------------------
class _PlugContext:
    def __init__(self, dev):
        self._dev = dev

    def __enter__(self):
        return self._dev

    def __exit__(self, *exc):
        self._dev.unplug()
        return False


class USBDevice:
    def __init__(
        self,
        vid,
        pid,
        *,
        product=None,
        manufacturer=None,
        serial=None,
        bcdDevice=0x0100,
        bcdUSB=0x0200,
        device_class=0,
        device_subclass=0,
        device_protocol=0,
    ):
        self.vid = vid
        self.pid = pid
        self.bcdDevice = bcdDevice
        self.bcdUSB = bcdUSB
        self.device_class = device_class
        self.device_subclass = device_subclass
        self.device_protocol = device_protocol
        self.composite = False  # set by set_composite()
        self._msos = []  # Microsoft OS advertisements; see enable_msos()
        self._msos_v1 = 0x20  # vendor request codes, see set_msos_vendor_codes()
        self._msos_v2 = 0x21
        self._webusb = None  # set by enable_webusb()
        self.speed = core.SPEED_FULL  # reported speed; set by set_speed()
        self.iso_paced = False  # set by set_iso_pacing()
        self._pace_q = []  # heap of (deadline, seq, respond, urb) - iso pacer
        self._pace_cv = threading.Condition()
        self._pace_thread = None
        self._pace_seq = itertools.count()
        self._groups = []  # DescriptorGroup objects, in wire order
        self._ifnum_owners = []  # one entry per claimed bInterfaceNumber
        self._ctrl = {}  # ifnum (or None = device fallback) -> control handler
        self._set_alt = {}  # ifnum -> SET_INTERFACE handler
        self._reset_hooks = []  # run by reset_io() after flushing endpoints
        self._speed_hooks = []  # run by set_speed()
        self.config = 0
        self._ep_map = {}  # (number, dir) -> Endpoint
        self._transport = None
        # Wire identity on the bus, assigned by plug() unless set_busid() named it
        # first. devnum is unique per listener, which is what lets a host tell two
        # exported devices apart (`lsusb -s`, and each device's pcap stream).
        self.busid = None
        self.busnum = 0
        self.devnum = 0
        self._strings = [None]
        self.iManufacturer = self._add_string(manufacturer)
        self.iProduct = self._add_string(product)
        self.iSerialNumber = self._add_string(serial)

    def _add_string(self, text):
        if not text:
            return 0
        self._strings.append(text)
        return len(self._strings) - 1

    def add_string(self, text: str) -> int:
        """Register a string descriptor and return its index (for iInterface etc.)."""
        return self._add_string(text)

    # --- composition primitives (what the class layer, or a raw author, builds on) ---
    def add_group(self, owner=None) -> DescriptorGroup:
        """Open a new :class:`DescriptorGroup` - one function's worth of
        configuration-descriptor content and the Microsoft OS scoping unit. `owner` is an
        opaque tag the layer above may attach (never interpreted here)."""
        group = DescriptorGroup(self, owner)
        self._groups.append(group)
        return group

    def _claim_interface(self, owner=None) -> int:
        self._ifnum_owners.append(owner)
        return len(self._ifnum_owners) - 1

    @property
    def num_interfaces(self) -> int:
        """Number of claimed interfaces == the next free bInterfaceNumber."""
        return len(self._ifnum_owners)

    @property
    def interfaces(self):
        """The claim owners, one per bInterfaceNumber (class-built devices: the
        :class:`Interface` objects; raw-authored devices: whatever tag - possibly
        None - was passed to ``claim_interface``)."""
        return list(self._ifnum_owners)

    @property
    def functions(self):
        """The group owners in wire order (class-built devices: the
        :class:`Function` objects)."""
        return [group.owner for group in self._groups if group.owner is not None]

    def __getitem__(self, i):
        return self._ifnum_owners[i]

    def route_endpoint(self, ep: Endpoint, owner=None) -> Endpoint:
        """Route an endpoint into the device, relocating its address if taken.

        The declared address is a *preference*: it is honoured when the number is
        free, so a lone function keeps the addresses it asks for, but when another
        endpoint already holds it the endpoint is moved to the lowest free number
        in the same direction. Without this a composite would emit a descriptor in
        which two interfaces claim one address, and the host would route both to
        whichever registered last. Mirrors ep_alloc_number() in
        the C library's src/usbip_device.c. `owner` tags the endpoint for diagnostics."""
        if (ep.number, ep.dir) in self._ep_map:
            for number in range(1, 16):
                if (number, ep.dir) not in self._ep_map:
                    ep.number = number
                    ep.addr = number | (0x80 if ep.dir == IN else 0x00)
                    break
            else:
                raise RuntimeError(
                    f"no free endpoint number left for {owner.__class__.__name__} "
                    f"({'IN' if ep.dir == IN else 'OUT'})"
                )
        ep.owner = owner
        self._ep_map[(ep.number, ep.dir)] = ep
        return ep

    def on_control(self, ifnum, handler):
        """Register `handler(setup, data=b"") -> bytes | None` for class/vendor
        control requests addressed to interface `ifnum` (raise :class:`Stall` to
        reject). ``ifnum=None`` registers the device-level fallback: requests with
        a non-interface recipient, or for an interface nobody registered."""
        self._ctrl[ifnum] = handler

    def has_control_fallback(self) -> bool:
        """Whether a device-level control fallback (``on_control(None, ...)``) is set."""
        return None in self._ctrl

    def on_set_alt(self, ifnum, handler):
        """Register `handler(alt)` for SET_INTERFACE on `ifnum`. Unregistered
        interfaces acknowledge SET_INTERFACE with no side effect."""
        self._set_alt[ifnum] = handler

    def add_reset_hook(self, fn):
        """Run `fn()` on bus reset (host attach), after all endpoints are flushed."""
        self._reset_hooks.append(fn)

    def add_speed_hook(self, fn):
        """Run `fn(speed)` when the reported link speed changes (see set_speed)."""
        self._speed_hooks.append(fn)

    def set_device_triple(self, cls, sub, proto):
        """Set the device-descriptor class triple - unless the device is composite,
        where 0xEF/0x02/0x01 is pinned (a function's own triple must not silently
        unmake the composite) and the request is ignored with a diagnostic."""
        if self.composite:
            sys.stderr.write(
                f"[usbip] device class {cls:02x}/{sub:02x}/{proto:02x} ignored: composite pins "
                "the triple to EF/02/01\n"
            )
        else:
            self.device_class, self.device_subclass, self.device_protocol = cls, sub, proto

    def set_composite(self):
        """Declare the device composite: several independent class functions on one
        device. Call BEFORE adding any class.

        Sets the device-descriptor triple to 0xEF/0x02/0x01 (Miscellaneous / Common
        Class / Interface Association) and asks every multi-interface class to emit
        an Interface Association Descriptor grouping its own interfaces.

        Linux does not need this - cdc_acm and friends group their interfaces from
        the class-specific descriptors (CDC's Union descriptor). Windows does:
        usbccgp splits a composite into one child devnode per *function*, and
        without an IAD it splits per *interface*, handing CDC's data interface to a
        different devnode from its communications interface, so no COM port forms.

        A single-function device must NOT set this. The triple is pinned from
        here on: a class's own ``device_triple`` (e.g. Bluetooth's
        0xE0/0x01/0x01) is ignored with a diagnostic, since overwriting
        0xEF/0x02/0x01 would silently unmake the composite. Mirrors the C
        ``usbip_device_set_composite()``."""
        self.composite = True
        self.device_class, self.device_subclass, self.device_protocol = 0xEF, 0x02, 0x01

    def enable_msos(self, compatible=b"WINUSB", guid=None, function=None):
        """Advertise Microsoft OS 1.0 + 2.0 descriptors with the given Compatible ID so
        Windows auto-binds a function driver without an .inf: "WINUSB" (so libusb
        apps like dfu-util work without Zadig) or "MTP" (Media Transfer Protocol).
        `guid` adds a DeviceInterfaceGUID - WinUSB wants one, MTP leaves it None.
        Bumps bcdUSB to 0x0210 so the host fetches the BOS (Microsoft OS 2.0).

        `function` scopes the advertisement to one :class:`DescriptorGroup` (a
        :class:`Function` or :class:`Interface` is accepted and resolved to its
        group). Leave it None to describe the whole device, which is right for a
        single-function device but not for a composite: there Windows gives each
        function its own devnode, and a device-wide Compatible ID would bind one
        driver over all of them. Prefer :meth:`Function.enable_msos`. Re-enabling
        the same scope replaces it."""
        group = self._resolve_group(function)
        entry = {"group": group, "compatible": bytes(compatible), "guid": guid}
        for i, existing in enumerate(self._msos):
            if existing["group"] is group:
                self._msos[i] = entry
                break
        else:
            self._msos.append(entry)
        self.bcdUSB = 0x0210

    @staticmethod
    def _resolve_group(scope):
        """A DescriptorGroup, or anything carrying one: an object with a ``_group``
        (a Function) or with a ``func`` that has one (an Interface)."""
        if scope is None or isinstance(scope, DescriptorGroup):
            return scope
        group = getattr(scope, "_group", None)
        if group is None:
            group = scope.func._group  # an Interface -> its owning Function
        return group

    def enable_winusb(self, guid=None, function=None):
        """Advertise WinUSB (Compatible ID "WINUSB" + a DeviceInterfaceGUID)."""
        self.enable_msos(b"WINUSB", guid or core.DEFAULT_WINUSB_GUID, function)

    def set_msos_vendor_codes(self, v1=None, v2=None):
        """Override the vendor request codes for the Microsoft OS requests (default 0x20 for
        Microsoft OS 1.0, 0x21 for Microsoft OS 2.0). The device answers those codes itself before
        any interface sees them, so change them if a function on this device uses
        vendor request 0x20/0x21 with wIndex 0x0004, 0x0005 or 0x0007."""
        if v1:
            self._msos_v1 = v1
        if v2:
            self._msos_v2 = v2

    # --- Microsoft OS descriptor assembly ---
    def _msos_entry_for_group(self, group):
        """The entry describing a group: a group-scoped entry wins, else the
        device-wide entry fans out to every group, else the group is simply not
        advertised (its own driver binds by class, e.g. CDC next to a WinUSB
        DFU)."""
        for entry in self._msos:
            if entry["group"] is group:
                return entry
        for entry in self._msos:
            if entry["group"] is None:
                return entry
        return None

    def _msos_sections(self):
        """One (first_interface, entry) pair per covered group. The interface
        numbers are resolved on demand, not when the entry was recorded: a class
        enables Microsoft OS descriptors before its interfaces are numbered. A device
        with no groups is one implicit device-wide group."""
        if not self._groups:
            entry = self._msos_entry_for_group(None)
            return [(0, entry)] if entry else []
        out = []
        for group in self._groups:
            entry = self._msos_entry_for_group(group)
            if entry is None:
                continue
            first = group.interface_numbers[0] if group.interface_numbers else 0
            out.append((first, entry))
        return out

    def _msos_needs_subsets(self):
        """Whether the Microsoft OS 2.0 set must scope its features per function. Only worth
        doing on a real composite - a single-function device keeps the flat form,
        which is also what keeps its descriptor bytes unchanged."""
        return len(self._groups) > 1 and bool(self._msos)

    def _msos_entry_for_interface(self, ifnum):
        """The entry describing a bInterfaceNumber: an exact group match wins,
        else a device-wide entry. None when nothing covers it - the caller answers
        with an empty property set rather than leaking another function's GUID."""
        for entry in self._msos:
            group = entry["group"]
            if group is not None and ifnum in group.interface_numbers:
                return entry
        for entry in self._msos:
            if entry["group"] is None:
                return entry
        return None

    def _msos2_bytes(self):
        if self._msos_needs_subsets():
            return core.ms_os2_set_subsets(
                [(first, entry["compatible"], entry["guid"]) for first, entry in self._msos_sections()]
            )
        entry = (self._msos_entry_for_group(self._groups[0]) if self._groups else None) or self._msos[0]
        return core.ms_os2_set(compatible=entry["compatible"], guid=entry["guid"])

    def enable_webusb(self, vendor_code, url):
        """Advertise WebUSB: add a WebUSB platform-capability descriptor to the BOS
        and answer the bVendorCode/GET_URL (wIndex 0x02) vendor request with `url`,
        so a WebUSB-capable browser surfaces the device and can open it. Pick
        vendor_code distinct from WinUSB's 0x20/0x21 if both are enabled. Pair with
        enable_winusb() so the device also binds WinUSB on Windows. Bumps bcdUSB to
        0x0210 so the host fetches the BOS. iLandingPage is derived from the URL
        (present when non-empty), matching the C usbip_device_enable_webusb()."""
        self._webusb = {"vendor": vendor_code, "url": url, "landing": 1 if url else 0}
        self.bcdUSB = 0x0210

    def set_speed(self, speed: int):
        """Report a link speed to the importer (default SPEED_FULL). USB/IP is URB-level, so
        "high speed" is just the reported speed plus speed-correct endpoint sizing. Call BEFORE
        add() so speed-aware classes (cdc_acm, msc, mtp, uac, uvc) size their endpoints; for
        robustness any already-added interface is re-sized here too."""
        self.speed = speed
        for fn in self._speed_hooks:
            fn(speed)

    def add(self, obj):
        """Add a :class:`Function` (or a bare :class:`Interface`, wrapped in an anonymous
        single-interface Function) - the one way to add anything, bundled class or your
        own. Composition itself lives in the class layer: any object implementing
        ``_attach(dev)`` can be added; the device only provides the primitives
        (`add_group`, `claim_interface`, `route_endpoint`, `on_control`, ...).

        Returns the class's data-plane handle - ``_attach``'s return value, which for a
        :class:`Function` is its :attr:`~usbip.function.Function.primary` (``CDCACM`` ->
        the Data port) and for a bare interface is the interface itself. An ``_attach``
        that returns nothing yields the object passed in."""
        handle = obj._attach(self)
        return obj if handle is None else handle

    def is_iso(self, ep_number, direction) -> bool:
        ep = self._ep_map.get((ep_number, direction))
        return bool(ep and ep.type == "iso")

    def set_iso_pacing(self, enabled=True):
        """Pace isochronous completions to real time. USB/IP has no SOF clock, so by
        default iso transfers complete instantly and the host's audio/video engine
        free-runs (a UAC speaker plays many times too fast). When enabled, each iso
        completion is scheduled for the wall-clock time its packet schedule would
        really take (a per-endpoint deadline) and delivered by a background pacer
        thread, so the serve thread never blocks - full-duplex (speaker + mic at
        once) stays real time, like real hardware."""
        self.iso_paced = enabled

    def _iso_deadline(self, ep, npkts):
        """Advance the endpoint's deadline clock by npkts service intervals; return the deadline.
        The interval depends on the link speed: FS counts bInterval whole 1 ms frames, HS counts
        125 µs microframes as 2^(bInterval-1) of them (so a bInterval=1 endpoint polls every 1 ms
        at FS but every 125 µs at HS)."""
        bint = ep.interval or 1
        if self.speed >= core.SPEED_HIGH:
            frame = (125 * (1 << (bint - 1))) / 1_000_000.0  # high speed: 125 µs microframes
        else:
            frame = bint / 1000.0  # full/low speed: 1 ms frames
        now = time.monotonic()
        if ep.iso_deadline < now:  # first transfer / recover from a gap
            ep.iso_deadline = now
        ep.iso_deadline += frame * npkts
        return ep.iso_deadline

    def _pace_schedule(self, deadline, respond, urb):
        with self._pace_cv:
            if self._pace_thread is None:
                self._pace_thread = threading.Thread(target=self._pace_run, daemon=True)
                self._pace_thread.start()
            heapq.heappush(self._pace_q, (deadline, next(self._pace_seq), respond, urb))
            self._pace_cv.notify()

    def _pace_run(self):
        while True:
            with self._pace_cv:
                while not self._pace_q:
                    self._pace_cv.wait()
                deadline = self._pace_q[0][0]
                wait = deadline - time.monotonic()
                if wait > 0:
                    self._pace_cv.wait(timeout=wait)
                    continue  # re-check (an earlier deadline may have arrived)
                _, _, respond, urb = heapq.heappop(self._pace_q)
            try:
                respond(urb)  # outside the lock; respond serializes its own writes
            except Exception:
                pass

    def pace_cancel(self, seqnum):
        """Drop a queued iso completion whose URB was unlinked (so the host's stream
        releases cleanly instead of getting a late completion)."""
        with self._pace_cv:
            self._pace_q = [entry for entry in self._pace_q if entry[3].seqnum != seqnum]
            heapq.heapify(self._pace_q)

    def cancel_urb(self, seqnum):
        """Handle a host CMD_UNLINK for `seqnum`: drop any parked IN URB (real
        kernels pipeline interrupt-IN URBs and unlink the spares; completing an
        unlinked seqnum makes vhci 'cannot find urb' and tear the device down) and
        any queued iso completion."""
        self.pace_cancel(seqnum)
        for ep in self._ep_map.values():
            if ep.cancel(seqnum):
                return

    # --- descriptors ---
    def device_descriptor(self) -> core.DeviceDescriptor:
        return core.DeviceDescriptor(
            bcdUSB=self.bcdUSB,
            idVendor=self.vid,
            idProduct=self.pid,
            bcdDevice=self.bcdDevice,
            bDeviceClass=self.device_class,
            bDeviceSubClass=self.device_subclass,
            bDeviceProtocol=self.device_protocol,
            iManufacturer=self.iManufacturer,
            iProduct=self.iProduct,
            iSerialNumber=self.iSerialNumber,
            bNumConfigurations=1,
        )

    def config_bytes(self) -> bytes:
        """The full configuration descriptor, exactly as served to the host: the
        groups' blocks rendered in order (callables re-render every time, so
        late-bound content is always current)."""
        body = b"".join(group._render() for group in self._groups)
        cfg = core.ConfigurationDescriptor(
            wTotalLength=9 + len(body), bNumInterfaces=self.num_interfaces
        )
        return cfg.pack() + body

    def interface_triples(self):
        """(bInterfaceClass, bInterfaceSubClass, bInterfaceProtocol) per interface,
        in bInterfaceNumber order, read from the rendered configuration (alternate
        setting 0) - used for the USB/IP device-list reply."""
        blob = self.config_bytes()
        triples = {}
        for desc in core.iter_descriptors(blob, offset=9):
            if desc[1] == core.DT_INTERFACE and len(desc) >= 8 and desc[3] == 0:  # alt 0
                triples[desc[2]] = (desc[5], desc[6], desc[7])
        return [triples[ifnum] for ifnum in sorted(triples)]

    def _string_bytes(self, index) -> bytes:
        if index == 0:
            return core.langid_descriptor()
        if index == 0xEE and self._msos:  # Microsoft OS 1.0 magic string
            return core.ms_os1_string(self._msos_v1)
        if 0 < index < len(self._strings) and self._strings[index]:
            return core.string_descriptor(self._strings[index])
        raise Stall

    def bos_bytes(self) -> bytes:
        """The BOS descriptor (WebUSB / Microsoft OS 2.0 capabilities), as served to the host."""
        caps = []
        if self._webusb:  # WebUSB platform capability
            caps.append(core.webusb_platform_cap(self._webusb["vendor"], self._webusb["landing"]))
        if self._msos:  # Microsoft OS 2.0 platform capability
            caps.append(core.msos2_platform_cap(self._msos_v2, len(self._msos2_bytes())))
        return core.bos(*caps)

    def _winusb_vendor(self, setup):
        """Answer a Microsoft OS vendor request (None if not one of ours)."""
        if setup.bRequest == self._msos_v1 and setup.wIndex == 0x0004:  # Microsoft OS 1.0 Compatible ID
            return core.ms_os1_compatid(
                sections=[(first, entry["compatible"]) for first, entry in self._msos_sections()]
            )
        if setup.bRequest == self._msos_v1 and setup.wIndex == 0x0005:  # Microsoft OS 1.0 Extended Props
            # per-interface: the recipient interface is in wValue's high byte, so a
            # composite answers each function with its own GUID
            entry = self._msos_entry_for_interface((setup.wValue >> 8) & 0xFF)
            return core.ms_os1_extprops(entry["guid"] if entry else None)
        if setup.bRequest == self._msos_v2 and setup.wIndex == 0x0007:  # Microsoft OS 2.0 descriptor set
            return self._msos2_bytes()
        return None

    def vendor_descriptor(self, setup):
        """Answer a core-handled vendor request - Microsoft OS (WinUSB) or WebUSB
        GET_URL - or None if it isn't one of ours (so an interface can handle it)."""
        if self._msos and (resp := self._winusb_vendor(setup)) is not None:
            return resp
        if self._webusb and setup.bRequest == self._webusb["vendor"] and setup.wIndex == 0x02:
            return core.webusb_url(self._webusb["url"])  # WebUSB GET_URL
        return None

    def reset_io(self):
        """Flush every endpoint (buffered IN data + parked URBs) and let each
        interface reset its own buffers. The USB/IP server calls this when a host
        attaches, so a re-attach behaves like a fresh plug (a real device sees a
        bus reset). Without it, events that piled up after a previous host left
        get delivered to the new host and corrupt its init."""
        for ep in self._ep_map.values():
            ep.reset()
        for fn in self._reset_hooks:
            fn()

    # --- URB handling (device/server side) ---
    def handle_urb(self, urb, respond=None):
        """Process one URB. Returns the urb to send its response now, or None if
        the URB was PARKED (an IN endpoint with no data yet) - it will be
        completed asynchronously via `respond` when the device writes data, so
        the server's read loop never blocks. `respond` is supplied by the USB/IP
        server; when absent we fall back to a short blocking wait."""
        if urb.ep == 0:
            return self._handle_control(urb)
        ep = self._ep_map.get((urb.ep, urb.direction))
        if ep is None:
            urb.status = -19  # -ENODEV
            _dbg_xfer(urb.direction, urb.ep, None, urb.length, "-> no such endpoint")
            return urb
        if ep.halted:  # until CLEAR_FEATURE(ENDPOINT_HALT)
            urb.buffer, urb.actual, urb.status = b"", 0, STALL_STATUS
            _dbg_xfer(urb.direction, urb.ep, ep.type, urb.length, "-> STALL")
            return urb
        if ep.type == "iso" and urb.direction == IN and urb.iso_packets is not None:
            lengths = [pkt[1] for pkt in urb.iso_packets]
            chunks = ep.on_iso(ep, lengths) if ep.on_iso else []
            buf = bytearray()
            for i, pkt in enumerate(urb.iso_packets):
                ch = chunks[i] if i < len(chunks) else b""
                pkt[2], pkt[3] = len(ch), 0  # actual_length, status
                buf += ch
            urb.buffer, urb.actual, urb.status = bytes(buf), len(buf), 0
            _dbg_xfer(IN, urb.ep, ep.type, len(buf), f"{len(urb.iso_packets)} pkts")
            if self.iso_paced and respond is not None:  # deliver at real time via the pacer
                deadline = self._iso_deadline(ep, len(urb.iso_packets))
                self._pace_schedule(deadline, respond, urb)
                return None
            return urb
        if ep.type == "iso" and urb.direction == OUT and urb.iso_packets is not None:
            packets = [urb.buffer[off : off + length] for off, length, _a, _s in urb.iso_packets]
            if ep.on_iso:
                ep.on_iso(ep, packets)
            for pkt in urb.iso_packets:
                pkt[2], pkt[3] = pkt[1], 0  # actual_length = length, status ok
            urb.actual, urb.status = 0, 0  # OUT RET carries descriptors, no data
            _dbg_xfer(
                OUT, urb.ep, ep.type, sum(len(pkt) for pkt in packets), f"{len(urb.iso_packets)} pkts"
            )
            if self.iso_paced and respond is not None:  # deliver at real time via the pacer
                self._pace_schedule(self._iso_deadline(ep, len(urb.iso_packets)), respond, urb)
                return None
            return urb
        if urb.direction == IN:
            length = urb.length or ep.mps
            if respond is None:  # synchronous fallback (no async server)
                data = ep._take_in(length, max(0.5, urb.interval / 1000 or 2.0))
                urb.buffer, urb.actual, urb.status = data, len(data), 0
                _dbg_xfer(IN, urb.ep, ep.type, len(data))
                return urb

            def complete(data, status=0):  # runs when ep.write() feeds the parked URB
                urb.buffer, urb.actual, urb.status = data, len(data), status
                note = "-> STALL" if status else "parked URB"
                _dbg_xfer(IN, urb.ep, ep.type, len(data), note)
                respond(urb)

            data = ep.take_or_park(length, complete, urb.seqnum)
            if data is None:
                _dbg_xfer(IN, urb.ep, ep.type, length, "-> parked, no data yet")
                return None  # parked: hold the URB until data (NAK)
            urb.buffer, urb.actual, urb.status = data, len(data), 0
            _dbg_xfer(IN, urb.ep, ep.type, len(data))
            return urb
        # OUT: the endpoint's hook consumes it; without one, queue it on the
        # endpoint for Endpoint.read
        try:
            if ep.on_out:
                ep.on_out(ep, urb.buffer)
            else:
                ep._put_out(urb.buffer)
        except Stall:
            urb.status = -32
            _dbg_xfer(OUT, urb.ep, ep.type, len(urb.buffer), "-> STALL")
            return urb
        urb.actual, urb.status = len(urb.buffer), 0
        _dbg_xfer(OUT, urb.ep, ep.type, len(urb.buffer))
        return urb

    def _route_control(self, ifnum):
        """The registered handler for an interface, else the device fallback,
        else None."""
        handler = self._ctrl.get(ifnum)
        return handler if handler is not None else self._ctrl.get(None)

    def _handle_control(self, urb):
        setup = Setup.parse(urb.setup)
        try:
            if setup.type == STANDARD:
                data = self._standard(setup)
            elif setup.type == VENDOR and (resp := self.vendor_descriptor(setup)) is not None:
                data = resp  # Microsoft OS / WebUSB descriptor
            elif setup.recipient == 1:  # interface recipient
                handler = self._route_control(setup.wIndex & 0xFF)
                if handler is None:
                    raise Stall
                data = handler(setup, urb.buffer)
            else:  # device/endpoint recipient -> fallback
                handler = self._ctrl.get(None)
                data = handler(setup, urb.buffer) if handler else b""
            data = data or b""
            if setup.direction == IN:
                data = data[: setup.wLength]
                urb.buffer, urb.actual = data, len(data)
            else:
                urb.actual = setup.wLength
            urb.status = 0
        except Stall:
            urb.status = -32  # -EPIPE -> STALL
        if _DEBUG:
            print(
                f"[usbip_device] ctrl type=0x{setup.bmRequestType:02x} req=0x{setup.bRequest:02x} "
                f"val=0x{setup.wValue:04x} idx=0x{setup.wIndex:04x} len={setup.wLength} "
                f"-> {'STALL' if urb.status else 'ok'}",
                flush=True,
            )
        return urb

    def _standard(self, setup: Setup):
        request = setup.bRequest
        if request == 0x06:  # GET_DESCRIPTOR
            dtype, idx = setup.wValue >> 8, setup.wValue & 0xFF
            if dtype == core.DT_DEVICE:
                return self.device_descriptor().pack()
            if dtype == core.DT_CONFIG:
                return self.config_bytes()
            if dtype == core.DT_STRING:
                return self._string_bytes(idx)
            if dtype == core.DT_BOS and (self._msos or self._webusb):  # WebUSB / Microsoft OS 2.0
                return self.bos_bytes()
            if dtype < 0x21:  # unsupported standard descriptor (e.g. device qualifier)
                raise Stall
            handler = self._route_control(setup.wIndex & 0xFF)  # class desc (dt >= 0x21), e.g. HID report
            if handler is None:
                raise Stall
            return handler(setup, b"")
        if request == 0x09:  # SET_CONFIGURATION
            self.config = setup.wValue & 0xFF
            self.reset_io()  # (re)configure -> flush endpoints
            return b""
        if request == 0x08:  # GET_CONFIGURATION
            return bytes([self.config])
        if request == 0x0B:  # SET_INTERFACE
            handler = self._set_alt.get(setup.wIndex & 0xFF)
            if handler is not None:
                handler(setup.wValue & 0xFF)
            return b""
        if request == 0x0A:  # GET_INTERFACE
            return b"\x00"
        if request == 0x00:  # GET_STATUS
            ep = self._ep_by_addr(setup.wIndex & 0xFF) if setup.recipient == ENDPOINT else None
            if ep is not None and ep.halted:
                return b"\x01\x00"  # bit 0 = Halt
            return b"\x00\x00"  # not self-powered, no remote wakeup
        if request in (0x01, 0x03):  # CLEAR_FEATURE / SET_FEATURE
            # The halt is ours to track: the host clears it to recover a stalled
            # pipe, as MSC does after a failed data phase. Other features are vhci's.
            if setup.recipient == ENDPOINT and setup.wValue == FEATURE_ENDPOINT_HALT:
                ep = self._ep_by_addr(setup.wIndex & 0xFF)
                if ep is None:
                    pass
                elif request == 0x01:
                    ep.clear_halt()
                else:
                    ep.stall()
            return b""
        if request == 0x05:  # SET_ADDRESS (vhci assigns it)
            return b""
        raise Stall

    def _ep_by_addr(self, addr):
        """Look an endpoint up by bEndpointAddress, the form a SETUP wIndex carries."""
        return self._ep_map.get((addr & 0x0F, IN if addr & 0x80 else OUT))

    # --- lifecycle ---
    def _check_endpoint_conflicts(self):
        """Reject a configuration where two interfaces claim one endpoint address.

        :meth:`route_endpoint` prevents this for endpoints it routes, so a failure
        here means something bypassed it (a hand-built descriptor). The rendered
        configuration is scanned - the same bytes the host will see - so raw
        descriptor blocks are covered too. An address repeated under ONE interface
        is legal (alternate settings re-describe their endpoints). Catching it at
        plug time beats routing one interface's traffic to another at run time.
        Mirrors check_endpoint_conflicts() in the C library's src/usbip_device.c."""
        blob = self.config_bytes()
        owner = {}
        ifnum = None
        for desc in core.iter_descriptors(blob, offset=9):  # skip the configuration header
            if desc[1] == core.DT_INTERFACE:
                ifnum = desc[2]
            elif desc[1] == core.DT_ENDPOINT and ifnum is not None:
                addr = desc[2]
                first = owner.setdefault(addr, ifnum)
                if first != ifnum:
                    raise RuntimeError(
                        f"endpoint 0x{addr:02X} claimed by two interfaces "
                        f"({first} and {ifnum}) -- the device descriptor is invalid"
                    )

    def _check_composite_iads(self):
        """Warn when a multi-interface function shares the device with another and
        emits no Interface Association Descriptor.

        A lone function is described by the device descriptor's class triple and
        needs no association; anything sharing the device does. Windows' usbccgp
        splits an IAD-less composite per INTERFACE, so such a function lands in
        several devnodes - a CDC port arrives as a Communications interface that
        cannot start plus a Data interface nothing binds. Linux pairs them from the
        class-specific descriptors regardless, which is why this goes unnoticed
        until someone tries Windows. A diagnostic, not an error."""
        if not self.composite and len(self._groups) < 2:
            return
        for group in self._groups:
            if len(group.interface_numbers) <= 1:
                continue
            blob = group._render()
            has_iad = any(
                desc[1] == core.DT_INTERFACE_ASSOCIATION for desc in core.iter_descriptors(blob)
            )
            if not has_iad:
                sys.stderr.write(
                    f"[usbip] composite function at interface {group.interface_numbers[0]} has "
                    f"{len(group.interface_numbers)} interfaces but no IAD -- call set_composite() "
                    f"BEFORE adding classes\n"
                )

    def plug(self, via=None):
        """Serve this device and return immediately (a context manager that
        unplugs on exit).

        Plugging another device onto the same transport exports it alongside this
        one rather than failing to bind: the listener is shared, and each device
        is named by its own busid - ``1-1``, ``1-2``, ... in plug order unless
        :meth:`set_busid` named it. The importer picks one (``usbip attach -b
        1-2``), and every imported device gets its own connection. Distinct from a
        *composite* device (:meth:`set_composite`), which is one device with
        several functions.
        """
        from .transport import default_transport

        self._check_endpoint_conflicts()
        self._check_composite_iads()
        self._transport = via or default_transport()
        self._transport.serve(self)
        return _PlugContext(self)

    def set_busid(self, busid: str):
        """Name this device on the wire instead of the ``1-<n>`` assigned by
        :meth:`plug`. Only useful when a process exports several devices and the
        importer wants stable names regardless of plug order. Call before plug()."""
        self.busid = busid
        return self

    def unplug(self):
        if self._transport:
            # Per device: a listener shared with other plugged devices keeps
            # serving them, and closes when the last one unplugs.
            self._transport.stop(self)
            self._transport = None
