# USBDevice, functions & interfaces

A [`USBDevice`][usbip.device.USBDevice] is the virtual device: vendor/product IDs, strings,
speed, and one or more interfaces. Create it, add interfaces, then `plug()`.

=== "Python"

    ```python
    dev = USBDevice(0x1209, 0x0001, product="My Device", manufacturer="USB over IP", serial="0001")
    dev.set_speed(usbip.SPEED_HIGH)        # default is full speed
    ```

=== "C"

    ```c
    usbip_device *dev = usbip_device_create(0x1209, 0x0001);
    usbip_device_set_strings(dev, "USB over IP", "My Device", "0001");
    usbip_device_set_speed(dev, USB_SPEED_HIGH);     /* default is full speed */
    ```

Both libraries are split the same way: a class-free **device core** (`usbip.device` /
`usbip-device.h` - descriptors, requests, endpoint pipes) and a **class layer** on top
(`usbip.function` / `classes/usb_class.h` - interfaces and functions), built entirely
on the core's public API. You normally author with the class layer, shown next; a
core-only path exists too.

An [`Interface`][usbip.function.Interface] is one interface. It declares endpoints with
[`In`][usbip.device.In] / [`Out`][usbip.device.Out] (byte pipes), may emit
class-specific descriptors, and handles class/vendor control requests. Standard
requests are answered for you by the core.

=== "Python"

    ```python
    from usbip import Interface, In, Out

    class Echo(Interface):
        bInterfaceClass = 0xFF              # vendor-specific
        tx = In(0x81, "bulk")
        rx = Out(0x01, "bulk")

        def on_out(self, ep, data):
            self.tx.write(bytes(data))      # device -> host

    dev.add(Echo())
    ```

=== "C"

    ```c
    usbip_function *fn = usbip_device_add_function(dev);
    usbip_function_add_descriptor(fn, &(usb_interface_descriptor){
        .bDescriptorType    = USB_DT_INTERFACE,
        .bInterfaceNumber   = 0,
        .bNumEndpoints      = 0,            /* auto-counted as endpoints are added */
        .bInterfaceClass    = 0xFF,         /* vendor-specific */
    });
    usbip_ep *tx = usbip_function_add_endpoint(fn, &(usb_endpoint_descriptor){
        .bDescriptorType  = USB_DT_ENDPOINT,
        .bEndpointAddress = 0x81,
        .bmAttributes     = USB_BULK,
        .wMaxPacketSize   = 64,
    });
    usbip_ep *rx = usbip_function_add_endpoint(fn, &(usb_endpoint_descriptor){
        .bDescriptorType  = USB_DT_ENDPOINT,
        .bEndpointAddress = 0x01,
        .bmAttributes     = USB_BULK,
        .wMaxPacketSize   = 64,
    });
    /* keep the returned pipes: on a composite the allocator may relocate them */
    /* read with usbip_device_read(rx, ...); write with usbip_device_write(tx, ...) */
    ```

A [`Function`][usbip.function.Function] is one class instance grouping **one or more**
interfaces bound as a unit - the USB-IF "function". A webcam is one function of two
interfaces (VideoControl + VideoStreaming); a CDC serial port is one function of two
(Communications + Data). Single-interface classes need no explicit `Function` - `add()`
wraps a bare `Interface` in an anonymous one, so the examples above just work.

A true composite opts in to an **Interface Association Descriptor** with `iad = True`
plus `device_triple = (0xEF, 0x02, 0x01)`. Class-defined groupings such as CDC,
Bluetooth and audio carry their own grouping descriptors and omit the IAD. In C the
equivalents are `usbip_function_associate()` and `usbip_device_set_class()`.

```python
class UVC(Function):                        # webcam: VC + VS grouped by an IAD
    iad = True
    iad_class, iad_subclass, iad_protocol = 0x0E, 0x03, 0
    device_triple = (0xEF, 0x02, 0x01)
    def __init__(self):
        self.interfaces = (VideoControl(), VideoStreaming())
        super().__init__()
```

Most of the time you don't write an interface from scratch - you use a
[device class](classes.md). But the class system is built on exactly these calls, so
nothing is hidden.

The core can also be used directly - hand-author every descriptor and answer every
request yourself, with no `Interface`/`Function` at all. In Python a
[`DescriptorGroup`][usbip.device.DescriptorGroup] holds one function's worth of
descriptors; in C the same calls sit on the device:

=== "Python"

    ```python
    g = dev.add_group()
    i0 = g.claim_interface()
    g.add(InterfaceDescriptor(bInterfaceNumber=i0, bAlternateSetting=0,
                              bNumEndpoints=1, bInterfaceClass=0xFF,
                              bInterfaceSubClass=0, bInterfaceProtocol=0).pack())
    tx = g.add_endpoint(In(0x81, "bulk"))
    dev.on_control(i0, my_ctrl)             # class/vendor requests
    tx.write(data)                          # device -> host
    ```

=== "C"

    ```c
    usbip_device_add_descriptor(dev, &(usb_interface_descriptor){
        .bDescriptorType  = USB_DT_INTERFACE,
        .bInterfaceNumber = 0,
        .bInterfaceClass  = 0xFF,
    });
    usbip_ep *tx = usbip_device_add_endpoint(dev, &(usb_endpoint_descriptor){
        .bDescriptorType  = USB_DT_ENDPOINT,
        .bEndpointAddress = 0x81,
        .bmAttributes     = USB_BULK,
        .wMaxPacketSize   = 64,
    });
    usbip_device_on_control(dev, 0, my_ctrl, my_ctx);   /* class/vendor requests */
    usbip_device_write(tx, data, len, 0);               /* device -> host */
    ```

In C, `examples/device/vendor_device.c` and `webusb_device.c` are complete programs
authored this way, with no class code behind them at all.

See the [Device core API](../api/device.md) and the [class layer](../api/function.md).
