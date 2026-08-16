#!/usr/bin/env python3
"""
Copyright (C) 2026 Jabez Winston

SPDX-License-Identifier: MIT

Date : 05-June-2026

A WebUSB device over USB/IP - a vendor bulk loopback dressed up for the browser.

Self-contained: the whole device lives in this file using only the public device API
(usbip.device) - there is NO class code behind it. It is a vendor device (class 0xFF)
with one Bulk IN and one Bulk OUT that echoes whatever it receives back with the case
of ASCII letters swapped, plus the WebUSB descriptors so a WebUSB-capable browser can
find and open it.

To be a WebUSB device (per the WebUSB spec) it advertises, via the core:
  - a BOS descriptor carrying a WebUSB platform-capability descriptor, and
  - a vendor request (bVendorCode + wIndex=GET_URL) that returns its landing-page URL
    -- enable_webusb() does both.
It also co-advertises WinUSB (MS OS 2.0) so the interface auto-binds WinUSB on Windows,
which Chrome requires there -- enable_winusb().

Everything is fixed - there are no command-line options; edit the constants below
to change the identity, endpoints, landing page or port.

  python3 webusb_device.py                              # serve 1209:0012 on :3240

  attach it with a USB/IP client
  (Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1)
then open it from a WebUSB page served over https (or http://localhost):
  const dev = await navigator.usb.requestDevice({filters:[{vendorId:0x1209}]});
  await dev.open(); await dev.selectConfiguration(1); await dev.claimInterface(0);
  await dev.transferOut(1, new TextEncoder().encode("hi"));
  const r = await dev.transferIn(1, 64);   // -> "HI" (echoed, case swapped)
"""

from usbip import In, Interface, Out, USBDevice
from usbip.transport import USBIP

# identity + wiring (a vendor device defines its own class 0xFF)
VENDOR_ID = 0x1209
PRODUCT_ID = 0x0012
EP_BULK_OUT = 0x01  # host -> device
EP_BULK_IN = 0x81  # device -> host
MAX_PACKET = 64

# the bRequest the browser uses for the WebUSB GET_URL request. Kept distinct from
# WinUSB's MS-OS codes (0x20/0x21) so the two mechanisms never collide.
WEBUSB_VENDOR_CODE = 0x22
LANDING_PAGE = "https://jabezwinston.github.io/web-apps/webusb-test.html"

HOST = "0.0.0.0"  # bind address
PORT = 3240  # USB/IP port


class WebUsbBulk(Interface):
    """One vendor interface (class 0xFF) with a bulk OUT and a bulk IN endpoint."""

    bInterfaceClass = 0xFF
    bInterfaceSubClass = 0x00
    bInterfaceProtocol = 0x00

    bulk_out = Out(EP_BULK_OUT, "bulk", mps=MAX_PACKET)  # host -> device
    bulk_in = In(EP_BULK_IN, "bulk", mps=MAX_PACKET)  # device -> host


def main():
    dev = USBDevice(
        VENDOR_ID, PRODUCT_ID, manufacturer="USB over IP", product="USBIP WebUSB", serial="0012"
    )
    dev.enable_webusb(WEBUSB_VENDOR_CODE, LANDING_PAGE)  # BOS WebUSB cap + GET_URL
    dev.enable_winusb()  # MS OS 2.0 -> WinUSB on Windows
    fn = dev.add(WebUsbBulk())

    print(
        f"[webusb] serving {VENDOR_ID:04x}:{PRODUCT_ID:04x} on {HOST}:{PORT} "
        f"(bulk OUT 0x{EP_BULK_OUT:02x}, bulk IN 0x{EP_BULK_IN:02x}) "
        f"- echoing OUT back on IN",
        flush=True,
    )

    print(
        f"[webusb] landing page {LANDING_PAGE} (GET_URL vendor code 0x{WEBUSB_VENDOR_CODE:02x}), WinUSB on",
        flush=True,
    )
    print("[webusb] attach: sudo usbip attach -r 127.0.0.1 -b 1-1", flush=True)

    transport = USBIP(HOST, PORT)
    dev.plug(via=transport)
    try:
        while True:  # loopback: read OUT, echo to IN
            data = fn.bulk_out.read(timeout=1.0)  # b"" on timeout (lets Ctrl-C through)
            if data:
                print(f"[webusb] RX {len(data)} bytes -> echo (case swapped)", flush=True)
                fn.bulk_in.write(data.swapcase())  # device -> host, ASCII case swapped
    except KeyboardInterrupt:
        pass
    finally:
        dev.unplug()


if __name__ == "__main__":
    main()
