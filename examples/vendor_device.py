#!/usr/bin/env python3
"""
Copyright (C) 2026 Jabez Winston

SPDX-License-Identifier: MIT

Date : 05-June-2026

A vendor-defined USB device with one Bulk IN and one Bulk OUT, over USB/IP.

Self-contained: the whole device lives in this file using only the public device
API (usbip.device) - there is NO class code behind it. Behaviour is a loopback:
whatever the host sends on Bulk OUT is echoed straight back on Bulk IN.

Everything is fixed - there are no command-line options; edit the constants below
to change the identity, endpoints or port.

  python3 vendor_device.py                         # serve 1209:0004 on :3240

Drive it with the libusb wrapper (no kernel needed), e.g. from pyusb:
  be = usb.backend.libusb1.get_backend(find_library=lambda _: ".../libusb-1.0.so.0")
  dev = usb.core.find(idVendor=0x1209, idProduct=0x0004, backend=be)
  dev.write(0x01, b"hi"); dev.read(0x81, 64)        # -> b"hi"
or attach it to the local kernel:
  attach it with a USB/IP client
  (Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1)
"""

from usbip import In, Interface, Out, USBDevice
from usbip.transport import USBIP

# identity + wiring (a vendor device defines its own class 0xFF)
VENDOR_ID = 0x1209
PRODUCT_ID = 0x0004

HOST = "0.0.0.0"  # bind address
PORT = 3240  # USB/IP port


class VendorBulk(Interface):
    """One vendor interface (class 0xFF) with a bulk OUT and a bulk IN endpoint."""

    bInterfaceClass = 0xFF
    bInterfaceSubClass = 0x00
    bInterfaceProtocol = 0x00

    bulk_out = Out(0x01, "bulk", mps=64)  # host   -> device
    bulk_in = In(0x81, "bulk", mps=64)  # device -> host


def main():
    dev = USBDevice(
        VENDOR_ID,
        PRODUCT_ID,
        manufacturer="USB over IP",
        product="USBIP Vendor Bulk",
        serial="0004",
    )
    fn = dev.add(VendorBulk())

    print("[vendor] attach: sudo usbip attach -r 127.0.0.1 -b 1-1", flush=True)

    transport = USBIP(HOST, PORT)
    dev.plug(via=transport)
    try:
        while True:  # loopback: read OUT, echo to IN
            data = fn.bulk_out.read(timeout=1.0)  # b"" on timeout (lets Ctrl-C through)
            if data:
                print(f"[vendor] RX {len(data)} bytes -> echo", flush=True)
                fn.bulk_in.write(data)  # device -> host
    except KeyboardInterrupt:
        pass
    finally:
        dev.unplug()


if __name__ == "__main__":
    main()
