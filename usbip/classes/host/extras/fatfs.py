# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
Read-only FAT12/16/32 reader over a BlockDevice (MBR-aware, VFAT LFN-aware).

Parses the boot sector / BPB, follows cluster chains through the FAT, and reads
directory entries including long file names. Lookups are case-insensitive. On an
MBR-partitioned disk it locates the first FAT partition automatically.
"""

from __future__ import annotations

from collections import namedtuple

DirEntry = namedtuple("DirEntry", "name is_dir size first_cluster")

_FAT_PART_TYPES = {0x01, 0x04, 0x06, 0x0B, 0x0C, 0x0E}


class FatFs:
    def __init__(self, dev):
        sec0 = dev.read(0, 512)
        base = 0
        is_bpb = sec0[0x36:0x3A] == b"FAT1" or sec0[0x52:0x57] == b"FAT32"
        if not is_bpb and sec0[510:512] == b"\x55\xaa":
            base = self._first_fat_partition(sec0)
        self.dev = dev.view(base) if base else dev
        self._fat_cache_sec = -1
        self._fat_cache = b""
        self._parse_bpb()

    @staticmethod
    def _first_fat_partition(sec0):
        for i in range(4):
            part = sec0[446 + i * 16 : 462 + i * 16]
            ptype, lba, size = (
                part[4],
                int.from_bytes(part[8:12], "little"),
                int.from_bytes(part[12:16], "little"),
            )
            if ptype in _FAT_PART_TYPES and size:
                return lba * 512
        raise ValueError("no FAT partition found in MBR")

    def _parse_bpb(self):
        sector = self.dev.read(0, 512)

        def u16(off):
            return int.from_bytes(sector[off : off + 2], "little")

        def u32(off):
            return int.from_bytes(sector[off : off + 4], "little")

        self.bps = u16(0x0B)
        self.spc = sector[0x0D]
        if self.bps == 0 or self.spc == 0:
            raise ValueError("not a FAT filesystem")
        rsvd, self.nfat = u16(0x0E), sector[0x10]
        root_ent = u16(0x11)
        totsec = u16(0x13) or u32(0x20)
        fatsz = u16(0x16) or u32(0x24)
        self.root_dir_sectors = (root_ent * 32 + self.bps - 1) // self.bps
        self.first_data_sector = rsvd + self.nfat * fatsz + self.root_dir_sectors
        self.first_root_sector = rsvd + self.nfat * fatsz
        self.root_cluster = u32(0x2C)
        self.fat_start = rsvd * self.bps
        clusters = (totsec - self.first_data_sector) // self.spc
        self.fat_type = 32
        if clusters < 4085:
            self.fat_type = 12
        elif clusters < 65525:
            self.fat_type = 16
        self.eoc = {12: 0xFF8, 16: 0xFFF8, 32: 0x0FFFFFF8}[self.fat_type]

    # ---- FAT table ----
    def _fat_entry(self, cluster):
        if self.fat_type == 32:
            off, width = self.fat_start + cluster * 4, 4
        elif self.fat_type == 16:
            off, width = self.fat_start + cluster * 2, 2
        else:
            off, width = self.fat_start + cluster + cluster // 2, 2
        sec = off // self.bps
        if off + width <= (sec + 1) * self.bps:
            if sec != self._fat_cache_sec:
                self._fat_cache = self.dev.read(sec * self.bps, self.bps)
                self._fat_cache_sec = sec
            val = int.from_bytes(
                self._fat_cache[off - sec * self.bps : off - sec * self.bps + width], "little"
            )
        else:  # FAT12 entry across a sector boundary
            val = int.from_bytes(self.dev.read(off, width), "little")
        if self.fat_type == 12:
            val = (val >> 4) if (cluster & 1) else (val & 0xFFF)
        elif self.fat_type == 32:
            val &= 0x0FFFFFFF
        return val

    def _chain(self, start):
        out, cluster, guard = [], start, 0
        while 2 <= cluster < self.eoc:
            out.append(cluster)
            cluster = self._fat_entry(cluster)
            guard += 1
            if guard > (1 << 24):
                break
        return out

    def _read_cluster(self, cluster):
        sector = self.first_data_sector + (cluster - 2) * self.spc
        return self.dev.read(sector * self.bps, self.spc * self.bps)

    def _chain_bytes(self, start):
        return b"".join(self._read_cluster(cluster) for cluster in self._chain(start))

    # ---- directory parsing ----
    def _entries(self, data):
        out, lfn = [], []
        for i in range(0, len(data), 32):
            raw = data[i : i + 32]
            if len(raw) < 32 or raw[0] == 0x00:
                break
            if raw[0] == 0xE5:
                lfn = []
                continue
            attr = raw[11]
            if attr == 0x0F:  # long-filename fragment
                lfn.append(
                    (raw[0] & 0x1F, (raw[1:11] + raw[14:26] + raw[28:32]).decode("utf-16-le", "replace"))
                )
                continue
            if attr & 0x08:  # volume label
                lfn = []
                continue
            if lfn:
                name = "".join(piece for _, piece in sorted(lfn)).split("\x00", 1)[0].rstrip("￿")
            else:
                name = self._short_name(raw)
            lfn = []
            if name in (".", ".."):
                continue
            first = int.from_bytes(raw[26:28], "little") | (int.from_bytes(raw[20:22], "little") << 16)
            out.append(DirEntry(name, bool(attr & 0x10), int.from_bytes(raw[28:32], "little"), first))
        return out

    @staticmethod
    def _short_name(dirent):
        raw = (bytes([0xE5]) + dirent[1:11]) if dirent[0] == 0x05 else dirent[:11]
        base = raw[:8].decode("ascii", "replace").rstrip()
        ext = raw[8:11].decode("ascii", "replace").rstrip()
        if dirent[12] & 0x08:
            base = base.lower()
        if dirent[12] & 0x10:
            ext = ext.lower()
        return base + ("." + ext if ext else "")

    def _dir_entries(self, entry):
        if entry is None:  # root directory
            if self.fat_type == 32:
                return self._entries(self._chain_bytes(self.root_cluster))
            return self._entries(
                self.dev.read(self.first_root_sector * self.bps, self.root_dir_sectors * self.bps)
            )
        return self._entries(self._chain_bytes(entry.first_cluster))

    def _find(self, path):
        entry = None
        for part in [seg for seg in path.replace("\\", "/").split("/") if seg]:
            entry = next(
                (cand for cand in self._dir_entries(entry) if cand.name.lower() == part.lower()), None
            )
            if entry is None:
                raise FileNotFoundError(path)
        return entry

    # ---- public API ----
    def listdir(self, path="/"):
        entry = self._find(path) if path.strip("/") else None
        if entry is not None and not entry.is_dir:
            raise NotADirectoryError(path)
        return [child.name for child in self._dir_entries(entry)]

    def stat(self, path):
        return self._find(path)

    def read_file(self, path):
        entry = self._find(path)
        if entry is None or entry.is_dir:
            raise IsADirectoryError(path)
        data = bytearray()
        for cluster in self._chain(entry.first_cluster):
            data += self._read_cluster(cluster)
            if len(data) >= entry.size:
                break
        return bytes(data[: entry.size])
