# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
MTP / PTP - host driver: the bulk container protocol (command -> data ->
response) over two bulk endpoints, plus convenience operations.

Small but complete enough to drive the device-side `classes/device/mtp.py` in
tests and to script real MTP devices: open a session, enumerate objects, download
/upload files, read object properties. Endpoint addresses are discovered from the
configuration descriptor. Mirrors what libmtp does on the wire.
"""

from __future__ import annotations

import struct

from ...core import BULK, DT_ENDPOINT, DT_INTERFACE, INTERRUPT, iter_descriptors
from ...host import Driver
from . import fetch_config
from ..device.mtp import (
    ALL,
    TYPE_SIZES,
    build_object_info,
    parse_object_info,
    CT_COMMAND,
    CT_DATA,
    CT_RESPONSE,
    FMT_ASSOCIATION,
    FMT_UNDEFINED,
    OP_BEGIN_EDIT_OBJECT,
    OP_CLOSE_SESSION,
    OP_COPY_OBJECT,
    OP_DELETE_OBJECT,
    OP_END_EDIT_OBJECT,
    OP_GET_DEVICE_INFO,
    OP_GET_DEVICE_PROP_VALUE,
    OP_GET_NUM_OBJECTS,
    OP_GET_OBJECT,
    OP_GET_OBJECT_HANDLES,
    OP_GET_OBJECT_INFO,
    OP_GET_OBJECT_PROP_LIST,
    OP_GET_OBJECT_PROP_VALUE,
    OP_GET_PARTIAL_OBJECT,
    OP_GET_PARTIAL_OBJECT_64,
    OP_GET_STORAGE_IDS,
    OP_GET_STORAGE_INFO,
    OP_MOVE_OBJECT,
    OP_OPEN_SESSION,
    OP_SEND_OBJECT,
    OP_SEND_OBJECT_INFO,
    OP_SEND_PARTIAL_OBJECT,
    OP_SET_OBJECT_PROP_VALUE,
    OP_TRUNCATE_OBJECT,
    OPC_FILENAME,
    RC_OK,
    ROOT,
    T_STR,
    T_U8,
    T_U16,
    T_U32,
    T_U64,
    T_U128,
    ptp_str,
    ptp_str_parse,
)


class MtpError(IOError):
    def __init__(self, code):
        super().__init__(f"MTP response 0x{code:04X}")
        self.code = code


class MtpHost(Driver):
    matches = {"bInterfaceClass": 0x06}

    def __init__(self, handle):
        super().__init__(handle)
        self.ep_in, self.ep_out, self.ep_intr = 0x81, 0x01, 0x82
        self._txid = 0
        self.session = 0
        self._discover_endpoints()

    def _discover_endpoints(self):
        try:
            cfg = fetch_config(self.handle)
        except Exception:
            return
        in_iface = False
        for desc in iter_descriptors(cfg):
            if desc[1] == DT_INTERFACE and len(desc) >= 9:
                in_iface = desc[5] == 0x06  # bInterfaceClass == Still Image
            elif desc[1] == DT_ENDPOINT and in_iface and len(desc) >= 5:
                addr, attr = desc[2], desc[3] & 0x03
                if attr == BULK:
                    setattr(self, "ep_in" if addr & 0x80 else "ep_out", addr)
                elif attr == INTERRUPT and addr & 0x80:  # interrupt IN
                    self.ep_intr = addr

    # ---- container transport ----
    def _recv_container(self):
        buf = b""
        while len(buf) < 12:
            part = self.handle.bulk_in(self.ep_in, 512)
            if not part:
                raise OSError("MTP: empty read on container header")
            buf += part
        length = int.from_bytes(buf[0:4], "little")
        while len(buf) < length:  # drain the rest of this container
            part = self.handle.bulk_in(self.ep_in, length - len(buf))
            if not part:
                raise OSError("MTP: short read in container body")
            buf += part
        ctype = int.from_bytes(buf[4:6], "little")
        code = int.from_bytes(buf[6:8], "little")
        txid = int.from_bytes(buf[8:12], "little")
        return ctype, code, txid, buf[12:length]

    def transaction(self, op, params=(), data_out=None):
        """Run command [-> data] -> response. Returns (resp_code, resp_params, data_in)."""
        self._txid += 1
        txid = self._txid
        body = b"".join(struct.pack("<I", param) for param in params)
        self.handle.bulk_out(
            self.ep_out, struct.pack("<IHHI", 12 + len(body), CT_COMMAND, op, txid) + body
        )
        if data_out is not None:
            self.handle.bulk_out(
                self.ep_out, struct.pack("<IHHI", 12 + len(data_out), CT_DATA, op, txid) + data_out
            )
        data_in = None
        while True:
            ctype, code, _, payload = self._recv_container()
            if ctype == CT_DATA:
                data_in = payload
            elif ctype == CT_RESPONSE:
                rparams = [
                    int.from_bytes(payload[i : i + 4], "little") for i in range(0, len(payload), 4)
                ]
                return code, rparams, data_in

    def _ok(self, op, params=(), data_out=None):
        code, rparams, data_in = self.transaction(op, params, data_out)
        if code != RC_OK:
            raise MtpError(code)
        return rparams, data_in

    # ---- convenience operations ----
    def open_session(self, sid=1):
        self._ok(OP_OPEN_SESSION, [sid])
        self.session = sid

    def close_session(self):
        self._ok(OP_CLOSE_SESSION)
        self.session = 0

    def device_info(self):
        _, data = self._ok(OP_GET_DEVICE_INFO)
        return _parse_device_info(data)

    def storage_ids(self):
        _, data = self._ok(OP_GET_STORAGE_IDS)
        return _u32_array(data)

    def storage_info(self, storage_id):
        _, data = self._ok(OP_GET_STORAGE_INFO, [storage_id])
        return data

    def num_objects(self, parent=ROOT, storage=ALL, fmt=0):
        rparams, _ = self._ok(OP_GET_NUM_OBJECTS, [storage, fmt, parent])
        return rparams[0] if rparams else 0

    def object_handles(self, parent=ROOT, storage=ALL, fmt=0):
        _, data = self._ok(OP_GET_OBJECT_HANDLES, [storage, fmt, parent])
        return _u32_array(data)

    def object_info(self, handle):
        _, data = self._ok(OP_GET_OBJECT_INFO, [handle])
        return parse_object_info(data)

    def get_object(self, handle):
        _, data = self._ok(OP_GET_OBJECT, [handle])
        return data

    def get_partial_object(self, handle, offset, count):
        _rparams, data = self._ok(OP_GET_PARTIAL_OBJECT, [handle, offset, count])
        return data

    def send_object_info(self, parent, name, size, fmt=None, storage=0x00010001):
        is_dir = fmt == FMT_ASSOCIATION
        fmt = FMT_ASSOCIATION if is_dir else (fmt or FMT_UNDEFINED)
        info = build_object_info(storage, fmt, size, parent, name, is_dir)
        rparams, _ = self._ok(OP_SEND_OBJECT_INFO, [storage, parent], data_out=info)
        return rparams[2] if len(rparams) >= 3 else 0  # the reserved ObjectHandle

    def send_object(self, data):
        self._ok(OP_SEND_OBJECT, [], data_out=data)

    def create_file(self, parent, name, data, storage=0x00010001):
        handle = self.send_object_info(parent, name, len(data), storage=storage)
        self.send_object(data)
        return handle

    def make_dir(self, parent, name, storage=0x00010001):
        return self.send_object_info(parent, name, 0, fmt=FMT_ASSOCIATION, storage=storage)

    def delete_object(self, handle):
        self._ok(OP_DELETE_OBJECT, [handle, 0])

    def set_object_prop_value(self, handle, prop, value):
        self._ok(OP_SET_OBJECT_PROP_VALUE, [handle, prop], data_out=value)

    def rename(self, handle, new_name):
        self.set_object_prop_value(handle, OPC_FILENAME, ptp_str(new_name))

    def move_object(self, handle, parent, storage=0x00010001):
        self._ok(OP_MOVE_OBJECT, [handle, storage, parent])

    def copy_object(self, handle, parent, storage=0x00010001):
        rparams, _ = self._ok(OP_COPY_OBJECT, [handle, storage, parent])
        return rparams[0] if rparams else 0

    # ---- partial / in-place edit operations ----
    def get_partial_object_64(self, handle, offset, count):
        _, data = self._ok(
            OP_GET_PARTIAL_OBJECT_64, [handle, offset & 0xFFFFFFFF, offset >> 32, count]
        )
        return data

    def begin_edit(self, handle):
        self._ok(OP_BEGIN_EDIT_OBJECT, [handle])

    def end_edit(self, handle):
        self._ok(OP_END_EDIT_OBJECT, [handle])

    def truncate_object(self, handle, size):
        self._ok(OP_TRUNCATE_OBJECT, [handle, size & 0xFFFFFFFF, size >> 32])

    def send_partial_object(self, handle, offset, data):
        rparams, _ = self._ok(
            OP_SEND_PARTIAL_OBJECT,
            [handle, offset & 0xFFFFFFFF, offset >> 32, len(data)],
            data_out=data,
        )
        return rparams[0] if rparams else 0

    def object_prop_value(self, handle, prop):
        _, data = self._ok(OP_GET_OBJECT_PROP_VALUE, [handle, prop])
        return data

    def device_prop_value(self, prop):
        _, data = self._ok(OP_GET_DEVICE_PROP_VALUE, [prop])
        return data

    def object_prop_list(self, handle=ALL, fmt=0, prop=ALL):
        _, data = self._ok(OP_GET_OBJECT_PROP_LIST, [handle, fmt, prop, 0, 0])
        return _parse_prop_list(data)


# ---- dataset (de)serialization -------------------------------------------
def _u32_array(data):
    count = int.from_bytes(data[0:4], "little")
    return [int.from_bytes(data[4 + 4 * i : 8 + 4 * i], "little") for i in range(count)]


def _decode_value(data, off, dtype):
    if dtype == T_STR:
        return ptp_str_parse(data, off)
    size = TYPE_SIZES.get(dtype, 0)
    raw = data[off : off + size]
    if dtype == T_U128:
        return raw, off + size
    return int.from_bytes(raw, "little"), off + size


def _parse_prop_list(data):
    off, count = 4, int.from_bytes(data[0:4], "little")
    out = {}
    for _ in range(count):
        handle = int.from_bytes(data[off : off + 4], "little")
        prop = int.from_bytes(data[off + 4 : off + 6], "little")
        dtype = int.from_bytes(data[off + 6 : off + 8], "little")
        value, off = _decode_value(data, off + 8, dtype)
        out.setdefault(handle, {})[prop] = value
    return out


def _parse_device_info(data):
    off = [0]

    def take(count):
        value = data[off[0] : off[0] + count]
        off[0] += count
        return value

    def u16():
        return int.from_bytes(take(2), "little")

    def u32():
        return int.from_bytes(take(4), "little")

    def arr():
        return [u16() for _ in range(u32())]

    def ptp_string():
        value, no = ptp_str_parse(data, off[0])
        off[0] = no
        return value

    return {
        "standard_version": u16(),
        "vendor_ext": u32(),
        "mtp_version": u16(),
        "extensions": ptp_string(),
        "functional_mode": u16(),
        "operations": arr(),
        "events": arr(),
        "device_props": arr(),
        "capture_formats": arr(),
        "playback_formats": arr(),
        "manufacturer": ptp_string(),
        "model": ptp_string(),
        "device_version": ptp_string(),
        "serial": ptp_string(),
    }


