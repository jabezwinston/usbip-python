# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
USB-level types shared by host and device sides.

Nothing in here knows about USB/IP, sockets, or transports - it is pure USB:
the SETUP packet, descriptors, transfer enums, errors, and the URB that the
transport layer ferries around.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# ---- directions / transfer types / request types -------------------------
OUT, IN = 0, 1
CONTROL, ISO, BULK, INTERRUPT = 0, 1, 2, 3  # USB bmAttributes values
STANDARD, CLASS, VENDOR = 0, 1, 2  # SETUP bmRequestType[6:5]
DEVICE, INTERFACE, ENDPOINT = 0, 1, 2  # SETUP bmRequestType[4:0], the recipient
FEATURE_ENDPOINT_HALT = 0  # CLEAR_FEATURE/SET_FEATURE wValue on an endpoint

# link speeds (enum usb_speed, matching the C side) + the kernel usb_device_speed
# wire value in OP_REP_DEVLIST/IMPORT. USB/IP is URB-level, so "high speed" is just
# SPEED_HIGH plus correct endpoint sizing (512 B bulk, 125 µs iso microframes).
SPEED_LOW, SPEED_FULL, SPEED_HIGH, SPEED_SUPER = 0, 1, 2, 3
WIRE_SPEED = {SPEED_LOW: 1, SPEED_FULL: 2, SPEED_HIGH: 3, SPEED_SUPER: 5}

XFER_BY_NAME = {"control": CONTROL, "iso": ISO, "bulk": BULK, "interrupt": INTERRUPT}

# descriptor type codes
DT_DEVICE, DT_CONFIG, DT_STRING, DT_INTERFACE, DT_ENDPOINT = 1, 2, 3, 4, 5
DT_INTERFACE_ASSOCIATION = 0x0B
DT_BOS = 0x0F


def iter_descriptors(blob, offset=0):
    """Walk concatenated USB descriptors, yielding each one as its bytes slice
    (d[0] = bLength, d[1] = bDescriptorType). Pass offset=9 to skip a leading
    configuration header. Stops at a malformed (< 2 byte) length; a descriptor
    truncated by the end of the blob is yielded short."""
    i = offset
    while i + 2 <= len(blob):
        blen = blob[i]
        if blen < 2:
            break
        yield blob[i : i + blen]
        i += blen


# ---- errors --------------------------------------------------------------
class USBError(Exception):
    """Base for all usbip errors."""


class Stall(USBError):
    """Raised by a device control/endpoint handler to STALL the request."""


class Timeout(USBError):
    pass


class NotFound(USBError):
    pass


# ---- SETUP packet --------------------------------------------------------
@dataclass
class Setup:
    bmRequestType: int
    bRequest: int
    wValue: int
    wIndex: int
    wLength: int

    @classmethod
    def parse(cls, raw: bytes) -> Setup:
        return cls(*struct.unpack("<BBHHH", bytes(raw[:8])))

    def pack(self) -> bytes:
        return struct.pack(
            "<BBHHH", self.bmRequestType, self.bRequest, self.wValue, self.wIndex, self.wLength
        )

    @property
    def direction(self) -> int:
        return IN if self.bmRequestType & 0x80 else OUT

    @property
    def type(self) -> int:
        return (self.bmRequestType >> 5) & 0x03

    @property
    def recipient(self) -> int:
        return self.bmRequestType & 0x1F


# ---- descriptors ---------------------------------------------------------
@dataclass
class DeviceDescriptor:
    bcdUSB: int = 0x0200
    bDeviceClass: int = 0
    bDeviceSubClass: int = 0
    bDeviceProtocol: int = 0
    bMaxPacketSize0: int = 64
    idVendor: int = 0
    idProduct: int = 0
    bcdDevice: int = 0x0100
    iManufacturer: int = 0
    iProduct: int = 0
    iSerialNumber: int = 0
    bNumConfigurations: int = 1

    def pack(self) -> bytes:
        return struct.pack(
            "<BBHBBBBHHHBBBB",
            18,
            DT_DEVICE,
            self.bcdUSB,
            self.bDeviceClass,
            self.bDeviceSubClass,
            self.bDeviceProtocol,
            self.bMaxPacketSize0,
            self.idVendor,
            self.idProduct,
            self.bcdDevice,
            self.iManufacturer,
            self.iProduct,
            self.iSerialNumber,
            self.bNumConfigurations,
        )

    @classmethod
    def parse(cls, raw: bytes) -> DeviceDescriptor:
        (_, _, bcdUSB, dc, sc, pr, mps, vid, pid, bcdDev, im, ip, iser, nc) = struct.unpack(
            "<BBHBBBBHHHBBBB", bytes(raw[:18])
        )
        return cls(bcdUSB, dc, sc, pr, mps, vid, pid, bcdDev, im, ip, iser, nc)


# Spec-named descriptor dataclasses shaped like DeviceDescriptor above: exact USB
# spec field names, bLength/bDescriptorType implied, pack()/parse() symmetric.
@dataclass
class ConfigurationDescriptor:
    """Standard Configuration descriptor (USB 2.0 Sec.9.6.3). wTotalLength spans this
    descriptor plus every interface/endpoint/class descriptor that follows it."""

    wTotalLength: int
    bNumInterfaces: int
    bConfigurationValue: int = 1
    iConfiguration: int = 0
    bmAttributes: int = 0x80  # bit7 reserved (1), bit6 self-powered, bit5 remote-wakeup
    bMaxPower: int = 50  # in 2 mA units (50 -> 100 mA)

    def pack(self) -> bytes:
        return struct.pack(
            "<BBHBBBBB",
            9,
            DT_CONFIG,
            self.wTotalLength,
            self.bNumInterfaces,
            self.bConfigurationValue,
            self.iConfiguration,
            self.bmAttributes,
            self.bMaxPower,
        )

    @classmethod
    def parse(cls, raw: bytes) -> ConfigurationDescriptor:
        (_, _, total, n_ifaces, val, icfg, attrs, power) = struct.unpack("<BBHBBBBB", bytes(raw[:9]))
        return cls(total, n_ifaces, val, icfg, attrs, power)


@dataclass
class InterfaceDescriptor:
    """Standard Interface descriptor (USB 2.0 Sec.9.6.5)."""

    bInterfaceNumber: int
    bAlternateSetting: int
    bNumEndpoints: int
    bInterfaceClass: int
    bInterfaceSubClass: int
    bInterfaceProtocol: int
    iInterface: int = 0

    def pack(self) -> bytes:
        return struct.pack(
            "<BBBBBBBBB",
            9,
            DT_INTERFACE,
            self.bInterfaceNumber,
            self.bAlternateSetting,
            self.bNumEndpoints,
            self.bInterfaceClass,
            self.bInterfaceSubClass,
            self.bInterfaceProtocol,
            self.iInterface,
        )

    @classmethod
    def parse(cls, raw: bytes) -> InterfaceDescriptor:
        (_, _, num, alt, neps, iclass, isub, iproto, i_if) = struct.unpack("<BBBBBBBBB", bytes(raw[:9]))
        return cls(num, alt, neps, iclass, isub, iproto, i_if)


@dataclass
class EndpointDescriptor:
    """Standard Endpoint descriptor (USB 2.0 Sec.9.6.6). bmAttributes' low two bits
    select the transfer type (CONTROL/ISO/BULK/INTERRUPT)."""

    bEndpointAddress: int
    bmAttributes: int
    wMaxPacketSize: int
    bInterval: int

    def pack(self) -> bytes:
        return struct.pack(
            "<BBBBHB",
            7,
            DT_ENDPOINT,
            self.bEndpointAddress,
            self.bmAttributes,
            self.wMaxPacketSize,
            self.bInterval,
        )

    @classmethod
    def parse(cls, raw: bytes) -> EndpointDescriptor:
        (_, _, addr, attrs, mps, ivl) = struct.unpack("<BBBBHB", bytes(raw[:7]))
        return cls(addr, attrs, mps, ivl)


def string_descriptor(text: str) -> bytes:
    data = text.encode("utf-16-le")
    return bytes([len(data) + 2, DT_STRING]) + data


def langid_descriptor(lang=0x0409) -> bytes:
    return struct.pack("<BBH", 4, DT_STRING, lang)


# ---- Microsoft OS descriptors (WinUSB auto-binding) ----------------------
# Two independent pure-descriptor mechanisms:
#   1.0 - the magic 0xEE string descriptor points at a vendor request returning
#         Compatible ID + Extended Properties;
#   2.0 - a BOS platform-capability descriptor points at a vendor request returning
#         one self-describing descriptor set.
#
# CompatibleID "WINUSB" auto-binds WinUSB, so dfu-util works without Zadig.

# default DeviceInterfaceGUID handed to Windows if the caller doesn't override it
DEFAULT_WINUSB_GUID = "{D2A45C1E-7B3F-4A6E-9C2D-1E5F8B0A3C7D}"

# Microsoft OS 2.0 platform-capability UUID {D8DD60DF-4589-4CC7-9CD2-659D9E648A9F},
# serialized in the GUID's mixed-endian wire order (per the Microsoft OS 2.0 spec).
_MSOS20_UUID = bytes(
    (0xDF, 0x60, 0xDD, 0xD8, 0x89, 0x45, 0xC7, 0x4C, 0x9C, 0xD2, 0x65, 0x9D, 0x9E, 0x64, 0x8A, 0x9F)
)

# WebUSB platform-capability UUID {3408b638-09a9-47a0-8bfd-a0768815b665}, in the
# GUID's mixed-endian wire order (per the WebUSB spec).
_WEBUSB_UUID = bytes(
    (0x38, 0xB6, 0x08, 0x34, 0xA9, 0x09, 0xA0, 0x47, 0x8B, 0xFD, 0xA0, 0x76, 0x88, 0x15, 0xB6, 0x65)
)


def ms_os1_string(vendor_code: int) -> bytes:
    """The 0xEE 'OS String Descriptor': 'MSFT100' + the vendor request code."""
    return (
        bytes((0x12, DT_STRING)) + "MSFT100".encode("utf-16-le") + bytes((vendor_code & 0xFF, 0x00))
    )


def ms_os1_compatid(first_interface=0, compatible=b"WINUSB", sections=None) -> bytes:
    """Microsoft OS 1.0 Compatible-ID feature descriptor.

    `sections` is a list of ``(first_interface, compatible)`` pairs, one per
    advertised function -- a composite device needs one each, since every function
    binds its own driver. The default builds the single-section form from
    `first_interface`/`compatible`."""
    if sections is None:
        sections = [(first_interface, compatible)]
    body = b""
    for first, compat in sections:
        body += bytes((first, 0x01)) + compat[:8].ljust(8, b"\x00") + b"\x00" * 8 + b"\x00" * 6
    header = struct.pack("<IHHB", 16 + len(body), 0x0100, 0x0004, len(sections)) + b"\x00" * 7
    return header + body


def ms_os1_extprops(guid=DEFAULT_WINUSB_GUID) -> bytes:
    """Microsoft OS 1.0 Extended-Properties feature descriptor: one REG_SZ
    'DeviceInterfaceGUID' so libusb can find a stable interface GUID. With
    guid=None it returns an empty set (0 properties), e.g. for MTP."""
    if guid is None:
        return struct.pack("<IHHH", 10, 0x0100, 0x0005, 0)  # header only, 0 properties
    name = "DeviceInterfaceGUID\0".encode("utf-16-le")
    data = (guid + "\0").encode("utf-16-le")
    section = struct.pack("<IIH", 0, 1, len(name)) + name + struct.pack("<I", len(data)) + data
    section = struct.pack("<I", len(section)) + section[4:]  # patch dwSize
    header = struct.pack("<IHHH", 10 + len(section), 0x0100, 0x0005, 1)
    return header + section


def ms_os2_feature(compatible=b"WINUSB", guid=DEFAULT_WINUSB_GUID) -> bytes:
    """One Microsoft OS 2.0 feature block: a Compatible-ID, plus a REG_MULTI_SZ
    'DeviceInterfaceGUIDs' registry property (omitted when guid is None, e.g. MTP).
    No set header -- see :func:`ms_os2_set` / :func:`ms_os2_set_subsets`."""
    compat = compatible[:8].ljust(8, b"\x00")
    compatid = struct.pack("<HH", 0x14, 0x03) + compat + b"\x00" * 8
    regprop = b""
    if guid is not None:
        name = "DeviceInterfaceGUIDs\0".encode("utf-16-le")
        data = (guid + "\0\0").encode("utf-16-le")  # REG_MULTI_SZ: double NUL
        body = struct.pack("<HH", 0x07, len(name)) + name + struct.pack("<H", len(data)) + data
        regprop = struct.pack("<HH", 4 + len(body), 0x04) + body
    return compatid + regprop


def ms_os2_set(compatible=b"WINUSB", guid=DEFAULT_WINUSB_GUID) -> bytes:
    """Microsoft OS 2.0 descriptor set for a single-function device: set header + the
    feature, applying device-wide."""
    body = ms_os2_feature(compatible, guid)
    return struct.pack("<HHIH", 10, 0x00, 0x06030000, 10 + len(body)) + body


def ms_os2_set_subsets(entries) -> bytes:
    """Microsoft OS 2.0 descriptor set for a COMPOSITE device.

    `entries` is a list of ``(first_interface, compatible, guid)``. Each becomes a
    Function Subset naming its bFirstInterface, all wrapped in one Configuration
    Subset. Without the subsets a feature sits directly under the set header and
    applies to the whole device -- which on a composite would bind one driver across
    every function instead of the one it was meant for."""
    subsets = b""
    for first, compatible, guid in entries:
        body = ms_os2_feature(compatible, guid)
        # Function Subset Header: wLength, wDescriptorType, bFirstInterface,
        # bReserved, wSubsetLength
        subsets += struct.pack("<HHBBH", 8, 0x0002, first, 0, 8 + len(body)) + body
    # Configuration Subset Header. bConfigurationValue is an INDEX, so 0 = the first
    # configuration (not the bConfigurationValue sent in SET_CONFIGURATION).
    cfg = struct.pack("<HHBBH", 8, 0x0001, 0, 0, 8 + len(subsets)) + subsets
    return struct.pack("<HHIH", 10, 0x00, 0x06030000, 10 + len(cfg)) + cfg


def msos2_platform_cap(vendor_code: int, set_total_len: int) -> bytes:
    """Microsoft OS 2.0 BOS platform-capability descriptor (28 bytes)."""
    return (
        bytes((28, 0x10, 0x05, 0x00))
        + _MSOS20_UUID
        + struct.pack("<IHBB", 0x06030000, set_total_len, vendor_code & 0xFF, 0)
    )


# ---- WebUSB descriptors --------------------------------------------------
# A BOS platform-capability descriptor (UUID + vendor request code); that request
# with wIndex=GET_URL (0x02) returns the landing-page URL a browser surfaces.


def webusb_platform_cap(vendor_code: int, landing_page: int = 1) -> bytes:
    """WebUSB BOS platform-capability descriptor (24 bytes)."""
    return (
        bytes((24, 0x10, 0x05, 0x00))
        + _WEBUSB_UUID
        + struct.pack("<HBB", 0x0100, vendor_code & 0xFF, landing_page & 0xFF)
    )


def webusb_url(url: str) -> bytes:
    """WebUSB URL descriptor: bScheme + UTF-8 URL (scheme prefix stripped:
    0=http:// 1=https:// 255=URL carries its own scheme)."""
    scheme = 255
    if url.startswith("https://"):
        scheme, url = 1, url[8:]
    elif url.startswith("http://"):
        scheme, url = 0, url[7:]
    body = url.encode("utf-8")
    return bytes((3 + len(body), 0x03, scheme)) + body


def bos(*caps: bytes) -> bytes:
    """BOS descriptor wrapping the given device-capability descriptors."""
    body = b"".join(caps)
    return struct.pack("<BBHB", 5, DT_BOS, 5 + len(body), len(caps)) + body


# ---- URB: the unit the transport carries ---------------------------------
@dataclass
class Urb:
    command: int = 0
    seqnum: int = 0
    devid: int = 0
    direction: int = 0  # IN / OUT
    ep: int = 0  # endpoint number 0..15
    flags: int = 0
    length: int = 0  # requested transfer length
    interval: int = 0
    setup: bytes = b"\x00" * 8
    buffer: bytes = b""  # OUT payload, or IN result after completion
    status: int = 0  # 0 = OK, negative = error (STALL etc.)
    actual: int = 0  # bytes actually transferred
    number_of_packets: int = 0  # >0 only for isochronous transfers
    iso_packets: list | None = None  # [[offset, length, actual_length, status], ...] or None

    @property
    def ep_addr(self) -> int:
        """Endpoint address byte: the endpoint number plus the IN direction bit."""
        return self.ep | (0x80 if self.direction == IN else 0)
