#!/usr/bin/env python3
"""
The documentation's host example: import a device from a USB/IP server and drive
it with usbip.host (libusb-shaped), with no kernel driver, no vhci and no root.
Mirrors the C library's doc/examples/host_drive.c.

In USB terms this program is the HOST; in USB/IP terms it is the CLIENT - it
CONNECTS to a server that is already serving a device (the device end listens).

Start the device first, then drive it:
  python3 examples/vendor_device.py &       # serves 1209:0004 on :3240
  python3 host_drive.py                            # ...or: host_drive.py --host 10.0.0.5

The same code drives a REAL device exported by a real usbipd - point it at that
server, name the bus id it exported, and drop the --vid/--pid check.
"""

import argparse
import sys

import usbip.host
from usbip import USBIP, Stall
from usbip.core import DeviceDescriptor

VENDOR_ID = 0x1209  # what vendor_device.py serves
PRODUCT_ID = 0x0004
EP_BULK_OUT = 0x01  # host -> device
EP_BULK_IN = 0x81  # device -> host


def main():
    ap = argparse.ArgumentParser(description="drive a USB/IP device from Python")
    ap.add_argument("--host", default=None, help="USB/IP server (default: local)")
    ap.add_argument("--port", type=int, default=3240)
    ap.add_argument("--busid", default="1-1", help="bus id to import (real usbipd exports others)")
    ap.add_argument("--vid", type=lambda s: int(s, 0), default=VENDOR_ID)
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=PRODUCT_ID)
    args = ap.parse_args()

    # 1. import the device. Naming a transport is the only USB/IP-aware step;
    #    without one it connects to 127.0.0.1:3240. open() imports the bus id,
    #    checks vid/pid, reads the device descriptor and sets configuration 1.
    transport = USBIP(args.host, args.port) if args.host else None
    try:
        handle = usbip.host.open(args.vid, args.pid, busid=args.busid, transport=transport)
    except OSError as exc:
        print(
            f"cannot reach the USB/IP server: {exc} (is vendor_device.py running?)", file=sys.stderr
        )
        return 1
    except Exception as exc:  # NotFound, protocol errors
        print(f"import failed: {exc}", file=sys.stderr)
        return 1

    with handle:
        # 2. a control transfer: the standard GET_DESCRIPTOR(device)
        raw = handle.control(0x80, 0x06, 0x0100, 0, 18)
        desc = DeviceDescriptor.parse(raw)
        print(
            f"opened {desc.idVendor:04x}:{desc.idProduct:04x} "
            f"(device descriptor: {len(raw)} bytes, USB {desc.bcdUSB >> 8:x}.{desc.bcdUSB & 0xFF:02x})"
        )

        # 3. bulk I/O: send a payload, read the device's echo back
        message = b"hello device"
        try:
            handle.bulk_out(EP_BULK_OUT, message)
            echo = handle.bulk_in(EP_BULK_IN, 64)
        except Stall:
            print("the device STALLed the pipe", file=sys.stderr)
            return 1
        print(f"sent {len(message)} bytes, received {len(echo)} back: {echo!r}")

    matched = echo == message
    verdict = "OK: loopback round-trip matched" if matched else "MISMATCH"
    print(verdict)
    return 0 if matched else 1


if __name__ == "__main__":
    sys.exit(main())
