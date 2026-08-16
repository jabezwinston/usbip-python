# Composite devices

Adding two class functions to one device makes it **composite** - the bundled
`msc_cdc_device` example is a disk *plus* a serial console for it, and the C-only
`cdc_dfu_device` is a serial port plus firmware upgrade. The host sees both functions
at once - one plug, a drive **and** a serial port.

Declare the device composite **before** adding the functions, so the classes emit the
Interface Association Descriptors that Windows needs to keep each function's
interfaces together:

=== "Python"

    ```python
    import usbip
    from usbip import USBDevice
    from usbip.classes.device import CDCACM, MSC

    dev = USBDevice(0x1209, 0x0005, product="USBIP MSC+CDC")
    dev.set_composite()                  # BEFORE adding the functions

    console = dev.add(CDCACM(on_rx=on_rx, name="Disk console"))
    disk = dev.add(MSC(store))           # the block backend from the MSC recipe

    transport = usbip.USBIP("0.0.0.0", 3240)
    dev.plug(via=transport)
    ```

=== "C"

    ```c
    usbip_device *dev = usbip_device_create(0x1209, 0x0005);
    usbip_device_set_composite(dev);     /* BEFORE adding the functions */

    cdc_acm_opts cops = { .on_rx = on_rx, .name = "Disk console" };
    cdc_port *console = cdc_acm_add(dev, &cops);

    msc_opts mops = { /* block callbacks over the image file */ };
    msc_disk *disk = msc_add(dev, &mops);

    usbip_device_plug(dev);
    ```

Each class asks for the endpoint addresses it would use alone, so on one device they
collide - the core relocates the later ones onto free numbers, and each class keeps
the pipe it was handed. You never assign endpoint addresses yourself.

How functions group interfaces, when the `EF/02/01` composite device class triple is
required, and how per-function Microsoft OS descriptors behave on a composite device
are covered in
[Components → functions & interfaces](../components/device.md).

A composite is one device with several functions. To serve several **separate**
devices from one program instead - each with its own VID/PID, busid and attach - see
[Multiple devices](multi-device.md).

Runnable program: `examples/cdc_hid_device.py` (and its C twin) - a serial port plus a
HID consumer control (media keys) on one device, and the pair the cross-language test
holds byte-identical.
See [Examples](../examples.md).
