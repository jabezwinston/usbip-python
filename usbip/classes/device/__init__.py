# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
Device-side class implementations (USB Interfaces), built on the public core.

Every class is added the one way anything is added - ``dev.add(CLASS(...))`` - and
the call gives back that class's primary data-plane object (port / interface /
camera / hci ...); the returned object always carries a ``.func`` back-pointer to
the owning Function for function-scoped calls such as ``enable_winusb()``.

    from usbip.classes.device import CDCACM, MSC

    port = dev.add(CDCACM(on_rx=on_rx, name="Console"))
    disk = dev.add(MSC(store, read_only=True))
"""

from . import bluetooth, cdc_acm, dfu, hid, msc, mtp, uac, uvc
from .bluetooth import Bluetooth
from .cdc_acm import CDCACM
from .dfu import DFU
from .hid import HID
from .msc import MSC
from .mtp import MTP
from .uac import UAC
from .uvc import UVC

__all__ = [
    "bluetooth",
    "cdc_acm",
    "dfu",
    "hid",
    "msc",
    "mtp",
    "uac",
    "uvc",
    "Bluetooth",
    "CDCACM",
    "DFU",
    "HID",
    "MSC",
    "MTP",
    "UAC",
    "UVC",
]
