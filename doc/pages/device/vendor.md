# Vendor Devices & WebUSB

Not every device fits a standard class. A **vendor-specific** interface (class
`0xFF`) has no OS driver at all - you define the protocol, and the other end is
your own [host driver](../host/host-driver.md), a libusb program, or (with WebUSB) a web
page. This is the shape of most lab tools, programmers and one-off gadgets.

## A vendor bulk device

One interface, one Bulk OUT, one Bulk IN, and a loopback for behaviour - the
device the [host driver walkthrough](../host/host-driver.md) drives:

=== "Python"

    ```python
    from usbip import USBDevice, Interface, In, Out

    class VendorBulk(Interface):
        bInterfaceClass = 0xFF                   # vendor-specific
        bulk_out = Out(0x01, "bulk", mps=64)     # host   -> device
        bulk_in = In(0x81, "bulk", mps=64)       # device -> host

    dev = USBDevice(0x1209, 0x0004, product="USBIP Vendor Bulk")
    fn = dev.add(VendorBulk())
    dev.plug()
    while True:                                  # loopback: OUT -> IN
        data = fn.bulk_out.read(timeout=1.0)
        if data:
            fn.bulk_in.write(data)
    ```

=== "C"

    ```c
    #include "usbip-device.h"

    int main(void) {
        usbip_device *g = usbip_device_create(0x1209, 0x0004);

        usbip_device_add_descriptor(g, &(usb_interface_descriptor){
            .bDescriptorType = USB_DT_INTERFACE,
            .bInterfaceClass = 0xFF 
        });

        usbip_ep *tx_ep = usbip_device_add_endpoint(g, &(usb_endpoint_descriptor){
            .bDescriptorType  = USB_DT_ENDPOINT,
            .bEndpointAddress = 0x81,
            .bmAttributes     = USB_BULK,
            .wMaxPacketSize   = 64
        });
        usbip_ep *rx_ep = usbip_device_add_endpoint(g, &(usb_endpoint_descriptor){
            .bDescriptorType  = USB_DT_ENDPOINT,
            .bEndpointAddress = 0x01,
            .bmAttributes     = USB_BULK,
            .wMaxPacketSize   = 64 
        });

        usbip_device_plug(g, NULL);
        for (;;) {  
            /* loopback: OUT -> IN */
            uint8_t buf[64];
            int n = usbip_device_read(rx_ep, buf, sizeof(buf), 1000);
            if (n > 0) usbip_device_write(tx_ep, buf, n, 0);
        }
    }
    ```

Vendor **control** requests (`bmRequestType` type = vendor) arrive at the
interface's `on_control` - see [USB concepts](../concepts.md)
for decoding them.

Drive it from the host side with `bulk_out`/`bulk_in`
([libusb-shaped transfers](../host/host-driver.md)), or from
stock libusb through the C [wrapper](../c-reference.md) or [pyusb](../host/pyusb.md).

No class driver claims a vendor device once a [client](../platforms/index.md) has
imported it - your own program does:

=== "Linux"

    Nothing in the kernel binds it; claim it with libusb.

    ```bash
    lsusb -v -d 1209:
    ```

=== "Windows"

    Advertise the Microsoft OS descriptors with `dev.enable_winusb()` and Windows binds
    **WinUSB** automatically - no INF, no Zadig
    ([Platforms → Windows](../platforms/windows.md)).

## WebUSB - open it from a browser

WebUSB lets an `https` page (or `http://localhost`) open a vendor device
directly. Per the spec the device advertises a BOS platform-capability
descriptor plus a vendor `GET_URL` request returning its landing page -
`enable_webusb()` does both:

=== "Python"

    ```python
    dev.enable_webusb(0x22, "https://example.com")  # vendor code + landing page
    dev.enable_winusb()                             # Chrome on Windows needs WinUSB
    ```

=== "C"

    ```c
    usbip_device_enable_webusb(g, 0x22, "https://example.com");
    usbip_device_enable_winusb(g, NULL);        /* Chrome on Windows needs WinUSB */
    ```

Pick a vendor code that stays clear of the Microsoft OS codes (`0x20`/`0x21`). On the
page side:

```js
const dev = await navigator.usb.requestDevice({filters: [{vendorId: 0x1209}]});
await dev.open(); 
await dev.selectConfiguration(1);
await dev.claimInterface(0);
await dev.transferOut(1, new TextEncoder().encode("hi"));
const r = await dev.transferIn(1, 64);
```

Full programs: `examples/vendor_device.py`, `webusb_device.py`; in C,
`examples/device/vendor_device.c` and `webusb_device.c`.
