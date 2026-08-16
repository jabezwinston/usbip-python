# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 29-July-2026
"""
The class layer - author reusable USB device classes as objects.

An :class:`Interface` subclass declares endpoints (:func:`~usbip.device.In` /
:func:`~usbip.device.Out`), emits class-specific descriptors
(``extra_descriptors``) and handles class/vendor control requests
(``on_control``); a :class:`Function` groups one or more interfaces into the
unit Windows binds a driver to, optionally with an Interface Association
Descriptor. Both compose themselves onto a :class:`~usbip.device.USBDevice`
through its public primitives alone (``add_group`` / ``claim_interface`` /
``route_endpoint`` / ``on_control`` / ...) - the device core knows nothing
about them. Mirrors the C class layer (classes/usb_class.c) over the class-free
device core.
"""

from __future__ import annotations

import struct

from . import core
from .core import Setup, Stall
from .device import Endpoint, _EPSpec


# ---- a device class ------------------------------------------------------
class Interface:
    # The interface descriptor's own fields, spelled as on the wire: set them in a
    # subclass body (or per instance) and descriptor_block() emits them as they are.
    bInterfaceClass = 0xFF
    bInterfaceSubClass = 0
    bInterfaceProtocol = 0
    string_index = 0  # iInterface, the string index (see USBDevice.add_string)

    def __init__(self):
        self.interface_number = 0
        self.dev = None  # set by USBDevice.add()
        self.func = None  # owning Function (set by USBDevice.add())
        self._endpoints = {}
        for name, spec in self._collect_specs().items():
            ep = Endpoint(spec)
            setattr(self, name, ep)  # shadow the class-level spec
            self._endpoints[(ep.number, ep.dir)] = ep

    @classmethod
    def _collect_specs(cls):
        specs = {}
        for klass in reversed(cls.__mro__):
            for name, value in vars(klass).items():
                if isinstance(value, _EPSpec):
                    specs[name] = value
        return specs

    # --- override points ---
    def extra_descriptors(self) -> bytes:
        """Class-specific descriptor bytes appended after the interface desc."""
        return b""

    def on_control(self, setup: Setup, data: bytes = b""):
        """Handle a class/vendor control request. `data` is the OUT payload (for
        host->device requests). Return bytes (IN) or b'' (OUT ack); raise Stall to
        reject. Default rejects everything."""
        raise Stall

    def set_alt(self, alt: int):
        pass

    def adjust_for_speed(self, speed: int):
        """Resize speed-dependent endpoints for the device's reported speed. Called by
        USBDevice.add() (and USBDevice.set_speed()). Default: no change. Speed-aware classes
        override this to size bulk endpoints (512 B at HS) or iso endpoints (per-microframe
        at HS) - both the descriptor and the runtime mps follow from the endpoint's .mps."""

    def on_iso(self, ep, packets):
        """Isochronous data: one call handles one transfer's packets. On an OUT
        endpoint the host sent data - `packets` is a list of bytes objects, one
        per packet; read them (the return value is ignored). On an IN endpoint
        the host wants data - `packets` is a list of ints, the size it asked for
        per packet; return a list of bytes objects, each up to its requested
        size. An endpoint's direction never changes, so an override only ever
        sees one of the two cases. Default: no data (IN) / discard (OUT)."""
        return []

    def on_out(self, ep, data):
        """Bulk/interrupt OUT data from the host. Override to consume it as it
        arrives; the default queues it on the endpoint for :meth:`Endpoint.read`."""
        ep._put_out(data)

    def on_reset(self):
        """Called when a host attaches (bus reset): endpoint queues are already
        flushed - override to reset class-level state. Default: nothing."""

    def _add_endpoint(self, spec):
        """Register a per-instance endpoint from an :func:`In`/:func:`Out` spec -
        for endpoints whose address is a constructor argument rather than a
        class-level declaration (e.g. HID). Returns the :class:`Endpoint`."""
        ep = Endpoint(spec)
        self._endpoints[(ep.number, ep.dir)] = ep
        return ep

    def _attach(self, dev):
        """Adding a bare Interface wraps it in an anonymous single-interface Function.
        Returns the interface itself - a single-interface class IS its own data plane."""
        Function(interfaces=[self])._attach(dev)
        return self

    def _rekey_endpoints(self):
        """Re-key ``_endpoints`` after routing may have relocated addresses.

        Unlike C, where each alternate setting materializes its own descriptor
        (so two endpoint objects of one interface legitimately share an address),
        here one pipe is ONE Endpoint object reused by every alt setting -- the
        dict is keyed by (number, dir), so relocation must not collapse two
        siblings onto one key."""
        rekeyed = {(ep.number, ep.dir): ep for ep in self._endpoints.values()}
        assert len(rekeyed) == len(self._endpoints), (
            f"endpoint lost in relocation for {self.__class__.__name__}"
        )
        self._endpoints = rekeyed

    # --- descriptor assembly ---
    def descriptor_block(self) -> bytes:
        eps = list(self._endpoints.values())
        blk = core.InterfaceDescriptor(
            bInterfaceNumber=self.interface_number,
            bAlternateSetting=0,
            bNumEndpoints=len(eps),
            bInterfaceClass=self.bInterfaceClass,
            bInterfaceSubClass=self.bInterfaceSubClass,
            bInterfaceProtocol=self.bInterfaceProtocol,
            iInterface=self.string_index,
        ).pack()
        blk += self.extra_descriptors()
        for ep in eps:
            blk += core.EndpointDescriptor(
                bEndpointAddress=ep.addr,
                bmAttributes=core.XFER_BY_NAME[ep.type],
                wMaxPacketSize=ep.mps,
                bInterval=ep.interval,
            ).pack()
        return blk


class Function:
    """A **function**: one class instance grouping one or more USB :class:`Interface`\\ s,
    bound as a unit (the USB-IF Interface Association Descriptor model). Owns the child
    interfaces; a composite function optionally emits an IAD (``iad = True``) and sets the
    device-descriptor class triple (``device_triple``). Most classes are a single interface
    and need no explicit Function - :meth:`USBDevice.add` wraps a bare :class:`Interface` in
    an anonymous one. Mirrors the C ``usbip_function``.

    A subclass either sets ``self.interfaces = (...)`` before ``super().__init__()`` or
    passes the list to ``Function.__init__``. Set ``iad = True`` plus ``iad_class`` /
    ``iad_subclass`` / ``iad_protocol`` (and usually ``device_triple = (0xEF, 0x02, 0x01)``)
    only for a true composite function; CDC/Bluetooth/audio group their interfaces by
    class-specific means and must NOT emit an IAD."""

    iad = False
    # Emit the IAD only when the device is declared composite.
    # A class that groups its own interfaces by class-specific means -- CDC's Union
    # descriptor -- needs none alone, but needs one once other functions share the
    # device, or usbccgp splits it per interface.
    iad_on_composite = False
    iad_class = 0
    iad_subclass = 0
    iad_protocol = 0
    iad_function_str = 0
    device_triple: tuple | None = None  # (cls, sub, proto) applied to the device, or None

    def __init__(self, interfaces=None):
        self.dev = None
        ifaces = interfaces if interfaces is not None else getattr(self, "interfaces", ())
        self._interfaces = list(ifaces)
        for iface in self._interfaces:
            iface.func = self

    @property
    def primary(self):
        """The handle :meth:`USBDevice.add` gives back: this class's data-plane object.
        Default is the function itself; a multi-interface class points at the child
        that carries the data-plane API (CDCACM -> the Data port, UVC -> the streaming
        interface, ...), so one line both adds the class and names what you talk to.
        Mirrors the C ``<class>_add()`` return type."""
        return self

    def enable_msos(self, compatible=b"WINUSB", guid=None):
        """Advertise Microsoft OS descriptors for THIS function (see
        :meth:`USBDevice.enable_msos`). On a composite this is what scopes the
        Compatible ID to this function's interfaces instead of the whole device.
        Call it after the function has been added to a device."""
        self.dev.enable_msos(compatible, guid, function=self)

    def enable_winusb(self, guid=None):
        """:meth:`enable_msos` with the "WINUSB" Compatible ID."""
        self.enable_msos(b"WINUSB", guid or core.DEFAULT_WINUSB_GUID)

    def association_descriptor(self) -> bytes:
        """The 8-byte Interface Association Descriptor, or ``b""`` when this function
        emits none - see ``iad`` / ``iad_on_composite``."""
        composite = self.dev is not None and self.dev.composite
        if not (self.iad or (self.iad_on_composite and composite)):
            return b""
        first = self._interfaces[0].interface_number
        return struct.pack(
            "<BBBBBBBB",
            8,
            core.DT_INTERFACE_ASSOCIATION,
            first,
            len(self._interfaces),
            self.iad_class,
            self.iad_subclass,
            self.iad_protocol,
            self.iad_function_str,
        )

    def _on_added(self):
        """Called by USBDevice.add() once ``self.dev`` is set and the interfaces are
        numbered - the earliest point a class can register string descriptors
        (e.g. an iInterface name). The default forwards to any interface that
        defines its own ``_on_added``."""
        for iface in self._interfaces:
            hook = getattr(iface, "_on_added", None)
            if callable(hook):
                hook()

    def _attach(self, dev):
        """Compose this function onto a device, using only the device's public
        primitives: open a group, claim interface numbers, route endpoints, and
        register the interfaces' handlers. The whole function renders as ONE lazy
        descriptor block (:meth:`descriptor_block`), so IADs, class-specific
        descriptors, alternate settings and relocated endpoint addresses are
        always current. Returns :attr:`primary`, the handle the caller gets back
        from :meth:`USBDevice.add`."""
        self.dev = dev
        self._group = g = dev.add_group(owner=self)
        if self.device_triple is not None:
            dev.set_device_triple(*self.device_triple)
        for iface in self._interfaces:
            iface.dev = dev
            iface.func = self
            iface.interface_number = g.claim_interface(owner=iface)
            iface.adjust_for_speed(dev.speed)  # size speed-aware endpoints before routing
            for ep in list(iface._endpoints.values()):
                dev.route_endpoint(ep, owner=iface)
                ep.on_out = iface.on_out
                ep.on_iso = iface.on_iso
            iface._rekey_endpoints()
            dev.on_control(iface.interface_number, iface.on_control)
            dev.on_set_alt(iface.interface_number, iface.set_alt)
            dev.add_reset_hook(iface.on_reset)
            dev.add_speed_hook(iface.adjust_for_speed)
            if not dev.has_control_fallback():  # the device's first interface is the
                dev.on_control(None, iface.on_control)  # fallback for non-interface recipients
        g.add(self.descriptor_block)
        self._on_added()
        return self.primary

    def descriptor_block(self) -> bytes:
        return self.association_descriptor() + b"".join(
            iface.descriptor_block() for iface in self._interfaces
        )
