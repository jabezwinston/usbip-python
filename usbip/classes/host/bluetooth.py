# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 05-June-2026
"""
Bluetooth (class 0xE0) - generic host driver for the USBIP BT dongle.

Discovers the HCI/ACL endpoints from the configuration descriptor and exposes
the USB Bluetooth transport: send HCI commands on EP0, read HCI events from the
interrupt IN, and exchange ACL over the bulk pair. This is what the loopback and
cross-language tests drive; a real Bluetooth host stack (BlueZ via the kernel
``btusb`` driver) talks to the dongle the same way over the wire.
"""

from __future__ import annotations

import struct

from ...core import BULK, DT_ENDPOINT, DT_INTERFACE, INTERRUPT, iter_descriptors
from ...host import Driver
from ..device.bluetooth import BT_CLASS
from . import fetch_config

# HCI event codes (BT Core, Vol 4 Part E Sec.7.7)
EVT_COMMAND_COMPLETE = 0x0E
EVT_COMMAND_STATUS = 0x0F
EVT_LE_META = 0x3E

# a few common opcodes (OGF<<10 | OCF)
OP_RESET = 0x0C03
OP_READ_BD_ADDR = 0x1009


class BluetoothDriver(Driver):
    matches = {"bInterfaceClass": BT_CLASS}

    # --- discovery: parse the config descriptor once, cache the result -----
    def _discover(self):
        if getattr(self, "_eps", None) is not None:
            return self._eps
        info = {"event_in": 0x81, "acl_in": 0x82, "acl_out": 0x02}
        try:
            cfg = fetch_config(self.handle)
        except Exception:
            self._eps = info
            return info
        in_bt = False
        for desc in iter_descriptors(cfg):
            if desc[1] == DT_INTERFACE and len(desc) >= 9:
                in_bt = desc[5] == BT_CLASS and desc[3] == 0  # BT class, alt 0
            elif desc[1] == DT_ENDPOINT and in_bt and len(desc) >= 7:
                addr, attr = desc[2], desc[3] & 0x03
                if attr == INTERRUPT and addr & 0x80:  # interrupt IN -> events
                    info["event_in"] = addr
                elif attr == BULK:  # bulk -> ACL
                    info["acl_in" if addr & 0x80 else "acl_out"] = addr
        self._eps = info
        return info

    # --- HCI commands (EP0) + events (interrupt IN) ------------------------
    def send_command(self, cmd) -> int:
        """Send a raw HCI command packet (opcode-LE + plen + params) on EP0."""
        return self.handle.control(0x20, 0x00, 0x0000, 0x0000, bytes(cmd))

    def command(self, opcode, params=b"") -> int:
        """Build and send an HCI command from (opcode, parameters)."""
        return self.send_command(struct.pack("<HB", opcode, len(params)) + bytes(params))

    def recv_event(self, length=257) -> bytes:
        data = self._discover()
        return self.handle.interrupt_in(data["event_in"], length)

    def reset(self) -> bytes:
        """HCI Reset (0x0C03); returns the Command Complete event."""
        self.command(OP_RESET)
        return self.recv_event()

    # --- ACL over the bulk pair --------------------------------------------
    def send_acl(self, pdu) -> int:
        data = self._discover()
        return self.handle.bulk_out(data["acl_out"], bytes(pdu))

    def recv_acl(self, length=1024) -> bytes:
        data = self._discover()
        return self.handle.bulk_in(data["acl_in"], length)
