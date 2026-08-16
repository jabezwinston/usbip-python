# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 05-June-2026
"""
Optional USB-traffic capture to a PCAPNG file (for Wireshark).

Off by default. Enable by setting ``USBIP_PCAPNG=<path>`` (a ``%p`` in the path
expands to the PID, so concurrent processes don't clobber one file) or by calling
``open(path)``. Every USB transfer is written as the Linux usbmon
**Submit ('S') -> Complete ('C')** event pair using **DLT_USB_LINUX_MMAPPED (220)**,
so Wireshark applies its USB and class dissectors exactly as for a real ``usbmon``
capture of the same device - the USB/IP CMD_SUBMIT/RET_SUBMIT pair maps 1:1 onto it.

The capture's Section Header / Interface blocks are stamped with identifying
options (``shb_userappl`` = ``usbip <version>``, ``shb_os``/``shb_hardware`` from
``uname``, ``if_name``/``if_description``) so Wireshark/``capinfos`` show context.

This module only OBSERVES: it never alters traffic, and when disabled (the default)
every entry point is a single cheap boolean check.

The C library has a byte-compatible twin in its ``src/pcap.c``.
"""

from __future__ import annotations

import builtins
import os
import platform
import struct
import sys
import threading
import time

# pcap link-layer type for the usbmon "mmapped" binary format
LINKTYPE_USB_LINUX_MMAPPED = 220

# usbmon transfer_type numbering - note it differs from USB bmAttributes
# (control=0, iso=1, bulk=2, interrupt=3); map by endpoint-type name here.
# Named USBMON_* (unlike core.XFER_BY_NAME) so the two numberings can't be mixed up.
XFER_ISO, XFER_INTR, XFER_CTRL, XFER_BULK = 0, 1, 2, 3
USBMON_XFER_BY_NAME = {
    "control": XFER_CTRL, "iso": XFER_ISO, "bulk": XFER_BULK, "interrupt": XFER_INTR
}

_EINPROGRESS = -115  # usbmon's status on a Submit event

_lock = threading.Lock()
_fp = None
_enabled = False
_checked = False  # have we consulted USBIP_PCAPNG yet?

# 64-byte usbmon "mmapped" packet header (little-endian, no struct padding)
_HDR = struct.Struct("<QBBBBHBBqiiII8siiII")
_ISODESC = struct.Struct("<iIII")  # per-iso-packet: status, offset, length, pad


# ---- pcapng block helpers (all little-endian) ----------------------------
def _block(btype: int, body: bytes) -> bytes:
    """Wrap a 4-byte-aligned body as a pcapng block (length stored head + tail)."""
    total = len(body) + 12
    return struct.pack("<II", btype, total) + body + struct.pack("<I", total)


# ---- pcapng option TLVs --------------------------------------------------
# SHB option codes: shb_hardware=2, shb_os=3, shb_userappl=4.
# IDB option codes: if_name=2, if_description=3, if_tsresol=9.
def _opt(code: int, value: bytes) -> bytes:
    """One option TLV: u16 code, u16 length, value, padded to 4 bytes."""
    pad = (-len(value)) % 4
    return struct.pack("<HH", code, len(value)) + value + b"\x00" * pad


def _stropt(code: int, text: str) -> bytes:
    return _opt(code, text.encode("utf-8"))


def _options(*opts: bytes) -> bytes:
    """An option list (skipping falsy entries) plus the terminating opt_endofopt,
    or empty when there are no options."""
    kept = [opt for opt in opts if opt]
    return b"".join(kept) + b"\x00\x00\x00\x00" if kept else b""


def _writer() -> str:
    from . import __version__ as v  # single source of truth for the version

    return f"USBIP {v} (github.com/jabezwinston/usbip-python)"


def _shb() -> bytes:  # Section Header Block
    head = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)  # BO magic, ver 1.0, len -1
    uts = platform.uname()
    return _block(
        0x0A0D0D0A,
        head
        + _options(
            _stropt(2, uts.machine),  # shb_hardware  (e.g. x86_64)
            _stropt(3, f"{uts.system} {uts.release}"),  # shb_os        (e.g. Linux 6.x)
            _stropt(4, _writer()),
        ),
    )  # shb_userappl


def _idb(linktype: int, snaplen: int = 0) -> bytes:  # Interface Description Block
    head = struct.pack("<HHI", linktype, 0, snaplen)  # linktype, reserved, snaplen
    return _block(
        0x00000001,
        head
        + _options(
            _stropt(2, "usbmon"),  # if_name
            _stropt(3, "Virtual USB (USB/IP)"),  # if_description
            _opt(9, b"\x06"),
        ),
    )  # if_tsresol = 10^-6 s (microseconds)


def _epb(data: bytes, ts_usec: int, iface: int = 0) -> bytes:  # Enhanced Packet Block
    caplen = len(data)
    pad = (-caplen) % 4
    body = (
        struct.pack("<IIIII", iface, ts_usec >> 32, ts_usec & 0xFFFFFFFF, caplen, caplen)
        + data
        + b"\x00" * pad
    )
    return _block(0x00000006, body)


# a "just turn it on" value (USBIP_PCAPNG=1 / on / yes / ...) picks the default name
_FLAG_VALUES = frozenset({"", "1", "on", "yes", "true", "auto"})


def _default_name() -> str:
    """Capture filename derived from the running script/executable name (e.g.
    ``bluetooth_device.py`` -> ``bluetooth_device.pcapng``); falls back to
    ``usb_traffic.pcapng`` when the name can't be determined."""
    base = os.path.basename(sys.argv[0]) if sys.argv else ""
    stem = os.path.splitext(base)[0]
    if not stem or stem in ("python", "python3", "-c", "-m"):
        stem = "usb_traffic"
    return stem + ".pcapng"


# ---- public control ------------------------------------------------------
def open(path: str | None = None) -> None:
    """Begin capturing (truncating the file). With no path - or a flag value like
    ``"1"`` - the filename is derived from the script/executable name (fallback
    ``usb_traffic.pcapng``). ``%p`` in an explicit path expands to the PID. Idempotent."""
    global _fp, _enabled, _checked
    _checked = True
    if path is None or path.strip().lower() in _FLAG_VALUES:
        path = _default_name()
    path = path.replace("%p", str(os.getpid()))
    with _lock:
        if _fp is not None:
            return
        fp = builtins.open(path, "wb")
        fp.write(_shb())
        fp.write(_idb(LINKTYPE_USB_LINUX_MMAPPED))
        fp.flush()
        _fp = fp
        _enabled = True


def close() -> None:
    global _fp, _enabled
    with _lock:
        if _fp is not None:
            _fp.close()
            _fp = None
        _enabled = False


def _ensure() -> None:
    """Honor USBIP_PCAPNG the first time anyone asks whether capture is on.
    Any value (even empty) enables capture; a path is used verbatim, a flag value
    picks the default name."""
    global _checked
    if _checked:
        return
    _checked = True
    val = os.environ.get("USBIP_PCAPNG")
    if val is not None:
        open(val)


def is_enabled() -> bool:
    if not _checked:
        _ensure()
    return _enabled


# ---- record emission -----------------------------------------------------
def _emit(
    event: str,
    devid: int,
    xfer_type: int,
    ep_addr: int,
    seqnum: int,
    status: int,
    setup,
    payload: bytes,
    urb_len: int,
    iso=None,
) -> None:
    busnum = (devid >> 16) & 0xFFFF
    devnum = devid & 0xFF
    payload = payload or b""
    setup_flag = 0 if setup is not None else 0x2D  # 0 = setup present, '-' = none
    data_flag = 0 if payload else 0x2D  # 0 = data present, '-' = none
    ndesc = len(iso) if iso else 0
    if iso is not None:  # iso: setup slot holds {err_count, numdesc}
        setup_field = struct.pack("<ii", 0, ndesc)
        setup_flag = 0x2D
    else:
        setup_field = (bytes(setup) if setup is not None else b"\x00" * 8)[:8].ljust(8, b"\x00")

    sec = int(time.time())
    usec = int((time.time() - sec) * 1_000_000)
    hdr = _HDR.pack(
        seqnum & 0xFFFFFFFFFFFFFFFF,
        ord(event),
        xfer_type,
        ep_addr & 0xFF,
        devnum,
        busnum & 0xFFFF,
        setup_flag,
        data_flag,
        sec,
        usec,
        status,
        urb_len & 0xFFFFFFFF,
        len(payload) & 0xFFFFFFFF,
        setup_field,
        0,
        0,
        0,
        ndesc,
    )
    record = hdr
    if iso:
        record += b"".join(_ISODESC.pack(int(pkt[3]), int(pkt[0]), int(pkt[1]), 0) for pkt in iso)
    record += payload

    ts = sec * 1_000_000 + usec
    with _lock:
        if _fp is None:
            return
        _fp.write(_epb(record, ts))
        _fp.flush()


def submit(
    devid: int,
    xfer_type: int,
    ep_addr: int,
    seqnum: int,
    setup,
    out_data: bytes,
    urb_len: int,
    iso=None,
) -> None:
    """Log the request half of a transfer (setup + any OUT payload)."""
    _emit("S", devid, xfer_type, ep_addr, seqnum, _EINPROGRESS, setup, out_data, urb_len, iso=iso)


def complete(
    devid: int, xfer_type: int, ep_addr: int, seqnum: int, status: int, in_data: bytes, iso=None
) -> None:
    """Log the response half of a transfer (status + any IN payload)."""
    _emit(
        "C", devid, xfer_type, ep_addr, seqnum, status, None, in_data, len(in_data or b""), iso=iso
    )
