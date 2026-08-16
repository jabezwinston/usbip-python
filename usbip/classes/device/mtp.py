# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
USB MTP (Media Transfer Protocol) v1.1 - device class.

Exports a host directory tree as an MTP storage: Windows Explorer, libmtp
(mtp-detect/mtp-files) and gphoto2 can browse it, download files, and - unless
read-only - upload and delete. MTP is layered on PTP: it rides the **Still Image**
interface (class 0x06 / sub 0x01 / proto 0x01) and a bulk **container** protocol
(command -> optional data -> response), with an interrupt IN endpoint for events.
Everything is bulk/interrupt, so no USB/IP wire support is needed.

With `winusb=True` (the default) the function advertises the Microsoft OS "MTP"
Compatible ID, which makes Windows bind its MTP/WPD driver with no INF.

Mirrors the C classes/device/mtp.c. The MTP protocol and object model live here;
the actual storage is a **pluggable backend** so no filesystem access happens in
this class - `dev.add(MTP(store))` takes the application's own store, e.g. the
FilesystemStore in examples/device/mtp_device.py.
"""

from __future__ import annotations

import hashlib
import struct
import time

from ...core import SPEED_HIGH
from ...device import In, Out
from ...function import Interface

# ---- container types -----------------------------------------------------
CT_COMMAND, CT_DATA, CT_RESPONSE, CT_EVENT = 1, 2, 3, 4

# ---- operation codes -----------------------------------------------------
# fmt: off
OP_GET_DEVICE_INFO        = 0x1001
OP_OPEN_SESSION           = 0x1002
OP_CLOSE_SESSION          = 0x1003
OP_GET_STORAGE_IDS        = 0x1004
OP_GET_STORAGE_INFO       = 0x1005
OP_GET_NUM_OBJECTS        = 0x1006
OP_GET_OBJECT_HANDLES     = 0x1007
OP_GET_OBJECT_INFO        = 0x1008
OP_GET_OBJECT             = 0x1009
OP_DELETE_OBJECT          = 0x100B
OP_SEND_OBJECT_INFO       = 0x100C
OP_SEND_OBJECT            = 0x100D
OP_GET_PARTIAL_OBJECT     = 0x101B
OP_GET_DEVICE_PROP_DESC   = 0x1014
OP_GET_DEVICE_PROP_VALUE  = 0x1015
OP_SET_DEVICE_PROP_VALUE  = 0x1016
OP_GET_OBJECT_PROPS_SUPPORTED = 0x9801
OP_GET_OBJECT_PROP_DESC   = 0x9802
OP_GET_OBJECT_PROP_VALUE  = 0x9803
OP_GET_OBJECT_PROP_LIST   = 0x9805
OP_GET_OBJECT_REFERENCES  = 0x9810
OP_MOVE_OBJECT            = 0x1019
OP_COPY_OBJECT            = 0x101A
OP_SET_OBJECT_PROP_VALUE  = 0x9804
# fmt: on
# Android partial/edit extensions (in-place object editing)
# fmt: off
OP_GET_PARTIAL_OBJECT_64  = 0x95C1
OP_SEND_PARTIAL_OBJECT    = 0x95C2
OP_TRUNCATE_OBJECT        = 0x95C3
OP_BEGIN_EDIT_OBJECT      = 0x95C4
OP_END_EDIT_OBJECT        = 0x95C5
# fmt: on

# Op code -> log text, derived from the OP_ symbols above: strip the prefix and
# CamelCase the words, PTP's own spelling ("GetDeviceInfo"). Defining an opcode IS
# naming it. The dict doubles as the supported-operations list GetDeviceInfo
# advertises, in definition order. Mirrors MTP_OP_LIST in the C classes/device/mtp.c.
_OP_NAMES: dict[int, str] = {
    code: "".join(word.capitalize() for word in sym[len("OP_") :].split("_"))
    for sym, code in vars().items()
    if sym.startswith("OP_")
}

# ---- response codes ------------------------------------------------------
# fmt: off
RC_OK                       = 0x2001
RC_GENERAL_ERROR            = 0x2002
RC_SESSION_NOT_OPEN         = 0x2003
RC_OPERATION_NOT_SUPPORTED  = 0x2005
RC_INVALID_STORAGE_ID       = 0x2008
RC_INVALID_OBJECT_HANDLE    = 0x2009
RC_DEVICEPROP_NOT_SUPPORTED = 0x200A
RC_STORE_FULL               = 0x200C
RC_STORE_READ_ONLY          = 0x200E
RC_ACCESS_DENIED            = 0x200F
RC_INVALID_PARENT           = 0x201A
RC_INVALID_PARAMETER        = 0x201D
RC_SESSION_ALREADY_OPEN     = 0x201E
RC_NO_VALID_OBJECT_INFO     = 0x2015
RC_INVALID_OBJECT_PROP_CODE = 0xA801
RC_OBJECT_PROP_NOT_SUPPORTED = 0xA80A
# fmt: on

# ---- event codes ---------------------------------------------------------
# fmt: off
EV_OBJECT_ADDED   = 0x4002
EV_OBJECT_REMOVED = 0x4003
EV_STORE_ADDED    = 0x4004
# fmt: on

# ---- object format codes -------------------------------------------------
# fmt: off
FMT_UNDEFINED   = 0x3000
FMT_ASSOCIATION = 0x3001            # a folder
FMT_TEXT        = 0x3004
# fmt: on

# fmt: off
_FMT_BY_EXT = {
    ".txt": FMT_TEXT, ".htm": 0x3005, ".html": 0x3005, ".wav": 0x3008, ".mp3": 0x3009,
    ".avi": 0x300A, ".mpg": 0x300B, ".mpeg": 0x300B, ".asf": 0x300C,
    ".jpg": 0x3801, ".jpeg": 0x3801, ".bmp": 0x3804, ".gif": 0x3807, ".png": 0x380B,
    ".tif": 0x380D, ".tiff": 0x380D, ".wma": 0xB901, ".ogg": 0xB902, ".aac": 0xB903,
    ".flac": 0xB906, ".wmv": 0xB981, ".mp4": 0xB982, ".m4a": 0xB982, ".3gp": 0xB984,
    ".xml": 0xBA82, ".doc": 0xBA83, ".xls": 0xBA85,
}
# fmt: on

# ---- datatype codes ------------------------------------------------------
T_U8, T_U16, T_U32, T_U64, T_U128, T_STR = 0x0002, 0x0004, 0x0006, 0x0008, 0x000A, 0xFFFF

# ---- device property codes -----------------------------------------------
DPC_SYNC_PARTNER = 0xD401
DPC_FRIENDLY_NAME = 0xD402

# ---- object property codes (the required set per format + a few for display) --
# fmt: off
OPC_STORAGE_ID    = 0xDC01
OPC_OBJECT_FORMAT = 0xDC02
OPC_PROTECTION    = 0xDC03
OPC_OBJECT_SIZE   = 0xDC04
OPC_FILENAME      = 0xDC07
OPC_DATE_MODIFIED = 0xDC09
OPC_PARENT        = 0xDC0B
OPC_PUID          = 0xDC41
OPC_NAME          = 0xDC44
# fmt: on

# property -> datatype, and the order we advertise them
# fmt: off
PROP_TYPE = {
    OPC_STORAGE_ID: T_U32, OPC_OBJECT_FORMAT: T_U16, OPC_PROTECTION: T_U16,
    OPC_OBJECT_SIZE: T_U64, OPC_FILENAME: T_STR, OPC_DATE_MODIFIED: T_STR,
    OPC_PARENT: T_U32, OPC_PUID: T_U128, OPC_NAME: T_STR,
}
# fmt: on
# fmt: off
PROP_LIST = [OPC_STORAGE_ID, OPC_OBJECT_FORMAT, OPC_PROTECTION, OPC_OBJECT_SIZE,
             OPC_FILENAME, OPC_DATE_MODIFIED, OPC_PARENT, OPC_PUID, OPC_NAME]
# fmt: on

ROOT = 0xFFFFFFFF  # "objects in the root" in operation parameters
ALL = 0xFFFFFFFF  # "all storages" / "all objects" sentinel


# ---- PTP encoding primitives ---------------------------------------------
def _u16(value):
    return struct.pack("<H", value & 0xFFFF)


def _u32(value):
    return struct.pack("<I", value & 0xFFFFFFFF)


def _u64(value):
    return struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF)


def ptp_str(text: str) -> bytes:
    """PTP string: 1 byte = number of UTF-16 chars *including the NUL*, then the
    UTF-16LE chars including the terminating NUL. Empty string is a single 0x00."""
    if not text:
        return b"\x00"
    chars = text[:254]  # length byte counts chars incl NUL
    body = chars.encode("utf-16-le") + b"\x00\x00"
    return bytes([len(chars) + 1]) + body


def ptp_str_parse(buf: bytes, off: int):
    nchars = buf[off]
    off += 1
    if nchars == 0:
        return "", off
    raw = buf[off : off + 2 * nchars]
    off += 2 * nchars
    return raw.decode("utf-16-le", "replace").split("\x00", 1)[0], off


def u16_array(items) -> bytes:
    return _u32(len(items)) + b"".join(_u16(item) for item in items)


def u32_array(items) -> bytes:
    return _u32(len(items)) + b"".join(_u32(item) for item in items)


def _dt(epoch) -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.localtime(epoch)) if epoch else ""


def _puid(path: str) -> bytes:
    return hashlib.md5(path.encode("utf-8", "replace")).digest()  # stable 16-byte id


# PTP datatype -> encoded size in bytes (integers only; T_STR is length-prefixed)
TYPE_SIZES = {T_U8: 1, T_U16: 2, T_U32: 4, T_U64: 8, T_U128: 16}


def _zero_value(dtype) -> bytes:
    if dtype == T_STR:
        return b"\x00"
    return b"\x00" * TYPE_SIZES[dtype]


def build_object_info(storage_id, fmt, size, parent, name, is_dir, date="") -> bytes:
    """The ObjectInfo dataset (PIMA 15740 Sec.5.3.1.5): fixed fields, then the
    Filename / CaptureDate / ModificationDate / Keywords strings. Shared with the
    host driver so the layout is written down exactly once."""
    assoc = 0x0001 if is_dir else 0x0000
    return (
        _u32(storage_id)
        + _u16(fmt)
        + _u16(0)  # protection
        + _u32(size)
        + _u16(0)
        + _u32(0)
        + _u32(0)
        + _u32(0)  # thumb format/size/w/h
        + _u32(0)
        + _u32(0)
        + _u32(0)  # image w/h/depth
        + _u32(parent)
        + _u16(assoc)
        + _u32(0)
        + _u32(0)  # assoc description, sequence number
        + ptp_str(name)
        + ptp_str(date)
        + ptp_str(date)
        + ptp_str("")
    )


def parse_object_info(data) -> dict:
    """The ObjectInfo fields either side reads back out of the dataset above."""
    name, _ = ptp_str_parse(data, 52)
    return {
        "fmt": int.from_bytes(data[4:6], "little"),
        "size": int.from_bytes(data[8:12], "little"),
        "parent": int.from_bytes(data[38:42], "little"),
        "assoc": int.from_bytes(data[42:44], "little"),
        "name": name,
    }


def _fmt_of(name: str) -> int:
    ext = name[name.rfind(".") :].lower() if "." in name else ""
    return _FMT_BY_EXT.get(ext, FMT_UNDEFINED)


# ---- the backing storage: a host directory tree --------------------------
class MtpStorage:
    """The MTP object model over a pluggable storage *backend*: assigns stable u32
    ObjectHandles and tracks the parent/child tree (folders are Association objects,
    parent 0 is the root), delegating ALL I/O to the backend so this class performs
    no filesystem access. A backend uses relative "/"-separated paths ("" = root) and
    provides: `listdir(path) -> [names]`, `stat(path) -> (is_dir, size, mtime)`,
    `read(path, off, size)`, `write(path, data)`, `mkdir(path)`, `remove(path)`,
    `disk_usage() -> (total, free)`, plus the attributes `read_only`/`description`."""

    def __init__(self, stores):
        self.stores = list(stores) if isinstance(stores, (list, tuple)) else [stores]
        self.storage_ids = [((i + 1) << 16) | 0x0001 for i in range(len(self.stores))]
        self.read_only = bool(getattr(self.stores[0], "read_only", False))
        self._handle_of = {}  # (store_index, relpath) -> handle
        self._next = 1
        self.objects = {}  # handle -> entry dict
        self.rescan()

    def _si(self, storage_id):
        return self.storage_ids.index(storage_id) if storage_id in self.storage_ids else None

    def _handle_for(self, key):
        handle = self._handle_of.get(key)
        if handle is None:
            handle = self._next
            self._next += 1
            self._handle_of[key] = handle
        return handle

    def _walk(self, si, relpath, parent, objects, alive):
        store = self.stores[si]
        for name in sorted(store.listdir(relpath)):
            full = f"{relpath}/{name}" if relpath else name
            try:
                is_dir, size, mtime = store.stat(full)
            except OSError:
                continue
            key = (si, full)
            alive.add(key)
            handle = self._handle_for(key)
            objects[handle] = {
                "handle": handle,
                "parent": parent,
                "si": si,
                "storage": self.storage_ids[si],
                "path": full,
                "name": name,
                "is_dir": is_dir,
                "fmt": FMT_ASSOCIATION if is_dir else _fmt_of(name),
                "size": size,
                "mtime": mtime,
            }
            if is_dir:
                self._walk(si, full, handle, objects, alive)

    def rescan(self):
        objects, alive = {}, set()
        for si in range(len(self.stores)):
            self._walk(si, "", 0, objects, alive)  # "" = each backend's root
        self.objects = objects
        self._handle_of = {k: handle for k, handle in self._handle_of.items() if k in alive}

    def list_handles(self, storage_id, fmt, parent):
        sel = []
        for handle, obj in self.objects.items():
            if storage_id not in (0, ALL) and obj["storage"] != storage_id:
                continue
            if parent == ROOT:
                if obj["parent"] != 0:
                    continue
            elif parent != 0 and obj["parent"] != parent:  # parent 0 = all on the store(s)
                continue
            if fmt and obj["fmt"] != fmt:
                continue
            sel.append(handle)
        return sorted(sel)

    def descendants(self, handle):
        out, stack = [], [handle]
        while stack:
            cur = stack.pop()
            for child, obj in self.objects.items():
                if obj["parent"] == cur:
                    out.append(child)
                    stack.append(child)
        return out

    def target(self, storage_id, parent):
        """Resolve (store_index, dir_relpath) for a write under (storage_id, parent)."""
        if parent in (0, ROOT):
            si = self._si(storage_id)
            return (si if si is not None else 0, "")
        obj = self.objects.get(parent)
        return (obj["si"], obj["path"]) if obj and obj["is_dir"] else None

    def parent_dir(self, handle):
        """The (store_index, dir_relpath) the object lives in (for an in-place rename)."""
        obj = self.objects[handle]
        return (obj["si"], "" if obj["parent"] == 0 else self.objects[obj["parent"]]["path"])

    def disk_usage(self, storage_id):
        si = self._si(storage_id)
        return self.stores[si].disk_usage() if si is not None else (0, 0)

    def store_description(self, storage_id):
        si = self._si(storage_id)
        return (
            getattr(self.stores[si], "description", "USB over IP")
            if si is not None
            else "USB over IP"
        )

    def read(self, handle, off=0, size=None):
        obj = self.objects.get(handle)
        if not obj or obj["is_dir"]:
            return b""
        return self.stores[obj["si"]].read(obj["path"], off, size)

    def make_file(self, si, parent_dir, name):
        path = f"{parent_dir}/{name}" if parent_dir else name
        self.stores[si].write(path, b"")
        self.rescan()
        return self._handle_of.get((si, path))

    def make_dir(self, si, parent_dir, name):
        path = f"{parent_dir}/{name}" if parent_dir else name
        self.stores[si].mkdir(path)
        self.rescan()
        return self._handle_of.get((si, path))

    def write_file(self, handle, data):
        obj = self.objects[handle]
        self.stores[obj["si"]].write(obj["path"], data)
        self.rescan()

    def delete(self, handle):
        obj = self.objects.get(handle)
        if not obj:
            return False
        try:
            self.stores[obj["si"]].remove(obj["path"])
        except OSError:
            return False
        self.rescan()
        return True

    def rename(self, handle, dest_si, dest_dir, new_name):
        """Rename/move an object to (dest_si, dest_dir, new_name). A same-store move
        keeps handles stable; a cross-store move is a copy + delete."""
        obj = self.objects.get(handle)
        if not obj:
            return False
        src_si, old = obj["si"], obj["path"]
        new = f"{dest_dir}/{new_name}" if dest_dir else new_name
        if dest_si != src_si:  # cross-storage move
            self._copy_tree(handle, dest_si, dest_dir, new_name)
            try:
                self.stores[src_si].remove(old)
            except OSError:
                pass
            self.rescan()
            return True
        if new == old:
            return True
        try:
            self.stores[src_si].rename(old, new)
        except OSError:
            return False
        remap = {
            k: (src_si, new if k[1] == old else new + k[1][len(old) :])
            for k in self._handle_of
            if k[0] == src_si and (k[1] == old or k[1].startswith(old + "/"))
        }
        for oldk, newk in remap.items():
            self._handle_of[newk] = self._handle_of.pop(oldk)
        self.rescan()
        return True

    def _copy_tree(self, handle, dest_si, dest_dir, name):
        obj = self.objects[handle]
        new = f"{dest_dir}/{name}" if dest_dir else name
        if obj["is_dir"]:
            self.stores[dest_si].mkdir(new)
            for ch in [cand for cand in self.objects.values() if cand["parent"] == handle]:
                self._copy_tree(ch["handle"], dest_si, new, ch["name"])
        else:
            data = self.stores[obj["si"]].read(obj["path"], 0, None)
            self.stores[dest_si].write(new, data)
        return new

    def copy(self, handle, dest_si, dest_dir, name):
        if handle not in self.objects:
            return 0
        new = self._copy_tree(handle, dest_si, dest_dir, name)
        self.rescan()
        return self._handle_of.get((dest_si, new), 0)

    def pwrite(self, handle, offset, data):
        obj = self.objects.get(handle)
        if not obj or obj["is_dir"]:
            return False
        self.stores[obj["si"]].pwrite(obj["path"], offset, data)
        self.rescan()
        return True

    def truncate(self, handle, size):
        obj = self.objects.get(handle)
        if not obj or obj["is_dir"]:
            return False
        self.stores[obj["si"]].truncate(obj["path"], size)
        self.rescan()
        return True


# ---- the MTP interface ---------------------------------------------------
class MTP(Interface):
    """An MTP interface backed by `store` - an application-supplied storage backend
    (the example's FilesystemStore) or an already-built :class:`MtpStorage`. With
    winusb=True (default) the function advertises the Microsoft OS "MTP" Compatible ID
    so Windows binds its MTP driver automatically."""

    bInterfaceClass = 0x06  # Still Image
    bInterfaceSubClass = 0x01  # Still Image Capture Device
    bInterfaceProtocol = 0x01  # PTP / Bulk-Only
    out_ep = Out(0x01, "bulk", mps=64)
    in_ep = In(0x81, "bulk", mps=64)
    intr_ep = In(0x82, "interrupt", mps=28, interval=6)

    def __init__(
        self,
        store,
        *,
        name="USBIP MTP",
        manufacturer="USB over IP",
        serial=None,
        winusb=True,
        on_event=None,
    ):
        super().__init__()
        self.storage = store if isinstance(store, MtpStorage) else MtpStorage(store)
        self.winusb = winusb
        self.model = name
        self.manufacturer = manufacturer
        self.serial = (serial or "0123456789ABCDEF0123456789ABCDEF")[:32].ljust(32, "0")
        self.friendly_name = name
        self.sync_partner = ""
        self.on_event = on_event or (lambda text: None)
        self.session = 0
        self._rx = None  # pending data-out phase, or None
        self._send_target = None  # handle reserved by SendObjectInfo for SendObject

    def _on_added(self):
        if self.winusb:
            # scoped to THIS function: the "MTP" Compatible ID must name the MTP
            # interface, or Windows loads WPD over whichever function sits at 0.
            self.func.enable_msos(b"MTP")

    def adjust_for_speed(self, speed):
        self.in_ep.mps = self.out_ep.mps = 512 if speed >= SPEED_HIGH else 64  # HS bulk = 512

    # ---- container I/O ----
    def _send(self, ctype, code, txid, payload=b""):
        self.in_ep.write(struct.pack("<IHHI", 12 + len(payload), ctype, code, txid) + payload)

    def _data(self, code, txid, payload):
        self._send(CT_DATA, code, txid, payload)

    def _resp(self, rc, txid, params=()):
        self._send(CT_RESPONSE, rc, txid, b"".join(_u32(param) for param in params))

    def _data_ok(self, code, txid, payload):
        self._data(code, txid, payload)
        self._resp(RC_OK, txid)

    def _event(self, code, params=()):
        self.intr_ep.write(
            struct.pack("<IHHI", 12 + 4 * len(params), CT_EVENT, code, ALL)
            + b"".join(_u32(param) for param in params)
        )

    # ---- bulk OUT: command containers, then any data-out phase ----
    def on_out(self, ep, data):
        data = bytes(data)
        if self._rx is None:
            self._dispatch(data)
            return
        rx = self._rx
        rx["buf"] += data
        if rx["need"] is None and len(rx["buf"]) >= 12:
            rx["need"] = int.from_bytes(rx["buf"][0:4], "little")
        if rx["need"] is not None and len(rx["buf"]) >= rx["need"]:
            self._rx = None
            self._complete_rx(rx, bytes(rx["buf"][12 : rx["need"]]))

    def _dispatch(self, data):
        if len(data) < 12:
            return None
        _, _, code, txid = struct.unpack_from("<IHHI", data, 0)
        np = max(0, min(5, (len(data) - 12) // 4))
        params = list(struct.unpack_from(f"<{np}I", data, 12)) + [0] * (5 - np)
        self.on_event(_OP_NAMES.get(code, f"Op 0x{code:04X}"))

        if code not in (OP_GET_DEVICE_INFO, OP_OPEN_SESSION) and not self.session:
            return self._resp(RC_SESSION_NOT_OPEN, txid)

        if code == OP_GET_DEVICE_INFO:
            self._data_ok(code, txid, self._device_info())
        elif code == OP_OPEN_SESSION:
            if self.session:
                self._resp(RC_SESSION_ALREADY_OPEN, txid, [self.session])
            elif params[0] == 0:
                self._resp(RC_INVALID_PARAMETER, txid)
            else:
                self.session = params[0]
                self._resp(RC_OK, txid)
        elif code == OP_CLOSE_SESSION:
            self.session = 0
            self._resp(RC_OK, txid)
        elif code == OP_GET_STORAGE_IDS:
            self._data_ok(code, txid, u32_array(self.storage.storage_ids))
        elif code == OP_GET_STORAGE_INFO:
            if params[0] not in self.storage.storage_ids:
                self._resp(RC_INVALID_STORAGE_ID, txid)
            else:
                self._data_ok(code, txid, self._storage_info(params[0]))
        elif code == OP_GET_NUM_OBJECTS:
            handles = self.storage.list_handles(params[0], params[1], params[2])
            self._resp(RC_OK, txid, [len(handles)])
        elif code == OP_GET_OBJECT_HANDLES:
            handles = self.storage.list_handles(params[0], params[1], params[2])
            self._data_ok(code, txid, u32_array(handles))
        elif code == OP_GET_OBJECT_INFO:
            obj = self.storage.objects.get(params[0])
            self._data_ok(code, txid, self._object_info(obj)) if obj else self._resp(
                RC_INVALID_OBJECT_HANDLE, txid
            )
        elif code == OP_GET_OBJECT:
            obj = self.storage.objects.get(params[0])
            if not obj or obj["is_dir"]:
                self._resp(RC_INVALID_OBJECT_HANDLE, txid)
            else:
                self._data_ok(code, txid, self.storage.read(params[0]))
        elif code == OP_GET_PARTIAL_OBJECT:
            obj = self.storage.objects.get(params[0])
            if not obj or obj["is_dir"]:
                self._resp(RC_INVALID_OBJECT_HANDLE, txid)
            else:
                chunk = self.storage.read(params[0], params[1], params[2])
                self._data(code, txid, chunk)
                self._resp(RC_OK, txid, [len(chunk)])
        elif code == OP_DELETE_OBJECT:
            if self.storage.read_only:
                self._resp(RC_STORE_READ_ONLY, txid)
            elif self.storage.delete(params[0]):
                self._resp(RC_OK, txid)
                self._event(EV_OBJECT_REMOVED, [params[0]])
            else:
                self._resp(RC_INVALID_OBJECT_HANDLE, txid)
        elif code == OP_SEND_OBJECT_INFO:
            self._resp(RC_STORE_READ_ONLY, txid) if self.storage.read_only else self._begin_rx(
                code, txid, params
            )
        elif code == OP_SEND_OBJECT:
            if self.storage.read_only:
                self._resp(RC_STORE_READ_ONLY, txid)
            elif self._send_target is None:
                self._resp(RC_NO_VALID_OBJECT_INFO, txid)
            else:
                self._begin_rx(code, txid, params)
        elif code == OP_GET_DEVICE_PROP_DESC:
            desc = self._device_prop_desc(params[0])
            self._data_ok(code, txid, desc) if desc else self._resp(RC_DEVICEPROP_NOT_SUPPORTED, txid)
        elif code == OP_GET_DEVICE_PROP_VALUE:
            value = self._device_prop_value(params[0])
            self._data_ok(code, txid, value) if value is not None else self._resp(
                RC_DEVICEPROP_NOT_SUPPORTED, txid
            )
        elif code == OP_SET_DEVICE_PROP_VALUE:
            self._begin_rx(code, txid, params)
        elif code == OP_GET_OBJECT_PROPS_SUPPORTED:
            self._data_ok(code, txid, u16_array(PROP_LIST))
        elif code == OP_GET_OBJECT_PROP_DESC:
            desc = self._object_prop_desc(params[0])
            self._data_ok(code, txid, desc) if desc else self._resp(RC_INVALID_OBJECT_PROP_CODE, txid)
        elif code == OP_GET_OBJECT_PROP_VALUE:
            obj = self.storage.objects.get(params[0])
            if not obj:
                self._resp(RC_INVALID_OBJECT_HANDLE, txid)
            else:
                pv = self._prop(obj, params[1])
                self._data_ok(code, txid, pv[1]) if pv else self._resp(
                    RC_OBJECT_PROP_NOT_SUPPORTED, txid
                )
        elif code == OP_GET_OBJECT_PROP_LIST:
            self._data_ok(code, txid, self._object_prop_list(params))
        elif code == OP_MOVE_OBJECT:
            obj = self.storage.objects.get(params[0])
            tgt = self.storage.target(params[1], params[2])
            if self.storage.read_only:
                self._resp(RC_STORE_READ_ONLY, txid)
            elif not obj:
                self._resp(RC_INVALID_OBJECT_HANDLE, txid)
            elif tgt is None:
                self._resp(RC_INVALID_PARENT, txid)
            else:
                self._resp(
                    RC_OK
                    if self.storage.rename(params[0], tgt[0], tgt[1], obj["name"])
                    else RC_GENERAL_ERROR,
                    txid,
                )
        elif code == OP_COPY_OBJECT:
            obj = self.storage.objects.get(params[0])
            tgt = self.storage.target(params[1], params[2])
            if self.storage.read_only:
                self._resp(RC_STORE_READ_ONLY, txid)
            elif not obj:
                self._resp(RC_INVALID_OBJECT_HANDLE, txid)
            elif tgt is None:
                self._resp(RC_INVALID_PARENT, txid)
            else:
                self._resp(RC_OK, txid, [self.storage.copy(params[0], tgt[0], tgt[1], obj["name"])])
        elif code == OP_SET_OBJECT_PROP_VALUE:
            self._resp(RC_STORE_READ_ONLY, txid) if self.storage.read_only else self._begin_rx(
                code, txid, params
            )
        elif code in (OP_BEGIN_EDIT_OBJECT, OP_END_EDIT_OBJECT):
            known = self.storage.objects.get(params[0])
            self._resp(RC_OK if known else RC_INVALID_OBJECT_HANDLE, txid)
        elif code == OP_GET_PARTIAL_OBJECT_64:
            obj = self.storage.objects.get(params[0])
            if not obj or obj["is_dir"]:
                self._resp(RC_INVALID_OBJECT_HANDLE, txid)
            else:
                chunk = self.storage.read(params[0], params[1] | (params[2] << 32), params[3])
                self._data(code, txid, chunk)
                self._resp(RC_OK, txid, [len(chunk)])
        elif code == OP_SEND_PARTIAL_OBJECT:
            obj = self.storage.objects.get(params[0])
            if self.storage.read_only:
                self._resp(RC_STORE_READ_ONLY, txid)
            elif not obj or obj["is_dir"]:
                self._resp(RC_INVALID_OBJECT_HANDLE, txid)
            else:
                self._begin_rx(code, txid, params)
        elif code == OP_TRUNCATE_OBJECT:
            obj = self.storage.objects.get(params[0])
            if self.storage.read_only:
                self._resp(RC_STORE_READ_ONLY, txid)
            elif not obj or obj["is_dir"]:
                self._resp(RC_INVALID_OBJECT_HANDLE, txid)
            else:
                self._resp(
                    RC_OK if self.storage.truncate(params[0], params[1] | (params[2] << 32)) else RC_GENERAL_ERROR,
                    txid,
                )
        elif code == OP_GET_OBJECT_REFERENCES:
            self._data_ok(code, txid, u32_array([]))
        else:
            self._resp(RC_OPERATION_NOT_SUPPORTED, txid)
        return None

    # ---- data-out phase (SendObjectInfo / SendObject / SetDevicePropValue) ----
    def _begin_rx(self, code, txid, params):
        self._rx = {"code": code, "txid": txid, "p": params, "buf": bytearray(), "need": None}

    def _complete_rx(self, rx, payload):
        code, txid, params = rx["code"], rx["txid"], rx["p"]
        if code == OP_SEND_OBJECT_INFO:
            self._recv_object_info(txid, params, payload)
        elif code == OP_SEND_OBJECT:
            self._recv_object(txid, payload)
        elif code == OP_SET_DEVICE_PROP_VALUE:
            self._recv_device_prop(txid, params, payload)
        elif code == OP_SET_OBJECT_PROP_VALUE:
            self._recv_set_object_prop(txid, params, payload)
        elif code == OP_SEND_PARTIAL_OBJECT:
            self._recv_send_partial(txid, params, payload)

    def _recv_object_info(self, txid, params, payload):
        if len(payload) < 52:
            return self._resp(RC_GENERAL_ERROR, txid)
        info = parse_object_info(payload)
        fmt, name = info["fmt"], info["name"]
        tgt = self.storage.target(params[0], params[1])
        if tgt is None or not name:
            return self._resp(RC_INVALID_PARENT, txid)
        si, parent_dir = tgt
        if fmt == FMT_ASSOCIATION:  # a new folder completes here
            handle = self.storage.make_dir(si, parent_dir, name)
            self._send_target = None
        else:  # a file: reserve, fill in SendObject
            handle = self.storage.make_file(si, parent_dir, name)
            self._send_target = handle
        self.on_event(f"SendObjectInfo {name}")
        self._resp(RC_OK, txid, [self.storage.storage_ids[si], params[1], handle or 0])
        if handle:
            self._event(EV_OBJECT_ADDED, [handle])
        return None

    def _recv_object(self, txid, payload):
        handle = self._send_target
        self._send_target = None
        if handle is None or handle not in self.storage.objects:
            return self._resp(RC_NO_VALID_OBJECT_INFO, txid)
        self.storage.write_file(handle, payload)
        self.on_event(f"SendObject ({len(payload)}B)")
        self._resp(RC_OK, txid)
        self._event(EV_OBJECT_ADDED, [handle])
        return None

    def _recv_device_prop(self, txid, params, payload):
        try:
            value, _ = ptp_str_parse(payload, 0)
        except Exception:
            value = ""
        if params[0] == DPC_FRIENDLY_NAME:
            self.friendly_name = value
        elif params[0] == DPC_SYNC_PARTNER:
            self.sync_partner = value
        self._resp(RC_OK, txid)

    def _recv_set_object_prop(self, txid, params, payload):
        handle, propcode = params[0], params[1]
        obj = self.storage.objects.get(handle)
        if not obj:
            return self._resp(RC_INVALID_OBJECT_HANDLE, txid)
        if propcode in (OPC_FILENAME, OPC_NAME):  # rename (in place, same storage)
            newname, _ = ptp_str_parse(payload, 0)
            si, pdir = self.storage.parent_dir(handle)
            if newname and self.storage.rename(handle, si, pdir, newname):
                self.on_event(f"Rename -> {newname}")
                self._resp(RC_OK, txid)
            else:
                self._resp(RC_GENERAL_ERROR, txid)
        else:
            self._resp(RC_OBJECT_PROP_NOT_SUPPORTED, txid)
        return None

    def _recv_send_partial(self, txid, params, payload):
        offset = params[1] | (params[2] << 32)
        if self.storage.pwrite(params[0], offset, payload):
            self.on_event(f"SendPartialObject (@{offset} {len(payload)}B)")
            self._resp(RC_OK, txid, [len(payload)])
        else:
            self._resp(RC_INVALID_OBJECT_HANDLE, txid)

    # ---- dataset builders ----
    def _device_info(self):
        ops = list(_OP_NAMES.keys())
        events = [EV_OBJECT_ADDED, EV_OBJECT_REMOVED, EV_STORE_ADDED]
        dprops = [DPC_FRIENDLY_NAME, DPC_SYNC_PARTNER]
        playback = [FMT_UNDEFINED, FMT_ASSOCIATION, FMT_TEXT, 0x3009, 0x3801, 0x380B, 0xB982]
        return (
            _u16(100)
            + _u32(0x00000006)
            + _u16(100)
            + ptp_str("microsoft.com: 1.0; android.com: 1.0;")
            + _u16(0)
            + u16_array(ops)
            + u16_array(events)
            + u16_array(dprops)
            + u16_array([])
            + u16_array(playback)
            + ptp_str(self.manufacturer)
            + ptp_str(self.model)
            + ptp_str("1.0")
            + ptp_str(self.serial)
        )

    def _storage_info(self, storage_id):
        try:
            total, free = self.storage.disk_usage(storage_id)
        except OSError:
            total = free = 0
        access = 0x0001 if self.storage.read_only else 0x0000
        return (
            _u16(0x0003)
            + _u16(0x0002)
            + _u16(access)  # fixed RAM, hierarchical FS
            + _u64(total)
            + _u64(free)
            + _u32(ALL)
            + ptp_str(self.storage.store_description(storage_id))
            + ptp_str(f"vol-{storage_id:08x}")
        )

    def _object_info(self, obj):
        return build_object_info(
            obj["storage"],
            obj["fmt"],
            min(obj["size"], ALL),
            obj["parent"],
            obj["name"],
            obj["is_dir"],
            date=_dt(obj["mtime"]),
        )

    def _prop(self, obj, pc):
        """One object property -> (datatype, value bytes), or None if unsupported."""
        if pc == OPC_STORAGE_ID:
            return T_U32, _u32(obj["storage"])
        if pc == OPC_OBJECT_FORMAT:
            return T_U16, _u16(obj["fmt"])
        if pc == OPC_PROTECTION:
            return T_U16, _u16(0x0001 if self.storage.read_only else 0)
        if pc == OPC_OBJECT_SIZE:
            return T_U64, _u64(obj["size"])
        if pc == OPC_FILENAME:
            return T_STR, ptp_str(obj["name"])
        if pc == OPC_DATE_MODIFIED:
            return T_STR, ptp_str(_dt(obj["mtime"]))
        if pc == OPC_PARENT:
            return T_U32, _u32(obj["parent"])
        if pc == OPC_PUID:
            return T_U128, _puid(obj["path"])
        if pc == OPC_NAME:
            return T_STR, ptp_str(obj["name"])
        return None

    def _object_prop_desc(self, pc):
        if pc not in PROP_TYPE:
            return None
        dtype = PROP_TYPE[pc]
        getset = 1 if pc in (OPC_FILENAME, OPC_NAME) else 0  # name is settable (rename)
        return (
            _u16(pc) + _u16(dtype) + bytes([getset]) + _zero_value(dtype) + _u32(0) + bytes([0x00])
        )  # group code, form flag = none

    def _object_prop_list(self, params):
        handle, fmt, propcode = params[0], params[1], params[2]
        if handle == ALL:
            handles = sorted(self.storage.objects.keys())
        elif handle == 0:
            handles = self.storage.list_handles(ALL, 0, ROOT)
        else:
            handles = [handle] + (self.storage.descendants(handle) if params[4] == ALL else [])
        if fmt:
            handles = [obj_handle for obj_handle in handles if self.storage.objects[obj_handle]["fmt"] == fmt]
        all_props = propcode in (0, ALL)
        out, count = bytearray(), 0
        for obj_handle in handles:
            obj = self.storage.objects.get(obj_handle)
            if not obj:
                continue
            for pc in PROP_LIST if all_props else [propcode]:
                pv = self._prop(obj, pc)
                if pv is None:
                    continue
                out += _u32(obj_handle) + _u16(pc) + _u16(pv[0]) + pv[1]
                count += 1
        return _u32(count) + bytes(out)

    def _device_prop_desc(self, pc):
        if pc not in (DPC_FRIENDLY_NAME, DPC_SYNC_PARTNER):
            return None
        cur = self.friendly_name if pc == DPC_FRIENDLY_NAME else self.sync_partner
        return (
            _u16(pc)
            + _u16(T_STR)
            + bytes([0x01])  # get/set
            + ptp_str("")
            + ptp_str(cur)
            + bytes([0x00])
        )

    def _device_prop_value(self, pc):
        if pc == DPC_FRIENDLY_NAME:
            return ptp_str(self.friendly_name)
        if pc == DPC_SYNC_PARTNER:
            return ptp_str(self.sync_partner)
        return None
