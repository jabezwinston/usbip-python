# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
USB/IP wire protocol - byte-compatible with the Linux kernel.

This is the ONLY module that touches sockets. Layouts follow
Documentation/usb/usbip_protocol.rst: all multi-byte header fields are
big-endian (network order); the 8-byte SETUP block and payloads are passed
through verbatim. Header is a fixed 48 bytes (20-byte basic + 28-byte union).
"""

from __future__ import annotations

import struct
import threading

from . import core, pcap
from .core import IN, OUT, Urb

# URB-phase commands
CMD_SUBMIT, CMD_UNLINK, RET_SUBMIT, RET_UNLINK = 1, 2, 3, 4

# isochronous packet descriptor: offset, length, actual_length, status (all BE u32)
_ISO = struct.Struct(">IIII")

# operation-phase op codes
VERSION = 0x0111
OP_REQ_DEVLIST, OP_REP_DEVLIST = 0x8005, 0x0005
OP_REQ_IMPORT, OP_REP_IMPORT = 0x8003, 0x0003

_USB_DEVICE = ">256s32sIIIHHHBBBBBB"  # struct usbip_usb_device (312 bytes)
_USB_DEVICE_LEN = struct.calcsize(_USB_DEVICE)


def _recvall(sock, want: int):
    buf = bytearray()
    while len(buf) < want:
        chunk = sock.recv(want - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


# ---- usbip_usb_device (carried by IMPORT/DEVLIST replies) -----------------
def pack_usb_device(dev) -> bytes:
    n_if = max(1, dev.num_interfaces)
    speed = core.WIRE_SPEED.get(getattr(dev, "speed", core.SPEED_FULL), 2)
    # busid/busnum/devnum are assigned when the device joins a listener; serving
    # alone - or packed directly by a test - keeps the lone-device values.
    busid = (getattr(dev, "busid", None) or "1-1").encode()
    return struct.pack(
        _USB_DEVICE,
        b"/sys/devices/platform/vhci_hcd.0/usb1/" + busid,
        busid,
        getattr(dev, "busnum", 0) or 1,  # busnum, devnum, speed
        getattr(dev, "devnum", 0) or 2,  # (kernel usb_device_speed)
        speed,
        dev.vid,
        dev.pid,
        dev.bcdDevice,
        getattr(dev, "device_class", 0),
        getattr(dev, "device_subclass", 0),
        getattr(dev, "device_protocol", 0),
        1,
        1,
        n_if,
    )  # class/sub/proto, cfgval, nCfg, nIf


def parse_usb_device(raw: bytes) -> dict:
    # leading underscores: fields of the wire layout this parser doesn't surface
    (_path, busid, busnum, devnum, speed, vid, pid, bcd, _dc, _sc, _pr, _cfgval, ncfg, nif) = (
        struct.unpack(_USB_DEVICE, raw)
    )
    return {
        "busid": busid.split(b"\0")[0].decode(errors="replace"),
        "busnum": busnum,
        "devnum": devnum,
        "speed": speed,
        "idVendor": vid,
        "idProduct": pid,
        "bcdDevice": bcd,
        "bNumConfigurations": ncfg,
        "bNumInterfaces": nif,
    }


# ---- device side: handshake + serve loop ---------------------------------
def _send_devlist(sock, devs):
    out = struct.pack(">HHI", VERSION, OP_REP_DEVLIST, 0)
    out += struct.pack(">I", len(devs))
    for dev in devs:
        out += pack_usb_device(dev)
        for cls, sub, proto in dev.interface_triples():
            out += struct.pack(">BBBB", cls, sub, proto, 0)
    sock.sendall(out)


def serve_handshake(sock, devs):
    """Returns the device the client imported, or None to end the connection.

    `devs` is one device or the list a listener exports; the importer picks one
    by busid, exactly as usbipd exports a bus.
    """
    if not isinstance(devs, (list, tuple)):
        devs = [devs]
    hdr = _recvall(sock, 8)
    if hdr is None:
        return None
    _version, code, _status = struct.unpack(">HHI", hdr)
    if code == OP_REQ_DEVLIST:
        _send_devlist(sock, devs)
        return None
    if code == OP_REQ_IMPORT:
        raw = _recvall(sock, 32)
        if raw is None:
            return None
        busid = raw.split(b"\0")[0].decode(errors="replace")
        for dev in devs:
            if (getattr(dev, "busid", None) or "1-1") == busid:
                sock.sendall(struct.pack(">HHI", VERSION, OP_REP_IMPORT, 0) + pack_usb_device(dev))
                return dev
        # No such bus. Silently importing the wrong device is worse than a failed
        # attach. Twin: handshake() in the C library's src/usbip.c.
        sock.sendall(struct.pack(">HHI", VERSION, OP_REP_IMPORT, 1))
        return None
    return None


def read_cmd(sock, dev=None):
    hdr = _recvall(sock, 48)
    if hdr is None:
        return None
    command, seqnum, devid, direction, ep = struct.unpack(">IIIII", hdr[:20])
    flags, length, _start, npkts, interval = struct.unpack(">Iiiii", hdr[20:40])
    setup = hdr[40:48]
    data = b""
    if direction == OUT and length > 0:
        data = _recvall(sock, length) or b""
    urb = Urb(command, seqnum, devid, direction, ep, flags, length, interval, setup, data)
    # isochronous descriptors follow, but only for iso endpoints (the wire's
    # number_of_packets is unreliable for non-iso transfers - may be 0/0xffffffff)
    if command == CMD_SUBMIT and npkts > 0 and dev is not None and dev.is_iso(ep, direction):
        raw = _recvall(sock, npkts * 16) or b""
        urb.number_of_packets = npkts
        urb.iso_packets = [list(_ISO.unpack_from(raw, i * 16)) for i in range(npkts)]
    return urb


def write_ret(sock, urb):
    hdr = struct.pack(">IIIII", RET_SUBMIT, urb.seqnum, urb.devid, urb.direction, urb.ep)
    if urb.iso_packets is not None:  # isochronous: de-padded data + descriptors
        npkts = len(urb.iso_packets)
        actual = sum(pkt[2] for pkt in urb.iso_packets)  # Σ per-packet actual_length
        hdr += struct.pack(">iiiii", urb.status, actual, 0, npkts, 0) + b"\x00" * 8
        sock.sendall(hdr)
        if urb.direction == IN and actual > 0:
            sock.sendall(urb.buffer[:actual])
        sock.sendall(b"".join(_ISO.pack(*pkt) for pkt in urb.iso_packets))
        return
    hdr += struct.pack(">iiiii", urb.status, urb.actual, 0, 0, 0) + b"\x00" * 8
    sock.sendall(hdr)
    if urb.direction == IN and urb.actual > 0:
        sock.sendall(urb.buffer[: urb.actual])


def write_ret_unlink(sock, urb):
    hdr = struct.pack(">IIIII", RET_UNLINK, urb.seqnum, urb.devid, urb.direction, urb.ep)
    hdr += struct.pack(">iiiii", 0, 0, 0, 0, 0) + b"\x00" * 8
    sock.sendall(hdr)


def _xfer_type(dev, ep, direction):
    """usbmon transfer_type for a device endpoint (ep0 is always control)."""
    if ep == 0:
        return pcap.XFER_CTRL
    entry = dev._ep_map.get((ep, direction))
    if entry is not None:
        return pcap.USBMON_XFER_BY_NAME.get(entry.type, pcap.XFER_BULK)
    return pcap.XFER_BULK


def serve_connection(sock, devs):
    # `devs` is one device or every device the listener exports; the handshake below
    # binds `dev` to the one this connection imported, so everything past it serves
    # exactly one device, as the kernel does.
    dev = None
    # The read loop must never block: Bluetooth keeps an interrupt-IN URB outstanding
    # for async HCI events while still issuing EP0 commands.
    # So handle_urb may PARK an IN URB (return None) and complete it later via
    # `respond` - the NAK-until-data semantics a real controller has.
    # `respond` may fire from another thread, so serialize socket writes with a lock.
    lock = threading.Lock()

    def respond(urb):
        with lock:
            try:
                write_ret(sock, urb)
            except OSError:
                pass
        if pcap.is_enabled():  # the universal completion point (incl. parked IN)
            ep_addr = urb.ep_addr
            if urb.direction == IN:
                in_data = urb.buffer if urb.iso_packets is not None else urb.buffer[: urb.actual]
            else:
                in_data = b""
            pcap.complete(
                urb.devid,
                _xfer_type(dev, urb.ep, urb.direction),
                ep_addr,
                urb.seqnum,
                urb.status,
                in_data,
                iso=urb.iso_packets,
            )

    try:
        dev = serve_handshake(sock, devs)
        if dev is None:
            return
        reset = getattr(dev, "reset_io", None)
        if callable(reset):
            reset()  # fresh connection: drop stale IN data/URBs
        while True:
            urb = read_cmd(sock, dev)
            if urb is None:
                break
            if urb.command == CMD_UNLINK:
                # urb.flags = the seqnum being unlinked. Drop any parked IN URB or iso
                # entry with that seqnum: completing an unlinked seqnum makes vhci
                # lose sync and disconnect the device.
                cancel = getattr(dev, "cancel_urb", None) or getattr(dev, "pace_cancel", None)
                if cancel:
                    cancel(urb.flags)
                with lock:
                    write_ret_unlink(sock, urb)
                continue
            if pcap.is_enabled():  # request half (setup + any OUT payload)
                ep_addr = urb.ep_addr
                setup = urb.setup if urb.ep == 0 else None
                out_data = urb.buffer if urb.direction == OUT else b""
                pcap.submit(
                    urb.devid,
                    _xfer_type(dev, urb.ep, urb.direction),
                    ep_addr,
                    urb.seqnum,
                    setup,
                    out_data,
                    urb.length,
                    iso=urb.iso_packets,
                )
            if dev.handle_urb(urb, respond) is not None:
                respond(urb)  # immediate; parked URBs return None
    except OSError:
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass


# ---- host side: client import + submit -----------------------------------
def _need(sock, want, what):
    """_recvall() or raise: the handshake replies below are all-or-nothing."""
    body = _recvall(sock, want)
    if body is None:
        raise core.NotFound(f"connection closed during {what}")
    return body


def _op_request(sock, code, what, payload=b""):
    """Send one OP_REQ_* (header + payload) and check the OP_REP status word."""
    sock.sendall(struct.pack(">HHI", VERSION, code, 0) + payload)
    _version, _code, status = struct.unpack(">HHI", _need(sock, 8, what))
    if status != 0:
        raise core.NotFound(f"{what} rejected (status {status})")


def client_import(sock, busid: bytes = b"1-1") -> dict:
    _op_request(sock, OP_REQ_IMPORT, "import", busid.ljust(32, b"\0"))
    raw = _need(sock, _USB_DEVICE_LEN, "import")
    return parse_usb_device(raw)


def client_devlist(sock) -> list:
    """Ask a USB/IP server what it exports -> a list of parse_usb_device() dicts,
    each with its `busid` (what client_import() takes). Twin: usbip_client_devlist()
    in the C library's src/usbip.c."""
    _op_request(sock, OP_REQ_DEVLIST, "devlist")
    devices = []
    for _ in range(struct.unpack(">I", _need(sock, 4, "devlist"))[0]):
        raw = _need(sock, _USB_DEVICE_LEN, "devlist")
        info = parse_usb_device(raw)
        # one 4-byte (class, subclass, protocol, pad) tuple per interface
        ifaces = []
        for _i in range(info["bNumInterfaces"]):
            ifaces.append(struct.unpack(">BBBB", _need(sock, 4, "devlist"))[:3])
        info["interfaces"] = ifaces
        devices.append(info)
    return devices


# Client-side endpoint-type map, for capture only. The USB/IP header carries no
# transfer type, so learn each endpoint's from the CONFIGURATION descriptors read
# on EP0. Byte-compatible twin: the C library's src/usbip.c.
_EPMAP_DEVS = 8
_epmap: dict = {}  # devid -> {(ep, direction): usbmon type}
# bmAttributes -> usbmon numbering, composed from the two by-name maps so each
# numbering is stated exactly once (core = bmAttributes, pcap = usbmon).
_EP_TYPE = tuple(
    pcap.USBMON_XFER_BY_NAME[name]
    for name, _value in sorted(core.XFER_BY_NAME.items(), key=lambda kv: kv[1])
)


def _epmap_learn(devid, cfg):
    """Walk a CONFIGURATION descriptor and record what every endpoint in it declared."""
    eps = {}
    for desc in core.iter_descriptors(cfg):
        if desc[1] == core.DT_ENDPOINT and len(desc) >= 7:
            ep = desc[2] & 0x0F
            direction = IN if desc[2] & 0x80 else OUT
            eps[(ep, direction)] = _EP_TYPE[desc[3] & 3]
    if not eps:
        return
    if devid not in _epmap and len(_epmap) >= _EPMAP_DEVS:
        _epmap.pop(next(iter(_epmap)))  # full: recycle the oldest device
    _epmap.setdefault(devid, {}).update(eps)


def _host_xfer_type(urb):
    """usbmon transfer_type as the client can tell it: ep0 is control, iso is detected
    by the descriptor list, the rest comes from the learned endpoint map, else from the
    polling interval (only interrupt endpoints are polled), else bulk."""
    if urb.ep == 0:
        return pcap.XFER_CTRL
    if urb.iso_packets is not None:
        return pcap.XFER_ISO
    known = _epmap.get(urb.devid, {}).get((urb.ep, urb.direction))
    if known is not None:
        return known
    return pcap.XFER_INTR if urb.interval > 0 else pcap.XFER_BULK


def _is_config_descriptor_reply(urb, status, data) -> bool:
    """Did this transfer just complete a GET_DESCRIPTOR(CONFIGURATION)? That is the one
    reply whose payload names every endpoint's type, so it is the only one worth handing
    to _epmap_learn(). Mirrors is_config_descriptor_reply() in C."""
    if urb.ep != 0 or urb.direction != IN or status != 0 or not data:
        return False
    # 0x80, 0x06: IN, GET_DESCRIPTOR
    return urb.setup[0] == 0x80 and urb.setup[1] == 0x06 and urb.setup[3] == core.DT_CONFIG


def client_submit(sock, urb: Urb) -> Urb:
    if urb.iso_packets is not None:
        return _client_submit_iso(sock, urb)
    cap = pcap.is_enabled()
    ep_addr = urb.ep_addr
    # Computed once, up front: _epmap_learn() below can change what _host_xfer_type()
    # answers, and both pcap halves of one URB must report the same type. The 0 is
    # unused, only there to keep the value an int when capture is off.
    xfer_type = _host_xfer_type(urb) if cap else 0
    if cap:  # request half
        setup = urb.setup if urb.ep == 0 else None
        out_data = urb.buffer[: urb.length] if urb.direction == OUT else b""
        pcap.submit(urb.devid, xfer_type, ep_addr, urb.seqnum, setup, out_data, urb.length)
    hdr = struct.pack(">IIIII", CMD_SUBMIT, urb.seqnum, urb.devid, urb.direction, urb.ep)
    hdr += struct.pack(">Iiiii", urb.flags, urb.length, 0, 0, urb.interval) + urb.setup
    sock.sendall(hdr)
    if urb.direction == OUT and urb.length > 0:
        sock.sendall(urb.buffer[: urb.length])
    rhdr = _recvall(sock, 48)
    if rhdr is None:
        raise core.USBError("connection closed during transfer")
    _command, _seqnum, _devid, _direction, _ep = struct.unpack(">IIIII", rhdr[:20])
    status, actual, _s, _n, _e = struct.unpack(">iiiii", rhdr[20:40])
    data = b""
    if urb.direction == IN and actual > 0:
        data = _recvall(sock, actual) or b""
    urb.status, urb.actual, urb.buffer = status, actual, data
    # Learn the endpoint types from this device's configuration, so the endpoints it
    # describes are captured as what they are instead of as bulk.
    if cap and _is_config_descriptor_reply(urb, status, data):
        _epmap_learn(urb.devid, data)
    if cap:  # response half
        pcap.complete(
            urb.devid, xfer_type, ep_addr, urb.seqnum, status, data if urb.direction == IN else b""
        )
    return urb


def _client_submit_iso(sock, urb: Urb) -> Urb:
    """Isochronous submit: send descriptors, read back de-padded data + descriptors,
    then scatter each packet into its slot (offset = the value we sent)."""
    pkts = urb.iso_packets
    if pkts is None:  # only client_submit routes here, and only for iso URBs
        raise core.USBError("isochronous submit without packet descriptors")
    npkts = len(pkts)
    total_len = sum(pkt[1] for pkt in pkts)  # Σ requested lengths
    cap = pcap.is_enabled()
    ep_addr = urb.ep_addr
    if cap:
        pcap.submit(
            urb.devid,
            pcap.XFER_ISO,
            ep_addr,
            urb.seqnum,
            None,
            urb.buffer if urb.direction == OUT else b"",
            total_len,
            iso=pkts,
        )
    hdr = struct.pack(">IIIII", CMD_SUBMIT, urb.seqnum, urb.devid, urb.direction, urb.ep)
    hdr += struct.pack(">Iiiii", urb.flags, total_len, 0, npkts, urb.interval) + urb.setup
    sock.sendall(hdr)
    if urb.direction == OUT:  # gather OUT data from the slots
        sock.sendall(b"".join(urb.buffer[pkt[0] : pkt[0] + pkt[1]] for pkt in pkts))
    sock.sendall(b"".join(_ISO.pack(pkt[0], pkt[1], 0, 0) for pkt in pkts))

    rhdr = _recvall(sock, 48)
    if rhdr is None:
        raise core.USBError("connection closed during iso transfer")
    status, actual, _s, rnp, _e = struct.unpack(">iiiii", rhdr[20:40])
    data = _recvall(sock, actual) if (urb.direction == IN and actual > 0) else b""
    rraw = _recvall(sock, rnp * 16) if rnp > 0 else b""

    buf = bytearray(max(total_len, urb.length))
    cum = 0
    for i in range(min(rnp, npkts)):
        off, length, al, st = _ISO.unpack_from(rraw, i * 16)
        pkts[i][:] = [off, length, al, st]
        if urb.direction == IN and al:
            buf[off : off + al] = data[cum : cum + al]
            cum += al
    urb.status, urb.actual, urb.buffer = status, actual, bytes(buf)
    if cap:
        pcap.complete(
            urb.devid,
            pcap.XFER_ISO,
            ep_addr,
            urb.seqnum,
            status,
            urb.buffer if urb.direction == IN else b"",
            iso=pkts,
        )
    return urb
