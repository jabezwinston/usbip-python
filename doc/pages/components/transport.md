# Transport

The transport is the only layer that knows about the USB/IP wire. Every device and
host entry point takes an **optional** transport; omit it (or pass `None`) and you get
the default local transport - a loopback to the host kernel on `127.0.0.1:3240`.

The device side **listens** on the transport and the host side **connects** to it,
following the [USB/IP roles](../getting-started.md).

## Local (default)

=== "Python"

    ```python
    dev.plug()                       # local
    h = usbip.open(0x1209, 0x0001)   # local
    ```

=== "C"

    ```c
    usbip_device_plug(g, NULL);              /* local */
    usbip_host_init(&ctx);                 /* local; or usbip_host_set_transport(ctx, NULL) */
    ```

## Remote (TCP)

Name a [`USBIP`][usbip.transport.USBIP] transport to bind/serve on a specific address
or to reach a remote server. One process serves **one device**; to export several
devices, run several processes on different ports.

=== "Python"

    ```python
    serve_on = usbip.USBIP("0.0.0.0", 3240)
    dev.plug(via=serve_on)                                 # serve

    connect_to = usbip.USBIP("10.0.0.5", 3240)
    h = usbip.open(0x1209, 0x0001, transport=connect_to)   # connect
    ```

=== "C"

    ```c
    usb_transport *serve_on = usbip_transport("0.0.0.0", 3240);
    usbip_device_plug(g, serve_on);                        /* serve */

    usb_transport *connect_to = usbip_transport("10.0.0.5", 3240);
    usbip_host_set_transport(ctx, connect_to);             /* connect */
    ```

## Loopback (tests)

[`Loopback`][usbip.transport.Loopback] / `usbip_loopback()` connects a device and a
host **in the same process** - no sockets, no kernel, no root. The test suite uses this
to drive a device from a host directly, including across languages.

## Defaults and custom transports

`usbip.transport.use(t)` sets the process-wide default transport, picked up by any
call that doesn't name one. To carry USB/IP over something other than TCP, subclass
[`Transport`][usbip.transport.Transport] and implement `serve()` / `connect()`.

See [Going remote](../tools/remote.md), [capturing traffic](../tools/capture.md)
and the [Transport API](../api/transport.md).
