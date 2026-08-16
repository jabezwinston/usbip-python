# Class layer

The object-oriented authoring layer over the [device core](device.md): a
[`Function`][usbip.function.Function] is one class instance grouping one or
more interfaces, and an [`Interface`][usbip.function.Interface] is one
`bInterfaceNumber`. Single-interface classes need no explicit `Function` -
[`add()`][usbip.device.USBDevice.add] wraps a bare `Interface` in one.
[Components → USBDevice](../components/device.md) explains the model; a new
device class is just an `Interface` subclass
([Components → Device classes](../components/classes.md)).

::: usbip.function
    options:
      members:
        - Function
        - Interface
