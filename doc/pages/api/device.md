# Device core API

Build a virtual USB device from descriptors and requests: create a
[`USBDevice`][usbip.device.USBDevice], author its configuration through
[`DescriptorGroup`][usbip.device.DescriptorGroup]s (raw bytes or lazy blocks,
with [`Endpoint`][usbip.device.Endpoint] pipes declared via
[`In`][usbip.device.In] / [`Out`][usbip.device.Out]), register per-interface
control / SET_INTERFACE handlers and per-endpoint data callbacks, then
`plug()` it onto a transport. The core has no class concept - the
object-oriented authoring layer lives in the [class layer](function.md);
[Components → USBDevice](../components/device.md) explains the split.

Most applications author devices with the class layer or the
[ready-made classes](classes-device.md); the core API is for hand-built devices -
see [Components → USBDevice](../components/device.md) for what authoring one
directly looks like.

::: usbip.device
    options:
      members:
        - USBDevice
        - DescriptorGroup
        - Endpoint
        - In
        - Out
