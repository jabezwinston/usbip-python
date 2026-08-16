# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
Host-side extras: read filesystems directly over the MSC host driver,
without mounting via the kernel.

    from usbip.classes.host.msc import MSCDriver
    from usbip.classes.host.extras import BlockDevice, FatFs

    with MSCDriver.open(vid, pid) as msc:
        fat = FatFs(BlockDevice.from_msc(msc))
        print(fat.listdir("/"))
        data = fat.read_file("/HELLO.TXT")
"""

from .blockdev import BlockDevice
from .fatfs import FatFs
from .isofs import IsoFs

__all__ = ["BlockDevice", "FatFs", "IsoFs"]
