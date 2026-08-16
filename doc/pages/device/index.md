# Device

The **device side** of the library: your program *is* the USB device. Everything
under this section is a task-focused recipe - the smallest code that does something
real, with **Python and C side by side** (switch the tab on any code block). Every
recipe has a matching runnable program under [Examples](../examples.md).

<div class="grid cards" markdown>

- :material-shape: **[USB Classes](classes/index.md)** - the ready-made,
  host-recognised classes: keyboard, serial port, disk, webcam, sound card, media
  transfer, firmware upgrade, Bluetooth dongle. One recipe per class.
- :material-connection: **[Vendor Devices & WebUSB](vendor.md)** - your own
  protocol, opened from your code or a browser.
- :material-layers-triple: **[Composite devices](composite.md)** - several
  functions on one device: a disk *and* its serial console.

</div>

New to USB? Read the short [USB concepts](../concepts.md) primer first - it explains
the vocabulary (descriptors, endpoints, the SETUP packet, STALL) these recipes use.
To *drive* a device instead of being one, see the [Host](../host/index.md) section.
