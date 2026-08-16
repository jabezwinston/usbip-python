# Going remote

USB/IP is local by default, but a device served on one machine can be imported on
another - change one argument. This is the original purpose of USB/IP: a device
"plugged in" over the network.

**Serve** the device bound to a network address instead of loopback:

=== "Python"

    ```python
    import time, usbip
    from usbip.device import USBDevice
    from usbip.classes.device import CDCACM

    dev = USBDevice(0x1209, 0x0001, product="USBIP CDC-ACM")
    dev.add(CDCACM())                          # a (silent) serial port
    transport = usbip.USBIP("0.0.0.0", 3240)   # all interfaces, TCP :3240
    dev.plug(via=transport)
    time.sleep(3600)
    ```

=== "C"

    ```c
    #include <unistd.h>
    #include "usbip-device.h"
    #include "classes/cdc_acm.h"

    int main(void) {
        usbip_device *g = usbip_device_create(0x1209, 0x0001);
        cdc_acm_add(g, NULL);  /* a (silent) serial port */
        usb_transport *transport = usbip_transport("0.0.0.0", 3240);
        usbip_device_plug(g, transport);   /* serve on the network */
        for (;;) sleep(1);
    }
    ```

Then **import** it from another machine - either with the OS USB/IP client, or with a
USBIP [host](../host/host-driver.md):

```bash
# OS client: import the remote device into the local USB stack.
# Linux's client shown; see Platforms for the other OSes.
sudo usbip attach -r 10.0.0.5 -b 1-1
```

=== "Python"

    ```python
    import usbip
    h = usbip.open(0x1209, 0x0001, transport=usbip.USBIP("10.0.0.5", 3240))
    print("opened" if h else "not found")
    if h:
        h.close()
    ```

=== "C"

    ```c
    #include <stdio.h>
    #include "usbip-host.h"

    int main(void) {
        usbip_host_context *ctx;
        usbip_host_init(&ctx);
        usb_transport *transport = usbip_transport("10.0.0.5", 3240);
        usbip_host_set_transport(ctx, transport);   /* remote server */
        usbip_host_handle *h = usbip_host_open_vid_pid(ctx, 0x1209, 0x0001);
        printf(h ? "opened remote device\n" : "device not found\n");
        if (h) usbip_host_close(h);
        usbip_host_exit(ctx);
        return 0;
    }
    ```

Nothing else about your device or host code changes - the transport is the only
USB/IP-aware part of the API. See [Components → Transport](../components/transport.md).

!!! warning "Exposure"
    Serving on `0.0.0.0` makes the device reachable by anyone who can reach that port.
    Bind to a specific interface, or keep it on `127.0.0.1`, unless you intend remote
    access.
