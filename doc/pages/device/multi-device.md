# Multiple devices

One program can serve **several independent USB devices** at once. Plug each onto the
same transport and they share one listener: the host enumerates them all, attaches
them separately, and detaching one leaves the others alone.

This is the other way to ship two functions, and it is **not** the same as a
[composite device](composite.md):

| | Composite device | Several devices |
|---|---|---|
| What the host sees | **one** device, several interfaces | **two** devices, each with its own descriptors |
| Attach | one import | one per device (busids `1-1`, `1-2`) |
| Identity | one VID/PID | a VID/PID each |
| Endpoint space | shared - the core relocates collisions | independent (both can use `0x81`) |
| Detach one | not possible | the others keep running |

Reach for a composite when the functions belong to *one* product (a disk plus its
console). Reach for several devices when they are genuinely separate products that
happen to be emulated by one program.

=== "Python"

    ```python
    import usbip
    from usbip.device import USBDevice
    from usbip.classes.device import CDCACM, HID, hid

    kbd = USBDevice(0x1209, 0x0014, product="USBIP Keyboard")
    keyboard = kbd.add(HID(hid.keyboard_report_descriptor(),
                           subclass=hid.SUBCLASS_BOOT,
                           protocol=hid.PROTOCOL_KEYBOARD))

    ser = USBDevice(0x1209, 0x0015, product="USBIP Serial")
    ser.add(CDCACM(on_rx=on_rx, name="USBIP Serial"))

    transport = usbip.USBIP("0.0.0.0", 3240)
    kbd.plug(via=transport)          # busid 1-1
    ser.plug(via=transport)          # busid 1-2
    ```

=== "C"

    ```c
    usbip_device *kbd = usbip_device_create(0x1209, 0x0014);
    hid_opts hopts = { 
        .report_desc = RD_KEYBOARD, 
        .report_desc_len = sizeof(RD_KEYBOARD),
        .subclass = HID_SUBCLASS_BOOT,
        .protocol = HID_PROTOCOL_KEYBOARD
    };
    hid_iface *keyboard = hid_add(kbd, &hopts);

    usbip_device *ser = usbip_device_create(0x1209, 0x0015);
    cdc_acm_opts copts = { .on_rx = on_rx, .name = "USBIP Serial" };
    cdc_acm_add(ser, &copts);

    usb_transport *t = usbip_transport("0.0.0.0", 3240);
    usbip_device_plug(kbd, t);       /* busid 1-1 */
    usbip_device_plug(ser, t);       /* busid 1-2 */
    ```

Nothing else changes: each device is built exactly as it would be on its own.

Devices are named on the wire by **busid**, assigned `1-1`, `1-2`, … in plug order.
That is what an importer selects:

```console
$ usbip list -r 127.0.0.1
 - 127.0.0.1
        1-1: Generic : unknown product (1209:0014)
           :  0 - Human Interface Device / Boot Interface Subclass / Keyboard (03/01/01)

        1-2: Generic : unknown product (1209:0015)
           :  0 - Communications / Abstract (modem) / AT-commands (v.25ter) (02/02/01)
           :  1 - CDC Data / Unused / unknown protocol (0a/00/00)

$ sudo usbip attach -r 127.0.0.1 -b 1-1      # import the keyboard
$ sudo usbip attach -r 127.0.0.1 -b 1-2      # import the serial port
```

(Linux's client shown; every client takes a busid the same way - see
[Platforms](../platforms/index.md).) Each import is independent: the host binds a
separate driver to each device, exactly as if you had plugged in two.

Pin a name when plug order is not stable enough - call it **before** plugging:

=== "Python"

    ```python
    ser.set_busid("1-4")
    ser.plug(via=transport)
    print(ser.busid)                  # "1-4"
    ```

=== "C"

    ```c
    usbip_device_set_busid(ser, "1-4");
    usbip_device_plug(ser, t);
    printf("%s\n", usbip_device_get_busid(ser));   /* 1-4 */
    ```

Importing a busid nobody exports is **refused** - the importer gets an error rather
than some other device.

The host API resolves a busid for you when you name a device by VID/PID, and can list
what a server exports:

=== "Python"

    ```python
    for info in usbip.host.list_devices(transport=t):
        print(info["busid"], f"{info['idVendor']:04x}:{info['idProduct']:04x}")

    h = usbip.host.open(0x1209, 0x0015, transport=t)      # finds busid 1-2 itself
    ```

=== "C"

    ```c
    usbip_host_device **list;
    long n = usbip_host_get_device_list(ctx, &list);       /* every exported device */
    usbip_host_handle *h = usbip_host_open_vid_pid(ctx, 0x1209, 0x0015);
    ```

The C [libusb-1.0 drop-in](../c-reference.md) sees them the same way - a stock libusb
program lists both, each with its own bus/device address. Using Linux's `lsusb` and
`LD_PRELOAD` to demonstrate it:

```console
$ USBIP_PORT=3240 LD_PRELOAD=./libusb-1.0.so.0 lsusb
Bus 001 Device 002: ID 1209:0014 Generic
Bus 001 Device 003: ID 1209:0015 Generic
```

The listener is shared by address, so the second `plug()` on `0.0.0.0:3240` joins the
first one's socket instead of failing to bind. The `OP_REQ_DEVLIST` reply carries every
exported device, and `OP_REQ_IMPORT` selects one by busid.

Each **attached** device gets its own connection and its own serve thread, so two
devices under load do not queue behind one another; an unattached device costs no
thread at all. Unplugging one device leaves the listener serving the rest, and the last
one out closes it.

Runnable programs: `examples/multi_device.py` and, in C,
`examples/device/multi_device.c` - a HID keyboard and a CDC-ACM port from one
process. See [Examples](../examples.md).
