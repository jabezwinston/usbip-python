# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
USB Video Class (webcam) - device class: isochronous streaming.

Mirrors the C classes/device/uvc.c. Presents a VideoControl + VideoStreaming
interface pair (grouped by an IAD), advertising YUY2 and/or MJPEG at one
resolution, runs Probe/Commit negotiation, and streams UVC payloads over an
isochronous IN endpoint. The frame source is the built-in animated color-bar
generator unless a `source` callback is supplied.

Descriptor layout follows the kernel UVC gadget (drivers/usb/gadget/legacy/
webcam.c) and uapi/linux/usb/video.h. Use `dev.add(UVC(...))`.
"""

from __future__ import annotations

import struct

from ... import core
from ...core import Stall
from ...device import In
from ...function import Function, Interface

# class/subclass + descriptor subtypes (UVC 1.1 / 1.5)
CC_VIDEO, SC_CONTROL, SC_STREAMING, SC_COLLECTION = 0x0E, 0x01, 0x02, 0x03
CS_INTERFACE = 0x24
VC_HEADER, VC_INPUT_TERMINAL, VC_OUTPUT_TERMINAL = 0x01, 0x02, 0x03
VS_INPUT_HEADER = 0x01
VS_FORMAT_UNCOMP, VS_FRAME_UNCOMP = 0x04, 0x05
VS_FORMAT_MJPEG, VS_FRAME_MJPEG = 0x06, 0x07
VS_COLORFORMAT = 0x0D
ITT_CAMERA, TT_STREAMING = 0x0201, 0x0101

# class-specific requests / control selectors
SET_CUR = 0x01
# fmt: off
GET_CUR, GET_MIN, GET_MAX, GET_RES, GET_LEN, GET_INFO, GET_DEF = \
    0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87
# fmt: on
VS_PROBE_CONTROL, VS_COMMIT_CONTROL = 0x01, 0x02

# payload header info bits
STREAM_FID, STREAM_EOF = 0x01, 0x02

ISO_MPS = 1023  # full-speed isochronous max packet
PROBE_LEN = 34  # uvc_streaming_control (UVC 1.1)
EP_IN = 0x81
YUY2_GUID = b"YUY2\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"


def _interface(num, alt, n_eps, sub):
    return core.InterfaceDescriptor(
        bInterfaceNumber=num,
        bAlternateSetting=alt,
        bNumEndpoints=n_eps,
        bInterfaceClass=CC_VIDEO,
        bInterfaceSubClass=sub,
        bInterfaceProtocol=0,
    ).pack()


def _frame_desc(subtype, width, height, bitrate, maxbuf, iv) -> bytes:
    """The 30-byte VS frame descriptor - identical for YUY2 and MJPEG but for
    the subtype: one frame index, min/max bitrate, one interval choice."""
    return struct.pack(
        "<BBBBBHHIIIIBI", 30, CS_INTERFACE, subtype, 1, 0, width, height, bitrate, bitrate, maxbuf, iv, 1, iv
    )


# ---- synthetic frame source: animated SMPTE-ish color bars (YUY2) ----
# fmt: off
_BARS = (
    (235, 128, 128), (210, 16, 146), (170, 166, 16), (145, 54, 34),
    (106, 202, 222), (81, 90, 240), (41, 240, 110), (16, 128, 128),
)
# fmt: on


def color_bars_yuyv(width, height, index) -> bytes:
    """One packed-YUYV frame of vertical color bars, scrolling with `index`."""
    shift = index % (width or 1)
    row = bytearray(width * 2)
    for x in range(0, width, 2):
        c0 = _BARS[(((x + shift) * 8) // width) & 7]
        c1 = _BARS[(((x + 1 + shift) * 8) // width) & 7]
        row[x * 2 + 0] = c0[0]  # Y0
        row[x * 2 + 1] = c0[1]  # U
        row[x * 2 + 2] = c1[0]  # Y1
        row[x * 2 + 3] = c0[2]  # V
    return bytes(row) * height


# ---- the two interfaces ----
class VideoControl(Interface):
    """VideoControl interface (interface N): IAD + camera terminal -> output."""

    bInterfaceClass = CC_VIDEO
    bInterfaceSubClass = SC_CONTROL

    def descriptor_block(self) -> bytes:
        # The Interface Association Descriptor that groups VC+VS is emitted by the
        # owning UVC Function (Function.association_descriptor), not here.
        vc = self.interface_number
        vs = vc + 1
        intf = _interface(vc, 0, 0, SC_CONTROL)
        header = struct.pack(
            "<BBBHHIBB", 13, CS_INTERFACE, VC_HEADER, 0x0100, 40, 48000000, 1, vs
        )  # wTotalLength=13+18+9
        camera = (
            struct.pack(
                "<BBBBHBBHHHB", 18, CS_INTERFACE, VC_INPUT_TERMINAL, 1, ITT_CAMERA, 0, 0, 0, 0, 0, 3
            )
            + b"\x00\x00\x00"
        )
        output = struct.pack(
            "<BBBBHBBB", 9, CS_INTERFACE, VC_OUTPUT_TERMINAL, 2, TT_STREAMING, 0, 1, 0
        )
        return intf + header + camera + output


class VideoStreaming(Interface):
    """VideoStreaming interface (interface N+1): alt 0 (descriptors) + alt 1 (iso EP)."""

    bInterfaceClass = CC_VIDEO
    bInterfaceSubClass = SC_STREAMING
    video_in = In(EP_IN, "iso", mps=ISO_MPS, interval=1)

    def __init__(self, width, height, fps, formats=("yuyv",), source=None, on_event=None):
        super().__init__()
        self.width, self.height, self.fps = width, height, fps
        self.source = source
        self.on_event = on_event or (lambda text: None)
        self.interval = 10_000_000 // (fps or 30)
        self.max_frame_size = width * height * 2
        # 1-based format index -> 'Y' (YUYV) / 'M' (MJPEG)
        self.fmt_char = {}
        for name in formats:
            self.fmt_char[len(self.fmt_char) + 1] = "Y" if name == "yuyv" else "M"
        self.cur_format, self.cur_frame = 1, 1
        self.cur_interval = self.interval
        self.streaming = False
        self._frame = b""
        self._pos = 0
        self._fid = 0
        self.frame_index = 0

    def adjust_for_speed(self, speed):
        # HS iso allows 1024 B/packet (FS caps at 1023); with the 125 µs HS service
        # interval that is ~8× the FS bandwidth.
        self.video_in.mps = 1024 if speed >= core.SPEED_HIGH else ISO_MPS

    # --- descriptors ---
    def _format_blocks(self):
        width, height, iv = self.width, self.height, self.interval
        maxbuf = self.width * self.height * 2
        bitrate = maxbuf * 8 * (self.fps or 30)
        out = b""
        for idx, ch in self.fmt_char.items():
            if ch == "Y":
                out += struct.pack(
                    "<BBBBB16sBBBBBB",
                    27,
                    CS_INTERFACE,
                    VS_FORMAT_UNCOMP,
                    idx,
                    1,
                    YUY2_GUID,
                    16,
                    1,
                    0,
                    0,
                    0,
                    0,
                )
                out += _frame_desc(VS_FRAME_UNCOMP, width, height, bitrate, maxbuf, iv)
            else:
                out += struct.pack(
                    "<BBBBBBBBBBB", 11, CS_INTERFACE, VS_FORMAT_MJPEG, idx, 1, 0, 1, 0, 0, 0, 0
                )
                out += _frame_desc(VS_FRAME_MJPEG, width, height, bitrate, maxbuf, iv)
            out += struct.pack("<BBBBBB", 6, CS_INTERFACE, VS_COLORFORMAT, 1, 1, 4)
        return out

    def descriptor_block(self) -> bytes:
        vs = self.interface_number
        nfmt = len(self.fmt_char)
        fmts = self._format_blocks()
        vs_total = (13 + nfmt) + len(fmts)
        alt0 = _interface(vs, 0, 0, SC_STREAMING)
        # Read the address off the endpoint OBJECT, never the EP_IN constant: a
        # composite relocates it, and a class descriptor naming another function's
        # pipe makes the host not stream. Built on demand, so the address is final.
        addr = self.video_in.addr
        in_hdr = (
            struct.pack(
                "<BBBBHBBBBBBB",
                13 + nfmt,
                CS_INTERFACE,
                VS_INPUT_HEADER,
                nfmt,
                vs_total,
                addr,
                0,
                2,
                0,
                0,
                0,
                1,
            )
            + b"\x00" * nfmt
        )
        alt1 = _interface(vs, 1, 1, SC_STREAMING)
        ep = core.EndpointDescriptor(
            bEndpointAddress=addr,
            bmAttributes=0x05,  # iso, async
            wMaxPacketSize=self.video_in.mps,
            bInterval=1,
        ).pack()
        return alt0 + in_hdr + fmts + alt1 + ep

    # --- Probe/Commit negotiation ---
    def _probe(self) -> bytes:
        buf = bytearray(PROBE_LEN)
        buf[0] = 0x01  # bmHint: dwFrameInterval fixed
        buf[2] = self.cur_format
        buf[3] = self.cur_frame
        struct.pack_into("<I", buf, 4, self.cur_interval)
        struct.pack_into("<I", buf, 18, self.max_frame_size)
        struct.pack_into("<I", buf, 22, self.video_in.mps)  # dwMaxPayloadTransferSize
        struct.pack_into("<I", buf, 26, 48000000)  # dwClockFrequency
        return bytes(buf)

    def on_control(self, setup, data=b""):
        cs = setup.wValue >> 8
        if cs not in (VS_PROBE_CONTROL, VS_COMMIT_CONTROL):
            if setup.bRequest == GET_INFO:
                return b"\x03"
            raise Stall
        if setup.bRequest == GET_INFO:
            return b"\x03"  # GET | SET supported
        if setup.bRequest == GET_LEN:
            return struct.pack("<H", PROBE_LEN)
        if setup.bRequest in (GET_CUR, GET_MIN, GET_MAX, GET_DEF, GET_RES):
            return self._probe()
        if setup.bRequest == SET_CUR:
            if len(data) >= 4:
                if data[2]:
                    self.cur_format = data[2]
                if data[3]:
                    self.cur_frame = data[3]
            if len(data) >= 8:
                iv = struct.unpack_from("<I", data, 4)[0]
                if iv:
                    self.cur_interval = iv
            if cs == VS_COMMIT_CONTROL:
                self.on_event(
                    f"COMMIT format={self.fmt_char.get(self.cur_format, '?')} "
                    f"{self.width}x{self.height} @ {1e7 / self.cur_interval:.0f}fps"
                )
            return b""
        raise Stall

    # --- streaming on/off (only the VS interface has alternates) ---
    def set_alt(self, alt: int):
        if alt == 1 and not self.streaming:
            self.streaming = True
            self._frame, self._pos, self._fid, self.frame_index = b"", 0, 0, 0
            self.on_event(
                f"STREAM ON  {self.fmt_char.get(self.cur_format, '?')} {self.width}x{self.height}"
            )
        elif alt == 0 and self.streaming:
            self.streaming = False
            self.on_event(f"STREAM OFF ({self.frame_index} frames)")

    # --- frame production ---
    def _produce(self):
        ch = self.fmt_char.get(self.cur_format, "Y")
        frame = None
        if self.source:
            frame = self.source(self, ch, self.frame_index)
        if frame is None:
            frame = color_bars_yuyv(self.width, self.height, self.frame_index)
        self._frame, self._pos = frame, 0
        self.on_event(f"frame {ch} {self.width}x{self.height} ({len(frame)}B)")

    # --- isochronous IN: pack UVC payloads (2-byte header + data) per packet ---
    def on_iso(self, ep, lengths):
        if not self.streaming:
            return []
        out = []
        for cap in lengths:
            if cap < 2:
                out.append(b"")
                continue
            if not self._frame or self._pos >= len(self._frame):
                self._produce()
            remain = len(self._frame) - self._pos
            chunk = min(remain, cap - 2)
            info = self._fid
            if self._pos + chunk >= len(self._frame):
                info |= STREAM_EOF
            out.append(bytes((2, info)) + self._frame[self._pos : self._pos + chunk])
            self._pos += chunk
            if self._pos >= len(self._frame):  # frame complete -> next is fresh
                self._frame = b""
                self._fid ^= STREAM_FID
                self.frame_index += 1
        return out


class UVC(Function):
    """A UVC camera: a composite function (VideoControl + VideoStreaming) grouped by an
    Interface Association Descriptor. The IAD and the 0xEF/0x02/0x01 device triple are
    emitted by the Function machinery (opt-in), not hand-rolled by the interfaces.

    ``dev.add(UVC(...))`` returns the VideoStreaming interface (the streaming endpoint
    owner)."""

    iad = True
    iad_class, iad_subclass, iad_protocol = CC_VIDEO, SC_COLLECTION, 0
    device_triple = (0xEF, 0x02, 0x01)

    def __init__(
        self, width=320, height=240, fps=15, formats=("yuyv",), source=None, on_event=None
    ):
        self.vc = VideoControl()
        self.vs = VideoStreaming(width, height, fps, formats, source, on_event)
        self.interfaces = (self.vc, self.vs)
        super().__init__()

    @property
    def primary(self):
        return self.vs  # the streaming endpoint owner
