# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
CDC-ACM (virtual serial port) - device class (Communications + Data interfaces).

The class implements the CDC protocol; the app supplies callbacks for the events
it cares about (port opened/closed, line coding changed, bytes received) and uses
the port's write() to transmit. Mirrors the C library's src/classes/device/cdc_acm.c - protocol
handling lives here, not in the example. Built only on the public device API.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ...core import SPEED_HIGH, Stall
from ...device import In, Out
from ...function import Function, Interface

# CDC PSTN class request codes (USB CDC 1.1 Sec.6.2).
_SET_LINE_CODING = 0x20
_GET_LINE_CODING = 0x21
_SET_CONTROL_LINE_STATE = 0x22


@dataclass
class LineCoding:
    """USB CDC line coding (serial line parameters)."""

    baud: int = 115200
    stop_bits: int = 0  # 0 = 1 stop bit, 1 = 1.5, 2 = 2
    parity: int = 0  # 0 none, 1 odd, 2 even, 3 mark, 4 space
    data_bits: int = 8  # 5, 6, 7, 8, or 16

    def pack(self) -> bytes:
        return struct.pack("<IBBB", self.baud, self.stop_bits, self.parity, self.data_bits)

    @classmethod
    def unpack(cls, data) -> LineCoding:
        baud, stop, parity, bits = struct.unpack("<IBBB", bytes(data[:7]))
        return cls(baud, stop, parity, bits)


def _functional_descriptors() -> bytes:
    header = struct.pack("<BBBH", 5, 0x24, 0x00, 0x0110)  # Header
    call_mgmt = struct.pack("<BBBBB", 5, 0x24, 0x01, 0x00, 1)  # Call Management
    acm = struct.pack("<BBBB", 4, 0x24, 0x02, 0x02)  # ACM
    union = struct.pack("<BBBBB", 5, 0x24, 0x06, 0, 1)  # Union (ctrl=0, sub=1)
    return header + call_mgmt + acm + union


class CDCComm(Interface):
    """CDC Communications interface: answers the line-coding / control-line-state
    requests and drives the open / close / line-coding callbacks. `port` is the
    paired Data function passed to those callbacks (use port.write() to send)."""

    bInterfaceClass = 0x02  # Communications
    bInterfaceSubClass = 0x02  # Abstract Control Model
    bInterfaceProtocol = 0x01  # AT commands
    notify_ep = In(0x82, "interrupt", mps=16, interval=9)  # 9 ms FS / 32 ms HS, like C

    def __init__(self, port=None, on_open=None, on_close=None, on_line_coding=None):
        super().__init__()
        self.port = port
        self._on_open = on_open
        self._on_close = on_close
        self._on_line_coding = on_line_coding
        self.line_coding = LineCoding()
        self.is_open = False

    def extra_descriptors(self) -> bytes:
        return _functional_descriptors()

    def on_control(self, setup, data=b""):
        if setup.bRequest == _SET_LINE_CODING:
            if len(data) >= 7:
                self.line_coding = LineCoding.unpack(data)
                if self._on_line_coding:
                    self._on_line_coding(self.port, self.line_coding)
            return b""
        if setup.bRequest == _GET_LINE_CODING:
            return self.line_coding.pack()
        if setup.bRequest == _SET_CONTROL_LINE_STATE:
            opened = bool(setup.wValue & 0x01)  # bit0 = DTR -> host opened the port
            if opened != self.is_open:
                self.is_open = opened
                if opened and self._on_open:
                    self._on_open(self.port, self.line_coding)
                elif not opened and self._on_close:
                    self._on_close(self.port)
            return b""
        raise Stall


class CDCData(Interface):
    """CDC Data interface: bulk in/out. Received bytes go to on_rx, or echo if none."""

    bInterfaceClass = 0x0A  # CDC Data
    in_ep = In(0x81, "bulk", mps=64)  # declared IN-first: emission order matches C
    out_ep = Out(0x01, "bulk", mps=64)

    def __init__(self, on_rx=None):
        super().__init__()
        self._on_rx = on_rx

    def adjust_for_speed(self, speed):
        self.in_ep.mps = self.out_ep.mps = 512 if speed >= SPEED_HIGH else 64  # HS bulk = 512

    def on_out(self, ep, data: bytes):
        if self._on_rx:
            self._on_rx(self, data)
        else:
            self.in_ep.write(data)  # default: echo

    def write(self, data: bytes):
        """Transmit bytes to the host (device -> host)."""
        self.in_ep.write(data)


# Communications / Abstract Control Model / AT commands. The same triple names the
# function from an IAD (composite) or the device descriptor (alone).
CDC_DEVICE_TRIPLE = (0x02, 0x02, 0x01)


class CDCACM(Function):
    """A CDC-ACM serial port: a two-interface function (Communications + Data) bound by a
    CDC Union functional descriptor. Mirrors the C cdc_acm function.

    The pair must reach the host as ONE function or no COM port forms on Windows, so
    the function is named either by the device-descriptor triple (when it is alone on
    the device) or by an IAD (on a ``USBDevice.set_composite()`` device, where the
    triple is pinned to EF/02/01). Linux pairs the interfaces from the Union
    functional descriptor and needs neither.

    ``dev.add(CDCACM(...))`` returns the Data interface - the port you write to; the
    callbacks receive that same object as `port`, and `name` labels it (iInterface /
    IAD iFunction). Mirrors C ``cdc_acm_add()``, which returns a ``cdc_port *``."""

    iad_on_composite = True
    iad_class, iad_subclass, iad_protocol = CDC_DEVICE_TRIPLE

    def __init__(self, on_rx=None, on_open=None, on_close=None, on_line_coding=None, name=None):
        self.data = CDCData(on_rx)
        self.comm = CDCComm(
            self.data, on_open=on_open, on_close=on_close, on_line_coding=on_line_coding
        )
        self.interfaces = (self.comm, self.data)  # interface 0: Comm, interface 1: Data
        self.name = name
        super().__init__()

    @property
    def primary(self):
        return self.data  # the port: write() sends, and the callbacks are handed it

    def _on_added(self):
        # Alone on the device, the CDC triple at device level says "these interfaces
        # are one function", as the IAD does on a composite.
        # Being non-zero also keeps usbccgp away: it loads on bDeviceClass 0x00 with
        # several interfaces, and splits the pair so no COM port forms.
        if not self.dev.composite:
            self.dev.set_device_triple(*CDC_DEVICE_TRIPLE)
        # Mirrors C cdc_acm: ops.name becomes the Comm interface's iInterface and
        # the IAD's iFunction, so the host shows the port under that label.
        if self.name:
            istr = self.dev.add_string(self.name)
            self.comm.string_index = istr
            self.iad_function_str = istr
        super()._on_added()
