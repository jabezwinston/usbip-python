# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 05-June-2026
"""
USB Bluetooth (class 0xE0/0x01/0x01) - device-side HCI transport.

Mirrors the C classes/device/bluetooth.c. This is a *minimal transport*, nothing
more: it ferries HCI **commands** (EP0 class control OUT), HCI **events**
(interrupt IN) and **ACL** data (bulk OUT/IN) between the USB host and a
controller the application supplies. No HCI logic lives here - the class never
inspects a command or synthesizes an event.

This follows the Bluetooth USB Transport Layer (Core spec Vol 4 Part B):
  - device class 0xE0 / subclass 0x01 (RF) / protocol 0x01 (Bluetooth)
  - interface 0: interrupt-IN (events) + bulk-OUT (ACL out) + bulk-IN (ACL in)
  - interface 1: SCO isochronous (6 alt settings) - descriptor-only / no-op

Use ``dev.add(Bluetooth(on_command=..., on_acl=...))``.
"""

from __future__ import annotations

from ... import core
from ...core import Stall
from ...device import In, Out
from ...function import Function, Interface

# H4 packet-type prefixes. An HCI-over-TCP link speaks H4, but these USB endpoints
# carry *raw* HCI, so the prefix is added and stripped at that boundary only.
H4_CMD, H4_ACL, H4_SCO, H4_EVT, H4_ISO = 0x01, 0x02, 0x03, 0x04, 0x05

# USB Bluetooth class triple
BT_CLASS, BT_SUBCLASS, BT_PROTOCOL = 0xE0, 0x01, 0x01

# endpoint addresses (match a real Bluetooth dongle)
EP_EVENT = 0x81  # interrupt IN - HCI events
EP_ACL_OUT = 0x02  # bulk OUT     - ACL host -> controller
EP_ACL_IN = 0x82  # bulk IN      - ACL controller -> host
EP_SCO_OUT = 0x03  # iso OUT      - SCO (no-op)
EP_SCO_IN = 0x83  # iso IN       - SCO (no-op)

# SCO isochronous alt-setting packet sizes (alt 0 = zero-bandwidth placeholder)
SCO_ALT_SIZES = (0, 9, 17, 25, 33, 49)


class BluetoothInterface(Interface):
    """Interface 0 - the HCI/ACL transport.

    The host sends HCI commands on EP0 (class control OUT) and ACL on the bulk
    OUT endpoint; the controller sends HCI events on the interrupt IN and ACL on
    the bulk IN. Wire ``on_command``/``on_acl`` to a controller and call
    ``send_event``/``send_acl`` to push toward the host.
    """

    bInterfaceClass = BT_CLASS
    bInterfaceSubClass = BT_SUBCLASS
    bInterfaceProtocol = BT_PROTOCOL
    event_in = In(EP_EVENT, "interrupt", mps=16, interval=1)
    acl_out = Out(EP_ACL_OUT, "bulk", mps=64)
    acl_in = In(EP_ACL_IN, "bulk", mps=64)

    def __init__(self, on_command=None, on_acl=None):
        super().__init__()
        self.on_command = on_command or (lambda cmd: None)
        self.on_acl = on_acl or (lambda pdu: None)
        self._acl_rx = bytearray()

    # --- host -> controller ------------------------------------------------
    def on_control(self, setup, data=b""):
        """An HCI command arrives as a class control OUT (bmRequestType 0x20)."""
        if setup.type == core.CLASS and setup.direction == core.OUT:
            self.on_command(bytes(data))
            return b""
        raise Stall

    def on_out(self, ep, data):
        """ACL OUT. The host may split one PDU across 64-byte URBs, so reassemble
        by the 2-byte little-endian data length in the ACL header (offset 2)."""
        self._acl_rx += bytes(data)
        while len(self._acl_rx) >= 4:
            plen = self._acl_rx[2] | (self._acl_rx[3] << 8)
            if len(self._acl_rx) < 4 + plen:
                break
            pdu = bytes(self._acl_rx[: 4 + plen])
            del self._acl_rx[: 4 + plen]
            self.on_acl(pdu)

    def on_reset(self):
        """A host (re)attached - drop any half-assembled ACL so it can't bleed
        into the new session (the endpoint queues are flushed by the core)."""
        self._acl_rx.clear()

    # --- controller -> host ------------------------------------------------
    def send_event(self, evt):
        """Queue a raw HCI event for the interrupt-IN endpoint."""
        self.event_in.write(bytes(evt))

    def send_acl(self, pdu):
        """Queue an ACL PDU (4-byte header + payload) for the bulk-IN endpoint."""
        self.acl_in.write(bytes(pdu))


class _ScoInterface(Interface):
    """Interface 1 - SCO (voice) isochronous endpoints.

    Declared with the 6 standard alt settings purely for dongle fidelity; SCO is
    not routed (on_iso returns nothing). The two pipes ARE registered with the
    device - one Endpoint object each, shared by every alt setting - so the
    allocator can relocate them on a composite where another function holds 3/OUT
    or 3/IN; the emitted descriptors follow the assigned address. C's bt_build
    routes each alt through usbip_interface_add_endpoint for the same reason.
    """

    bInterfaceClass = BT_CLASS
    bInterfaceSubClass = BT_SUBCLASS
    bInterfaceProtocol = BT_PROTOCOL
    sco_out = Out(EP_SCO_OUT, "iso", mps=0, interval=1)
    sco_in = In(EP_SCO_IN, "iso", mps=0, interval=1)

    def descriptor_block(self) -> bytes:
        out = b""
        for alt, size in enumerate(SCO_ALT_SIZES):
            out += core.InterfaceDescriptor(
                bInterfaceNumber=self.interface_number,
                bAlternateSetting=alt,
                bNumEndpoints=2,
                bInterfaceClass=BT_CLASS,
                bInterfaceSubClass=BT_SUBCLASS,
                bInterfaceProtocol=BT_PROTOCOL,
            ).pack()

            out += core.EndpointDescriptor(
                bEndpointAddress=self.sco_out.addr,
                bmAttributes=core.ISO,
                wMaxPacketSize=size,
                bInterval=1,
            ).pack()

            out += core.EndpointDescriptor(
                bEndpointAddress=self.sco_in.addr,
                bmAttributes=core.ISO,
                wMaxPacketSize=size,
                bInterval=1,
            ).pack()
        return out

    def set_alt(self, alt: int):
        pass  # SCO not routed

    def on_iso(self, ep, lengths):
        return []  # no SCO data


class Bluetooth(Function):
    """A Bluetooth dongle (HCI transport): the HCI interface plus (for Windows' bthusb)
    the SCO isochronous interface. The two interfaces are bound by the device class
    0xE0/0x01/0x01, not an IAD. Mirrors the C bluetooth function.

    ``dev.add(Bluetooth(...))`` sets the device class to 0xE0/0x01/0x01 and returns the
    BluetoothInterface: call ``send_event``/``send_acl`` to push toward the host, and
    wire ``on_command``/``on_acl`` to your controller."""

    device_triple = (BT_CLASS, BT_SUBCLASS, BT_PROTOCOL)

    def __init__(self, *, on_command=None, on_acl=None, with_sco=True):
        self.hci = BluetoothInterface(on_command, on_acl)
        self.interfaces = (self.hci, _ScoInterface()) if with_sco else (self.hci,)
        super().__init__()

    @property
    def primary(self):
        return self.hci
