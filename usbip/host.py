# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
Host side - write host drivers that drive USB devices over USB/IP.

open()/attach() return a Handle with libusb-shaped transfers. A reusable host
driver is a `Driver` subclass that auto-binds by class code (the mirror of a
device-side Interface).
"""

from __future__ import annotations

from . import protocol
from .core import IN, OUT, DeviceDescriptor, NotFound, Setup, Stall, Urb, USBError


class Connection:
    """One imported device: owns the socket and submits URBs."""

    def __init__(self, sock):
        self.sock = sock
        self.info = None
        self.devid = 0x00010002
        self._seq = 0

    def import_device(self, busid="1-1") -> dict:
        self.info = protocol.client_import(self.sock, busid.encode())
        self.devid = (self.info["busnum"] << 16) | self.info["devnum"]
        return self.info

    def _submit(self, urb: Urb) -> Urb:
        self._seq += 1
        urb.seqnum, urb.devid = self._seq, self.devid
        return protocol.client_submit(self.sock, urb)

    def control(self, bmRequestType, bRequest, wValue, wIndex, data_or_len):
        if bmRequestType & 0x80:  # IN
            length = int(data_or_len)
            setup = Setup(bmRequestType, bRequest, wValue, wIndex, length).pack()
            urb = self._submit(Urb(direction=IN, ep=0, setup=setup, length=length))
            if urb.status != 0:
                raise Stall(f"control status {urb.status}")
            return urb.buffer
        data = data_or_len or b""
        setup = Setup(bmRequestType, bRequest, wValue, wIndex, len(data)).pack()
        request = Urb(direction=OUT, ep=0, setup=setup, length=len(data), buffer=data)
        urb = self._submit(request)
        if urb.status != 0:
            raise Stall(f"control status {urb.status}")
        return urb.actual

    def transfer_in(self, ep_number, length, interval=0):
        urb = self._submit(Urb(direction=IN, ep=ep_number, length=length, interval=interval))
        if urb.status != 0:
            raise Stall(f"transfer status {urb.status}")
        return urb.buffer

    def transfer_out(self, ep_number, data, interval=0):
        request = Urb(direction=OUT, ep=ep_number, length=len(data), buffer=data, interval=interval)
        urb = self._submit(request)
        if urb.status != 0:
            raise Stall(f"transfer status {urb.status}")
        return urb.actual

    def iso_transfer(self, addr, packet_lengths, data=None, interval=0):
        """One isochronous transfer (libusb-shaped, synchronous). `packet_lengths`
        is the requested length per packet; for OUT `data` holds the packets laid
        out at their slot offsets. Returns (buffer, packets) where packets is a
        list of [offset, length, actual_length, status]."""
        iso, off = [], 0
        for length in packet_lengths:
            iso.append([off, length, 0, 0])
            off += length
        direction = IN if (addr & 0x80) else OUT
        urb = Urb(
            direction=direction, ep=addr & 0x0F, interval=interval, length=off, buffer=data or b""
        )
        urb.iso_packets = iso
        urb = self._submit(urb)
        return urb.buffer, urb.iso_packets

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Handle:
    """libusb-shaped device handle."""

    def __init__(self, conn: Connection, device_descriptor: DeviceDescriptor):
        self.conn = conn
        self.device_descriptor = device_descriptor

    def control(self, *args):
        return self.conn.control(*args)

    def bulk_in(self, addr, length):
        return self.conn.transfer_in(addr & 0x0F, length)

    def bulk_out(self, addr, data):
        return self.conn.transfer_out(addr & 0x0F, data)

    def clear_halt(self, addr):
        """Clear a halted (STALLed) endpoint: CLEAR_FEATURE(ENDPOINT_HALT).

        The standard recovery after a transfer raises :class:`Stall` - a device
        halts a pipe to abandon a transfer it cannot complete, and the pipe stays
        halted until this clears it."""
        return self.conn.control(0x02, 0x01, 0x0000, addr, b"")

    def interrupt_in(self, addr, length):
        # interval=1: a polled endpoint, which is also how a capture tells an
        # interrupt transfer from a bulk one (same as the C host API).
        return self.conn.transfer_in(addr & 0x0F, length, interval=1)

    def interrupt_out(self, addr, data):
        return self.conn.transfer_out(addr & 0x0F, data, interval=1)

    def iso_in(self, addr, packet_len, num_packets, interval=0):
        """Isochronous IN. Returns a list of bytes objects, one per packet (each
        trimmed to its actual_length)."""
        buf, pkts = self.conn.iso_transfer(
            (addr & 0x0F) | 0x80, [packet_len] * num_packets, interval=interval
        )
        return [buf[off : off + actual] for off, length, actual, status in pkts]

    def iso_out(self, addr, packets, interval=0):
        """Isochronous OUT. `packets` is a list of bytes objects, one per packet.
        Returns the per-packet [offset, length, actual_length, status] list."""
        data = b"".join(packets)
        _buf, pkts = self.conn.iso_transfer(
            addr & 0x0F, [len(pkt) for pkt in packets], data=data, interval=interval
        )
        return pkts

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _resolve_busid(transport, vid, pid) -> str:
    """Which bus to import when the caller named a device, not a bus. Falls back
    to "1-1" - the lone-device busid - if the server won't list (a real usbipd
    always will, but a bare importer test or an older server may not)."""
    if vid is None:
        return "1-1"
    try:
        exported = list_devices(transport)
    except (OSError, USBError):
        return "1-1"
    for info in exported:
        if info["idVendor"] == vid and info["idProduct"] == pid:
            return info["busid"]
    if len(exported) <= 1:
        return "1-1"  # let import + the vid/pid check report the mismatch
    raise NotFound(
        f"{vid:04x}:{pid:04x} not exported by the server "
        "(busids: %s)" % ", ".join(entry["busid"] for entry in exported)
    )


def list_devices(transport=None) -> list:
    """What the server exports: a list of dicts with `busid`, `idVendor`,
    `idProduct`, `interfaces`, ... - the `usbip list -r` view. Pass a `busid`
    from here to :func:`open`."""
    from .transport import default_transport

    resolved = transport or default_transport()
    sock = resolved.connect()
    try:
        return protocol.client_devlist(sock)
    finally:
        sock.close()


def open(vid=None, pid=None, *, busid=None, transport=None, set_config=1):
    """Import a device and return a Handle. With vid/pid given, verifies the
    match; without, accepts whatever `busid` resolves to (real devices).

    When a server exports several devices, vid/pid alone is enough: the busid is
    looked up with :func:`list_devices`. Passing `busid` skips that and imports
    it directly.
    """
    from .transport import default_transport

    resolved = transport or default_transport()
    if busid is None:
        busid = _resolve_busid(resolved, vid, pid)
    conn = Connection(resolved.connect())
    info = conn.import_device(busid)
    if vid is not None and (info["idVendor"] != vid or info["idProduct"] != pid):
        conn.close()
        raise NotFound(
            f"{vid:04x}:{pid:04x} not found (got {info['idVendor']:04x}:{info['idProduct']:04x})"
        )
    dd = DeviceDescriptor.parse(conn.control(0x80, 0x06, 0x0100, 0, 18))
    if set_config is not None:
        try:
            conn.control(0x00, 0x09, set_config, 0, b"")  # SET_CONFIGURATION
        except Stall:
            pass
    return Handle(conn, dd)


def attach(remote_host, busid, *, port=3240, vid=None, pid=None):
    """Convenience for real remote devices: attach(host, '1-1.4')."""
    from .transport import USBIP

    transport = USBIP(remote_host, port)
    return open(vid, pid, busid=busid, transport=transport)


# ---- reusable host drivers (auto-bind by class) --------------------------
_drivers: dict = {}


class Driver:
    matches: dict = {}

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        _drivers[cls.__name__] = cls

    def __init__(self, handle: Handle):
        self.handle = handle

    @classmethod
    def open(cls, vid=None, pid=None, *, busid="1-1", transport=None):
        return cls(open(vid, pid, busid=busid, transport=transport))

    def close(self):
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
