# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
CDC-ACM - host driver. Auto-binds to the CDC Data interface (class 0x0A)."""

from __future__ import annotations

from ...host import Driver


class CDCDriver(Driver):
    matches = {"bInterfaceClass": 0x0A}

    def write(self, data: bytes):
        return self.handle.bulk_out(0x01, data)

    def read(self, length=64) -> bytes:
        return self.handle.bulk_in(0x81, length)
