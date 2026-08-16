# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
USB DFU (Device Firmware Upgrade), DFU 1.1 - device class.

Presents a DFU-mode device (bInterfaceProtocol 0x02, already in dfuIDLE) that
dfu-util can UPLOAD from and DOWNLOAD to. One **alternate setting per target**.
The class makes NO assumption about what backs a target - a file, a flash part,
whatever: each target is an application-supplied object exposing a small
linear-byte-store protocol (see examples/device/dfu_device.py for a file
backend). DFU is entirely EP0 control transfers, so this needs no USB/IP
transport support.

A target object must provide:
    name                     str, the iInterface label (`dfu-util -l`)
    i_string                 int, the string index (set for you when added)
    length                   int, bytes currently stored (read by the class)
    write(off, data)         store bytes at byte offset `off` (raise IndexError if full)
    read(off, size) -> bytes up to `size` bytes at `off` (b"" at/after the end)
    begin_download()         a fresh download is starting (discard the old image)
    finish_download()        the download finished (commit/flush)

With `winusb=True` (the default) the function advertises WinUSB, so Windows
auto-binds the WinUSB driver and dfu-util works without Zadig.

Mirrors the C classes/device/dfu.c. Use `dev.add(DFU(targets=[...]))`.
"""

from __future__ import annotations

import struct

from ...core import Stall
from ...function import Interface

# class-specific requests
DFU_DETACH, DFU_DNLOAD, DFU_UPLOAD = 0, 1, 2
DFU_GETSTATUS, DFU_CLRSTATUS, DFU_GETSTATE, DFU_ABORT = 3, 4, 5, 6

# states (DFU 1.1 Table 4.2)
# fmt: off
appIDLE, appDETACH, dfuIDLE, dfuDNLOAD_SYNC, dfuDNBUSY, dfuDNLOAD_IDLE, \
    dfuMANIFEST_SYNC, dfuMANIFEST, dfuMANIFEST_WAIT_RESET, dfuUPLOAD_IDLE, \
    dfuERROR = range(11)
# fmt: on

# bStatus codes (DFU 1.1 Sec.6.1.2), reported to the host via DFU_GETSTATUS
# fmt: off
OK, errTARGET, errFILE, errWRITE, errERASE, errCHECK_ERASED, errPROG, errVERIFY, \
    errADDRESS, errNOTDONE, errFIRMWARE, errVENDOR, errUSBR, errPOR, errUNKNOWN, \
    errSTALLEDPKT = range(16)
# fmt: on


class DFUError(Exception):
    """Raised by a target's write()/read() to report a specific DFU bStatus code:
    the class catches it, enters dfuERROR, and returns that status on GETSTATUS. A
    plain IndexError from write() is the simple case and maps to errADDRESS."""

    def __init__(self, status=errWRITE):
        super().__init__(f"DFU status 0x{status:02x}")
        self.status = status


DFU_FUNCTIONAL = 0x21  # bDescriptorType of the DFU functional descriptor
# bmAttributes: bitCanDnload | bitCanUpload | bitManifestationTolerant
ATTR_DEFAULT = 0x07


# ---- the DFU interface ---------------------------------------------------
class DFU(Interface):
    """A DFU interface, one alternate setting per target. `targets` is a list of
    app-supplied target objects (see the module docstring for the protocol; the
    example provides a file backend). With winusb=True (default) the function
    advertises WinUSB so dfu-util works on Windows without Zadig."""

    bInterfaceClass = 0xFE  # Application Specific
    bInterfaceSubClass = 0x01  # Device Firmware Upgrade
    bInterfaceProtocol = 0x02  # DFU mode

    def __init__(
        self,
        targets,
        transfer_size=1024,
        attributes=ATTR_DEFAULT,
        detach_timeout=1000,
        winusb=True,
        on_event=None,
    ):
        super().__init__()
        self.targets = list(targets)
        self.transfer_size = transfer_size
        self.attributes = attributes
        self.detach_timeout = detach_timeout
        self.winusb = winusb
        self.on_event = on_event or (lambda text: None)
        self.cur = 0
        self.state = dfuIDLE
        self.status = OK

    def _on_added(self):
        for target in self.targets:
            target.i_string = self.dev.add_string(target.name)
        if self.winusb:
            # scoped to THIS function: a device-wide WINUSB Compatible ID would bind
            # WinUSB over everything and a neighbouring CDC port would never get usbser.
            # Identical bytes when DFU is the only function.
            self.func.enable_winusb()

    @property
    def target(self):
        return self.targets[self.cur]

    # --- descriptors: one interface, N alternate settings + functional desc ---
    def _functional(self) -> bytes:
        return struct.pack(
            "<BBBHHH",
            9,
            DFU_FUNCTIONAL,
            self.attributes,
            self.detach_timeout,
            self.transfer_size,
            0x0110,
        )

    def descriptor_block(self) -> bytes:
        from ... import core

        ifnum = self.interface_number
        blk = b"".join(
            core.InterfaceDescriptor(
                bInterfaceNumber=ifnum,
                bAlternateSetting=alt,
                bNumEndpoints=0,
                bInterfaceClass=self.bInterfaceClass,
                bInterfaceSubClass=self.bInterfaceSubClass,
                bInterfaceProtocol=self.bInterfaceProtocol,
                iInterface=tgt.i_string,
            ).pack()
            for alt, tgt in enumerate(self.targets)
        )
        return blk + self._functional()  # one functional descriptor after the alts

    # --- alternate setting selects the active target ---
    def set_alt(self, alt: int):
        if 0 <= alt < len(self.targets):
            self.cur = alt
            self.state, self.status = dfuIDLE, OK
            self.on_event(f"SELECT {self.target.name} (alt {alt})")

    # --- DFU class requests (all on EP0) ---
    def on_control(self, setup, data=b""):
        if (setup.bmRequestType & 0x60) != 0x20:  # DFU uses class requests only
            raise Stall
        req = setup.bRequest
        if req == DFU_DNLOAD:
            return self._dnload(setup, bytes(data))
        if req == DFU_UPLOAD:
            return self._upload(setup)
        if req == DFU_GETSTATUS:
            if self.state == dfuDNLOAD_SYNC:
                self.state = dfuDNLOAD_IDLE
            elif self.state == dfuMANIFEST_SYNC:  # tolerant: manifest is instant
                self.state = dfuIDLE
            return bytes((self.status, 0, 0, 0, self.state, 0))  # bwPollTimeout = 0
        if req == DFU_GETSTATE:
            return bytes((self.state,))
        if req in (DFU_CLRSTATUS, DFU_ABORT):
            self.state, self.status = dfuIDLE, OK
            return b""
        if req == DFU_DETACH:
            return b""  # no-op in DFU mode
        raise Stall

    def _dnload(self, setup, data):
        target = self.target
        if not data:  # zero-length block = end of download
            target.finish_download()
            self.state, self.status = dfuMANIFEST_SYNC, OK
            self.on_event(f"DOWNLOAD done {target.name} ({target.length}B)")
            return b""
        if setup.wValue == 0:  # first block = fresh image
            target.begin_download()
        try:
            target.write(setup.wValue * self.transfer_size, data)
        except (DFUError, IndexError) as exc:  # backend rejected the block
            # IndexError is the simple "out of range" case; DFUError carries a chosen code.
            self.status = exc.status if isinstance(exc, DFUError) else errADDRESS
            self.state = dfuERROR
            self.on_event(f"DNLOAD error: status 0x{self.status:02x}")
            return b""  # ACK the block; the host learns the error from GETSTATUS (dfuERROR)
        self.state, self.status = dfuDNLOAD_SYNC, OK
        self.on_event(f"DNLOAD ({len(data)}B)")
        return b""

    def _upload(self, setup):
        target = self.target
        chunk = target.read(setup.wValue * self.transfer_size, min(self.transfer_size, setup.wLength))
        self.state = dfuUPLOAD_IDLE if chunk else dfuIDLE
        self.status = OK
        event = f"UPLOAD ({len(chunk)}B)" if chunk else f"UPLOAD done {target.name}"
        self.on_event(event)
        return chunk
