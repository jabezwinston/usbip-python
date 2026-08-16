#!/usr/bin/env python3
"""
Copyright (C) 2026 Jabez Winston

SPDX-License-Identifier: MIT

Date : 14-June-2026

Virtual USB CDC-ACM serial port over USB/IP (mirrors the C library's
examples/device/cdc_acm_device.c).

The CDC class is generic; THIS app decides what the port does, via callbacks: it
logs open/close/baud and echoes received bytes back to the host.

  python3 cdc_acm_device.py
  attach it with a USB/IP client
  (Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1)
  then open the serial port it creates with any terminal program and type:
  whatever you send is echoed straight back
"""

import argparse
import logging
import sys
import time

import usbip
from usbip.classes.device import CDCACM
from usbip.device import USBDevice

log = logging.getLogger("cdc")


def on_open(port, coding):
    log.info(f"port OPENED @ {coding.baud} baud, {coding.data_bits} data bits")


def on_close(port):
    log.info("port CLOSED")


def on_line_coding(port, coding):
    log.info(
        f"line coding -> {coding.baud} baud, {coding.data_bits} data bits, parity {coding.parity}, stop {coding.stop_bits}"
    )


def on_rx(port, data):
    log.info(f"RX {len(data)} bytes: {bytes(data)!r}")
    port.write(bytes(data))  # echo back to the host
    log.info(f"TX {len(data)} bytes (echo)")


def parse_int(s):
    """argparse type: an integer in any base, so --vid 0x1209 and --vid 4617 both work."""
    try:
        return int(s, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad number {s!r}") from None


def main():
    ap = argparse.ArgumentParser(description="virtual USB CDC-ACM serial port over USB/IP")
    ap.add_argument("--vid", type=parse_int, default=0x1209)
    ap.add_argument("--pid", type=parse_int, default=0x0001)
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=3240)
    ap.add_argument(
        "--high-speed", action="store_true", help="Report a high-speed device (default: full speed)"
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s", stream=sys.stderr)

    dev = USBDevice(
        args.vid, args.pid, product="USBIP CDC-ACM", manufacturer="USB over IP", serial="0001"
    )
    speed = usbip.SPEED_HIGH if args.high_speed else usbip.SPEED_FULL
    dev.set_speed(speed)

    port = CDCACM(on_rx=on_rx, on_open=on_open, on_close=on_close, on_line_coding=on_line_coding)
    dev.add(port)

    speed = "high speed" if args.high_speed else "full speed"

    log.info(
        f"serving CDC-ACM ({args.vid:04x}:{args.pid:04x}, {speed}, on {args.host}:{args.port})"
    )
    log.info(
        "attach it with a USB/IP client to get a serial port "
        "(Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1)"
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
