#!/usr/bin/env python3
"""
Copyright (C) 2026 Jabez Winston

SPDX-License-Identifier: MIT

Date : 05-June-2026

Virtual USB HID device over USB/IP, built on the generic HID class.

  python3 hid_device.py                                 # mouse (default): nudges the pointer
  python3 hid_device.py --profile keyboard --type hello # types a string once
  python3 hid_device.py --profile raw                   # vendor in/out (no class driver)
  python3 hid_device.py --vid 0x1209 --pid 0x0011 --host 0.0.0.0 --port 3240

  attach it with a USB/IP client
  (Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1)

One generic HID interface drives every profile - only the Report descriptor and the
report-sending loop differ. The "raw" profile declares an interrupt-OUT endpoint and
an Output report, so the host can send data back (logged here).
"""

import argparse
import itertools
import time

import usbip
from usbip.classes.device import hid
from usbip.device import USBDevice


# --- keyboard helpers ------------------------------------------------------
def key_code(ch):
    """Map an ASCII char to (modifier, HID keycode), or None if unsupported."""
    if "a" <= ch <= "z":
        return 0x00, 0x04 + ord(ch) - ord("a")
    if "A" <= ch <= "Z":
        return 0x02, 0x04 + ord(ch) - ord("A")  # 0x02 = Left Shift
    if "1" <= ch <= "9":
        return 0x00, 0x1E + ord(ch) - ord("1")
    special = {"0": 0x27, " ": 0x2C, "\n": 0x28}
    return (0x00, special[ch]) if ch in special else None


def type_string(iface, text):
    """Send each character as a key press followed by a release."""
    for ch in text:
        code = key_code(ch)
        if not code:
            continue
        mod, key = code
        iface.send_report(bytes([mod, 0, key, 0, 0, 0, 0, 0]))
        time.sleep(0.02)
        iface.send_report(bytes(8))  # all keys up
        time.sleep(0.02)
    print(f'[hid] typed "{text}"', flush=True)


def show_leds(data):
    """Decode a keyboard LED Output report (HID LED page 0x08): bit0 NumLock,
    bit1 CapsLock, bit2 ScrollLock, bit3 Compose, bit4 Kana. The host sends it via
    SET_REPORT(Output) on EP0 (boot keyboards have no interrupt-OUT endpoint)."""
    b = data[0] if data else 0

    def lit(mask):
        return "on" if b & mask else "off"

    extra = "".join(s for s, mask in ((" Compose=on", 0x08), (" Kana=on", 0x10)) if b & mask)
    print(
        f"[hid] LEDs: NumLock={lit(0x01)} CapsLock={lit(0x02)} ScrollLock={lit(0x04)}{extra}",
        flush=True,
    )


# --- profiles: each builds a HID interface and a loop to run once plugged ----
def build_profile(args):
    """Return (HID interface, run) for the chosen profile; `run()` drives the device
    after it is plugged (sending Input reports / reacting to the host)."""
    if args.profile == "keyboard":
        iface = hid.HID(
            hid.keyboard_report_descriptor(),
            subclass=hid.SUBCLASS_BOOT,
            protocol=hid.PROTOCOL_KEYBOARD,
            in_mps=8,
            on_output=show_leds,
        )

        def run():
            if args.text:
                type_string(iface, args.text)
            # then sit idle (no key spam)
            while True:
                time.sleep(3600)

    elif args.profile == "raw":

        def show_output(data):
            print("[hid] output report:", data.hex(), flush=True)

        iface = hid.HID(
            hid.vendor_report_descriptor(8, 8),
            in_ep=0x81,
            in_mps=8,
            out_ep=0x01,
            out_mps=8,
            on_output=show_output,
        )

        def run():
            # push an incrementing counter
            for counter in itertools.cycle(range(1, 256)):
                iface.send_report(bytes([counter, 0, 0, 0, 0, 0, 0, 0]))
                if args.verbose:
                    print(f"[hid] input report {counter}", flush=True)
                time.sleep(1)

    else:  # mouse (default)
        iface = hid.HID(
            hid.mouse_report_descriptor(),
            subclass=hid.SUBCLASS_BOOT,
            protocol=hid.PROTOCOL_MOUSE,
            in_mps=8,
        )

        def run():
            # square
            for dx, dy in itertools.cycle([(12, 0), (0, 12), (-12, 0), (0, -12)]):
                iface.send_report(bytes([0, dx & 0xFF, dy & 0xFF, 0]))
                time.sleep(1)

    return iface, run


def parse_int(s):
    """argparse type: an integer in any base, so --vid 0x1209 and --vid 4617 both work."""
    try:
        return int(s, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad number {s!r}") from None


def main():
    ap = argparse.ArgumentParser(description="Virtual USB HID device over USB/IP")
    ap.add_argument("--profile", choices=["mouse", "keyboard", "raw"], default="mouse")
    ap.add_argument("--type", dest="text", help="(keyboard) type this string once")
    ap.add_argument("--vid", type=parse_int, default=0x1209)
    ap.add_argument("--pid", type=parse_int, default=0x0011)
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=3240)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    iface, run = build_profile(args)
    dev = USBDevice(
        args.vid, args.pid, product="USBIP HID", manufacturer="USB over IP", serial="0011"
    )
    dev.add(iface)

    where = f"{args.vid:04x}:{args.pid:04x} on {args.host}:{args.port}"
    print(f"[hid] profile {args.profile} ({where})", flush=True)
    print("[hid] attach: sudo usbip attach -r 127.0.0.1 -b 1-1", flush=True)

    transport = usbip.USBIP(args.host, args.port)
    dev.plug(via=transport)
    try:
        run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
