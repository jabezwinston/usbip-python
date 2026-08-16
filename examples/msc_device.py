#!/usr/bin/env python3
"""
Copyright (C) 2026 Jabez Winston

SPDX-License-Identifier: MIT

Date : 04-June-2026

Virtual USB Mass Storage device over USB/IP, backed by image files.

  python3 msc_device.py                          # 64 MiB ./msc_device.img, read-write
  python3 msc_device.py --size 128M --file disk.img
  python3 msc_device.py --read-only
  python3 msc_device.py --cdrom --file image.iso
  python3 msc_device.py --floppy                 # 1.44 MB floppy, ./msc_device.img
  python3 msc_device.py --floppy 720K --ufi      # ... as a real USB floppy drive would

  Several images = several logical units (LUNs) behind one interface, each its own
  drive on the host; a tag on an entry says what that one is:
  python3 msc_device.py --file a.img,b.img --size 8M,64M
  python3 msc_device.py --file disk.img,install.iso:cdrom,key.img:ro

  attach it with a USB/IP client
  (Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1)
"""

import argparse
import logging
import os
import sys
import time

import usbip
from usbip.classes.device.msc import (
    FLOPPY_1_2M,
    FLOPPY_1_44M,
    FLOPPY_2_88M,
    FLOPPY_360K,
    FLOPPY_720K,
    MAX_LUNS,
    MEDIUM_CDROM,
    MEDIUM_DISK,
    MEDIUM_FLOPPY,
    MSC,
    floppy_format_size,
)
from usbip.device import USBDevice

log = logging.getLogger("msc")

DEFAULT_IMAGE = "msc_device.img"

# --floppy names its format on the command line; the class API takes a code.
FLOPPY_NAMES = {
    "2.88M": FLOPPY_2_88M,
    "1.44M": FLOPPY_1_44M,
    "1.2M": FLOPPY_1_2M,
    "720K": FLOPPY_720K,
    "360K": FLOPPY_360K,
}


# The block backend. The MSC class runs BOT + SCSI and delegates block I/O to a store
# like this one, doing no filesystem access itself: where the blocks live is the
# application's business. Mirrors the C example.
class FileStore:
    """A disk backed by an image file.

    An existing image keeps its contents and is only ever grown (never shortened)
    to `num_blocks` blocks, so pointing --file at a prepared image - a formatted
    disk, an ISO - cannot silently discard it. A file that exists but is not
    writable is served read-only.

    `medium`, `read_only` and `product` are what the class reads off a store to
    let one logical unit differ from the next, so a device can serve a disk and a
    CD-ROM at once.
    """

    def __init__(self, path, num_blocks, block_size=512, medium=None, read_only=False, product=None):
        self.path = path
        self.block_size = block_size
        size = num_blocks * block_size
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.truncate(size)
        self.writable = os.access(path, os.W_OK)
        if self.writable and os.path.getsize(path) < size:
            with open(path, "r+b") as f:  # grow, keeping the contents
                f.truncate(size)
        # buffering=0: the SCSI handlers alternate reads and writes on this stream
        self._f = open(path, "r+b" if self.writable else "rb", buffering=0)
        self.num_blocks = os.path.getsize(path) // block_size
        self.medium = medium
        self.read_only = read_only or not self.writable
        self.product = product

    def read(self, lba, count):
        self._f.seek(lba * self.block_size)
        return self._f.read(count * self.block_size)

    def write(self, lba, count, data):
        self._f.seek(lba * self.block_size)
        self._f.write(data)


def parse_int(s):
    """argparse type: an integer in any base, so --vid 0x1209 and --vid 4617 both work."""
    try:
        return int(s, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad number {s!r}") from None


def parse_one_size(s):
    """A byte count with an optional K/M/G suffix (64M, 512K, 1G, 64MiB)."""
    text = s.strip()
    if text[-1:].lower() == "b":  # "64MB" -> "64M", "64MiB" -> "64Mi"
        text = text[:-1]
    if text[-1:].lower() == "i":  # "64Mi" -> "64M"
        text = text[:-1]

    shift = 0  # bits to shift left for the suffix
    if text[-1:].upper() == "K":
        shift = 10
    elif text[-1:].upper() == "M":
        shift = 20
    elif text[-1:].upper() == "G":
        shift = 30
    if shift:
        text = text[:-1].strip()

    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"bad size {s!r} (try 64M, 512K, 1G)")
    return int(text) << shift


def parse_size(s):
    """argparse type: one size, or one per image (--size 8M,64M)."""
    return [parse_one_size(x) for x in s.split(",") if x.strip()]


def parse_block_size(s):
    """argparse type: one block size, or one per image (--block-size 512,2048)."""
    return [parse_int(x) for x in s.split(",") if x.strip()]


# --file entries carry an optional tag saying what that unit is
FILE_MEDIA = {"cdrom": MEDIUM_CDROM, "floppy": MEDIUM_FLOPPY, "disk": MEDIUM_DISK}
FILE_READ_ONLY = ("ro", "read-only")


def parse_file_entry(entry):
    """Split one --file entry into (path, medium, read_only).

    A trailing colon counts as a tag only when the text after it names one, so a
    Windows path ("C:\\disks\\a.img", and even "C:\\disks\\a.img:cdrom") keeps the
    colon that belongs to it."""
    medium, read_only = None, False
    while ":" in entry:
        head, _, tag = entry.rpartition(":")
        tag = tag.lower()
        if tag in FILE_MEDIA:
            medium = FILE_MEDIA[tag]
        elif tag in FILE_READ_ONLY:
            read_only = True
        else:
            break  # not a tag: it is part of the path
        entry = head
    return entry, medium, read_only


def product_of(medium, read_only):
    """The INQUIRY product id: what the host shows for this unit."""
    if medium == MEDIUM_CDROM:
        return "CD-ROM"
    if medium == MEDIUM_FLOPPY:
        return "FLOPPY"
    return "DISK-RO" if read_only else "DISK"


def medium_name(store, floppy_format):
    """What to call a unit on the console."""
    if store.medium == MEDIUM_CDROM:
        return "CD-ROM"
    if store.medium == MEDIUM_FLOPPY:
        return floppy_format
    return "read-only" if store.read_only else "read-write"


# group SCSI traffic by command name - strip the lba/result tail so a run of reads
# at different addresses collapses into one "READ(10) xN" summary line
def scsi_key(text):
    return text.split(" lba=")[0].split(" -> ")[0]


def is_status_poll(text):
    """The host polls TEST UNIT READY constantly; drop it from the grouped log."""
    return text.startswith("TEST UNIT READY")


def main():
    ap = argparse.ArgumentParser(description="virtual USB mass-storage device over USB/IP")
    ap.add_argument(
        "--size",
        type=parse_size,
        default=None,
        help="disk size, e.g. 64M (default); one value, or one per image",
    )
    ap.add_argument(
        "--block-size",
        type=parse_block_size,
        default=None,
        help="bytes/block (default 512, 2048 for a CD-ROM); one value, or one per image",
    )
    ap.add_argument(
        "--file",
        default=DEFAULT_IMAGE,
        help=f"image file(s) backing the disk, comma-separated (default {DEFAULT_IMAGE}, "
        "created at --size if missing; an existing image keeps its contents). Each extra "
        "image is another logical unit, and may carry a tag saying what it is: "
        "IMG:cdrom, IMG:floppy, IMG:ro",
    )
    ap.add_argument("--read-only", action="store_true", help="present a write-protected medium")
    ap.add_argument(
        "--cdrom",
        action="store_true",
        help="present a read-only CD-ROM (pair with --file image.iso)",
    )
    ap.add_argument(
        "--floppy",
        nargs="?",
        const="1.44M",
        metavar="FORMAT",
        help="present a floppy: 2.88M, 1.44M (default), 1.2M, 720K, 360K "
        "(sets --size, so an image of that size is created)",
    )
    ap.add_argument(
        "--ufi",
        action="store_true",
        help="declare the UFI command set (subclass 0x04) rather than SCSI "
        "transparent - what a real USB floppy drive reports",
    )
    ap.add_argument("--vid", type=parse_int, default=0x1209)
    ap.add_argument("--pid", type=parse_int, default=0x0008)
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=3240)
    ap.add_argument(
        "--high-speed", action="store_true", help="Report a high-speed device (default: full speed)"
    )
    ap.add_argument(
        "--verbose", action="store_true", help="log every SCSI command (default: grouped)"
    )
    args = ap.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(name)s] %(message)s", stream=sys.stderr)

    if args.cdrom and args.floppy:
        ap.error("--cdrom and --floppy are different media; pick one")

    floppy_name = (args.floppy or "1.44M").upper()
    floppy_size = floppy_format_size(FLOPPY_NAMES.get(floppy_name, -1))
    if not floppy_size:
        ap.error(f"unknown floppy format {args.floppy!r} (2.88M, 1.44M, 1.2M, 720K, 360K)")

    # one image per logical unit: the global flags are the default, an entry's tag
    # says what that unit alone is
    default_medium = MEDIUM_DISK
    if args.cdrom:
        default_medium = MEDIUM_CDROM
    elif args.floppy:
        default_medium = MEDIUM_FLOPPY
    entries = [e for e in args.file.split(",") if e]
    if not entries:
        ap.error("--file names no image")
    if len(entries) > MAX_LUNS:
        ap.error(f"at most {MAX_LUNS} images: one per logical unit")
    sizes, block_sizes = args.size or [], args.block_size or []
    if (len(sizes) > 1 and len(sizes) != len(entries)) or (
        len(block_sizes) > 1 and len(block_sizes) != len(entries)
    ):
        ap.error(f"--size / --block-size take one value, or one per image ({len(entries)})")

    stores = []
    for i, entry in enumerate(entries):
        path, medium, read_only = parse_file_entry(entry)
        medium = medium or default_medium
        read_only = read_only or args.read_only
        # one value applies to every unit; otherwise it is one per unit, in order
        size = 64 * 1024**2
        if sizes:
            size = sizes[0 if len(sizes) == 1 else i]

        block_size = 2048 if medium == MEDIUM_CDROM else 512
        if block_sizes:
            block_size = block_sizes[0 if len(block_sizes) == 1 else i]
        if medium == MEDIUM_FLOPPY:  # the format fixes the capacity
            size, block_size = floppy_size, 512
        store = FileStore(
            path, max(1, size // block_size), block_size, medium=medium, read_only=read_only
        )
        if medium == MEDIUM_FLOPPY:
            store.num_blocks = size // block_size  # a floppy is the size its format says
        store.product = product_of(medium, store.read_only)
        stores.append(store)

    sink = usbip.GroupedLog("msc", verbose=args.verbose, key=scsi_key, skip=is_status_poll)

    dev = USBDevice(
        args.vid, args.pid, product="USBIP MSC", manufacturer="USB over IP", serial="0008"
    )
    speed = usbip.SPEED_HIGH if args.high_speed else usbip.SPEED_FULL
    dev.set_speed(speed)
    dev.add(MSC(stores, ufi=args.ufi, on_command=sink))

    def capacity(store):
        return (
            f"{store.num_blocks} x {store.block_size}B = "
            f"{store.num_blocks * store.block_size / 1024**2:.1f} MiB"
        )

    speed = "high speed" if args.high_speed else "full speed"
    where = f"({args.vid:04x}:{args.pid:04x}, {speed}, on {args.host}:{args.port})"
    ufi = " (UFI)" if args.ufi else ""
    if len(stores) == 1:
        st = stores[0]
        log.info(f"{medium_name(st, floppy_name)}{ufi} {st.path}: {capacity(st)}  {where}")
    else:
        log.info(f"{len(stores)} logical units{ufi}  {where}")
        for i, st in enumerate(stores):
            log.info(f"  lun {i}: {medium_name(st, floppy_name):10} {st.path}: {capacity(st)}")
    log.info("attach: sudo usbip attach -r 127.0.0.1 -b 1-1")

    transport = usbip.USBIP(args.host, args.port)
    dev.plug(via=transport)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
