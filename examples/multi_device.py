#!/usr/bin/env python3
"""
Copyright (C) 2026 Jabez Winston

SPDX-License-Identifier: MIT

Date : 31-July-2026

TWO independent USB devices served from ONE process (mirrors
the C library's examples/device/multi_device.c): a HID keyboard and a CDC-ACM serial port,
each with its own VID/PID, its own descriptors and its own busid.

This is the counterpart to cdc_hid_device.py, which puts the same two classes
on ONE composite device. Here they are separate devices: the host enumerates
two, attaches them independently, and either can be detached without touching
the other. All it takes is plugging both onto the same transport - the listener
is shared and each device is named by its busid.

  python3 multi_device.py                          # serve both on :3240
  usbip list -r 127.0.0.1                          # 1-1 keyboard, 1-2 serial
  attach BOTH busids with a USB/IP client -- they are two separate devices
  (Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1  and  -b 1-2)
  then write to the serial port: what you send is echoed back AND typed
"""

import argparse
import logging
import sys
import time

import usbip
from usbip.classes.device import CDCACM, HID, hid
from usbip.device import USBDevice

log = logging.getLogger("multi")

# Two devices, two identities. The serial port takes the pid after the keyboard's.
KBD_VID, KBD_PID = 0x1209, 0x0014
SER_VID, SER_PID = 0x1209, 0x0015

keyboard = None  # the HID interface, set up in main()


def key_code(ch):
    """Map an ASCII char to (modifier, HID keycode), or None if unsupported."""
    if "a" <= ch <= "z":
        return 0x00, 0x04 + ord(ch) - ord("a")
    if "A" <= ch <= "Z":
        return 0x02, 0x04 + ord(ch) - ord("A")  # 0x02 = Left Shift
    if "1" <= ch <= "9":
        return 0x00, 0x1E + ord(ch) - ord("1")
    special = {"0": 0x27, " ": 0x2C, "\n": 0x28, "\r": 0x28}
    return (0x00, special[ch]) if ch in special else None


def type_char(ch):
    code = key_code(ch)
    if not code:
        return
    mod, key = code
    keyboard.send_report(bytes([mod, 0, key, 0, 0, 0, 0, 0]))  # key down
    time.sleep(0.02)
    keyboard.send_report(bytes(8))  # key up
    time.sleep(0.02)


def show_leds(data):
    b = data[0] if data else 0

    def lit(mask):
        return "on" if b & mask else "off"

    log.info(f"keyboard LEDs: NumLock={lit(0x01)} CapsLock={lit(0x02)} ScrollLock={lit(0x04)}")


# Independent on the wire, not in the app: bytes arriving on the serial port are
# echoed there AND typed on the keyboard, so one process is visibly driving both.
def on_rx(port, data):
    log.info(f"serial RX {len(data)} bytes -> echo + type on the keyboard")
    port.write(bytes(data))
    for ch in bytes(data).decode("latin-1"):
        type_char(ch)


def on_open(port, coding):
    log.info(f"serial port OPENED @ {coding.baud} baud")


def on_close(port):
    log.info("serial port CLOSED")


def parse_int(s):
    """argparse type: an integer in any base, so --vid 0x1209 and --vid 4617 both work."""
    try:
        return int(s, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad number {s!r}") from None


def main():
    global keyboard
    ap = argparse.ArgumentParser(
        description="two independent USB devices (HID keyboard + CDC-ACM) from one process"
    )
    ap.add_argument("--vid", type=parse_int, default=KBD_VID, help="keyboard vendor id")
    ap.add_argument("--pid", type=parse_int, default=KBD_PID, help="keyboard product id")
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=3240)
    ap.add_argument(
        "--high-speed", action="store_true", help="Report high-speed devices (default: full speed)"
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s", stream=sys.stderr)
    speed = usbip.SPEED_HIGH if args.high_speed else usbip.SPEED_FULL

    # --- device 1: the HID keyboard ---------------------------------------
    kbd = USBDevice(
        args.vid, args.pid, product="USBIP Keyboard", manufacturer="USB over IP", serial="0014"
    )
    kbd.set_speed(speed)
    keyboard = kbd.add(
        HID(
            hid.keyboard_report_descriptor(),
            subclass=hid.SUBCLASS_BOOT,
            protocol=hid.PROTOCOL_KEYBOARD,
            in_mps=8,
            on_output=show_leds,
        )
    )

    # --- device 2: the CDC-ACM serial port ---------------------------------
    # A second USBDevice(), NOT a second function on the first one: separate
    # descriptors, separate endpoint space (both use 0x81 without clashing),
    # separate attach.
    ser = USBDevice(
        SER_VID, SER_PID, product="USBIP Serial", manufacturer="USB over IP", serial="0015"
    )
    ser.set_speed(speed)
    ser.add(CDCACM(on_rx=on_rx, on_open=on_open, on_close=on_close, name="USBIP Serial"))

    # --- serve both on one listener ----------------------------------------
    # Same transport, two plugs. The first device takes busid 1-1, the second
    # 1-2; set_busid() before plugging would pin either.
    transport = usbip.USBIP(args.host, args.port)
    kbd.plug(via=transport)
    ser.plug(via=transport)

    log.info(f"serving 2 devices on {args.host}:{args.port}")
    log.info(f"  {kbd.busid}  {kbd.vid:04x}:{kbd.pid:04x}  HID keyboard")
    log.info(f"  {ser.busid}  {ser.vid:04x}:{ser.pid:04x}  CDC-ACM serial port")
    log.info(f"attach: sudo usbip attach -r 127.0.0.1 -b {kbd.busid}   (and -b {ser.busid})")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        ser.unplug()
        kbd.unplug()


if __name__ == "__main__":
    main()
