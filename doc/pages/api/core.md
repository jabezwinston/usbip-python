# Core types & errors

Role-neutral USB building blocks shared by the device and host sides. If any of the USB
terms below are unfamiliar, the [USB concepts](../concepts.md) primer explains them in
plain language; this page is the reference for the actual types.

Most names are re-exported from the top-level `usbip` package (e.g. `usbip.Setup`,
`usbip.Stall`).

## What's here

**`Setup` - the control-request packet.** Every control transfer starts with an 8-byte
SETUP packet. `Setup` holds its fields: `bmRequestType` (a bitmask: direction · type ·
recipient - [decoded here](../concepts.md)), `bRequest` (the
request code), `wValue` / `wIndex` (parameters), and `wLength` (bytes of data to
follow). Your `on_control` callback receives a `Setup` and decides how to respond.

**Descriptors** - the typed structs the library serialises to describe the device to the
host: `DeviceDescriptor` (VID/PID, class, …), `ConfigurationDescriptor`,
`InterfaceDescriptor`, `EndpointDescriptor`. A [device class](../device/classes/index.md) fills these
in for you; you only touch them for a hand-built [`Interface`](../components/device.md).

**Exceptions** - all USB failures are `USBError` subclasses:

- `Stall` - the device rejected the request or halted the endpoint (USB's "no"). Raise
  it from a control handler to STALL a request; catch it on the host side when a request
  is refused. See [STALL](../concepts.md).
- `Timeout` - no response within the timeout.
- `NotFound` - no matching device or endpoint.

**Constants** (from `usbip`): directions `IN` / `OUT`, and link speeds `SPEED_LOW` ·
`SPEED_FULL` · `SPEED_HIGH` · `SPEED_SUPER`.

## Reference

::: usbip.core
    options:
      members:
        - USBError
        - Stall
        - Timeout
        - NotFound
        - Setup
        - DeviceDescriptor
        - ConfigurationDescriptor
        - InterfaceDescriptor
        - EndpointDescriptor
