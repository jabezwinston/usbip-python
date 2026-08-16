# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 05-June-2026
"""
USB Audio Class 1.0 (UAC1) - device class: speaker + microphone.

Mirrors the C classes/device/uac.c. Presents an AudioControl interface (a
USB->Speaker playback chain and a Microphone->USB capture chain, each with a
master mute+volume Feature Unit) plus two AudioStreaming interfaces: a stereo
speaker over an isochronous OUT endpoint and a mono microphone over an
isochronous IN endpoint, both 48 kHz / 16-bit PCM. The microphone streams a
built-in 440 Hz synthetic tone (or app-supplied frames); the speaker is a sink
that meters the level and hands the PCM to the app.

Descriptor layout follows the UAC1 spec and the kernel UAC1 gadget
(drivers/usb/gadget/function/f_uac1.c). Use `dev.add(UAC(...))`.
"""

from __future__ import annotations

import math
import struct

from ... import core
from ...core import Stall
from ...device import In, Out
from ...function import Function, Interface

# class / subclass + descriptor types (Audio 1.0)
CC_AUDIO, SC_CONTROL, SC_STREAMING = 0x01, 0x01, 0x02
CS_INTERFACE, CS_ENDPOINT = 0x24, 0x25
# AudioControl interface descriptor subtypes
AC_HEADER, AC_INPUT_TERMINAL, AC_OUTPUT_TERMINAL, AC_FEATURE_UNIT = 0x01, 0x02, 0x03, 0x06
# AudioStreaming interface / endpoint descriptor subtypes
AS_GENERAL, AS_FORMAT_TYPE, EP_GENERAL = 0x01, 0x02, 0x01
# terminal types
TT_USB_STREAMING, TT_MICROPHONE, TT_SPEAKER = 0x0101, 0x0201, 0x0301
# format
FORMAT_TYPE_I, FORMAT_PCM = 0x01, 0x0001
# class requests
SET_CUR, GET_CUR, GET_MIN, GET_MAX, GET_RES = 0x01, 0x81, 0x82, 0x83, 0x84
# Feature Unit control selectors
FU_MUTE, FU_VOLUME = 0x01, 0x02

# entity IDs
IT_PLAY, FU_PLAY, OT_SPK = 1, 2, 3  # USB-streaming -> FU -> Speaker
IT_MIC, FU_CAP, OT_USB = 4, 5, 6  # Microphone -> FU -> USB-streaming

RATE, SPK_CH, MIC_CH = 48000, 2, 1
EP_SPK, EP_MIC = 0x01, 0x82  # iso OUT (playback), iso IN (capture)
SPK_MPS = RATE // 1000 * SPK_CH * 2  # 192 (full speed: one 1 ms frame of PCM)
MIC_MPS = RATE // 1000 * MIC_CH * 2  # 96


def _iso_mps(channels, speed):
    """Bytes of 48 kHz/16-bit PCM in one iso service interval: a 1 ms frame at full speed, a
    125 µs microframe (1/8 as much) at high speed. The host polls 8× more often at HS, so the
    overall 48 kHz rate is unchanged; bInterval stays 1 and is reinterpreted for the speed."""
    frames_per_sec = 8000 if speed >= core.SPEED_HIGH else 1000
    return RATE // frames_per_sec * channels * 2


# volume controls (1/256 dB, signed)
VOL_CUR, VOL_MIN, VOL_MAX, VOL_RES = -2560, -16128, 0, 256  # -10 / -63 / 0 / 1 dB

# 440 Hz tone: 32-bit phase accumulator, top 8 bits index this sine table.
# PHASE_INC = round(440 / 48000 * 2**32). Pure integer -> byte-identical to C.
PHASE_INC = 39370534


def _sine_table(count=256, amplitude=16000):
    """One cycle of a sine wave as `n` signed 16-bit samples, peak `amplitude`.

    Generated rather than tabulated, but the result must stay byte-identical to the
    C twin's `SINE256[]` literal in the C library's src/classes/device/uac.c - the
    UAC cross-language test compares the two microphone streams sample for sample.
    That is safe because no sample lands on a .5 rounding boundary: the closest,
    i=54, is 15520.500051, which clears .5 by roughly 1e7 double ULPs - vastly more
    than any libm's sin() error - so every platform rounds all 256 values identically.
    """
    return tuple(round(amplitude * math.sin(2 * math.pi * i / count)) for i in range(count))


_SINE256 = _sine_table()


def tone(frames, index) -> bytes:
    """`frames` mono 16-bit-LE samples of a 440 Hz sine (48 kHz) starting at sample
    `index`. Pure integer, so it is byte-identical to the C uac_tone()."""
    out = bytearray(frames * 2)
    for k in range(frames):
        phase = ((index + k) * PHASE_INC) & 0xFFFFFFFF
        struct.pack_into("<h", out, k * 2, _SINE256[(phase >> 24) & 0xFF])
    return bytes(out)


def _format_block(channels, terminal_link):
    """AS-general + Type I format descriptors for one stream (48 kHz / 16-bit)."""
    general = struct.pack("<BBBBBH", 7, CS_INTERFACE, AS_GENERAL, terminal_link, 1, FORMAT_PCM)
    fmt = struct.pack(
        "<BBBBBBBB", 11, CS_INTERFACE, AS_FORMAT_TYPE, FORMAT_TYPE_I, channels, 2, 16, 1
    ) + RATE.to_bytes(3, "little")
    return general + fmt


def _iso_ep(addr, attributes, mps):
    """The 9-byte UAC iso data endpoint descriptor (standard 7 + bRefresh +
    bSynchAddress) followed by the class-specific iso endpoint descriptor."""
    ep = struct.pack("<BBBBHBBB", 9, core.DT_ENDPOINT, addr, attributes, mps, 1, 0, 0)
    cs = struct.pack("<BBBBBH", 7, CS_ENDPOINT, EP_GENERAL, 0, 0, 0)
    return ep + cs


def _streaming_block(count, channels, terminal_link, ep_addr, attributes, mps) -> bytes:
    """One AudioStreaming interface: idle alt 0, then streaming alt 1 with its
    class-specific block and iso data endpoint. Speaker and microphone differ
    only in the arguments."""
    alts = b"".join(
        core.InterfaceDescriptor(
            bInterfaceNumber=count,
            bAlternateSetting=alt,
            bNumEndpoints=alt,  # alt 0 idles with no endpoint, alt 1 streams on one
            bInterfaceClass=CC_AUDIO,
            bInterfaceSubClass=SC_STREAMING,
            bInterfaceProtocol=0,
        ).pack()
        for alt in (0, 1)
    )
    return alts + _format_block(channels, terminal_link) + _iso_ep(ep_addr, attributes, mps)


def _stream_edge(iface, alt):
    """SET_INTERFACE against a streaming interface: returns "on"/"off" when the
    alt setting crosses the streaming edge, None when nothing changes."""
    if alt == 1 and not iface.streaming:
        iface.streaming = True
        return "on"
    if alt == 0 and iface.streaming:
        iface.streaming = False
        return "off"
    return None


class AudioControl(Interface):
    """AudioControl interface (interface 0): the two terminal chains + Feature Units.
    Also owns the shared mute/volume state and answers Feature Unit controls."""

    bInterfaceClass = CC_AUDIO
    bInterfaceSubClass = SC_CONTROL

    def __init__(self, on_event=None):
        super().__init__()
        self.on_event = on_event or (lambda text: None)
        self.mute = {FU_PLAY: False, FU_CAP: False}
        self.volume = {FU_PLAY: VOL_CUR, FU_CAP: VOL_CUR}
        self.speaker = self.microphone = None  # wired by UAC.__init__

    def descriptor_block(self) -> bytes:
        ac = self.interface_number
        spk, mic = ac + 1, ac + 2
        ac_total = 10 + 12 + 10 + 9 + 12 + 9 + 9  # header + 6 terminals/units = 71
        intf = core.InterfaceDescriptor(
            bInterfaceNumber=ac,
            bAlternateSetting=0,
            bNumEndpoints=0,
            bInterfaceClass=CC_AUDIO,
            bInterfaceSubClass=SC_CONTROL,
            bInterfaceProtocol=0,
        ).pack()
        header = struct.pack(
            "<BBBHHBBB", 10, CS_INTERFACE, AC_HEADER, 0x0100, ac_total, 2, spk, mic
        )
        # playback: Input Terminal (USB streaming, stereo) -> Feature Unit -> Speaker
        it_play = struct.pack(
            "<BBBBHBBHBB",
            12,
            CS_INTERFACE,
            AC_INPUT_TERMINAL,
            IT_PLAY,
            TT_USB_STREAMING,
            0,
            SPK_CH,
            0x0003,
            0,
            0,
        )
        fu_play = struct.pack(
            "<BBBBBBBBBB",
            10,
            CS_INTERFACE,
            AC_FEATURE_UNIT,
            FU_PLAY,
            IT_PLAY,
            1,
            0x03,
            0x00,
            0x00,
            0,
        )
        ot_spk = struct.pack(
            "<BBBBHBBB", 9, CS_INTERFACE, AC_OUTPUT_TERMINAL, OT_SPK, TT_SPEAKER, 0, FU_PLAY, 0
        )
        # capture: Microphone (mono) -> Feature Unit -> Output Terminal (USB streaming)
        it_mic = struct.pack(
            "<BBBBHBBHBB",
            12,
            CS_INTERFACE,
            AC_INPUT_TERMINAL,
            IT_MIC,
            TT_MICROPHONE,
            0,
            MIC_CH,
            0x0000,
            0,
            0,
        )
        fu_cap = struct.pack(
            "<BBBBBBBBB", 9, CS_INTERFACE, AC_FEATURE_UNIT, FU_CAP, IT_MIC, 1, 0x03, 0x00, 0
        )
        ot_usb = struct.pack(
            "<BBBBHBBB", 9, CS_INTERFACE, AC_OUTPUT_TERMINAL, OT_USB, TT_USB_STREAMING, 0, FU_CAP, 0
        )
        return intf + header + it_play + fu_play + ot_spk + it_mic + fu_cap + ot_usb

    def on_control(self, setup, data=b""):
        cs, unit = setup.wValue >> 8, setup.wIndex >> 8
        if unit not in (FU_PLAY, FU_CAP):
            raise Stall
        if cs == FU_MUTE:
            if setup.bRequest == SET_CUR:
                if data:
                    self.mute[unit] = bool(data[0])
                return b""
            if setup.bRequest == GET_CUR:
                return bytes([1 if self.mute[unit] else 0])
            raise Stall
        if cs == FU_VOLUME:
            if setup.bRequest == SET_CUR:
                if len(data) >= 2:
                    self.volume[unit] = struct.unpack_from("<h", data)[0]
                return b""
            value = {
                GET_CUR: self.volume[unit],
                GET_MIN: VOL_MIN,
                GET_MAX: VOL_MAX,
                GET_RES: VOL_RES,
            }.get(setup.bRequest)
            if value is None:
                raise Stall
            return struct.pack("<h", value)
        raise Stall


class AudioStreamingOut(Interface):
    """AudioStreaming playback (interface 1): alt 0 (idle) + alt 1 (iso OUT speaker)."""

    bInterfaceClass = CC_AUDIO
    bInterfaceSubClass = SC_STREAMING
    audio_out = Out(EP_SPK, "iso", mps=SPK_MPS, interval=1)

    def __init__(self, ctl, sink=None, on_event=None):
        super().__init__()
        self.ctl = ctl
        self.sink = sink
        self.on_event = on_event or (lambda text: None)
        self.streaming = False
        self.total_bytes = 0
        self.peak = 0

    def adjust_for_speed(self, speed):
        self.audio_out.mps = _iso_mps(SPK_CH, speed)  # 192 FS / 24 HS (one (micro)frame of PCM)

    def descriptor_block(self) -> bytes:
        # iso / adaptive / data
        return _streaming_block(
            self.interface_number, SPK_CH, IT_PLAY, EP_SPK, 0x09, self.audio_out.mps
        )

    def set_alt(self, alt):
        edge = _stream_edge(self, alt)
        if edge == "on":
            self.total_bytes = self.peak = 0
            self.on_event("SPK ON  48000/16/2")
        elif edge == "off":
            self.on_event(f"SPK OFF ({self.total_bytes} bytes, peak {self.peak})")

    def on_iso(self, ep, packets):
        total = 0
        for pkt in packets:
            for i in range(0, len(pkt) - 1, 2):
                amp = abs(int.from_bytes(pkt[i : i + 2], "little", signed=True))
                if amp > self.peak:
                    self.peak = amp
            if pkt and self.sink and not self.ctl.mute[FU_PLAY]:
                self.sink(self, pkt)
            total += len(pkt)
        self.total_bytes += total
        if total:
            self.on_event(f"spk recv peak={self.peak} ({total}B)")


class AudioStreamingIn(Interface):
    """AudioStreaming capture (interface 2): alt 0 (idle) + alt 1 (iso IN microphone)."""

    bInterfaceClass = CC_AUDIO
    bInterfaceSubClass = SC_STREAMING
    audio_in = In(EP_MIC, "iso", mps=MIC_MPS, interval=1)

    def __init__(self, ctl, source=None, on_event=None):
        super().__init__()
        self.ctl = ctl
        self.source = source
        self.on_event = on_event or (lambda text: None)
        self.streaming = False
        self.samples = 0  # running mono-sample counter (tone phase)

    def adjust_for_speed(self, speed):
        self.audio_in.mps = _iso_mps(MIC_CH, speed)  # 96 FS / 12 HS (one (micro)frame of PCM)

    def descriptor_block(self) -> bytes:
        # iso / async / data
        return _streaming_block(
            self.interface_number, MIC_CH, OT_USB, EP_MIC, 0x05, self.audio_in.mps
        )

    def set_alt(self, alt):
        edge = _stream_edge(self, alt)
        if edge == "on":
            self.samples = 0
            self.on_event("MIC ON  48000/16/1")
        elif edge == "off":
            self.on_event(f"MIC OFF ({self.samples} samples)")

    def on_iso(self, ep, lengths):
        if not self.streaming:
            return []
        out = []
        produced = 0
        for cap in lengths:
            frames = cap // 2  # mono 16-bit
            if frames <= 0:
                out.append(b"")
                continue
            if self.ctl.mute[FU_CAP]:
                data = bytes(frames * 2)
            elif self.source:
                data = self.source(self, frames, self.samples) or tone(frames, self.samples)
            else:
                data = tone(frames, self.samples)
            out.append(data)
            self.samples += len(data) // 2
            produced += len(data)
        if produced:
            self.on_event(f"mic tone ({produced}B)")
        return out


class UAC(Function):
    """A UAC1 audio device: AudioControl + AudioStreaming (out/in) interfaces, bound by the
    AudioControl interface's collection - no IAD, no device triple (UAC1 predates the IAD).
    Mirrors the C uac function.

    ``dev.add(UAC(...))`` returns the AudioControl interface, with ``.speaker`` /
    ``.microphone`` attached for convenience."""

    def __init__(self, mic_source=None, spk_sink=None, on_event=None):
        on_event = on_event or (lambda text: None)
        self.control = AudioControl(on_event=on_event)
        self.speaker = AudioStreamingOut(self.control, sink=spk_sink, on_event=on_event)
        self.microphone = AudioStreamingIn(self.control, source=mic_source, on_event=on_event)
        self.control.speaker, self.control.microphone = self.speaker, self.microphone
        self.interfaces = (self.control, self.speaker, self.microphone)  # interfaces 0, 1, 2
        super().__init__()

    @property
    def primary(self):
        return self.control  # .speaker / .microphone hang off it for convenience
