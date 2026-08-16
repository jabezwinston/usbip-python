#!/usr/bin/env python3
"""

The documentation's first example: a USB Boot-protocol HID keyboard served over
USB/IP, written as an INTERFACE OF ITS OWN (usbip.Interface) - every descriptor
authored by hand, every class request answered in one method, no ready-made device
class. Mirrors the C library's doc/examples/boot_keyboard.c.

  python3 boot_keyboard.py                        # serve 1209:0011 on :3240
  python3 boot_keyboard.py --port 4000 --type world

Roles are inverted from USB's: in USB terms this program is the DEVICE, but in
USB/IP terms it is the SERVER - plug() listens on TCP 3240 and waits. The USB
host end is the USB/IP CLIENT, and it connects.

"""

import argparse
import logging
import struct
import sys
import threading
import time

import usbip
from usbip import In, Interface, Stall, USBDevice
from usbip.core import CLASS, STANDARD

log = logging.getLogger("kbd")

# HID 1.11 - the handful of constants this example needs
HID_DT_HID, HID_DT_REPORT = 0x21, 0x22  # class descriptor types
HID_GET_REPORT, HID_GET_IDLE, HID_GET_PROTOCOL = 0x01, 0x02, 0x03
HID_SET_REPORT, HID_SET_IDLE, HID_SET_PROTOCOL = 0x09, 0x0A, 0x0B

USB_CLASS_HID = 0x03
HID_SUBCLASS_BOOT = 0x01  # bInterfaceSubClass: boot interface
HID_PROTOCOL_KEYBOARD = 0x01  # bInterfaceProtocol: keyboard
KEY_REPORT_LEN = 8  # modifiers, reserved, 6 key codes

# The standard boot-keyboard Report descriptor: an 8-byte Input report plus a
# 1-byte LED Output report, raw bytes in wire order.
# fmt: off
KEYBOARD_REPORT_DESC = bytes([
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x06,        # Usage (Keyboard)
    0xA1, 0x01,        # Collection (Application)
    0x05, 0x07,        #   Usage Page (Keyboard/Keypad)
    0x19, 0xE0,        #   Usage Minimum (Left Control)
    0x29, 0xE7,        #   Usage Maximum (Right GUI)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x01,        #   Logical Maximum (1)
    0x75, 0x01,        #   Report Size (1)
    0x95, 0x08,        #   Report Count (8)
    0x81, 0x02,        #   Input (Data,Var,Abs) - modifiers
    0x95, 0x01,        #   Report Count (1)
    0x75, 0x08,        #   Report Size (8)
    0x81, 0x03,        #   Input (Const) - reserved byte
    0x95, 0x05,        #   Report Count (5)
    0x75, 0x01,        #   Report Size (1)
    0x05, 0x08,        #   Usage Page (LEDs)
    0x19, 0x01,        #   Usage Minimum (Num Lock)
    0x29, 0x05,        #   Usage Maximum (Kana)
    0x91, 0x02,        #   Output (Data,Var,Abs) - LEDs
    0x95, 0x01,        #   Report Count (1)
    0x75, 0x03,        #   Report Size (3)
    0x91, 0x03,        #   Output (Const) - padding
    0x95, 0x06,        #   Report Count (6)
    0x75, 0x08,        #   Report Size (8)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x65,        #   Logical Maximum (101)
    0x05, 0x07,        #   Usage Page (Keyboard/Keypad)
    0x19, 0x00,        #   Usage Minimum (0)
    0x29, 0x65,        #   Usage Maximum (101)
    0x81, 0x00,        #   Input (Data,Array) - 6 key codes
    0xC0,              # End Collection
])
# fmt: on


class BootKeyboard(Interface):
    """One USB interface, written from scratch: subclass Interface, then add it."""

    # the interface descriptor's own fields, spelled as on the wire
    bInterfaceClass = USB_CLASS_HID
    bInterfaceSubClass = HID_SUBCLASS_BOOT
    bInterfaceProtocol = HID_PROTOCOL_KEYBOARD

    # device -> host: the 8-byte key reports, and the pipe to write them to
    reports = In(0x81, "interrupt", mps=KEY_REPORT_LEN, interval=10)

    def extra_descriptors(self) -> bytes:
        """Appended after the interface descriptor: the HID class descriptor (HID 1.11 6.2.1)."""
        return struct.pack(
            "<BBHBBBH",
            9,
            HID_DT_HID,
            0x0111,  # bcdHID 1.11
            0,  # bCountryCode: not localized
            1,  # bNumDescriptors
            HID_DT_REPORT,
            len(KEYBOARD_REPORT_DESC),
        )

    def on_control(self, s, data=b""):
        """The requests the core cannot answer for us. Raising Stall says "no"."""
        if s.type == STANDARD:  # GET_DESCRIPTOR(Report)
            if s.bRequest == 0x06 and (s.wValue >> 8) == HID_DT_REPORT:
                return KEYBOARD_REPORT_DESC
            raise Stall
        if s.type != CLASS:
            raise Stall
        if s.bRequest == HID_GET_REPORT:
            return bytes(KEY_REPORT_LEN)  # no key is held right now
        if s.bRequest == HID_SET_REPORT:  # the LED report, sent on endpoint 0
            leds = data[0] if data else 0
            num_lock = "on" if leds & 0x01 else "off"
            caps_lock = "on" if leds & 0x02 else "off"
            scroll_lock = "on" if leds & 0x04 else "off"
            log.info("LEDs: NumLock=%s CapsLock=%s ScrollLock=%s", num_lock, caps_lock, scroll_lock)
            return b""
        if s.bRequest in (HID_SET_IDLE, HID_SET_PROTOCOL):
            return b""
        if s.bRequest == HID_GET_IDLE:
            return b"\x00"
        if s.bRequest == HID_GET_PROTOCOL:
            return b"\x01"  # report protocol
        raise Stall

    def type_string(self, text):
        """One key press is two reports on the interrupt IN endpoint: down, then up."""
        for ch in text:
            key = key_for(ch)
            if key is None:
                continue
            mod, code = key
            self.reports.write(bytes([mod, 0, code, 0, 0, 0, 0, 0]))  # key down
            time.sleep(0.02)
            self.reports.write(bytes(KEY_REPORT_LEN))  # key up
            time.sleep(0.02)
        log.info("typed %r", text)


def build_keyboard(vid, pid):
    """Put the interface on a device; returns (device, keyboard)."""
    dev = USBDevice(
        vid, pid, manufacturer="USB over IP", product="USBIP Boot Keyboard", serial="0011"
    )
    # add() numbers the interface, routes its endpoint and registers its handlers
    return dev, dev.add(BootKeyboard())


def key_for(ch):
    """Minimal ASCII -> (modifier, HID usage) on the Keyboard/Keypad page."""
    if "a" <= ch <= "z":
        return 0, 0x04 + ord(ch) - ord("a")
    if "A" <= ch <= "Z":
        return 0x02, 0x04 + ord(ch) - ord("A")  # 0x02 = Left Shift
    if "1" <= ch <= "9":
        return 0, 0x1E + ord(ch) - ord("1")
    if ch == "0":
        return 0, 0x27
    if ch == " ":
        return 0, 0x2C
    if ch in "\r\n":
        return 0, 0x28
    return None


def main():
    ap = argparse.ArgumentParser(description="a HID boot keyboard written as an Interface")
    ap.add_argument("--vid", type=lambda s: int(s, 0), default=0x1209)
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=0x0011)
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default: every interface)")
    ap.add_argument("--port", type=int, default=3240, help="TCP port to serve on")
    ap.add_argument("--type", default="hello", help="text to type once a host attaches")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s", stream=sys.stderr)

    dev, kbd = build_keyboard(args.vid, args.pid)

    # type on every (re)attach, off the serving thread so the delays don't stall it
    def on_attach():
        threading.Thread(target=kbd.type_string, args=(args.type,), daemon=True).start()

    dev.add_reset_hook(on_attach)

    # plug() starts SERVING: nothing enumerates until a USB/IP client imports it
    transport = usbip.USBIP(args.host, args.port)
    dev.plug(via=transport)
    log.info("serving %04x:%04x on %s:%d", args.vid, args.pid, args.host, args.port)
    log.info("attach it: sudo usbip attach -r 127.0.0.1 -b 1-1")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        dev.unplug()
    return 0


if __name__ == "__main__":
    sys.exit(main())
