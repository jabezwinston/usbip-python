# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
HID (class 0x03) - generic host driver. Auto-binds to HID devices by class code.

Discovers the HID interface, its interrupt IN/OUT endpoints, and the Report
descriptor length from the configuration descriptor, then exposes the full HID 1.11
request set: report-descriptor fetch, interrupt IN/OUT reports, GET/SET_REPORT over
EP0, and GET/SET_IDLE + GET/SET_PROTOCOL.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import DT_ENDPOINT, DT_INTERFACE, INTERRUPT, iter_descriptors
from ...host import Driver
from . import fetch_config

# class-specific requests (HID 1.11 Sec.7.2) - shared with the device side
from ..device.hid import (  # noqa: F401  (re-exported: this module's public names)
    GET_IDLE,
    GET_PROTOCOL,
    GET_REPORT,
    HID_DT_HID,
    REPORT_FEATURE,
    REPORT_INPUT,
    REPORT_OUTPUT,
    SET_IDLE,
    SET_PROTOCOL,
    SET_REPORT,
)


# ---- Report-descriptor parser (HID 1.11 Sec.6.2.2) --------------------------
@dataclass
class HIDField:
    """One Input/Output/Feature field group declared by a Report descriptor."""

    report_type: int  # 1 Input, 2 Output, 3 Feature
    report_id: int
    usage_page: int
    usages: tuple
    usage_min: int
    usage_max: int
    report_size: int  # bits per element
    report_count: int  # number of elements
    flags: int  # Main-item data (bit0 Constant, bit1 Variable, bit2 Relative…)
    logical_min: int
    logical_max: int

    @property
    def bits(self):
        return self.report_size * self.report_count

    @property
    def constant(self):
        return bool(self.flags & 0x01)


@dataclass
class HIDReport:
    """All fields belonging to one report, keyed by (report_type, report_id)."""

    report_type: int
    report_id: int
    fields: list

    @property
    def bits(self):
        return sum(field.bits for field in self.fields)

    @property
    def size(self):
        """Payload size in bytes (excludes any leading report-ID byte)."""
        return (self.bits + 7) // 8


def _signext(val, nbytes):
    if nbytes and val >= (1 << (nbytes * 8 - 1)):
        val -= 1 << (nbytes * 8)
    return val


def parse_report_descriptor(data):
    """Parse a HID Report descriptor (short items) and infer its reports.

    Returns ({(report_type, report_id): HIDReport}, numbered), where `numbered` is
    True when the descriptor declares Report IDs (each report then carries a leading
    1-byte ID on the wire). Tracks the global item state (usage page, report size/
    count/id, logical min/max, Push/Pop) and the local usages, emitting one HIDField
    per Input/Output/Feature Main item."""
    data = bytes(data)
    state = {
        "usage_page": 0,
        "logical_min": 0,
        "logical_max": 0,
        "report_size": 0,
        "report_count": 0,
        "report_id": 0,
    }
    stack, usages, umin, umax = [], [], None, None
    reports, numbered, i = {}, False, 0
    while i < len(data):
        prefix = data[i]
        i += 1
        if prefix == 0xFE:  # long item - skip its payload
            i += 2 + (data[i] if i < len(data) else 0)
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        val = int.from_bytes(data[i : i + size], "little")
        i += size
        tag = prefix & 0xFC  # bTag | bType (bSize masked off)
        if tag in (0x80, 0x90, 0xB0):  # Main: Input / Output / Feature
            rtype = {0x80: 1, 0x90: 2, 0xB0: 3}[tag]
            rep = reports.setdefault((rtype, state["report_id"]), HIDReport(rtype, state["report_id"], []))
            rep.fields.append(
                HIDField(
                    rtype,
                    state["report_id"],
                    state["usage_page"],
                    tuple(usages),
                    umin,
                    umax,
                    state["report_size"],
                    state["report_count"],
                    val,
                    state["logical_min"],
                    state["logical_max"],
                )
            )
            usages, umin, umax = [], None, None  # locals reset after each Main item
        elif tag in (0xA0, 0xC0):  # Collection / End Collection
            usages, umin, umax = [], None, None
        elif tag == 0x04:
            state["usage_page"] = val
        elif tag == 0x14:
            state["logical_min"] = _signext(val, size)
        elif tag == 0x24:
            state["logical_max"] = _signext(val, size)
        elif tag == 0x74:
            state["report_size"] = val
        elif tag == 0x94:
            state["report_count"] = val
        elif tag == 0x84:
            state["report_id"] = val
            numbered = True
        elif tag == 0xA4:
            stack.append(dict(state))  # Push
        elif tag == 0xB4 and stack:
            state.update(stack.pop())  # Pop
        elif tag == 0x08:
            usages.append(val)  # Usage
        elif tag == 0x18:
            umin = val  # Usage Minimum
        elif tag == 0x28:
            umax = val  # Usage Maximum
    return reports, numbered


class HIDDriver(Driver):
    matches = {"bInterfaceClass": 0x03}

    # --- discovery: parse the config descriptor once, cache the results ----
    def _discover(self):
        if getattr(self, "_discovered", None) is not None:
            return self._discovered
        info = {"iface": 0, "in_ep": 0x81, "out_ep": None, "in_mps": 64, "report_desc_len": 0}
        try:
            cfg = fetch_config(self.handle)
        except Exception:
            self._discovered = info
            return info
        in_hid = False
        for desc in iter_descriptors(cfg):
            if desc[1] == DT_INTERFACE and len(desc) >= 9:
                in_hid = desc[5] == 0x03
                if in_hid:
                    info["iface"] = desc[2]
            elif desc[1] == HID_DT_HID and in_hid and len(desc) >= 9:
                info["report_desc_len"] = desc[7] | (desc[8] << 8)
            elif desc[1] == DT_ENDPOINT and in_hid and len(desc) >= 7:
                addr, attr = desc[2], desc[3]
                if attr & 0x03 == INTERRUPT:
                    if addr & 0x80:
                        info["in_ep"] = addr
                        info["in_mps"] = desc[4] | (desc[5] << 8)
                    else:
                        info["out_ep"] = addr
        self._discovered = info
        return info

    @property
    def interface(self):
        return self._discover()["iface"]

    # --- descriptors -------------------------------------------------------
    def report_descriptor(self, length=None) -> bytes:
        info = self._discover()
        count = length or info["report_desc_len"] or 256
        return self.handle.control(0x81, 0x06, 0x2200, info["iface"], count)

    # --- report-descriptor inference ---------------------------------------
    def report_map(self):
        """Parse the device's Report descriptor into {(report_type, report_id):
        HIDReport}. Fetched and cached on first use."""
        if getattr(self, "_report_map", None) is None:
            self._report_map, self._numbered = parse_report_descriptor(self.report_descriptor())
        return self._report_map

    def input_report_size(self, report_id=0):
        """Inferred wire size (bytes) of an Input report - its payload plus the
        leading report-ID byte when the device uses numbered reports. None if the
        report descriptor doesn't declare it."""
        rep = self.report_map().get((REPORT_INPUT, report_id))
        if rep is None:
            return None
        prefix = 1 if self._numbered else 0
        return rep.size + prefix

    # --- interrupt IN: recurring Input reports -----------------------------
    def read_report(self, length=None) -> bytes:
        info = self._discover()
        if length is None:  # infer the Input report size
            length = self.input_report_size() or info["in_mps"] or 8
        return self.handle.interrupt_in(info["in_ep"], length)

    def reports(self, length=None):
        while True:
            yield self.read_report(length)

    # --- Output reports: interrupt OUT if present, else SET_REPORT (Sec.4.4) ---
    def output_report(self, data, report_id=0):
        info = self._discover()
        if info["out_ep"] is not None:
            return self.handle.interrupt_out(info["out_ep"], bytes(data))
        return self.set_report(REPORT_OUTPUT, report_id, data)

    # --- class requests over EP0 ------------------------------------------
    def get_report(self, report_type=REPORT_INPUT, report_id=0, length=None) -> bytes:
        info = self._discover()
        count = length or info["in_mps"] or 64
        return self.handle.control(0xA1, GET_REPORT, (report_type << 8) | report_id, info["iface"], count)

    def set_report(self, report_type, report_id, data):
        info = self._discover()
        return self.handle.control(
            0x21, SET_REPORT, (report_type << 8) | report_id, info["iface"], bytes(data)
        )

    def get_idle(self, report_id=0) -> int:
        info = self._discover()
        return self.handle.control(0xA1, GET_IDLE, report_id, info["iface"], 1)[0]

    def set_idle(self, duration=0, report_id=0):
        info = self._discover()
        return self.handle.control(0x21, SET_IDLE, (duration << 8) | report_id, info["iface"], b"")

    def get_protocol(self) -> int:
        info = self._discover()
        return self.handle.control(0xA1, GET_PROTOCOL, 0, info["iface"], 1)[0]

    def set_protocol(self, protocol):
        info = self._discover()
        return self.handle.control(0x21, SET_PROTOCOL, protocol, info["iface"], b"")
