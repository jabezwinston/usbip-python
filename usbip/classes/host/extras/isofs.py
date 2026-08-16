# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
Read-only ISO 9660 reader over a BlockDevice (2048-byte logical sectors).

Parses the Primary Volume Descriptor (sector 16), then walks directory records.
Strips the ``;1`` version suffix and matches names case-insensitively. Joliet
(the supplementary UCS-2 descriptor) is not parsed - primary names only.
"""

from __future__ import annotations

SECTOR = 2048


class IsoFs:
    def __init__(self, dev):
        self.dev = dev
        pvd = dev.read(16 * SECTOR, SECTOR)
        if pvd[0] != 1 or pvd[1:6] != b"CD001":
            raise ValueError("not an ISO 9660 volume")
        self.root = self._record(pvd, 156)  # root directory record lives in the PVD

    @staticmethod
    def _record(data, off):
        return {
            "len": data[off],
            "extent": int.from_bytes(data[off + 2 : off + 6], "little"),
            "size": int.from_bytes(data[off + 10 : off + 14], "little"),
            "is_dir": bool(data[off + 25] & 0x02),
            "name": data[off + 33 : off + 33 + data[off + 32]],
        }

    def _read_dir(self, rec):
        data = self.dev.read(rec["extent"] * SECTOR, rec["size"])
        out, i = [], 0
        while i < len(data):
            rlen = data[i]
            if rlen == 0:  # rest of the sector is padding
                i = (i // SECTOR + 1) * SECTOR
                continue
            record = self._record(data, i)
            if record["name"] not in (b"\x00", b"\x01"):  # skip '.' and '..'
                name = record["name"].decode("ascii", "replace").split(";", 1)[0]
                out.append((name, record))
            i += rlen
        return out

    def _find(self, path):
        rec = self.root
        for part in [seg for seg in path.strip("/").split("/") if seg]:
            match = next((record for name, record in self._read_dir(rec) if name.upper() == part.upper()), None)
            if match is None:
                raise FileNotFoundError(path)
            rec = match
        return rec

    # ---- public API ----
    def listdir(self, path="/"):
        rec = self._find(path)
        if not rec["is_dir"]:
            raise NotADirectoryError(path)
        return [name for name, _ in self._read_dir(rec)]

    def read_file(self, path):
        rec = self._find(path)
        if rec["is_dir"]:
            raise IsADirectoryError(path)
        return self.dev.read(rec["extent"] * SECTOR, rec["size"])
