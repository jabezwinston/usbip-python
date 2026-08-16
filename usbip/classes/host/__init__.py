# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
Host-side class drivers (USB Drivers), built on the public host API."""


def fetch_config(handle) -> bytes:
    """GET_DESCRIPTOR(CONFIGURATION) in the standard two steps: read the 9-byte
    header for wTotalLength, then the whole descriptor. Every driver's endpoint
    discovery starts here."""
    hdr = handle.control(0x80, 0x06, 0x0200, 0, 9)
    return handle.control(0x80, 0x06, 0x0200, 0, hdr[2] | (hdr[3] << 8))


from . import cdc_acm, hid, msc, mtp  # noqa: E402  (fetch_config must exist first)

__all__ = ["cdc_acm", "fetch_config", "hid", "msc", "mtp"]
