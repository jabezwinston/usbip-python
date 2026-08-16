#!/usr/bin/env python3
"""
Copyright (C) 2026 Jabez Winston

SPDX-License-Identifier: MIT

Date : 29-July-2026

A composite CDC-ACM serial port + HID consumer control, built on the ready-made
CLASSES (mirrors the C library's examples/device/cdc_hid_device.c): CDCACM and HID
author the descriptors, emit the Interface Association Descriptor that groups each
function, and answer the class requests, so this file is behaviour only. Compare
cdc_acm_device.py and hid_device.py, which are the same two functions on devices
of their own.

Behaviour: the serial port is a console that drives the media keys. Type a key
name on it and the HID function taps that consumer key on the host, so one action
is visible through both functions:

  python3 cdc_hid_device.py                # serve 1209:0013 on :3240
  attach it with a USB/IP client
  (Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1)
  open the serial port it creates and type, e.g.:
    v+        raise the volume one step
    v- v- v-  lower it three steps (a line may carry several keys)
    m         mute
    b+ / b-   screen brightness
    help      list every key
"""

import argparse
import logging
import sys
import time

import usbip
from usbip import USBDevice
from usbip.classes.device import CDCACM, hid

log = logging.getLogger("cdc_hid")

LINE_MAX_LEN = 64

# The console vocabulary: one token per bit of the report descriptor, same order.
KEYS = [
    ("v+", 0xE9, "volume up"),
    ("v-", 0xEA, "volume down"),
    ("m", 0xE2, "mute"),
    ("b+", 0x6F, "brightness up"),
    ("b-", 0x70, "brightness down"),
    ("play", 0xCD, "play/pause"),
    ("stop", 0xB7, "stop"),
    ("next", 0xB5, "next track"),
    ("prev", 0xB6, "previous track"),
    ("eject", 0xB8, "eject"),
]


# fmt: off
def consumer_report_descriptor():
    """A Consumer Control (Usage Page 0x0C) with one momentary bit per KEYS entry:
    a 2-byte Input report whose low 10 bits are those keys, in order, padded out to
    the byte boundary. A bitmap rather than the keyboard's array of key codes, so a
    report says "this key is down" directly; an all-zero report releases it. Every
    usage here is a single byte, which is what keeps the items readable - usages
    above 0xFF (AC Home, AL Calculator...) need a 2-byte Usage item."""
    return b"".join([
        hid.usage_page(0x0C), hid.usage(0x01),      # Consumer, Consumer Control
        hid.collection(0x01),                       # Application
        hid.logical_min(0), hid.logical_max(1),
        hid.report_size(1), hid.report_count(10),
        *[hid.usage(code) for _token, code, _what in KEYS],
        hid.input_(0x02),                           # 10 momentary bits
        hid.report_count(6), hid.input_(0x03),      # 6 bits padding
        hid.end_collection(),
    ])
# fmt: on


def tap(consumer, bit):
    """Press and release, because a consumer key is momentary: the host acts on the
    0 -> 1 edge and repeats while the bit stays set."""
    consumer.send_report(bytes([bit & 0xFF, bit >> 8]))
    time.sleep(0.02)
    consumer.send_report(bytes(2))
    time.sleep(0.02)


def build_device(vid, pid):
    """Add the two class functions to one composite device; returns (dev, port,
    consumer)."""
    dev = USBDevice(vid, pid, manufacturer="USB over IP", product="USBIP CDC+HID", serial="0013")
    dev.set_composite()  # EF/02/01: usbccgp splits per IAD
    state = {"line": "", "overflow": False}

    def write_line(port, text):
        port.write((text + "\r\n").encode())  # terminals opened raw want both

    def cmd_help(port):
        write_line(port, "consumer keys -- several per line, e.g. 'v+ v+ m':")
        for token, _code, what in KEYS:
            write_line(port, f"  {token:<6} {what}")

    def handle_word(port, word):
        """One word: send its key. The point of the example -- typing on one
        function is felt through the other."""
        if word in ("help", "?"):
            cmd_help(port)
            return
        for i, (token, _code, what) in enumerate(KEYS):
            if word == token:
                log.info("%s -> %s", word, what)
                write_line(port, what)
                tap(state["consumer"], 1 << i)
                return
        write_line(port, f"unknown key '{word}' -- try 'help'")

    def handle_line(port, line):
        words = line.split()
        if not words:  # bare Enter: the greeting, on demand
            write_line(port, "USBIP composite CDC+HID consumer control -- type 'help'")
        for word in words:
            handle_word(port, word)

    # the class answers line coding and DTR itself; these are just notifications
    def on_open(port, coding):
        log.info("port OPENED @ %u baud, %u data bits", coding.baud, coding.data_bits)
        state["line"] = ""
        state["overflow"] = False

    def on_close(port):
        log.info("port closed")

    def on_rx(port, data):
        """Serial input: echo it so typing is visible, and act on each completed
        line. The class delivers this on the serve thread."""
        for ch in bytes(data).decode(errors="ignore"):
            port.write(ch.encode())  # echo, so typing is visible
            if ch in "\r\n":
                port.write(b"\n")
                if state["overflow"]:
                    write_line(port, "line too long -- ignored")
                else:
                    handle_line(port, state["line"])
                state["line"] = ""
                state["overflow"] = False
            elif len(state["line"]) < LINE_MAX_LEN - 1:
                state["line"] += ch
            else:
                state["overflow"] = True  # drop the whole line, not just its tail

    # --- function 1: CDC-ACM (interfaces 0+1), grouped by the class's IAD ---
    port = dev.add(CDCACM(on_rx=on_rx, on_open=on_open, on_close=on_close))

    # --- function 2: HID consumer control (interface 2) ----------------------
    # No subclass or protocol: only a keyboard or a mouse can be a boot device.
    consumer = dev.add(hid.HID(consumer_report_descriptor(), in_mps=2))
    state["consumer"] = consumer
    return dev, port, consumer


def parse_int(s):
    try:
        return int(s, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad number {s!r}") from None


def main():
    ap = argparse.ArgumentParser(
        description="composite CDC serial + HID consumer control on the ready-made classes"
    )
    ap.add_argument("--vid", type=parse_int, default=0x1209)
    ap.add_argument("--pid", type=parse_int, default=0x0013)
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=3240)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s", stream=sys.stderr)

    dev, port, consumer = build_device(args.vid, args.pid)
    transport = usbip.USBIP(args.host, args.port)
    dev.plug(via=transport)
    log.info(
        "serving %04x:%04x on %s:%d - CDC on interfaces 0+1, consumer control on interface %d",
        args.vid,
        args.pid,
        args.host,
        args.port,
        consumer.interface_number,
    )
    log.info("type 'help' on the serial port for the key names")

    try:
        while True:  # both functions run from their callbacks
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
