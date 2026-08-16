# Host & drivers

The host side drives a device - virtual or real - over the same transport.

## Handle - libusb-shaped transfers

[`usbip.open()`][usbip.host.open] (or `usbip_host_open_vid_pid()` in C) returns a
[`Handle`][usbip.host.Handle] with control / bulk / interrupt / isochronous transfers.
Porting libusb code is essentially `s/libusb_/usbip_host_/`.

```python
h = usbip.open(0x1209, 0x0001)
h.control(0x80, 0x06, 0x0100, 0, 18)   # GET_DESCRIPTOR(device)
h.bulk_out(0x01, b"...")               # OUT
data = h.bulk_in(0x81, 64)             # IN
h.close()
```

Isochronous transfers use the libusb alloc/fill/submit model
(`usbip_host_alloc_transfer` / `usbip_host_fill_iso_transfer` / `usbip_host_submit_transfer` /
`iso_packet_desc[]` in C; `iso_in` / `iso_out` on the Python `Handle`), submitted
synchronously.

## Driver - bind by class

For something reusable, a [`Driver`][usbip.host.Driver] (Python) or
[`usbip_host_driver`](../c-reference.md) (C) binds to a device by class code. The bundled
host drivers open with `Driver.open()`:

```python
from usbip.classes.host.mtp import MtpHost

with MtpHost.open(0x1209, 0x0009) as mtp:
    ...                                # browse / pull / push files
```

See [Host driver](../host/host-driver.md), the [Host API](../api/host.md)
and [Host drivers](../api/classes-host.md).
