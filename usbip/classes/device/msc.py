# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
Mass Storage Class - device class: Bulk-Only Transport + SCSI (SBC/MMC).

The class is generic: it runs the BOT state machine (CBW -> data -> CSW) and a
SCSI command set, delegating block I/O to a duck-typed *store*:

    store.num_blocks    -> int
    store.block_size    -> int
    store.read(lba, count)        -> bytes
    store.write(lba, count, data) -> None

Hand it several stores and the interface carries several **logical units** - one
pair of bulk pipes, one command at a time, but a separate medium behind each LUN
(Linux gives every unit its own /dev/sd*, Windows its own drive letter). A store
may also carry `medium`, `read_only`, `product` or `serial` attributes, which
override for that unit alone what the interface was built with.

No store ships with the class - no filesystem access happens here; the
application supplies one (see examples/device/msc_device.py for FileStore, an
image-file backend). The app also supplies an `on_command(text)` callback for
human-readable logging. Per USB MSC BOT 1.0 and SCSI SPC/SBC.
"""

from __future__ import annotations

from ...core import SPEED_HIGH, Stall
from ...device import In, Out
from ...function import Interface

# SCSI opcodes. The OP_ prefix groups them: _NAMES below derives each one's log
# text from its symbol, so defining an opcode IS naming it.
# fmt: off
OP_TEST_UNIT_READY = 0x00
OP_REZERO_UNIT     = 0x01      # UFI: seek to track 0 - a no-op here
OP_REQUEST_SENSE   = 0x03
OP_FORMAT_UNIT     = 0x04      # UFI: format the medium - accepted, nothing to do
OP_INQUIRY         = 0x12
OP_MODE_SENSE_6    = 0x1A
OP_START_STOP_UNIT = 0x1B
OP_PREVENT_ALLOW   = 0x1E
OP_READ_CAPACITY_10 = 0x25
OP_READ_10         = 0x28
OP_WRITE_10        = 0x2A
OP_SEEK_10         = 0x2B      # UFI: position the heads - a no-op here
OP_VERIFY_10       = 0x2F      # UFI: verify blocks - a no-op, nothing can rot here
OP_SYNC_CACHE_10   = 0x35
OP_READ_FORMAT_CAPACITIES = 0x23
OP_READ_TOC        = 0x43
OP_GET_CONFIGURATION = 0x46
OP_GET_EVENT_STATUS = 0x4A
OP_MODE_SENSE_10   = 0x5A
OP_SERVICE_ACTION_IN_16 = 0x9E  # READ CAPACITY(16) via service action 0x10
OP_READ_12         = 0xA8      # UFI: read, 12-byte CDB with a 4-byte block count
OP_WRITE_12        = 0xAA      # UFI: write, 12-byte CDB with a 4-byte block count
# fmt: on

# Opcode -> log text, derived from the OP_ symbols above: strip the prefix, space
# the underscores. Mirrors SCSI_OP_LIST in the C classes/device/msc.c.
_NAMES: dict[int, str] = {
    code: sym[len("OP_") :].replace("_", " ")
    for sym, code in vars().items()
    if sym.startswith("OP_")
}

# ---- Bulk-Only Transport wire constants (BOT 1.0), shared with the host driver --
CBW_SIGNATURE, CSW_SIGNATURE = b"USBC", b"USBS"
GET_MAX_LUN = 0xFE  # class request: report the LAST unit's number

# ---- medium types, mirroring the MSC_MEDIUM_* codes in the C library's ----------
# ---- include/classes/msc.h --------------------------------------------------
# fmt: off
MEDIUM_DISK   = "disk"          # removable direct-access disk (SCSI type 0x00)
MEDIUM_CDROM  = "cdrom"         # read-only CD-ROM (SCSI type 0x05), MMC answered
MEDIUM_FLOPPY = "floppy"        # direct-access with a Flexible Disk geometry page
_MEDIA = (MEDIUM_DISK, MEDIUM_CDROM, MEDIUM_FLOPPY)
# fmt: on

# ---- floppy formats, mirroring the MSC_FLOPPY_* codes in the C library's --------
# ---- include/classes/msc.h ---------------------------------------------------
# fmt: off
FLOPPY_2_88M = 0                # 2.88 MB - 80 cylinders, 2 heads, 36 sectors
FLOPPY_1_44M = 1                # 1.44 MB - 80 cylinders, 2 heads, 18 sectors
FLOPPY_1_2M  = 2                # 1.2 MB - 80 cylinders, 2 heads, 15 sectors
FLOPPY_720K  = 3                # 720 KB - 80 cylinders, 2 heads, 9 sectors
FLOPPY_360K  = 4                # 360 KB - 40 cylinders, 2 heads, 9 sectors
# fmt: on

# The standard floppy formats, 512 bytes per sector: format code, block count,
# medium type code, cylinders, heads, sectors/track, transfer rate in kbit/s.
# A medium takes the geometry of the entry matching its block count.
# Medium type codes are UFI table 17, which names only these three.
FLOPPY_BLOCK_SIZE = 512
FLOPPY_FORMATS = (
    (FLOPPY_2_88M, 5760, 0x00, 80, 2, 36, 1000),
    (FLOPPY_1_44M, 2880, 0x94, 80, 2, 18, 500),
    (FLOPPY_1_2M, 2400, 0x93, 80, 2, 15, 500),
    (FLOPPY_720K, 1440, 0x1E, 80, 2, 9, 250),
    (FLOPPY_360K, 720, 0x00, 40, 2, 9, 250),
)
_FLOPPY_DEFAULT = 1  # index of 1.44M: the geometry an odd size borrows

FLOPPY_RPM = 300  # what every 3.5" drive spins at


def floppy_format_size(fmt):
    """Byte size of a standard floppy format (one of the FLOPPY_* codes), for sizing
    a backing image, or 0 if it is not one of them. Mirrors the C
    msc_floppy_format_size()."""
    for code, blocks, *_ in FLOPPY_FORMATS:
        if code == fmt:
            return blocks * FLOPPY_BLOCK_SIZE
    return 0


def _floppy_format(blocks, block_size):
    """The standard format a medium of `blocks` x `block_size` bytes is, or None
    for a size no standard format has - the caller then borrows 1.44M's geometry
    and reports the real cylinder count, so an odd image still enumerates."""
    if block_size != FLOPPY_BLOCK_SIZE:
        return None
    for entry in FLOPPY_FORMATS:
        if entry[1] == blocks:
            return entry
    return None


# sense (key, asc, ascq)
# fmt: off
SENSE_OK              = (0x00, 0x00, 0x00)
SENSE_NOT_READY       = (0x02, 0x3A, 0x00)   # medium not present
SENSE_INVALID_COMMAND = (0x05, 0x20, 0x00)
SENSE_INVALID_FIELD   = (0x05, 0x24, 0x00)
SENSE_LBA_RANGE       = (0x05, 0x21, 0x00)
SENSE_LUN_NOT_SUPPORTED = (0x05, 0x25, 0x00)  # no such logical unit
SENSE_WRITE_PROTECT   = (0x07, 0x27, 0x00)
# fmt: on

# The Bulk-Only Transport addresses a unit with the four low bits of the CBW's
# bCBWLUN byte, so sixteen units is the transport's own ceiling.
MAX_LUNS = 16
CBW_LUN_MASK = 0x0F


class Lun:
    """One logical unit: its store, how it presents itself, and its own sense data.

    Each presentation attribute is taken from the store when it carries one and
    from the interface otherwise, so a device can mix media - a disk beside its
    install CD - without a second interface. Sense is per unit, as SCSI requires:
    a failure on one LUN must not answer another LUN's REQUEST SENSE."""

    def __init__(self, store, iface):
        self.store = store
        self.medium = getattr(store, "medium", None) or iface.medium
        self.read_only = (
            bool(getattr(store, "read_only", False))
            or iface.read_only
            or self.medium == MEDIUM_CDROM  # a CD-ROM is read-only by definition
        )
        self.product = (
            getattr(store, "product", None)
            or iface.product
            or {MEDIUM_CDROM: "CD-ROM", MEDIUM_FLOPPY: "FLOPPY"}.get(self.medium, "DISK")
        )
        self.serial = getattr(store, "serial", None) or iface.serial
        self.sense = SENSE_OK


class MSC(Interface):
    bInterfaceClass = 0x08  # Mass Storage
    bInterfaceSubClass = 0x06  # SCSI transparent command set (0x04 = UFI, see `ufi`)
    bInterfaceProtocol = 0x50  # Bulk-Only Transport
    # One endpoint NUMBER per direction (0x82 IN, not 0x81): WCH USBFS cores only
    # double-buffer a number used in ONE direction. A preference; composites relocate.
    # Mirrors the C library's src/classes/device/msc.c, IN-first.
    in_ep = In(0x82, "bulk", mps=64)
    out_ep = Out(0x01, "bulk", mps=64)

    def __init__(
        self,
        stores,
        *,
        read_only=False,
        medium=MEDIUM_DISK,
        ufi=False,
        on_command=None,
        vendor="USB-IP",
        product=None,
        revision="0001",
        serial="0123456789ABCDEF",
        name=None,
    ):
        """`stores` is one block store, or a list of them - one logical unit each.
        `medium` is "disk" (the default), "cdrom" or "floppy", and with several
        units it is the default a store can override with a `medium` attribute of
        its own (as it can `read_only`, `product` and `serial`). `ufi` declares
        the UFI command set (bInterfaceSubClass 0x04, SFF-8070i) instead of SCSI
        transparent (0x06) - the subclass a real USB floppy drive reports, and
        what makes Windows show the drive as a floppy rather than a removable
        disk. It describes the interface, so it covers every unit. The transport
        stays Bulk-Only either way; UFI-over-CBI is not offered. The UFI commands
        themselves (FORMAT UNIT, READ/WRITE(12), VERIFY, SEEK, REZERO UNIT) are
        answered whatever the subclass, as they are legal SCSI too."""
        super().__init__()
        if medium not in _MEDIA:
            raise ValueError(f"medium must be one of {_MEDIA}, not {medium!r}")
        self.medium = medium
        self.ufi = ufi
        if ufi:
            self.bInterfaceSubClass = 0x04
        self.read_only = read_only
        self.on_command = on_command or (lambda text: None)
        self.vendor = vendor
        self.product = product
        self.revision = revision
        self.serial = serial  # INQUIRY EVPD page 0x80, which Windows asks for
        self.name = name  # iInterface label, mirrors C msc_opts.name
        stores = list(stores) if isinstance(stores, (list, tuple)) else [stores]
        if not 1 <= len(stores) <= MAX_LUNS:
            raise ValueError(f"a mass-storage interface carries 1..{MAX_LUNS} units")
        self.luns = [Lun(store, self) for store in stores]
        self._cbw_in = False  # the current command's data phase is device->host
        # The transport is shared by every unit, so a write in flight names the
        # unit it lands on: dict(lun,tag,lba,count,need,buf)
        self._write = None

    def _on_added(self):
        if self.name:
            self.string_index = self.dev.add_string(self.name)

    def adjust_for_speed(self, speed):
        self.in_ep.mps = self.out_ep.mps = 512 if speed >= SPEED_HIGH else 64  # HS bulk = 512

    # ---- class control requests ----
    def on_control(self, setup, data=b""):
        if setup.bRequest == GET_MAX_LUN:  # the LAST unit's number
            return bytes([len(self.luns) - 1])
        if setup.bRequest == 0xFF:  # Bulk-Only Mass Storage Reset
            self._write = None
            return b""
        raise Stall

    def _log(self, lun, text):
        """A disk with several units names the one it is talking about, up front,
        so a grouped logger keys per unit; a lone unit logs as it always did."""
        self.on_command(f"lun{lun} {text}" if len(self.luns) > 1 else text)

    # ---- Bulk-Only Transport ----
    def on_out(self, ep, data):
        if self._write is not None:
            self._recv_write(data)
        else:
            self._handle_cbw(bytes(data))

    def _handle_cbw(self, cbw):
        if len(cbw) != 31 or cbw[:4] != CBW_SIGNATURE:
            return  # invalid CBW: drop (host will reset)
        tag = cbw[4:8]
        dlen = int.from_bytes(cbw[8:12], "little")
        self._cbw_in = bool(cbw[12] & 0x80)  # bmCBWFlags bit 7: data phase is IN
        lun = cbw[13] & CBW_LUN_MASK
        cblen = cbw[14] & 0x1F
        cdb = cbw[15 : 15 + cblen]
        if lun >= len(self.luns):
            # No such unit. The sense goes on unit 0 -- the one addressed does not
            # exist to hold it, and unit 0 is where a host looks after this CSW.
            name = _NAMES.get(cdb[0], f"SCSI 0x{cdb[0]:02X}") if cdb else "SCSI"
            self._fail(0, tag, dlen, SENSE_LUN_NOT_SUPPORTED, f"lun{lun} {name}")
            return
        self._dispatch(lun, tag, dlen, cdb)

    def _csw(self, tag, residue, status):
        self.in_ep.write(CSW_SIGNATURE + tag + int(residue).to_bytes(4, "little") + bytes([status]))

    def _stall_data_phase(self, dlen):
        """Abandon the data phase of a command that produced none of the data it
        promised.

        The host has a bulk URB outstanding for that data. Sending only the CSW
        hands it to THAT URB, and the host's next read - the one that actually
        wants the CSW - is never answered: Windows waits out its 20 s timeout and
        resets the port, so the disk takes minutes to appear. Halting the pipe
        ends the data phase the way BOT 1.0 section 6.7 case 4 ("Hi > Dn")
        prescribes; the host clears the halt and reads the CSW queued behind it. A
        short-but-nonempty phase needs none of this - the data completes the URB."""
        if not dlen:
            return  # nothing promised, no data phase
        (self.in_ep if self._cbw_in else self.out_ep).stall()

    def _data_in(self, tag, dlen, data):
        send = data[:dlen]
        if send:
            self.in_ep.write(send)
        else:
            self._stall_data_phase(dlen)
        self._csw(tag, dlen - len(send), 0)  # 0 = passed

    def _fail(self, lun, tag, dlen, sense, label):
        self.luns[lun].sense = sense
        self._log(lun, f"{label} -> CHECK CONDITION {sense[1]:#04x}/{sense[2]:#04x}")
        self._stall_data_phase(dlen)
        self._csw(tag, dlen, 1)  # 1 = failed

    def _dispatch(self, lun, tag, dlen, cdb):
        op = cdb[0]
        name = _NAMES.get(op, f"SCSI 0x{op:02X}")
        unit = self.luns[lun]
        store = unit.store
        bs = store.block_size

        if op == OP_TEST_UNIT_READY:
            self._log(lun, f"{name} -> OK")
            self._csw(tag, 0, 0)
        elif op == OP_INQUIRY:
            if cdb[1] & 0x01:  # vital product data page
                page = self._vpd(unit, cdb[2])
                if page is None:
                    self._fail(lun, tag, dlen, SENSE_INVALID_FIELD, name)
                else:
                    self._log(lun, f"{name} page {cdb[2]:#04x} -> {len(page)} B")
                    self._data_in(tag, dlen, page)
            else:
                self._log(lun, f"{name} -> {self.vendor.strip()} {unit.product.strip()}")
                self._data_in(tag, dlen, self._inquiry(unit))
        elif op == OP_READ_FORMAT_CAPACITIES:
            self._log(lun, f"{name} -> {store.num_blocks} x {bs}")
            self._data_in(tag, dlen, self._format_capacities(unit))
        elif op == OP_READ_CAPACITY_10:
            last = store.num_blocks - 1
            self._log(
                lun,
                f"{name} -> {store.num_blocks} x {bs} ({store.num_blocks * bs // 1024} KiB)",
            )
            self._data_in(tag, dlen, last.to_bytes(4, "big") + bs.to_bytes(4, "big"))
        elif op == OP_SERVICE_ACTION_IN_16 and (cdb[1] & 0x1F) == 0x10:
            last = store.num_blocks - 1
            self._log(lun, f"{name} -> {store.num_blocks} x {bs}")
            self._data_in(tag, dlen, last.to_bytes(8, "big") + bs.to_bytes(4, "big") + bytes(20))
        elif op in (OP_READ_10, OP_READ_12):
            lba, count = self._lba_count(cdb)
            if lba + count > store.num_blocks:
                self._fail(lun, tag, dlen, SENSE_LBA_RANGE, f"{name} lba={lba} count={count}")
            else:
                data = store.read(lba, count)
                self._log(lun, f"{name} lba={lba} count={count} -> OK ({len(data)}B)")
                self._data_in(tag, dlen, data)
        elif op in (OP_WRITE_10, OP_WRITE_12):
            lba, count = self._lba_count(cdb)
            if unit.read_only:
                self._fail(lun, tag, dlen, SENSE_WRITE_PROTECT, f"{name} lba={lba} count={count}")
            elif lba + count > store.num_blocks:
                self._fail(lun, tag, dlen, SENSE_LBA_RANGE, f"{name} lba={lba} count={count}")
            else:
                self._write = {
                    "lun": lun,
                    "tag": tag,
                    "lba": lba,
                    "count": count,
                    "name": name,
                    "need": count * bs,
                    "buf": bytearray(),
                }
        elif op == OP_REQUEST_SENSE:
            self._log(lun, name)
            self._data_in(tag, dlen, self._request_sense(unit))
        elif op in (OP_MODE_SENSE_6, OP_MODE_SENSE_10):
            page = cdb[2] & 0x3F
            data = self._mode_sense(unit, op == OP_MODE_SENSE_10, page)
            self._log(lun, f"{name} page {page:#04x} -> {len(data)} B")
            self._data_in(tag, dlen, data)
        elif op == OP_FORMAT_UNIT:
            # Nothing to lay down: the blocks exist and the host writes the
            # filesystem. Refuse on a write-protected medium, where "success" lies.
            if unit.read_only:
                self._fail(lun, tag, dlen, SENSE_WRITE_PROTECT, name)
            else:
                self._log(lun, f"{name} -> OK")
                self._csw(tag, 0, 0)
        elif op in (
            OP_PREVENT_ALLOW,
            OP_START_STOP_UNIT,
            OP_SYNC_CACHE_10,
            OP_REZERO_UNIT,
            OP_SEEK_10,
            OP_VERIFY_10,
        ):
            self._log(lun, f"{name} -> OK")
            self._csw(tag, 0, 0)
        elif op == OP_READ_TOC and unit.medium == MEDIUM_CDROM:
            self._log(lun, name)
            self._data_in(tag, dlen, self._read_toc())
        elif op == OP_GET_CONFIGURATION and unit.medium == MEDIUM_CDROM:
            self._log(lun, name)
            self._data_in(tag, dlen, self._get_configuration())
        elif op == OP_GET_EVENT_STATUS:
            self._data_in(tag, dlen, b"\x00\x06\x80\x00\x00\x00\x00\x00"[:dlen])
        else:
            self._fail(lun, tag, dlen, SENSE_INVALID_COMMAND, name)

    def _lba_count(self, cdb):
        """The LBA and block count of a READ/WRITE. The 12-byte commands keep the
        LBA where the 10-byte ones do and widen the count to four bytes."""
        lba = int.from_bytes(cdb[2:6], "big")
        if cdb[0] in (OP_READ_12, OP_WRITE_12):
            return lba, int.from_bytes(cdb[6:10], "big")
        return lba, int.from_bytes(cdb[7:9], "big")

    def _recv_write(self, data):
        pending = self._write
        pending["buf"] += data
        if len(pending["buf"]) >= pending["need"]:
            self.luns[pending["lun"]].store.write(pending["lba"], pending["count"], bytes(pending["buf"][: pending["need"]]))
            self._log(
                pending["lun"],
                f"{pending['name']} lba={pending['lba']} count={pending['count']} -> OK ({pending['need']}B)",
            )
            self._csw(pending["tag"], 0, 0)
            self._write = None

    # ---- SCSI data builders ----
    @staticmethod
    def _pdt(unit):
        """The SCSI peripheral device type. A floppy is a direct-access device
        like any other disk - only its geometry page and its subclass say
        otherwise."""
        return 0x05 if unit.medium == MEDIUM_CDROM else 0x00

    def _vpd(self, unit, page):
        """An INQUIRY EVPD page, or None for one we do not publish - the caller
        fails those, which is what page 0x00 tells the host to expect.

        Windows asks every removable device for the serial number page while it
        enumerates; Linux never does. The 4-byte header is device type, page code,
        reserved, page length."""
        if page == 0x00:  # supported pages, ascending
            data = bytes([0x00, 0x80])
        elif page == 0x80:  # unit serial number
            data = unit.serial.encode("ascii", "replace")[:20]
        else:
            return None
        return bytes([self._pdt(unit), page, 0x00, len(data)]) + data

    def _format_capacities(self, unit):
        """READ FORMAT CAPACITIES (SFF-8070i / UFI 4.9): a 4-byte capacity list
        header, then the current-capacity descriptor - block count, type, 3-byte
        block size - then zero or more FORMATTABLE descriptors in the same shape,
        whose type byte is reserved (zero). A floppy lists the standard formats
        there, which is what lets Windows offer a capacity to format to; every
        other medium sends the current capacity alone."""

        def desc(blocks, bs, kind):
            return blocks.to_bytes(4, "big") + bytes([kind]) + bs.to_bytes(3, "big")

        store = unit.store
        body = desc(store.num_blocks, store.block_size, 0x02)  # formatted media
        if unit.medium == MEDIUM_FLOPPY:  # ... and what it could be formatted to
            for _, blocks, *_rest in FLOPPY_FORMATS:
                body += desc(blocks, FLOPPY_BLOCK_SIZE, 0x00)
        return bytes([0, 0, 0, len(body)]) + body

    def _inquiry(self, unit):
        buf = bytearray(36)
        buf[0] = self._pdt(unit)  # peripheral device type
        buf[1] = 0x80  # RMB: removable
        buf[2] = 0x04  # SPC-2
        buf[3] = 0x02  # response data format
        buf[4] = 31  # additional length
        buf[8:16] = self.vendor.ljust(8)[:8].encode("ascii", "replace")
        buf[16:32] = unit.product.ljust(16)[:16].encode("ascii", "replace")
        buf[32:36] = self.revision.ljust(4)[:4].encode("ascii", "replace")
        return bytes(buf)

    def _request_sense(self, unit):
        key, asc, ascq = unit.sense
        unit.sense = SENSE_OK
        buf = bytearray(18)
        buf[0] = 0x70  # current error, fixed format
        buf[2] = key
        buf[7] = 10  # additional sense length
        buf[12] = asc
        buf[13] = ascq
        return bytes(buf)

    def _mode_sense(self, unit, long_header, page):
        """The mode parameter header, optionally followed by the page asked for.

        header(6):  length, medium type, device-specific, block-descriptor length
        header(10): 2-byte length, medium type, device-specific, ...
        The header's own length field counts everything after it."""
        wp = 0x80 if unit.read_only else 0x00
        medium_type = 0x00
        pages = b""
        if unit.medium == MEDIUM_FLOPPY:
            fmt = _floppy_format(unit.store.num_blocks, unit.store.block_size)
            medium_type = fmt[2] if fmt else 0x00
            if page in (0x05, 0x3F):  # Flexible Disk, or "every page"
                pages = self._flex_disk_page(unit)
        if long_header:  # MODE SENSE(10): 8-byte header
            hdr = bytearray([0, 0, medium_type, wp, 0, 0, 0, 0])
            hdr[0:2] = (len(hdr) + len(pages) - 2).to_bytes(2, "big")
        else:  # MODE SENSE(6): 4-byte header
            hdr = bytearray([0, medium_type, wp, 0])
            hdr[0] = len(hdr) + len(pages) - 1
        return bytes(hdr) + pages

    def _flex_disk_page(self, unit):
        """The Flexible Disk page (UFI 4.5, page code 0x05): 32 bytes, the 2-byte
        page header then the drive geometry. Everything not set here (head settle
        times, motor delays) stays zero - no host reads them off a device with no
        motor."""
        blocks, bs = unit.store.num_blocks, unit.store.block_size
        fmt = _floppy_format(blocks, bs)
        if fmt:
            _, _, _, cylinders, heads, sectors, rate = fmt
        else:  # an odd image: keep the capacity honest
            _, _, _, _, heads, sectors, rate = FLOPPY_FORMATS[_FLOPPY_DEFAULT]
            cylinders = min(blocks // (heads * sectors), 0xFFFF)
        buf = bytearray(32)
        buf[0] = 0x05  # page code
        buf[1] = 0x1E  # page length, i.e. the 30 bytes after it
        buf[2:4] = rate.to_bytes(2, "big")  # transfer rate, kbit/s
        buf[4] = heads
        buf[5] = sectors  # sectors per track
        buf[6:8] = bs.to_bytes(2, "big")  # data bytes per sector
        buf[8:10] = cylinders.to_bytes(2, "big")
        buf[28:30] = FLOPPY_RPM.to_bytes(2, "big")  # medium rotation rate
        return bytes(buf)

    # fmt: off
    def _read_toc(self):
        # one data track + lead-out, minimal "format 0" TOC
        return bytes([0x00, 0x12, 0x01, 0x01,
                      0x00, 0x14, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00,
                      0x00, 0x14, 0xAA, 0x00, 0x00, 0x00, 0x00, 0x00])
    # fmt: on

    def _get_configuration(self):
        # feature header: data length, reserved, current profile = 0x0008 (CD-ROM)
        return bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x08])
