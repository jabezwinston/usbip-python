# Device classes

A device class turns the raw [device API](device.md) into a reusable, host-recognised
interface. The bundled classes (HID, CDC-ACM, …) are written **only** on the public API
- nothing about them is privileged, so your own class is a first-class citizen.

## Python - subclass `Interface`

A class is an [`Interface`][usbip.function.Interface] subclass: declare endpoints, optionally
emit class-specific descriptors, and handle the events you care about
(`on_out`, `on_control`, …). The HID/CDC/MSC classes follow exactly this shape.

```python
from usbip import Interface, In, Out

class MyClass(Interface):
    bInterfaceClass = 0xFF
    tx = In(0x81, "interrupt", mps=8)

    def on_control(self, setup, data=b""):
        ...                       # class/vendor requests; return bytes or raise Stall

    def notify(self, payload):
        self.tx.write(payload)    # device -> host
```

Beyond `on_out` and `on_control`, an `Interface` can override `on_iso`
(isochronous streams, producing on IN endpoints and consuming on OUT ones),
`set_alt` (alternate settings), `on_reset`, and
`adjust_for_speed` (resize endpoints for high speed) - see the
[class layer API](../api/function.md). Users add it the way they add any class -
`dev.add(YourClass(...))` - which returns the interface itself. A multi-interface
`Function` points its `primary` property at whichever child carries the data plane,
and that is what `add()` hands back (as the bundled classes do).

## C - a `usbip_device_class` descriptor

In C a class is a [`usbip_device_class`](../c-reference.md) descriptor (build / control / I/O
callbacks + per-instance state). Declare it statically, then instantiate it on a device:

```c
static const usbip_device_class my_class = {
    .name = "myclass",
    .bInterfaceClass = 0xFF,
    .build = my_build,
    .control = my_control,
    .state_size = sizeof(struct my_state),
};
usbip_function *fn = usbip_device_add_class(dev, &my_class, &params);
```

`.name` is only used in diagnostics. The built-in C classes expose an `*_add()` helper
(`hid_add`, `cdc_acm_add`, …) so most code never names the class object at all.

See the ready-made classes in [Device Classes](../device/classes/index.md) and their
[API](../api/classes-device.md).
