# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
usbip - create virtual USB devices and write host drivers over USB/IP.

Top-level vocabulary is pure USB; USB/IP is the (default-local) transport.

    import usbip
    from usbip.device import USBDevice
    from usbip.classes.device.hid import HID, keyboard_report_descriptor

    class Keyboard(HID):
        report_descriptor = keyboard_report_descriptor()
        def key(self, code):
            self.send_report(bytes([0, 0, code, 0, 0, 0, 0, 0]))
            self.send_report(bytes(8))

    dev = USBDevice(0x1209, 0x0001, product="Py Keyboard")
    dev.add(Keyboard())
    with dev.plug():            # local; or dev.plug(via=usbip.USBIP("10.0.0.5"))
        ...
"""

from .core import (
    IN,
    OUT,
    SPEED_FULL,
    SPEED_HIGH,
    SPEED_LOW,
    SPEED_SUPER,
    ConfigurationDescriptor,
    DeviceDescriptor,
    EndpointDescriptor,
    InterfaceDescriptor,
    NotFound,
    Setup,
    Stall,
    Timeout,
    USBError,
)
from .device import Endpoint, In, Out, USBDevice
from .function import Function, Interface
from .grouplog import GroupedLog
from .host import Connection, Driver, Handle, attach, open
from .transport import USBIP, Loopback, Transport, use

__version__ = "0.7.0"

__all__ = [
    "IN",
    "OUT",
    "SPEED_FULL",
    "SPEED_HIGH",
    "SPEED_LOW",
    "SPEED_SUPER",
    "USBIP",
    "ConfigurationDescriptor",
    "Connection",
    "DeviceDescriptor",
    "Driver",
    "Endpoint",
    "EndpointDescriptor",
    "Function",
    "GroupedLog",
    "Handle",
    "In",
    "Interface",
    "InterfaceDescriptor",
    "Loopback",
    "NotFound",
    "Out",
    "Setup",
    "Stall",
    "Timeout",
    "Transport",
    "USBDevice",
    "USBError",
    "attach",
    "open",
    "use",
]
