# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
Mass Storage - host driver: Bulk-Only Transport (CBW/CSW) over two bulk EPs.

Bulk endpoint addresses are discovered from the configuration descriptor (real
devices don't all use 0x01/0x81), so this works against any MSC device.
"""

from __future__ import annotations

from ...core import BULK, DT_ENDPOINT, DT_INTERFACE, Stall, iter_descriptors
from ...host import Driver
from ..device.msc import (
    CBW_SIGNATURE,
    CSW_SIGNATURE,
    GET_MAX_LUN,
    OP_INQUIRY,
    OP_READ_10,
    OP_READ_CAPACITY_10,
    OP_WRITE_10,
)
from . import fetch_config


class MSCDriver(Driver):
    matches = {"bInterfaceClass": 0x08}

    def __init__(self, handle):
        super().__init__(handle)
        self._tag = 0
        self.ep_in, self.ep_out = 0x81, 0x01  # sensible defaults
        self._discover_endpoints()

    def _discover_endpoints(self):
        try:
            cfg = fetch_config(self.handle)
        except Exception:
            return
        in_msc = False
        for desc in iter_descriptors(cfg):
            if desc[1] == DT_INTERFACE and len(desc) >= 9:
                in_msc = desc[5] == 0x08  # bInterfaceClass == Mass Storage
            elif desc[1] == DT_ENDPOINT and in_msc and len(desc) >= 5:
                addr, attr = desc[2], desc[3]
                if attr & 0x03 == BULK:
                    if addr & 0x80:
                        self.ep_in = addr
                    else:
                        self.ep_out = addr

    def max_lun(self) -> int:
        return self.handle.control(0xA1, GET_MAX_LUN, 0, 0, 1)[0]

    # ---- Bulk-Only Transport ----
    def _cbw(self, cdb, dlen, direction_in, lun=0):
        self._tag += 1
        flags = 0x80 if direction_in else 0x00
        cbw = (
            CBW_SIGNATURE
            + self._tag.to_bytes(4, "little")
            + dlen.to_bytes(4, "little")
            + bytes([flags, lun, len(cdb)])
            + bytes(cdb).ljust(16, b"\x00")
        )
        self.handle.bulk_out(self.ep_out, cbw)

    def _csw(self) -> int:
        csw = self.handle.bulk_in(self.ep_in, 13)
        if len(csw) != 13 or csw[:4] != CSW_SIGNATURE:
            raise OSError("bad CSW")
        return csw[12]  # bCSWStatus: 0 passed, 1 failed, 2 phase error

    def _data_phase(self, transfer, addr):
        """Run a data phase, recovering if the device halts the pipe.

        A device that cannot produce (or will not accept) the data the CBW
        promised STALLs the pipe to end the phase early - BOT 1.0 section 6.7.
        Clearing the halt is what lets the CSW be read afterwards, so a failed
        command reports its status instead of hanging the transport."""
        try:
            return transfer()
        except Stall:
            self.handle.clear_halt(addr)
            return b""

    def command_in(self, cdb, dlen, lun=0):
        self._cbw(cdb, dlen, True, lun)
        data = (
            self._data_phase(lambda: self.handle.bulk_in(self.ep_in, dlen), self.ep_in)
            if dlen
            else b""
        )
        return data, self._csw()

    def command_out(self, cdb, data=b"", lun=0):
        self._cbw(cdb, len(data), False, lun)
        if data:
            self._data_phase(lambda: self.handle.bulk_out(self.ep_out, data), self.ep_out)
        return self._csw()

    # ---- convenience SCSI ops ----
    # `lun` picks the logical unit where there is more than one (max_lun() above
    # reports the last one's number); the default suits a single-LUN device.
    def inquiry(self, lun=0) -> bytes:
        data, _ = self.command_in([OP_INQUIRY, 0, 0, 0, 36, 0], 36, lun)
        return data

    def read_capacity(self, lun=0):
        data, _ = self.command_in([OP_READ_CAPACITY_10] + [0] * 9, 8, lun)
        last = int.from_bytes(data[0:4], "big")
        block_size = int.from_bytes(data[4:8], "big")
        return last + 1, block_size

    @staticmethod
    def _cdb10(opcode, lba, count) -> bytes:
        """A 10-byte READ/WRITE CDB: opcode, LBA (BE32), transfer length (BE16)."""
        return (
            bytes([opcode, 0])
            + lba.to_bytes(4, "big")
            + bytes([0])
            + count.to_bytes(2, "big")
            + bytes([0])
        )

    def read_blocks(self, lba, count, block_size, lun=0) -> bytes:
        cdb = self._cdb10(OP_READ_10, lba, count)
        data, status = self.command_in(cdb, count * block_size, lun)
        if status:
            raise OSError(f"READ(10) failed, status {status}")
        return data

    def write_blocks(self, lba, data, block_size, lun=0) -> int:
        count = len(data) // block_size
        cdb = self._cdb10(OP_WRITE_10, lba, count)
        status = self.command_out(cdb, data, lun)
        if status:
            raise OSError(f"WRITE(10) failed, status {status}")
        return count
