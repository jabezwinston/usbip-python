# Serial Port (CDC-ACM)

A CDC-ACM function appears to the OS as an ordinary serial port, and the host's in-box
serial driver binds it - no driver install anywhere. The class runs the CDC protocol;
your app supplies callbacks (port opened/closed, line coding changed, bytes received)
and transmits back with the port's `write()` / `cdc_acm_send()`. This example echoes
whatever it receives.

## An echoing serial port

=== "Python"

    ```python
    import time, usbip
    from usbip.device import USBDevice
    from usbip.classes.device import CDCACM

    def on_rx(port, data):
        port.write(bytes(data))             # echo back to the host

    dev = USBDevice(0x1209, 0x0001, product="USBIP CDC-ACM")
    dev.add(CDCACM(on_rx=on_rx))
    dev.plug()                              # serve it; -> a serial port
    time.sleep(3600)                        # serve (Ctrl-C to stop)
    ```

=== "C"

    ```c
    #include <unistd.h>            /* sleep() */
    #include "usbip-device.h"
    #include "classes/cdc_acm.h"

    static void on_rx(cdc_port *port, void *user, const void *data, int len) {
        (void)user;
        cdc_acm_send(port, data, len);            /* echo back to the host */
    }

    int main(void) {
        usbip_device *g = usbip_device_create(0x1209, 0x0001);
        usbip_device_set_strings(g, "USB over IP", "USBIP CDC-ACM", "0001");

        cdc_acm_opts opts = {
            .on_rx = on_rx,
        };
        cdc_acm_add(g, &opts);

        usbip_device_plug(g, NULL);    /* serve it; -> a serial port */
        for (;;) sleep(1);             /* serve until Ctrl-C    */
    }
    ```

## Talk to it from the host

Once a [client](../../platforms/index.md) has imported the device, the host's serial
driver binds it and a port appears. Open that port with any terminal program and type -
whatever you send comes straight back.

=== "Linux"

    The kernel's `cdc_acm` driver binds it as `/dev/ttyACM*`:

    ```bash
    stty -F /dev/ttyACM0 raw -echo
    echo hi > /dev/ttyACM0 ; cat /dev/ttyACM0     # or: picocom /dev/ttyACM0
    ```

    pyserial works just as well.

=== "Windows"

    `usbser.sys` binds it as a **COM port** - Device Manager → Ports gives the number.
    Open it in PuTTY, Tera Term or any terminal.

!!! tip "No client, no OS"
    You can skip the operating system altogether and drive the endpoints directly with
    the [host API](../../host/host-driver.md) - no client, no kernel driver, no root.
    That works identically on all three platforms, and is how the tests exercise this
    class.

## The other callbacks

The other callbacks tell you what the host did: `on_open` / `on_close` fire when an
application opens or closes the port (the host asserts/clears the **DTR** control
line), and `on_line_coding` reports baud-rate / parity / stop-bit changes - see the
[USB concepts](../../concepts.md) primer for the underlying control-transfer vocabulary.

Full program: `examples/cdc_acm_device.py`; in C,
`examples/device/cdc_acm_device.c`. API:
[CDC-ACM](../../api/classes-device.md#cdc-acm-virtual-serial-port).
