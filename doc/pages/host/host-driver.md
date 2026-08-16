# Host driver

The **host** side drives a USB device instead of being one. It talks USB/IP itself, so
it needs **no kernel driver, no `vhci` and no root** - which makes it ideal for tests:
serve a device in one process and drive it from another, on any OS.

In USB/IP terms this side is the **client**
([which end is the server](../getting-started.md)): something
must already be serving before any of the calls below can succeed - a program on the
device API, or a real `usbipd` exporting genuine hardware.

This imports a device, reads its descriptor over a control transfer, and round-trips a
payload through its bulk endpoints. Run `vendor_device.py` first, then this against it.

```python title="examples/doc/host_drive.py"
--8<-- "examples/doc/host_drive.py"
```

Start the device in one terminal and the driver in another:

```bash
python3 examples/vendor_device.py       # the device being driven
python3 examples/doc/host_drive.py      # in a second terminal
```

[`usbip.open()`][usbip.host.open] / `usbip_host_open_vid_pid()` return a handle with the
familiar control / bulk / interrupt / iso transfers.

=== "Python"

    ```python
    import usbip

    h = usbip.open(0x1209, 0x0001)                      # local by default
    desc = h.control(0x80, 0x06, 0x0100, 0, 18)         # GET_DESCRIPTOR(device)
    h.bulk_out(0x01, b"ping\n")                         # OUT
    echo = h.bulk_in(0x81, 64)                          # IN
    h.close()
    ```

=== "C"

    ```c
    #include <stdio.h>
    #include "usbip-host.h"

    int main(void) {
        usbip_host_context *ctx;
        usbip_host_init(&ctx);                               /* local: 127.0.0.1:3240 */
        usbip_host_handle *h = usbip_host_open_vid_pid(ctx, 0x1209, 0x0001);
        if (!h) { 
            fprintf(stderr, "device not found\n");
            return 1;
        }

        usb_device_descriptor d;
        usbip_host_control_transfer(h, 0x80, 0x06, 0x0100, 0, (uint8_t *)&d, 18, 1000);

        int n; uint8_t echo[64];
        usbip_host_bulk_transfer(h, 0x01, (uint8_t *)"ping\n", 5, &n, 1000);   /* OUT */
        usbip_host_bulk_transfer(h, 0x81, echo, sizeof(echo), &n, 1000);        /* IN  */
        printf("%04x:%04x, %d echo bytes\n", d.idVendor, d.idProduct, n);

        usbip_host_close(h);
        usbip_host_exit(ctx);
        return 0;
    }
    ```

A STALLed transfer raises `Stall` (`USB_ERROR_PIPE` in C); recover the pipe with
`handle.clear_halt(addr)`.

The same code drives genuine hardware that a Linux box exports with the kernel's own
`usbipd` - the API neither knows nor cares that the device is physical, and your driver
still runs on any OS. Exporting real hardware is the one step that is Linux-only, since
`usbipd`/`usbip bind` have no equivalent elsewhere; run it on the machine holding the
device:

```bash
sudo modprobe usbip-host
sudo usbipd -D                       # the USB/IP server daemon
usbip list -l                        # find the bus id, e.g. 3-2
sudo usbip bind -b 3-2               # export it
```

Then import it by that bus id, without a vid/pid check:

```python
import usbip

h = usbip.attach("10.0.0.5", "3-2")          # remote host, the exported bus id
print(h.control(0x80, 0x06, 0x0100, 0, 18))  # its real device descriptor
```

Unlike the kernel's `usbip list -r`, the Python host has no device-list call: it imports
the bus id you name (`1-1` is what this library always serves).

For a real device class, subclass [`Driver`][usbip.host.Driver] (Python) or register a
[`usbip_host_driver`](../c-reference.md) (C). The bundled drivers - HID, CDC-ACM, MSC,
MTP, Bluetooth - open with `Driver.open()`; see
[Host & drivers](../components/host.md#driver-bind-by-class) for the pattern.

If the program that should drive the device already exists - `dfu-util`, `lsusb`, a
libusbK or WinUSB application - it needs no porting and none of the API above: the
[C wrapper libraries](../c-reference.md) let a stock binary drive a virtual device
unmodified. For **pyusb** specifically - a Python caller pointed at the libusb
wrapper - see [pyusb](pyusb.md).

!!! tip "Mix languages"
    A C device can be driven by the Python host and vice-versa - they share the wire
    protocol. The test suite does exactly this for cross-language verification.

Full programs: `examples/doc/host_drive.py`; in C, `examples/host/cdc_host.c`,
`uvc_host.c`, `host_probe.c`. API: [Host API](../api/host.md) ·
[Host drivers](../api/classes-host.md).
