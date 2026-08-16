# Troubleshooting

The problems people actually hit, in the order they usually hit them. For live
diagnostics, `USBIP_DEBUG=1` and `USBIP_PCAPNG` (see
[Capturing traffic](tools/capture.md)) answer most "what is it doing?"
questions.

## The client won't install or load

*Linux* - `modprobe: FATAL: Module vhci-hcd not found` means your kernel package
doesn't include the USB/IP client modules. On Debian/Ubuntu install
`linux-modules-extra-$(uname -r)`; the `usbip` tool itself is in `linux-tools-generic`
(or a distribution `usbip` package).

*Windows* - the client is third-party, and depending on the release its `vhci` driver
may be test-signed; it won't load until test signing is enabled. Follow the client
project's own installation notes ([Platforms → Windows](platforms/windows.md)).

*macOS* - there is no in-box client, and the experimental `usbip-macos` needs SIP
disabled. Use a [hardware client](platforms/hw-clients.md), serve from the Mac, or drive
the device with the [host API](host/host-driver.md)
([Platforms → macOS](platforms/macos.md)).

## `attach` fails

- **`connection refused`** - nothing is serving. Start the device program
  first; check it printed that it is listening, and that you attached to the
  right address.
- **Served on a non-default port?** The client assumes TCP 3240. For another port,
  Linux's `usbip` takes `usbip --tcp-port <port> attach -r <host> -b 1-1`.
- **`attach failed` with the server reachable** - the device may already be
  imported (`usbip port` on every machine that might have attached it; detach
  with `usbip detach -p <port>`), or an old client is speaking a different
  USB/IP protocol version.
- **Remote server unreachable** - the device must serve on `0.0.0.0`, not the
  loopback, and TCP 3240 must be open in the firewall
  ([Going remote](tools/remote.md)).

## `Address already in use` when the device starts

Another process is already listening on that port - a forgotten earlier run,
usually. One process serves one device; to serve several devices, run several
processes on different ports.

## The device attaches but doesn't work

- **It enumerates, but no driver binds** - `USBIP_DEBUG=1` on the server shows every
  request the host sent and what the device answered, which is usually enough to spot
  the descriptor the host disliked. The host's own log often names it outright:
  `dmesg | tail` on Linux, Device Manager's device status on Windows.
- **A `-> STALL` line in `USBIP_DEBUG` output** - either a request you chose
  not to answer (some are *supposed* to STALL, like a full-speed device's
  Device Qualifier) or a bug in your handler. See
  [Debugging unanswered requests](tools/capture.md#logging-transfers-without-a-capture).
- **Audio/video races far ahead of real time** - enable isochronous pacing:
  USB/IP has [no bus frame clock](platforms/hw-clients.md).
- **Windows doesn't install a driver for a vendor device** - advertise the
  Microsoft OS descriptors (`dev.enable_winusb()`) instead of hunting for an INF;
  see [Platforms → Windows](platforms/windows.md).

## Permissions

Importing a device is a privileged operation on every OS: root on Linux (loading
`vhci-hcd` and running `attach`/`detach`, or `CAP_NET_ADMIN` plus udev rules),
Administrator on Windows.

*Serving* a device never needs privilege - and for tests you can skip the OS entirely
and drive the device with the [host API](host/host-driver.md), which needs no client,
no kernel driver and no root on any platform.

## Still stuck?

Record a capture (`USBIP_PCAPNG=case.pcapng`, both sides if you can), open it in
Wireshark, and compare what the host asked with what the device answered - the
first mismatch is almost always the bug.
