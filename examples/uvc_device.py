#!/usr/bin/env python3
"""
Copyright (C) 2026 Jabez Winston

SPDX-License-Identifier: MIT

Date : 04-June-2026

Virtual USB webcam (UVC) over USB/IP - isochronous streaming.

  python3 uvc_device.py                              # 320x240 YUY2 color bars
  python3 uvc_device.py --width 640 --height 480 --fps 15
  python3 uvc_device.py --format both                # advertise YUY2 + MJPEG
  python3 uvc_device.py --source file --file cap.yuyv
  python3 uvc_device.py --file movie.mp4 --format mjpeg   # play an MP4

The class (usbip.classes.device.uvc) only ever deals in uncompressed YUY2 - it
synthesizes color bars and owns every descriptor/endpoint. THIS file does the
video processing, in four self-contained sections below:

  1. JPEG encode   - YUYV -> JPEG for --format mjpeg (via Pillow)
  2. DSP           - 8x8 IDCT, YUYV packing, bilinear resize (numpy)
  3. JPEG decode   - hand-rolled baseline decoder, for Motion-JPEG MP4s
  4. MP4 demux     - hand-rolled ISO-BMFF parser, no ffmpeg/libav

MP4 support (hand-rolled, no libav): the ISO-BMFF demuxer handles any track, but
only **Motion-JPEG MP4 decodes** (baseline JPEG decoder). Other codecs (H.264,
MPEG-4, H.263) are detected but not decoded - transcode such clips to a
Motion-JPEG MP4 or raw YUYV. Pure-Python decode is not real-time; small clips
work best, and --format mjpeg streams most smoothly.

Pillow is needed only for --format mjpeg/both, and numpy only for --source mp4;
plain YUY2 color bars need neither.

  attach it with a USB/IP client
  (Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1)
  then open it in any app that lists cameras
  (Linux: ffmpeg -f v4l2 -i /dev/videoN -frames:v 1 shot.png)
"""

from __future__ import annotations

import argparse
import io
import logging
import struct
import sys
import time

import usbip
from usbip.classes.device import UVC, uvc
from usbip.device import USBDevice

try:
    import numpy as np  # only the MP4 decode path needs it
except ImportError:
    np = None

log = logging.getLogger("uvc")

# a --file with one of these extensions implies --source mp4
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".3gp", ".mkv")

DEFAULT_WIDTH = 320
DEFAULT_HEIGHT = 240
DEFAULT_FPS = 15

YUYV_BYTES_PER_PIXEL = 2


# ===========================================================================
# 1. JPEG encoding - YUYV -> JPEG, for --format mjpeg
#
# The UVC class only deals in uncompressed YUY2, so advertising MJPEG makes the
# *example* turn each YUYV frame into a JPEG. The C example hand-rolls a baseline
# JFIF encoder for want of an image library; Python defers to Pillow. Either way
# the class itself never compresses anything.
# ===========================================================================
def encode_yuyv(yuyv, width, height, quality=75) -> bytes:
    """Encode one packed-YUYV frame (width*height*2 bytes) as a baseline JFIF JPEG."""
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit(
            "--format mjpeg needs Pillow (pip install pillow), or use --format yuyv"
        ) from None

    # YUY2 is 4:2:2 packed (Y0 Cb Y1 Cr per 2 px). Expand it to interleaved 4:4:4
    # YCbCr (3 bytes/px) goes straight into the JPEG, which is natively YCbCr, so no
    # colour conversion. Extended-slice assignment de-interleaves in C.
    ycc = bytearray(width * height * 3)
    ycc[0::6] = yuyv[0::4]  # Y0
    ycc[1::6] = yuyv[1::4]  # Cb
    ycc[2::6] = yuyv[3::4]  # Cr
    ycc[3::6] = yuyv[2::4]  # Y1
    ycc[4::6] = yuyv[1::4]  # Cb
    ycc[5::6] = yuyv[3::4]  # Cr

    img = Image.frombytes("YCbCr", (width, height), bytes(ycc))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


# ===========================================================================
# 2. DSP - the numpy pieces the JPEG decoder and the resizer share
# ===========================================================================
DCT_N = 8  # JPEG works on 8x8 blocks

_idct_basis_cache = None


def idct_basis():
    """The orthonormal 8x8 DCT-II basis, built once on first use.

    A[u,x] = sqrt(2/N) * c(u) * cos((2x+1) * u * pi / 2N),  with c(0) = 1/sqrt(2).
    """
    global _idct_basis_cache
    if _idct_basis_cache is not None:
        return _idct_basis_cache

    x = np.arange(DCT_N)
    angles = (2 * x[None, :] + 1) * x[:, None] * np.pi / (2 * DCT_N)
    basis = np.sqrt(2.0 / DCT_N) * np.cos(angles)
    basis[0, :] /= np.sqrt(2.0)

    _idct_basis_cache = basis
    return basis


def idct8x8(coeff):
    """Inverse DCT of float coefficient blocks shaped (..., 8, 8) -> spatial samples.

    f = A^T @ F @ A (the separable 2-D IDCT), batched over the leading dimensions.
    """
    basis = idct_basis()
    return np.einsum("ux,...uv,vy->...xy", basis, coeff, basis, optimize=True)


def pack_yuyv(luma, cb, cr):
    """Pack full-resolution Y/Cb/Cr planes (H,W uint8) into YUY2 (Y0 Cb Y1 Cr).

    Chroma is taken once per horizontal pair, because YUYV is 4:2:2.
    """
    height, width = luma.shape
    if width & 1:  # YUYV needs an even width
        width -= 1
        luma = luma[:, :width]
        cb = cb[:, :width]
        cr = cr[:, :width]

    out = np.empty((height, width, 2), np.uint8)
    out[:, :, 0] = luma
    out[:, 0::2, 1] = cb[:, 0::2]  # Cb on even (Y0) bytes
    out[:, 1::2, 1] = cr[:, 0::2]  # Cr on odd  (Y1) bytes
    return out.tobytes()


def resize(img, out_w, out_h):
    """Bilinear-resize a uint8 array (H,W[,C]) to (out_h, out_w[,C])."""
    in_h, in_w = img.shape[:2]
    if (in_w, in_h) == (out_w, out_h):
        return img

    # source coordinates of each output pixel, split into integer pair + weight
    fy = np.linspace(0, in_h - 1, out_h)
    fx = np.linspace(0, in_w - 1, out_w)
    y0 = np.floor(fy).astype(int)
    x0 = np.floor(fx).astype(int)
    y1 = np.minimum(y0 + 1, in_h - 1)
    x1 = np.minimum(x0 + 1, in_w - 1)

    weight_y = (fy - y0)[:, None]
    weight_x = (fx - x0)[None, :]
    if img.ndim == 3:  # keep the channel axis broadcastable
        weight_y = weight_y[..., None]
        weight_x = weight_x[..., None]

    # the four neighbours of every output pixel
    top_left = img[y0][:, x0].astype(np.float32)
    top_right = img[y0][:, x1].astype(np.float32)
    bot_left = img[y1][:, x0].astype(np.float32)
    bot_right = img[y1][:, x1].astype(np.float32)

    top = top_left + (top_right - top_left) * weight_x
    bot = bot_left + (bot_right - bot_left) * weight_x
    blended = top + (bot - top) * weight_y
    return np.clip(blended, 0, 255).astype(np.uint8)


def resize_yuyv(yuyv, width, height, out_w, out_h):
    """Resize a packed-YUYV frame to (out_w, out_h).

    Unpacks to Y/Cb/Cr first so the bilinear filter never mixes the interleaved
    Cb/Cr channel, then repacks.
    """
    if (width, height) == (out_w, out_h):
        return yuyv

    packed = np.frombuffer(yuyv, np.uint8).reshape(height, width, 2)
    luma = resize(packed[:, :, 0], out_w, out_h)
    cb = resize(packed[:, 0::2, 1], out_w // 2, out_h)  # chroma is W/2 wide in 4:2:2
    cr = resize(packed[:, 1::2, 1], out_w // 2, out_h)

    out = np.empty((out_h, out_w, 2), np.uint8)
    out[:, :, 0] = luma
    out[:, 0::2, 1] = cb
    out[:, 1::2, 1] = cr
    return out.tobytes()


# ===========================================================================
# 3. Baseline JPEG decoder -> packed YUYV
#
# Pure Python + numpy; no PIL, no libav. Covers the JFIF subset MJPEG-in-MP4
# produces: 8-bit, Huffman, one scan, 1 or 3 components, any 4:4:4/4:2:2/4:2:0
# sampling, restart intervals. No progressive or arithmetic JPEG.
# ===========================================================================
# fmt: off
ZIGZAG = (
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63)
# fmt: on

# JPEG markers
MARKER_SOF0 = 0xC0  # baseline sequential
MARKER_SOF1 = 0xC1  # extended sequential
MARKER_DHT = 0xC4  # define Huffman table
MARKER_SOI = 0xD8  # start of image
MARKER_EOI = 0xD9  # end of image
MARKER_SOS = 0xDA  # start of scan
MARKER_DQT = 0xDB  # define quantization table
MARKER_DRI = 0xDD  # define restart interval
MARKER_TEM = 0x01  # standalone, no length
MARKER_RST0 = 0xD0  # RST0..RST7 are standalone too
MARKER_RST7 = 0xD7
# SOF variants we cannot decode (progressive, lossless, arithmetic)
MARKERS_UNSUPPORTED_SOF = (0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB)

HUFF_MAX_CODE_LEN = 16
BLOCK_COEFFS = 64  # 8x8
LEVEL_SHIFT = 128.0  # JPEG stores samples centred on zero
ZRL = 15  # run-length code meaning "16 zero coefficients"


def build_huffman(counts, symbols):
    """Expand a canonical JPEG Huffman table into {(code_len, code): symbol}."""
    table = {}
    code = 0
    next_symbol = 0
    for length in range(1, HUFF_MAX_CODE_LEN + 1):
        for _ in range(counts[length - 1]):
            table[(length, code)] = symbols[next_symbol]
            next_symbol += 1
            code += 1
        code <<= 1
    return table


def extend(value, size):
    """JPEG sign-extend an `size`-bit magnitude into a signed coefficient."""
    if size and value < (1 << (size - 1)):
        return value - (1 << size) + 1
    return value


class BitReader:
    """Entropy-coded bit reader: unstuffs 0xFF00, stops at markers, handles restarts."""

    def __init__(self, data, pos):
        self.data = data
        self.pos = pos
        self.buf = 0
        self.cnt = 0

    def _byte(self):
        """Next entropy-coded byte, or None at a marker / end of data."""
        data = self.data
        if self.pos >= len(data):
            return None

        b = data[self.pos]
        self.pos += 1
        if b != 0xFF:
            return b

        # 0xFF is either a stuffed literal (0xFF00) or the start of a marker
        following = data[self.pos] if self.pos < len(data) else 0
        if following == 0x00:
            self.pos += 1
            return 0xFF
        self.pos -= 1  # leave the marker for the caller
        return None

    def bit(self):
        if self.cnt == 0:
            b = self._byte()
            if b is None:
                return 0  # pad past end of scan with zeros
            self.buf = b
            self.cnt = 8
        self.cnt -= 1
        return (self.buf >> self.cnt) & 1

    def get(self, n):
        value = 0
        for _ in range(n):
            value = (value << 1) | self.bit()
        return value

    def huff(self, table):
        """Read one Huffman-coded symbol, growing the code one bit at a time."""
        code = 0
        for length in range(1, HUFF_MAX_CODE_LEN + 1):
            code = (code << 1) | self.bit()
            symbol = table.get((length, code))
            if symbol is not None:
                return symbol
        return 0

    def restart(self):
        """Skip to just past the next RSTn marker and realign to a byte boundary."""
        self.cnt = 0  # drop the partial byte
        data = self.data
        while self.pos + 1 < len(data):
            is_marker = data[self.pos] == 0xFF
            if is_marker and MARKER_RST0 <= data[self.pos + 1] <= MARKER_RST7:
                self.pos += 2
                return
            self.pos += 1


class JpegDecoder:
    """Stateless baseline-JPEG decoder (one self-contained frame per call)."""

    def decode(self, data, is_keyframe=True):
        """Decode one JPEG image -> (packed YUYV bytes, width, height)."""
        quant = {}  # table id -> natural-order 8x8 (float)
        huff_dc = {}
        huff_ac = {}
        comps = []  # [id, H sampling, V sampling, quant id]
        width = 0
        height = 0
        restart_interval = 0

        pos = 2  # skip SOI (FFD8)
        end = len(data)
        while pos < end:
            if data[pos] != 0xFF:
                pos += 1
                continue

            marker = data[pos + 1]
            pos += 2
            if marker == MARKER_EOI:
                break
            if marker == MARKER_TEM or MARKER_RST0 <= marker <= MARKER_RST7:
                continue  # standalone markers carry no length

            seg_len = (data[pos] << 8) | data[pos + 1]
            seg = data[pos + 2 : pos + seg_len]
            seg_end = pos + seg_len

            if marker == MARKER_DQT:
                self._read_dqt(seg, quant)
            elif marker in (MARKER_SOF0, MARKER_SOF1):
                width, height = self._read_sof(seg, comps)
            elif marker in MARKERS_UNSUPPORTED_SOF:
                raise SystemExit(
                    "JPEG: only baseline/sequential is supported "
                    "(progressive/arithmetic not handled)"
                )
            elif marker == MARKER_DHT:
                self._read_dht(seg, huff_dc, huff_ac)
            elif marker == MARKER_DRI:
                restart_interval = (seg[0] << 8) | seg[1]
            elif marker == MARKER_SOS:
                scan = self._read_sos(seg, comps)
                return self._scan(
                    data,
                    seg_end,
                    width,
                    height,
                    comps,
                    quant,
                    huff_dc,
                    huff_ac,
                    scan,
                    restart_interval,
                )
            pos = seg_end

        raise SystemExit("JPEG: no scan found")

    @staticmethod
    def _read_dqt(seg, quant):
        """DQT: one or more quantization tables, stored in zig-zag order."""
        q = 0
        while q < len(seg):
            precision = seg[q] >> 4  # 0 = 8-bit entries, 1 = 16-bit
            table_id = seg[q] & 0xF
            q += 1

            zigzag_values = []
            for _ in range(BLOCK_COEFFS):
                if precision:
                    zigzag_values.append((seg[q] << 8) | seg[q + 1])
                    q += 2
                else:
                    zigzag_values.append(seg[q])
                    q += 1

            natural = np.zeros(BLOCK_COEFFS, np.float32)
            for i in range(BLOCK_COEFFS):
                natural[ZIGZAG[i]] = zigzag_values[i]
            quant[table_id] = natural.reshape(8, 8)

    @staticmethod
    def _read_sof(seg, comps):
        """SOF0/SOF1: frame size and the component sampling factors."""
        height = (seg[1] << 8) | seg[2]
        width = (seg[3] << 8) | seg[4]
        num_comps = seg[5]
        for c in range(num_comps):
            o = 6 + c * 3
            comp_id = seg[o]
            h_sampling = seg[o + 1] >> 4
            v_sampling = seg[o + 1] & 0xF
            quant_id = seg[o + 2]
            comps.append([comp_id, h_sampling, v_sampling, quant_id])
        return width, height

    @staticmethod
    def _read_dht(seg, huff_dc, huff_ac):
        """DHT: one or more Huffman tables, as 16 length-counts then the symbols."""
        q = 0
        while q < len(seg):
            table_class = seg[q] >> 4  # 0 = DC, 1 = AC
            table_id = seg[q] & 0xF
            q += 1

            counts = list(seg[q : q + HUFF_MAX_CODE_LEN])
            q += HUFF_MAX_CODE_LEN
            total = sum(counts)
            symbols = list(seg[q : q + total])
            q += total

            target = huff_ac if table_class else huff_dc
            target[table_id] = build_huffman(counts, symbols)

    @staticmethod
    def _read_sos(seg, comps):
        """SOS: map each scan component to its DC/AC table -> [(comp_index, dc, ac)]."""
        num_scan_comps = seg[0]
        scan = []
        for s in range(num_scan_comps):
            comp_selector = seg[1 + s * 2]
            tables = seg[2 + s * 2]
            comp_index = next(i for i, c in enumerate(comps) if c[0] == comp_selector)
            scan.append((comp_index, tables >> 4, tables & 0xF))
        return scan

    def _scan(
        self, data, pos, width, height, comps, quant, huff_dc, huff_ac, scan, restart_interval
    ):
        """Huffman-decode the entropy data, then dequantize/IDCT into YUYV."""
        h_max = max(c[1] for c in comps)
        v_max = max(c[2] for c in comps)
        mcus_x = (width + 8 * h_max - 1) // (8 * h_max)
        mcus_y = (height + 8 * v_max - 1) // (8 * v_max)

        # per-component coefficient block grids
        blocks_w = [mcus_x * c[1] for c in comps]
        blocks_h = [mcus_y * c[2] for c in comps]
        coef = [np.zeros((blocks_h[i] * blocks_w[i], 8, 8), np.float32) for i in range(len(comps))]

        reader = BitReader(data, pos)
        dc_pred = [0] * len(comps)
        mcu = 0
        for my in range(mcus_y):
            for mx in range(mcus_x):
                if restart_interval and mcu and mcu % restart_interval == 0:
                    reader.restart()
                    dc_pred = [0] * len(comps)

                for comp_index, dc_id, ac_id in scan:
                    h_sampling = comps[comp_index][1]
                    v_sampling = comps[comp_index][2]
                    dc_table = huff_dc[dc_id]
                    ac_table = huff_ac[ac_id]
                    for by in range(v_sampling):
                        for bx in range(h_sampling):
                            block = self._block(reader, dc_table, ac_table, comp_index, dc_pred)
                            col = mx * h_sampling + bx
                            row = my * v_sampling + by
                            coef[comp_index][row * blocks_w[comp_index] + col] = block
                mcu += 1

        planes = self._planes(comps, coef, quant, blocks_w, blocks_h, width, height, h_max, v_max)

        luma = planes[0]
        if len(planes) >= 3:
            cb = planes[1]
            cr = planes[2]
        else:  # greyscale: neutral chroma
            cb = np.full_like(luma, 128)
            cr = np.full_like(luma, 128)
        return pack_yuyv(luma, cb, cr), width, height

    @staticmethod
    def _planes(comps, coef, quant, blocks_w, blocks_h, width, height, h_max, v_max):
        """Dequantize + IDCT each component, then crop and upsample to full res."""
        planes = []
        for i, comp in enumerate(comps):
            blocks = coef[i] * quant[comp[3]]  # dequantize
            spatial = idct8x8(blocks) + LEVEL_SHIFT
            spatial = np.clip(spatial, 0, 255).astype(np.uint8)

            # (nblocks, 8, 8) -> one contiguous plane
            grid = spatial.reshape(blocks_h[i], blocks_w[i], 8, 8)
            plane = grid.transpose(0, 2, 1, 3).reshape(blocks_h[i] * 8, blocks_w[i] * 8)

            # crop to this component's true sample size, then upsample to full res
            comp_w = (width * comp[1] + h_max - 1) // h_max
            comp_h = (height * comp[2] + v_max - 1) // v_max
            plane = plane[:comp_h, :comp_w]
            if (comp[1], comp[2]) != (h_max, v_max):
                plane = np.repeat(plane, v_max // comp[2], axis=0)
                plane = np.repeat(plane, h_max // comp[1], axis=1)
            planes.append(plane[:height, :width])
        return planes

    @staticmethod
    def _block(reader, dc_table, ac_table, comp_index, dc_pred):
        """Decode one 8x8 block: a DC difference, then run-length coded AC coefficients."""
        coef = np.zeros(BLOCK_COEFFS, np.float32)

        # DC is coded as a difference from the previous block of this component
        size = reader.huff(dc_table)
        if size:
            dc_pred[comp_index] += extend(reader.get(size), size)
        coef[0] = dc_pred[comp_index]

        k = 1
        while k < BLOCK_COEFFS:
            run_size = reader.huff(ac_table)
            run = run_size >> 4
            size = run_size & 0xF
            if size == 0:
                if run == ZRL:
                    k += 16  # 16 zero coefficients
                    continue
                break  # end of block
            k += run
            if k >= BLOCK_COEFFS:
                break
            coef[ZIGZAG[k]] = extend(reader.get(size), size)
            k += 1
        return coef.reshape(8, 8)


# ===========================================================================
# 4. MP4 (ISO-BMFF) demuxer
#
# Parses the box tree, finds the video track, and exposes each coded frame as a
# (file-offset, size, keyframe) sample, plus the codec fourcc and resolution.
# EXAMPLE/app code: the UVC class only ever sees finished YUYV frames.
# ===========================================================================
# stsd visual-sample-entry fourccs we decode. 'mp4v' is a generic MPEG-4 wrapper;
# its real codec comes from the esds objectTypeIndication.
CODEC_MJPEG = ("jpeg", "mjpa", "mjpb")
CODEC_H263 = ("s263", "h263")
CODEC_H264 = ("avc1", "avc3")
# MPEG-4 objectTypeIndication values found inside an 'mp4v' esds
OTI = {0x20: "mpeg4", 0x21: "h264", 0x6C: "mjpeg"}

BOX_HEADER_LEN = 8  # size(4) + type(4)
BOX_HEADER_LEN_64 = 16  # ... plus a 64-bit largesize
VISUAL_SAMPLE_ENTRY_LEN = 78  # before any child boxes (esds, ...)

ES_DESCR_TAG = 0x03
DECODER_CONFIG_DESCR_TAG = 0x04


def u16(buf, off):
    return struct.unpack_from(">H", buf, off)[0]


def u32(buf, off):
    return struct.unpack_from(">I", buf, off)[0]


def u64(buf, off):
    return struct.unpack_from(">Q", buf, off)[0]


def iter_boxes(buf, off, end):
    """Yield (type, body_off, body_end) for each box in buf[off:end]."""
    while off + BOX_HEADER_LEN <= end:
        size = u32(buf, off)
        typ = buf[off + 4 : off + 8].decode("latin1", "replace")
        hdr = BOX_HEADER_LEN

        if size == 1:  # 64-bit largesize follows the type
            size = u64(buf, off + BOX_HEADER_LEN)
            hdr = BOX_HEADER_LEN_64
        elif size == 0:  # this box extends to the end
            size = end - off

        if size < hdr or off + size > end:
            break
        yield typ, off + hdr, off + size
        off += size


def find_box(buf, off, end, path):
    """Descend a box path like ['mdia', 'minf', 'stbl'] -> (body_off, body_end) or None."""
    want = path[0]
    for typ, body_off, body_end in iter_boxes(buf, off, end):
        if typ != want:
            continue
        if len(path) == 1:
            return body_off, body_end
        return find_box(buf, body_off, body_end, path[1:])
    return None


class Track:
    """One video track: codec, native size, nominal fps and the sample list.

    Each sample is (file offset, byte size, is_keyframe).
    """

    def __init__(self, codec, width, height, samples, fps):
        self.codec = codec
        self.width = width
        self.height = height
        self.samples = samples
        self.fps = fps


def sample_table(buf, stbl_off, stbl_end):
    """Combine stsz/stsc/stco(/co64)/stss into a flat [(offset, size, sync)]."""

    def box(name):
        return find_box(buf, stbl_off, stbl_end, [name])

    sizes = read_sample_sizes(buf, box("stsz"))
    chunks = read_chunk_offsets(buf, box("stco"), box("co64"))
    samples_per_chunk = read_samples_per_chunk(buf, box("stsc"), len(chunks))
    sync = read_sync_samples(buf, box("stss"))

    samples = []
    index = 0  # running 0-based sample index
    for chunk_index, chunk_off in enumerate(chunks):
        off = chunk_off
        for _ in range(samples_per_chunk[chunk_index]):
            if index >= len(sizes):
                break
            size = sizes[index]
            is_key = True if sync is None else (index + 1) in sync
            samples.append((off, size, is_key))
            off += size
            index += 1
    return samples


def read_sample_sizes(buf, stsz):
    """stsz -> a size for every sample (a single constant size is expanded)."""
    if not stsz:
        return []

    off = stsz[0]
    constant_size = u32(buf, off + 4)
    count = u32(buf, off + 8)
    if constant_size:
        return [constant_size] * count
    return [u32(buf, off + 12 + 4 * i) for i in range(count)]


def read_chunk_offsets(buf, stco, co64):
    """stco (32-bit) or co64 (64-bit) -> the file offset of each chunk."""
    if stco:
        off = stco[0]
        return [u32(buf, off + 8 + 4 * i) for i in range(u32(buf, off + 4))]
    if co64:
        off = co64[0]
        return [u64(buf, off + 8 + 8 * i) for i in range(u32(buf, off + 4))]
    return []


def read_samples_per_chunk(buf, stsc, num_chunks):
    """stsc -> how many samples each chunk holds, expanded over all chunks.

    The table is run-length coded: an entry applies from its first_chunk up to
    the chunk before the next entry's first_chunk.
    """
    if not stsc:
        return [0] * num_chunks

    off = stsc[0]
    entries = []
    for i in range(u32(buf, off + 4)):
        entry = off + 8 + 12 * i
        first_chunk = u32(buf, entry)  # 1-based
        per_chunk = u32(buf, entry + 4)
        entries.append((first_chunk, per_chunk))

    per_chunk_counts = [0] * num_chunks
    for i, (first_chunk, per_chunk) in enumerate(entries):
        if i + 1 < len(entries):
            last_chunk = entries[i + 1][0] - 1
        else:
            last_chunk = num_chunks
        for chunk in range(first_chunk, last_chunk + 1):
            if 1 <= chunk <= num_chunks:
                per_chunk_counts[chunk - 1] = per_chunk
    return per_chunk_counts


def read_sync_samples(buf, stss):
    """stss -> the set of 1-based keyframe indices; None means every sample is one."""
    if not stss:
        return None
    off = stss[0]
    return {u32(buf, off + 8 + 4 * i) for i in range(u32(buf, off + 4))}


def visual_entry(buf, stbl_off, stbl_end):
    """Parse the first stsd visual sample entry -> (codec, width, height).

    `codec` is normalized to one of mjpeg/mpeg4/h263/h264, or 'unknown:xxxx'.
    """
    stsd = find_box(buf, stbl_off, stbl_end, ["stsd"])
    if not stsd:
        return None

    # stsd body: version/flags(4) + entry_count(4), then the entries
    entry_off = stsd[0] + 8
    entry_size = u32(buf, entry_off)
    fourcc = buf[entry_off + 4 : entry_off + 8].decode("latin1", "replace")

    body = entry_off + 8  # SampleEntry: reserved(6) + dri(2)
    width = u16(buf, body + 24)
    height = u16(buf, body + 26)

    if fourcc in CODEC_MJPEG:
        codec = "mjpeg"
    elif fourcc in CODEC_H264:
        codec = "h264"
    elif fourcc in CODEC_H263:
        codec = "h263"
    else:
        codec = "unknown:" + fourcc

    # 'mp4v' is a generic wrapper: the real codec is the esds objectTypeIndication
    # (a child box after the 78-byte VisualSampleEntry) -> disambiguates MJPEG.
    children_off = body + VISUAL_SAMPLE_ENTRY_LEN
    for typ, child_off, child_end in iter_boxes(buf, children_off, entry_off + entry_size):
        if typ == "esds":
            codec = OTI.get(esds_oti(buf, child_off, child_end), codec)
    return codec, width, height


def esds_oti(buf, off, end):
    """Parse an esds box -> its objectTypeIndication (0 if not found)."""

    def descriptor_length(pos):
        """The expandable size field: 7 bits per byte, high bit continues."""
        value = 0
        for _ in range(4):
            b = buf[pos]
            value = (value << 7) | (b & 0x7F)
            pos += 1
            if not (b & 0x80):
                break
        return value, pos

    pos = off + 4  # skip version/flags
    if pos >= end or buf[pos] != ES_DESCR_TAG:
        return 0
    pos += 1
    _, pos = descriptor_length(pos)
    pos += 3  # ES_ID(2) + flags(1)

    if pos >= end or buf[pos] != DECODER_CONFIG_DESCR_TAG:
        return 0
    pos += 1
    _, pos = descriptor_length(pos)
    return buf[pos]  # objectTypeIndication


def demux(path):
    """Parse an MP4 file and return its first decodable video Track, or raise."""
    with open(path, "rb") as f:
        return demux_bytes(f.read())


def demux_bytes(buf):
    """Parse MP4 bytes and return the first decodable video Track, or raise."""
    moov = find_box(buf, 0, len(buf), ["moov"])
    if not moov:
        raise SystemExit("not an MP4 (no moov box)")

    for typ, trak_off, trak_end in iter_boxes(buf, *moov):
        if typ != "trak":
            continue

        # only video tracks; the handler box says which kind this is
        hdlr = find_box(buf, trak_off, trak_end, ["mdia", "hdlr"])
        if not hdlr or buf[hdlr[0] + 8 : hdlr[0] + 12] != b"vide":
            continue

        stbl = find_box(buf, trak_off, trak_end, ["mdia", "minf", "stbl"])
        if not stbl:
            continue
        entry = visual_entry(buf, *stbl)
        if not entry:
            continue

        codec, width, height = entry
        samples = sample_table(buf, *stbl)
        mdhd = find_box(buf, trak_off, trak_end, ["mdia", "mdhd"])
        fps = track_fps(buf, stbl, mdhd)
        return Track(codec, width, height, samples, fps)

    raise SystemExit("no video track found in MP4")


def track_fps(buf, stbl, mdhd):
    """Nominal frame rate = media timescale / the first run's sample delta."""
    if not mdhd:
        return 0.0

    # mdhd is a full box; version 1 widens the 64-bit times before the timescale
    mdhd_off = mdhd[0]
    timescale_off = 20 if buf[mdhd_off] == 1 else 12
    timescale = u32(buf, mdhd_off + timescale_off)
    if not timescale:
        return 0.0

    stts = find_box(buf, stbl[0], stbl[1], ["stts"])
    if not stts:
        return 0.0
    stts_off = stts[0]
    if not u32(buf, stts_off + 4):  # no entries
        return 0.0

    delta = u32(buf, stts_off + 12)  # first run's sample_delta
    if not delta:
        return 0.0
    return timescale / delta


class Mp4Source:
    """Decode an MP4 video track to packed-YUYV frames at (out_w, out_h), looping forever.

    One frame per next(). Dispatches to the hand-rolled decoder above.
    """

    def __init__(self, path, out_w=None, out_h=None):
        with open(path, "rb") as f:
            self.buf = f.read()
        self.track = demux_bytes(self.buf)
        self.out_w = out_w or self.track.width
        self.out_h = out_h or self.track.height
        self.decoder = self._make_decoder(self.track.codec)
        self._index = 0

    @staticmethod
    def _make_decoder(codec):
        """Pick a frame decoder for the track's codec - Motion-JPEG is all we have."""
        if codec != "mjpeg":
            raise SystemExit(
                f"MP4 video codec {codec!r} is not supported - this hand-rolled decoder "
                "handles Motion-JPEG only. Transcode to an MJPEG MP4, or use raw YUYV "
                "(--source file) / synthetic frames."
            )
        if np is None:
            raise SystemExit(
                "--source mp4 needs numpy (pip install numpy), "
                "or use --source file / synthetic frames"
            )
        return JpegDecoder()

    def next(self) -> bytes:
        """Decode the next frame, wrapping back to the start of the clip at the end."""
        samples = self.track.samples
        if not samples:
            raise SystemExit("MP4 video track has no frames")

        if self._index >= len(samples):
            self._index = 0
            if hasattr(self.decoder, "reset"):  # restart the GOP for P-frame codecs
                self.decoder.reset()

        off, size, is_key = samples[self._index]
        self._index += 1

        yuyv, width, height = self.decoder.decode(self.buf[off : off + size], is_key)
        if (width, height) != (self.out_w, self.out_h):
            yuyv = resize_yuyv(yuyv, width, height, self.out_w, self.out_h)
        return yuyv


# ===========================================================================
# frame source + command line
# ===========================================================================
def make_source(args, width, height):
    """Build the frame source, mirroring the C example's app_next_frame().

    Supplies YUYV frames from a raw --file, a hand-decoded MP4, or synthesized
    color bars; and, for MJPEG, JPEG-encodes the frame here in the example.
    Returns None for plain synthetic YUYV, so the class draws the bars itself.
    """
    frame_bytes = width * height * YUYV_BYTES_PER_PIXEL

    clip = None
    raw = None
    if args.source == "mp4":
        clip = Mp4Source(args.file, width, height)
    elif args.file:
        raw = open(args.file, "rb")

    def next_raw_frame():
        """One YUYV frame from the raw file, rewinding at EOF."""
        data = raw.read(frame_bytes)
        if len(data) < frame_bytes:
            raw.seek(0)
            data = raw.read(frame_bytes)
        if len(data) != frame_bytes:
            return None  # file smaller than a single frame
        return data

    def source(_fn, fmt, index):
        wants_mjpeg = fmt == "M"

        if clip:
            yuyv = clip.next()
        elif raw:
            yuyv = next_raw_frame()
            if yuyv is None:
                return None
        elif wants_mjpeg:
            yuyv = uvc.color_bars_yuyv(width, height, index)
        else:
            return None  # synthetic YUYV -> let the class draw

        if wants_mjpeg:
            return encode_yuyv(yuyv, width, height)
        return yuyv

    return source


def parse_int(s):
    """argparse type: an integer in any base, so --vid 0x1209 and --vid 4617 both work."""
    try:
        return int(s, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad number {s!r}") from None


def resolve_geometry(args):
    """Pick width/height/fps: an MP4 supplies its own native values as defaults."""
    if args.source != "mp4":
        width = args.width or DEFAULT_WIDTH
        height = args.height or DEFAULT_HEIGHT
        fps = args.fps or DEFAULT_FPS
        return width, height, fps

    track = demux(args.file)
    width = args.width or track.width
    height = args.height or track.height
    fps = args.fps or round(track.fps) or DEFAULT_FPS
    return width, height, fps


def main():
    ap = argparse.ArgumentParser(description="virtual USB webcam (UVC) over USB/IP")
    ap.add_argument(
        "--width", type=int, default=None, help="default 320, or the MP4's native width"
    )
    ap.add_argument(
        "--height", type=int, default=None, help="default 240, or the MP4's native height"
    )
    ap.add_argument("--fps", type=int, default=None, help="default 15, or the MP4's native rate")
    ap.add_argument("--format", choices=("yuyv", "mjpeg", "both"), default="yuyv")
    ap.add_argument("--source", choices=("synthetic", "file", "mp4"), default="synthetic")
    ap.add_argument("--file", help="raw YUY2 frames (--source file) or an MP4 video (--source mp4)")
    ap.add_argument("--vid", type=parse_int, default=0x1209)
    ap.add_argument("--pid", type=parse_int, default=0x000E)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=3240)
    ap.add_argument(
        "--high-speed", action="store_true", help="Report a high-speed device (default: full speed)"
    )
    ap.add_argument("--verbose", action="store_true", help="log every event (default: grouped)")
    args = ap.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(name)s] %(message)s", stream=sys.stderr)

    # a --file with a video extension implies --source mp4 (unless overridden)
    if args.file and args.source == "synthetic":
        if args.file.lower().endswith(VIDEO_EXTS):
            args.source = "mp4"
    if args.source in ("file", "mp4") and not args.file:
        ap.error(f"--source {args.source} needs --file")

    width, height, fps = resolve_geometry(args)

    if args.format == "both":
        formats = ("yuyv", "mjpeg")
    else:
        formats = (args.format,)

    # the class draws its own color bars unless we have a file, or owe it JPEGs
    source = None
    if args.file or "mjpeg" in formats:
        source = make_source(args, width, height)

    sink = usbip.GroupedLog("uvc", verbose=args.verbose)
    dev = USBDevice(
        args.vid, args.pid, product="USBIP Camera", manufacturer="USB over IP", serial="000e"
    )
    speed = usbip.SPEED_HIGH if args.high_speed else usbip.SPEED_FULL
    dev.set_speed(speed)
    camera = UVC(width=width, height=height, fps=fps, formats=formats, source=source, on_event=sink)
    dev.add(camera)

    src_desc = args.source
    if args.file:
        src_desc += f" {args.file}"
    speed = "high speed" if args.high_speed else "full speed"
    log.info(
        f"{args.format} {width}x{height} @ {fps}fps, {src_desc} source  "
        f"({args.vid:04x}:{args.pid:04x}, {speed}, on {args.host}:{args.port})"
    )
    log.info("attach: sudo usbip attach -r 127.0.0.1 -b 1-1")

    transport = usbip.USBIP(args.host, args.port)
    dev.plug(via=transport)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
