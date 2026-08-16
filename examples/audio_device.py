#!/usr/bin/env python3
"""
Copyright (C) 2026 Jabez Winston

SPDX-License-Identifier: MIT

Date : 05-June-2026

Virtual USB Audio (UAC1) device over USB/IP - speaker + microphone.

  python3 audio_device.py                     # stereo speaker + mono mic (440 Hz)
  python3 audio_device.py --out played.wav    # record what the host plays
  python3 audio_device.py --mic-in song.wav   # stream a WAV as the microphone

The microphone streams a built-in 440 Hz tone; the speaker is a sink that meters the
level (and optionally writes a .wav). Volume/mute are real Feature Unit controls.

Attach it locally, then use ALSA:
  attach it with a USB/IP client
  (Linux: sudo modprobe vhci-hcd ; sudo usbip attach -r 127.0.0.1 -b 1-1)
  then play to / record from it with your OS's sound tools
  (Linux: aplay -D plughw:CARD song.wav  ->  speaker sink, and --out file
          arecord -D plughw:CARD -d 3 mic.wav  <-  microphone tone)
  alsamixer                            # move the volume / mute controls
"""

from __future__ import annotations

import argparse
import logging
import struct
import sys
import time
import wave

import usbip
from usbip.classes.device import UAC
from usbip.device import USBDevice

log = logging.getLogger("uac")

# what the microphone claims to be; a --mic-in WAV must match this exactly
MIC_CHANNELS = 1
MIC_RATE = 48000
MIC_SAMPLE_BYTES = 2  # 16-bit

# ---- WAV writer -----------------------------------------------------------
# Tiny streaming RIFF/PCM writer (mirrors the C example's wav.c). The header goes out
# with placeholder sizes and is patched on close, since the total length is not known
# until streaming stops.

WAV_HEADER_TAIL = 36  # RIFF size field = 36 + data bytes
WAV_RIFF_SIZE_OFFSET = 4
WAV_DATA_SIZE_OFFSET = 40


class Wav:
    """A .wav file written as the host plays audio to the virtual speaker."""

    def __init__(self, path, rate=48000, channels=2, bits=16):
        self.fp = open(path, "wb")
        self.data_bytes = 0

        sample_bytes = bits // 8
        byte_rate = rate * channels * sample_bytes
        block_align = channels * sample_bytes

        self.fp.write(b"RIFF" + struct.pack("<I", WAV_HEADER_TAIL) + b"WAVE")
        self.fp.write(
            b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, byte_rate, block_align, bits)
        )
        self.fp.write(b"data" + struct.pack("<I", 0))

    def write(self, data):
        self.fp.write(data)
        self.data_bytes += len(data)

    def close(self):
        """Patch the two placeholder sizes, then close."""
        self.fp.seek(WAV_RIFF_SIZE_OFFSET)
        self.fp.write(struct.pack("<I", WAV_HEADER_TAIL + self.data_bytes))
        self.fp.seek(WAV_DATA_SIZE_OFFSET)
        self.fp.write(struct.pack("<I", self.data_bytes))
        self.fp.close()


# ---- audio sources and sinks ----------------------------------------------
def parse_int(s):
    """argparse type: an integer in any base, so --vid 0x1209 and --vid 4617 both work."""
    try:
        return int(s, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad number {s!r}") from None


def open_mic_wav(path, ap):
    """Open a --mic-in WAV, insisting on the exact format the microphone advertises."""
    mic_wav = wave.open(path, "rb")
    actual = (mic_wav.getnchannels(), mic_wav.getframerate(), mic_wav.getsampwidth())
    expected = (MIC_CHANNELS, MIC_RATE, MIC_SAMPLE_BYTES)
    if actual != expected:
        ap.error(
            "--mic-in needs a mono 48 kHz 16-bit WAV "
            "(convert with: ffmpeg -i song.mp3 -ac 1 -ar 48000 -c:a pcm_s16le mic.wav)"
        )
    return mic_wav


def make_mic_source(mic_wav):
    """Build the frame source the class pulls from: stream the WAV, looping at EOF."""

    def mic_source(_fn, frames, _index):
        data = mic_wav.readframes(frames)
        if len(data) < frames * MIC_SAMPLE_BYTES:  # hit EOF - wrap and top up
            mic_wav.rewind()
            short_by = frames - len(data) // MIC_SAMPLE_BYTES
            data += mic_wav.readframes(short_by)
        return data

    return mic_source


def make_speaker_sink(wav):
    """Build the sink the class pushes to: write everything the host plays into `wav`."""

    def speaker_sink(_fn, data):
        wav.write(data)

    return speaker_sink


def main():
    ap = argparse.ArgumentParser(description="virtual USB Audio (UAC1) over USB/IP")
    ap.add_argument("--out", help="record what the host plays to the speaker, as WAV")
    ap.add_argument(
        "--mic-in",
        dest="mic_in",
        help="stream a mono 48 kHz 16-bit WAV as the microphone (looping)",
    )
    ap.add_argument("--vid", type=parse_int, default=0x1209)
    ap.add_argument("--pid", type=parse_int, default=0x0013)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=3240)
    ap.add_argument(
        "--no-pace",
        action="store_true",
        help="free-run iso (default: pace to real time so audio plays at normal speed)",
    )
    ap.add_argument(
        "--high-speed", action="store_true", help="Report a high-speed device (default: full speed)"
    )
    ap.add_argument("--verbose", action="store_true", help="log every event (default: grouped)")
    args = ap.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(name)s] %(message)s", stream=sys.stderr)

    # the speaker side: metered by the class, and optionally recorded to a WAV
    wav = None
    spk_sink = None
    if args.out:
        wav = Wav(args.out, rate=48000, channels=2, bits=16)
        spk_sink = make_speaker_sink(wav)

    # the microphone side: the class's built-in 440 Hz tone unless --mic-in overrides it
    mic_wav = None
    mic_source = None
    if args.mic_in:
        mic_wav = open_mic_wav(args.mic_in, ap)
        mic_source = make_mic_source(mic_wav)

    sink = usbip.GroupedLog("uac", verbose=args.verbose)

    dev = USBDevice(
        args.vid, args.pid, product="USBIP Audio", manufacturer="USB over IP", serial="0013"
    )
    speed = usbip.SPEED_HIGH if args.high_speed else usbip.SPEED_FULL
    dev.set_speed(speed)
    dev.add(UAC(mic_source=mic_source, spk_sink=spk_sink, on_event=sink))
    dev.set_iso_pacing(not args.no_pace)  # play at real time unless --no-pace

    speed = "high speed" if args.high_speed else "full speed"
    log.info(
        f"speaker(2ch) + mic(1ch) 48000/16  ({args.vid:04x}:{args.pid:04x}, {speed}, on {args.host}:{args.port})"
    )

    if wav:
        log.info(f"recording speaker -> {args.out}")
    if args.mic_in:
        log.info(f"microphone streams {args.mic_in} (looping)")
    log.info("attach: sudo usbip attach -r 127.0.0.1 -b 1-1")

    transport = usbip.USBIP(args.host, args.port)
    dev.plug(via=transport)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        dev.unplug()
        sink.flush()
        if mic_wav:
            mic_wav.close()
        if wav:
            wav.close()
            log.info(f"wrote {args.out}")


if __name__ == "__main__":
    main()
