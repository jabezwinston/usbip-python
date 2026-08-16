#!/usr/bin/env python3
"""
Copyright (C) 2026 Jabez Winston

SPDX-License-Identifier: MIT

Date : 04-June-2026

Virtual USB MTP device over USB/IP - exports a directory as MTP storage.

  python3 mtp_device.py                       # exports a sample tree in /tmp
  python3 mtp_device.py --dir ~/Music         # export your own directory (read-write!)
  python3 mtp_device.py --dir ~/Pictures --read-only
  python3 mtp_device.py --name "My Player" --verbose

Then attach it locally and browse with libmtp / gphoto2 / gio:
  attach it with a USB/IP client
  (Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1)
  then browse it in your OS's file manager (Linux headless: mtp-detect)
  mtp-files             # lists objects
  mtp-getfile <id> out  # download   /   mtp-sendfile in <name>   # upload

The Microsoft-OS "MTP" descriptor is always advertised, so on Windows the device
shows up as an MTP portable device in Explorer with no driver install.
"""

import argparse
import logging
import os
import shutil
import sys
import tempfile
import time

import usbip
from usbip.classes.device import MTP
from usbip.device import USBDevice

log = logging.getLogger("mtp")

SAMPLE_DIR = os.path.join(
    tempfile.gettempdir(), "usbip_mtp_share"
)  # OS temp dir (no /tmp on Windows)


class FilesystemStore:
    """MTP storage backed by a real directory. ALL the filesystem access (open,
    mkdir, rmdir/rmtree, stat, listdir, …) lives here, behind the small backend
    interface the MTP class calls - relative "/"-paths with "" for the exported root."""

    def __init__(self, root, read_only=False, description="USB over IP"):
        self.root = os.path.abspath(root)
        self.read_only = read_only
        self.description = description
        os.makedirs(self.root, exist_ok=True)

    def _real(self, path):
        return os.path.join(self.root, *[p for p in path.split("/") if p])

    def listdir(self, path):
        return sorted(os.listdir(self._real(path)))

    def stat(self, path):
        real = self._real(path)
        is_dir = os.path.isdir(real)
        st = os.stat(real)
        return (is_dir, 0 if is_dir else st.st_size, st.st_mtime)

    def read(self, path, off=0, size=None):
        with open(self._real(path), "rb") as f:
            f.seek(off)
            return f.read() if size in (None, 0xFFFFFFFF) else f.read(size)

    def write(self, path, data):
        with open(self._real(path), "wb") as f:
            f.write(data)

    def mkdir(self, path):
        os.makedirs(self._real(path), exist_ok=True)

    def remove(self, path):
        real = self._real(path)
        shutil.rmtree(real) if os.path.isdir(real) else os.remove(real)

    def rename(self, old, new):
        os.rename(self._real(old), self._real(new))

    def pwrite(self, path, offset, data):
        with open(self._real(path), "r+b") as f:
            f.seek(offset)
            f.write(data)

    def truncate(self, path, size):
        with open(self._real(path), "r+b") as f:
            f.truncate(size)

    def disk_usage(self):
        u = shutil.disk_usage(self.root)
        return (u.total, u.free)


def make_sample_tree(path):
    """Create a small browsable tree so there's something to see out of the box."""
    os.makedirs(os.path.join(path, "Documents"), exist_ok=True)
    os.makedirs(os.path.join(path, "Music"), exist_ok=True)
    files = {
        "README.txt": b"This is a usbip virtual MTP device.\n"
        b"Drop files in here (or delete them) and watch them appear on the host.\n",
        os.path.join("Documents", "notes.txt"): b"hello from MTP\n",
        os.path.join("Music", "track.txt"): b"(pretend this is an audio file)\n",
    }
    for rel, data in files.items():
        full = os.path.join(path, rel)
        if not os.path.exists(full):
            with open(full, "wb") as f:
                f.write(data)
    return path


def parse_int(s):
    """argparse type: an integer in any base, so --vid 0x1209 and --vid 4617 both work."""
    try:
        return int(s, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad number {s!r}") from None


def main():
    ap = argparse.ArgumentParser(description="virtual USB MTP device over USB/IP")
    ap.add_argument(
        "--dir",
        help="directory(ies) to export, comma-separated - each becomes "
        "a separate MTP storage (default: a sample tree in /tmp)",
    )
    ap.add_argument("--read-only", action="store_true", help="forbid host writes/deletes")
    ap.add_argument("--name", default="USBIP MTP", help="device friendly name / model")
    ap.add_argument("--vid", type=parse_int, default=0x1209)
    ap.add_argument("--pid", type=parse_int, default=0x0010)
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=3240)
    ap.add_argument(
        "--high-speed", action="store_true", help="Report a high-speed device (default: full speed)"
    )
    ap.add_argument(
        "--verbose", action="store_true", help="log every MTP request (default: grouped)"
    )
    args = ap.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(name)s] %(message)s", stream=sys.stderr)

    # one MTP storage per --dir entry, or a generated sample tree if none was given
    if args.dir:
        paths = [p.strip() for p in args.dir.split(",") if p.strip()]
    else:
        paths = [make_sample_tree(SAMPLE_DIR)]
    for p in paths:
        if not os.path.isdir(p):
            ap.error(f"not a directory: {p}")

    stores = []
    for p in paths:
        description = os.path.basename(os.path.abspath(p)) or "storage"
        stores.append(FilesystemStore(p, read_only=args.read_only, description=description))

    sink = usbip.GroupedLog("mtp", verbose=args.verbose)
    dev = USBDevice(
        args.vid, args.pid, product=args.name, manufacturer="USB over IP", serial="000a"
    )
    speed = usbip.SPEED_HIGH if args.high_speed else usbip.SPEED_FULL
    dev.set_speed(speed)
    dev.add(MTP(stores, name=args.name, on_event=sink))  # MS-OS "MTP" descriptor is on

    exported = ", ".join(os.path.abspath(p) for p in paths)
    access = "read-only" if args.read_only else "read-write"
    speed = "high speed" if args.high_speed else "full speed"
    log.info(
        f"exporting {len(stores)} storage(s): {exported} ({access}, MS-OS MTP)  "
        f"({args.vid:04x}:{args.pid:04x}, {speed}, on {args.host}:{args.port})"
    )
    log.info("attach: sudo usbip attach -r 127.0.0.1 -b 1-1")
    log.info(
        "then browse it in your file manager "
        "(Linux headless: mtp-detect | mtp-files | mtp-getfile <id> out | mtp-sendfile in name)"
    )

    transport = usbip.USBIP(args.host, args.port)
    dev.plug(via=transport)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
