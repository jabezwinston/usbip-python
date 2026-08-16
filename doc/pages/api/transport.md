# USB/IP transport

The wire under every device/host call. Everything is **local by default**; name a
transport only when you go remote with [`USBIP`][usbip.transport.USBIP], or use
[`Loopback`][usbip.transport.Loopback] for in-process tests.
[`use()`][usbip.transport.use] sets a process-wide default, and
[`Transport`][usbip.transport.Transport] is the base class for carrying USB/IP over
something else.

::: usbip.transport
    options:
      members:
        - USBIP
        - Loopback
        - Transport
        - use
