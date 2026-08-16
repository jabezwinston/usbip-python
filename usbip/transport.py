# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 04-June-2026
"""
Transports - the only place USB/IP addressing (host/port/socket) lives.

The top layer never names these directly: device.plug() and host.open() use a
default transport unless you pass `via=`/`transport=`.

  USBIP(host, port)  - real USB/IP over TCP (kernel-compatible)
  Loopback()         - in-process socketpair; same protocol code, no network
"""

from __future__ import annotations

import socket
import threading

from . import protocol

_default = None


def use(transport):
    """Set the process-wide default transport."""
    global _default
    _default = transport


def default_transport():
    global _default
    if _default is None:
        _default = USBIP()  # local USB/IP (127.0.0.1:3240)
    return _default


MAX_DEVICES = 16  # devices one listener can export


class _Listener:
    """One serving endpoint carrying one or more devices.

    A listener can export several devices at once, each named by its busid; the
    importer picks one, which is what usbipd does with a real bus. TCP listeners
    are shared by address (see `_listener_for`), so plugging a second device onto
    the same host:port joins the first one's socket instead of failing to bind.
    Twin: struct usbip_server in the C library's src/usbip.c.
    """

    def __init__(self, key=None):
        self.key = key  # (host, port) for TCP, None for loopback
        self.devices = []
        self.socks = []  # listening socket / loopback socketpairs
        self.lock = threading.Lock()
        self.next_devnum = 2  # the first device is 1-1 at address 2

    def register(self, dev):
        """Give `dev` its wire identity and add it. True if it is the first device
        (the caller then binds and starts accepting)."""
        with self.lock:
            if len(self.devices) >= MAX_DEVICES:
                raise ValueError(f"cannot export more than {MAX_DEVICES} devices on one listener")
            if not dev.busid:
                dev.busid = f"1-{len(self.devices) + 1}"
            dev.busnum = 1
            dev.devnum = self.next_devnum
            self.next_devnum += 1
            self.devices.append(dev)
            return len(self.devices) == 1

    def unregister(self, dev):
        """Remove `dev`; True when it was the last one (close up shop)."""
        with self.lock:
            if dev in self.devices:
                self.devices.remove(dev)
            return not self.devices

    def snapshot(self):
        """The exported devices - the accept path reads this while the app may
        still be plugging or unplugging on another thread."""
        with self.lock:
            return list(self.devices)

    def close(self):
        for sock in self.socks:
            try:
                sock.close()
            except OSError:
                pass
        self.socks = []


_listeners: dict = {}  # (host, port) -> _Listener
_listeners_lock = threading.Lock()


def _listener_for(host, port):
    """The listener bound to this address, creating it on first use."""
    key = (host or "0.0.0.0", port)
    with _listeners_lock:
        listener = _listeners.get(key)
        if listener is None:
            listener = _Listener(key)
            _listeners[key] = listener
        return listener


def _drop_listener(listener):
    with _listeners_lock:
        if listener.key is not None and _listeners.get(listener.key) is listener:
            del _listeners[listener.key]
    listener.close()


class Transport:
    def serve(self, dev):  # device side
        raise NotImplementedError

    def connect(self):  # host side -> connected socket
        raise NotImplementedError

    def stop(self, dev=None):
        pass


class Loopback(Transport):
    """Device and host in one process, wired by a socketpair (no kernel/network).

    Each connect() mints its own socketpair and serves the listener's devices on
    it, so several devices - and several sequential opens - behave exactly as
    they do over TCP.
    """

    def __init__(self):
        self._listener = _Listener()  # private: loopback has no address to share

    def serve(self, dev):
        self._listener.register(dev)

    def connect(self):
        dev_sock, host_sock = socket.socketpair()
        # keep only live sockets: an open/close cycle per connect() must not pile up
        self._listener.socks = [sock for sock in self._listener.socks if sock.fileno() != -1]
        self._listener.socks += [dev_sock, host_sock]
        threading.Thread(
            target=protocol.serve_connection,
            args=(dev_sock, self._listener.snapshot()),
            daemon=True,
        ).start()
        return host_sock

    def stop(self, dev=None):
        if dev is not None and not self._listener.unregister(dev):
            return  # other devices still on this listener
        self._listener.close()


class USBIP(Transport):
    """Real USB/IP over TCP. Serving binds a usbipd-compatible listener;
    connecting attaches like the kernel client / `usbip attach` does."""

    def __init__(self, host=None, port=3240):
        self.host = host
        self.port = port
        self._listener = None

    def serve(self, dev):
        listener = _listener_for(self.host, self.port)
        self._listener = listener
        if not listener.register(dev):
            return  # already bound; dev just joined it

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((self.host or "0.0.0.0", self.port))
            srv.listen(4)
        except OSError:
            srv.close()
            listener.unregister(dev)
            _drop_listener(listener)
            raise
        listener.socks.append(srv)

        def accept_loop():
            while True:
                try:
                    conn, _addr = srv.accept()
                except OSError:
                    break
                # Tiny request/response messages, and write_ret() sends header+data
                # in separate sendall()s; without TCP_NODELAY, Nagle plus the peer's
                # delayed-ACK stalls each IN reply ~40-250 ms.
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # the handshake picks which of these the connection imported
                threading.Thread(
                    target=protocol.serve_connection, args=(conn, listener.snapshot()), daemon=True
                ).start()

        threading.Thread(target=accept_loop, daemon=True).start()

    def connect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((self.host or "127.0.0.1", self.port))
        return sock

    def stop(self, dev=None):
        listener = self._listener
        if listener is None:
            return
        if dev is not None and not listener.unregister(dev):
            return  # other devices still on this listener
        _drop_listener(listener)
        self._listener = None
