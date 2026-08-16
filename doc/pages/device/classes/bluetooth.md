# Bluetooth Dongle (HCI)

The Bluetooth class is a USB **HCI transport**: it carries HCI commands, events and
ACL data between the USB host and a controller *you* provide. No HCI logic lives in
the class - you implement (or reuse) the controller and respond to the host's
commands. Once attached, the OS's Bluetooth stack (BlueZ on Linux) binds it like a
real radio.

## An HCI transport shell

=== "Python"

    ```python
    import time, usbip
    from usbip.device import USBDevice
    from usbip.classes.device import Bluetooth

    def on_command(cmd):          # HCI command from the host (EP0)
        pass                      # parse, then fn.send_event(event)

    def on_acl(pdu):              # ACL data from the host (bulk OUT)
        pass

    dev = USBDevice(0x1209, 0x000C, product="USBIP BT")
    fn = dev.add(Bluetooth(on_command=on_command, on_acl=on_acl))  # fn.send_event() / .send_acl()
    dev.plug()
    time.sleep(3600)              # serve (Ctrl-C to stop)
    ```

=== "C"

    ```c
    #include <unistd.h>
    #include "usbip-device.h"
    #include "classes/bluetooth.h"

    /* wire these to a controller - the example ships a full one */
    static void on_command(bt_hci *f, const uint8_t *cmd, int len) {
        (void)f; (void)cmd; (void)len;
    }

    static void on_acl(bt_hci *f, const uint8_t *pdu, int len) {
        (void)f; (void)pdu; (void)len;
    }

    int main(void) {
        usbip_device *g = usbip_device_create(0x1209, 0x000C);
        bt_opts opts = {
            .on_command = on_command,
            .on_acl     = on_acl,
        };
        bluetooth_add(g, &opts);
        usbip_device_plug(g, NULL);
        for (;;) sleep(1);
    }
    ```

## Verify it

Once a [client](../../platforms/index.md) has imported the device, the host's own
Bluetooth stack binds the HCI transport and starts driving your controller.

=== "Linux"

    `btusb` binds it as an `hciN` device:

    ```bash
    hciconfig -a
    bluetoothctl scan on
    ```

=== "Windows"

    The in-box Bluetooth stack binds it as a Bluetooth radio - Settings → Bluetooth &
    devices.

API: [Bluetooth](../../api/classes-device.md#bluetooth-hci-transport).
