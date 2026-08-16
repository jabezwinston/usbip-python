# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
Reference classes, split by role: `device` (Interfaces) and `host` (Drivers).

    from usbip.classes.device.hid import HID
    from usbip.classes.host.hid import HIDDriver
"""

from . import device, host

__all__ = ["device", "host"]
