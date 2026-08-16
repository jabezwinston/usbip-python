#!/usr/bin/env python3
"""
Copyright (C) 2026 Jabez Winston

SPDX-License-Identifier: MIT

Date : 04-June-2026

Virtual USB DFU device over USB/IP (file-backed targets).

  python3 dfu_device.py                              # alt0 ./dfu_fw0.bin + alt1 ./dfu_fw1.bin
  python3 dfu_device.py --alt app:app.bin
  python3 dfu_device.py --alt fw:firmware.bin --transfer-size 4096
  python3 dfu_device.py --no-winusb --verbose

Then attach it locally and drive it with dfu-util:
  attach it with a USB/IP client
  (Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1)
  dfu-util -l                 # lists the alternate settings
  dfu-util -a 0 -D fw.bin     # download   /   dfu-util -a 0 -U out.bin   # upload

WinUSB MS-OS descriptors are advertised by default, so dfu-util works on
Windows without installing a driver via Zadig.
"""

import argparse
import logging
import os
import sys
import time

import usbip
from usbip.classes.device import DFU, dfu
from usbip.device import USBDevice

log = logging.getLogger("dfu")

DEFAULT_TARGETS = ["fw0:dfu_fw0.bin", "fw1:dfu_fw1.bin"]  # used when no --alt was given


# ---- DFU target backend (the dfu class is backend-agnostic; the file specifics
#      live here in the example). It implements the small linear-byte-store
#      protocol the class expects (see usbip/classes/device/dfu.py).
class FileTarget:
    """A target backed by a file (created if missing)."""

    def __init__(self, name, path, capacity=0):
        self.name = name
        self.path = path
        self.capacity = capacity
        if not os.path.exists(path):
            open(path, "wb").close()
        self._f = open(path, "r+b", buffering=0)
        self._f.seek(0, os.SEEK_END)
        self.length = self._f.tell()
        self.i_string = 0

    def write(self, off, data):
        if self.capacity and off + len(data) > self.capacity:
            raise IndexError("target full")  # out of range -> errADDRESS (simple case)
        try:
            self._f.seek(off)
            self._f.write(data)
        except OSError as e:  # disk full / I/O error: map to a DFU status
            raise dfu.DFUError(dfu.errWRITE) from e  # -> dfu-util reports "unable to write memory"
        self.length = max(self.length, off + len(data))

    def read(self, off, size):
        if off >= self.length:
            return b""
        self._f.seek(off)
        return self._f.read(min(size, self.length - off))

    def begin_download(self):
        self.length = 0

    def finish_download(self):
        self._f.truncate(self.length)
        self._f.flush()
        os.fsync(self._f.fileno())


def parse_int(s):
    """argparse type: an integer in any base, so --vid 0x1209 and --vid 4617 both work."""
    try:
        return int(s, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad number {s!r}") from None


def make_target(spec):
    """Build one target from 'NAME:PATH' (the --alt syntax). Split at the FIRST
    colon only, so a Windows path keeps its drive letter."""
    name, _, path = spec.partition(":")
    if not name or not path:
        raise argparse.ArgumentTypeError(f"bad --alt {spec!r} (NAME:PATH)")

    return FileTarget(name, path)


def main():
    ap = argparse.ArgumentParser(description="virtual USB DFU device over USB/IP")
    ap.add_argument(
        "--alt",
        action="append",
        default=[],
        metavar="NAME:PATH",
        help="add a file-backed target / alternate setting (repeatable; "
        f"default: {', '.join(DEFAULT_TARGETS)})",
    )
    ap.add_argument("--transfer-size", type=parse_int, default=1024)
    ap.add_argument(
        "--no-winusb", action="store_true", help="don't advertise WinUSB MS-OS descriptors"
    )
    ap.add_argument("--vid", type=parse_int, default=0x1209)
    ap.add_argument("--pid", type=parse_int, default=0x000F)
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=3240)
    ap.add_argument(
        "--verbose", action="store_true", help="log every DFU request (default: grouped)"
    )
    args = ap.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(name)s] %(message)s", stream=sys.stderr)

    targets = [make_target(spec) for spec in args.alt or DEFAULT_TARGETS]

    sink = usbip.GroupedLog("dfu", verbose=args.verbose)
    dev = USBDevice(
        args.vid, args.pid, product="USBIP DFU", manufacturer="USB over IP", serial="000f"
    )
    dev.add(
        DFU(
            targets=targets,
            transfer_size=args.transfer_size,
            winusb=not args.no_winusb,
            on_event=sink,
        )
    )

    winusb = "off" if args.no_winusb else "on"
    log.info(
        f"{len(targets)} target(s), transfer-size {args.transfer_size}, WinUSB {winusb}  "
        f"({args.vid:04x}:{args.pid:04x} on {args.host}:{args.port})"
    )
    for i, t in enumerate(targets):
        log.info(f"  alt {i}: {t.name} (file {t.path})")
    log.info("attach: sudo usbip attach -r 127.0.0.1 -b 1-1")
    log.info("then:   dfu-util -l  |  dfu-util -a 0 -D fw.bin  |  dfu-util -a 0 -U out.bin")

    transport = usbip.USBIP(args.host, args.port)
    dev.plug(via=transport)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
