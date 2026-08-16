# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
Byte-addressable read view over a block device (e.g. an MSC host driver).

The filesystem readers (fatfs, isofs) work in byte offsets; this adapts that to
the LBA/block reads exposed by MSCDriver. `view(base)` returns a sub-view shifted
by a byte offset (used for an MBR partition start).
"""

from __future__ import annotations


class BlockDevice:
    def __init__(self, read_blocks, block_size, num_blocks=0, base=0):
        self._read_blocks = read_blocks  # callable(lba, count) -> bytes
        self.block_size = block_size
        self.num_blocks = num_blocks
        self.base = base  # byte offset of this view

    def read(self, offset, length):
        if length <= 0:
            return b""
        offset += self.base
        first = offset // self.block_size
        last = (offset + length - 1) // self.block_size
        chunk = self._read_blocks(first, last - first + 1)
        start = offset - first * self.block_size
        return chunk[start : start + length]

    def view(self, base):
        return BlockDevice(self._read_blocks, self.block_size, self.num_blocks, self.base + base)

    @classmethod
    def from_msc(cls, msc):
        num_blocks, block_size = msc.read_capacity()
        return cls(
            lambda lba, count: msc.read_blocks(lba, count, block_size), block_size, num_blocks
        )
