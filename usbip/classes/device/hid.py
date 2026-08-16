# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
HID (class 0x03) - generic device class (USB HID 1.11).

A *generic* HID function: hand it any Report descriptor and it presents a proper
HID interface - keyboard, mouse, consumer control, or a vendor-defined raw device.
It speaks the full HID 1.11 request set over EP0 (GET/SET_REPORT, GET/SET_IDLE,
GET/SET_PROTOCOL) plus the required interrupt IN and an optional interrupt OUT for
Output reports (HID 1.11 Sec.4.4, Sec.7.2, Appendix G).

Quick start::

    from usbip.classes.device import hid
    iface = hid.HID(hid.mouse_report_descriptor(),
                    subclass=hid.SUBCLASS_BOOT,
                    protocol=hid.PROTOCOL_MOUSE)
    dev.add(iface)
    iface.send_report(bytes([0, 5, 0, 0]))          # buttons, dx, dy, wheel

Subclassing still works::

    class Keyboard(hid.HID):
        report_descriptor = hid.keyboard_report_descriptor()
"""

from __future__ import annotations

import struct

from ... import core
from ...core import Stall
from ...device import In, Out
from ...function import Interface

# ---- class-specific requests (HID 1.11 Sec.7.2) -----------------------------
GET_REPORT, GET_IDLE, GET_PROTOCOL = 0x01, 0x02, 0x03
SET_REPORT, SET_IDLE, SET_PROTOCOL = 0x09, 0x0A, 0x0B

# report types (the high byte of wValue), HID 1.11 Sec.7.2.1
REPORT_INPUT, REPORT_OUTPUT, REPORT_FEATURE = 1, 2, 3

# descriptor type codes (HID 1.11 Sec.7.1.1)
HID_DT_HID, HID_DT_REPORT, HID_DT_PHYSICAL = 0x21, 0x22, 0x23

# bInterfaceSubClass (HID 1.11 Sec.4.2)
SUBCLASS_NONE, SUBCLASS_BOOT = 0, 1

# bInterfaceProtocol (HID 1.11 Sec.4.3) - only meaningful with SUBCLASS_BOOT
PROTOCOL_NONE, PROTOCOL_KEYBOARD, PROTOCOL_MOUSE = 0, 1, 2

# Get/Set_Protocol values (HID 1.11 Sec.7.2.5) - which layout the host selected,
# distinct from the bInterfaceProtocol above
BOOT_PROTOCOL, REPORT_PROTOCOL = 0, 1


def hid_descriptor(report_len, *, bcd_hid=0x0111, country=0, extra_pairs=()) -> bytes:
    """The 9-byte HID descriptor that points at the Report descriptor (Sec.6.2.1).

    `extra_pairs` is an optional list of additional (bDescriptorType, length) tuples
    (e.g. a Physical descriptor); the Report pair is always emitted first."""
    pairs = [(HID_DT_REPORT, report_len), *extra_pairs]
    blk = struct.pack("<BBHBB", 6 + 3 * len(pairs), HID_DT_HID, bcd_hid, country, len(pairs))
    for dtype, dlen in pairs:
        blk += struct.pack("<BH", dtype, dlen)
    return blk


# ---- report-descriptor short-item builder (HID 1.11 Sec.6.2.2) --------------
MAIN, GLOBAL, LOCAL = 0, 1, 2  # item types (bType)


def ri(tag, typ, *data) -> bytes:
    """One HID short item: prefix byte (bTag<<4 | bType<<2 | bSize) + 0/1/2/4 data
    bytes. `bSize` is derived from the number of data bytes passed (Sec.6.2.2.2)."""
    size = {0: 0, 1: 1, 2: 2, 4: 3}[len(data)]
    return bytes([(tag << 4) | (typ << 2) | size, *data])


def _u8(tag, typ, value):
    return ri(tag, typ, value & 0xFF)


def _u16(tag, typ, value):
    return ri(tag, typ, value & 0xFF, (value >> 8) & 0xFF)


# convenience item emitters (auto-size to 1 or 2 bytes where it matters)
def usage_page(value):
    return _u8(0x0, GLOBAL, value) if value <= 0xFF else _u16(0x0, GLOBAL, value)


def logical_min(value):
    return _u8(0x1, GLOBAL, value) if -128 <= value <= 127 else _u16(0x1, GLOBAL, value)


def logical_max(value):
    return _u8(0x2, GLOBAL, value) if 0 <= value <= 127 else _u16(0x2, GLOBAL, value)


def report_size(value):
    return _u8(0x7, GLOBAL, value)


def report_id(value):
    return _u8(0x8, GLOBAL, value)


def report_count(value):
    return _u8(0x9, GLOBAL, value)


def usage(value):
    return _u8(0x0, LOCAL, value) if value <= 0xFF else _u16(0x0, LOCAL, value)


def usage_min(value):
    return _u8(0x1, LOCAL, value)


def usage_max(value):
    return _u8(0x2, LOCAL, value)


def collection(value):
    return ri(0xA, MAIN, value)


def end_collection():
    return ri(0xC, MAIN)


def input_(value):
    return ri(0x8, MAIN, value)


def output(value):
    return ri(0x9, MAIN, value)


def feature(value):
    return ri(0xB, MAIN, value)


# Laid out to read as HID items, one logical group per line with its spec meaning
# alongside, so the formatter is fenced off below.
# (The fence must be exactly `# fmt: off` for ruff/black to honour it.)
# fmt: off
# ---- canned report descriptors -------------------------------------------
def keyboard_report_descriptor() -> bytes:
    """Boot-protocol keyboard: 8-byte Input report (modifier bitmap, reserved byte,
    six key codes) + 1-byte Output report (Num/Caps/Scroll Lock + Compose/Kana LEDs)."""
    return b"".join([
        usage_page(0x01), usage(0x06),          # Generic Desktop, Keyboard
        collection(0x01),                       # Application
        usage_page(0x07),                       # Keyboard/Keypad
        usage_min(0xE0), usage_max(0xE7),       # Left Control .. Right GUI
        logical_min(0), logical_max(1),
        report_size(1), report_count(8), input_(0x02),    # 8 modifier bits
        report_count(1), report_size(8), input_(0x03),    # reserved byte
        report_count(5), report_size(1),
        usage_page(0x08), usage_min(1), usage_max(5),      # LED page: NumLock .. Kana
        output(0x02),                                      # 5 LED bits
        report_count(1), report_size(3), output(0x03),     # 3 bits padding
        report_count(6), report_size(8),
        logical_min(0), logical_max(101),
        usage_page(0x07), usage_min(0), usage_max(101),
        input_(0x00),                                      # 6 key codes (array)
        end_collection(),
    ])


def mouse_report_descriptor() -> bytes:
    """Boot-compatible mouse: 4-byte report = [buttons, dx, dy, wheel]."""
    return b"".join([
        usage_page(0x01), usage(0x02),          # Generic Desktop, Mouse
        collection(0x01),                       # Application
        usage(0x01),                            # Pointer
        collection(0x00),                       # Physical
        usage_page(0x09),                       # Buttons
        usage_min(1), usage_max(3),
        logical_min(0), logical_max(1),
        report_count(3), report_size(1), input_(0x02),   # 3 button bits
        report_count(1), report_size(5), input_(0x03),   # 5 bits padding
        usage_page(0x01),                       # Generic Desktop
        usage(0x30), usage(0x31), usage(0x38),  # X, Y, Wheel
        logical_min(-127), logical_max(127),
        report_size(8), report_count(3), input_(0x06),   # relative X/Y/wheel
        end_collection(),
        end_collection(),
    ])


def consumer_report_descriptor() -> bytes:
    """Consumer control: 1-byte report, 8 media-key bits (play/next/prev/stop/
    mute/vol+/vol-/eject)."""
    keys = [0xCD, 0xB5, 0xB6, 0xB7, 0xE2, 0xE9, 0xEA, 0xB8]
    return b"".join([
        usage_page(0x0C), usage(0x01),          # Consumer, Consumer Control
        collection(0x01),                       # Application
        logical_min(0), logical_max(1),
        report_size(1), report_count(8),
        *[usage(k) for k in keys],
        input_(0x02),                           # 8 momentary bits
        end_collection(),
    ])


def vendor_report_descriptor(in_size=8, out_size=8, usage_page_id=0xFF00) -> bytes:
    """A vendor-defined raw HID device: `in_size`-byte Input report + `out_size`-byte
    Output report on a vendor usage page. Round-trips bytes via interrupt IN/OUT
    and Get/Set_Report; no OS HID consumer claims it."""
    items = [
        usage_page(usage_page_id), usage(0x01),
        collection(0x01),                       # Application
        logical_min(0), logical_max(255),       # 0..255 -> 2-byte logical_max
        report_size(8),
        usage(0x01), report_count(in_size), input_(0x02),
    ]
    if out_size:
        items += [usage(0x01), report_count(out_size), output(0x02)]
    items.append(end_collection())
    return b"".join(items)
# fmt: on


class HID(Interface):
    """A generic HID interface. Pass a report descriptor (or set the class
    attribute) and, optionally, callbacks for Get/Set_Report and Output reports."""

    bInterfaceClass = 0x03
    bInterfaceSubClass = SUBCLASS_NONE  # SUBCLASS_BOOT for a boot interface
    bInterfaceProtocol = PROTOCOL_NONE  # meaningful only with SUBCLASS_BOOT
    report_descriptor = b""  # class-level default (subclassing/back-compat)

    def __init__(
        self,
        report_descriptor=None,
        *,
        subclass=None,
        protocol=None,
        in_ep=0x81,
        in_mps=64,
        in_interval=10,
        out_ep=None,
        out_mps=64,
        out_interval=10,
        country=0,
        bcd_hid=0x0111,
        get_report=None,
        set_report=None,
        on_output=None,
    ):
        super().__init__()
        # endpoints are per-instance: their addresses are constructor arguments,
        # not class-level In/Out declarations
        self.in_ep = self._add_endpoint(In(in_ep, "interrupt", in_mps, in_interval))
        self.out_ep = None
        if out_ep is not None:
            self.out_ep = self._add_endpoint(Out(out_ep, "interrupt", out_mps, out_interval))

        if subclass is not None:
            self.bInterfaceSubClass = subclass
        if protocol is not None:
            self.bInterfaceProtocol = protocol
        rd = report_descriptor if report_descriptor is not None else type(self).report_descriptor
        self.report_descriptor = bytes(rd)
        self.country = country
        self.bcd_hid = bcd_hid

        self._get_report_cb = get_report
        self._set_report_cb = set_report
        self._on_output_cb = on_output

        self.protocol = REPORT_PROTOCOL  # default protocol after enumeration
        self._idle = {}  # report-id -> idle duration (x4 ms)
        self._last_input = b""  # last Input report sent (for GET_REPORT)
        self._features = {}  # report-id -> last Feature report
        self.last_output = b""  # last Output report received

    # --- descriptors ---
    def extra_descriptors(self) -> bytes:
        return hid_descriptor(
            len(self.report_descriptor), bcd_hid=self.bcd_hid, country=self.country
        )

    # --- control (EP0): standard descriptor reads + HID class requests ---
    def on_control(self, setup, data=b""):
        if setup.bRequest == 0x06 and setup.type == core.STANDARD:  # GET_DESCRIPTOR
            dt = setup.wValue >> 8
            if dt == HID_DT_REPORT:
                return self.report_descriptor
            if dt == HID_DT_HID:
                return self.extra_descriptors()
            raise Stall  # e.g. Physical: none

        if setup.type != core.CLASS:
            raise Stall

        req, rtype, rid = setup.bRequest, setup.wValue >> 8, setup.wValue & 0xFF
        if req == GET_REPORT:
            return self._get_report(rtype, rid, setup.wLength)
        if req == SET_REPORT:
            self._set_report(rtype, rid, bytes(data))
            return b""
        if req == GET_IDLE:
            return bytes([self._idle.get(rid, 0)])
        if req == SET_IDLE:  # wValue hi = duration
            self._idle[rid] = setup.wValue >> 8
            return b""
        if req == GET_PROTOCOL:
            return bytes([self.protocol])
        if req == SET_PROTOCOL:
            self.protocol = setup.wValue & 0xFF
            return b""
        raise Stall

    def _get_report(self, rtype, rid, length):
        if self._get_report_cb is not None:
            return self._get_report_cb(rtype, rid, length)
        if rtype == REPORT_FEATURE:
            return self._features.get(rid, b"")
        if rtype == REPORT_OUTPUT:
            return self.last_output
        return self._last_input or b"\x00" * min(length or 1, 64)

    def _set_report(self, rtype, rid, payload):
        if self._set_report_cb is not None:
            self._set_report_cb(rtype, rid, payload)
            return
        if rtype == REPORT_FEATURE:
            self._features[rid] = payload
        else:
            self._handle_output(payload)

    # --- Output reports arriving on the interrupt OUT pipe ---
    def on_out(self, ep, data):
        self._handle_output(bytes(data))

    def _handle_output(self, payload):
        self.last_output = payload
        if self._on_output_cb is not None:
            self._on_output_cb(payload)

    # --- device -> host: send an Input report on the interrupt IN pipe ---
    def send_report(self, report: bytes):
        self._last_input = bytes(report)
        self.in_ep.write(report)
