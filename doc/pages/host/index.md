# Host

The **host side** drives a USB device instead of being one. It talks USB/IP itself,
so it needs **no kernel driver, no `vhci` and no root** - serve a device in one
process and drive it from another, on any OS. The device can be virtual (either
library) or genuine hardware exported by a real `usbipd`.

Two ways in, from most to least code you write:

<div class="grid cards" markdown>

- :material-import: **[Host driver](host-driver.md)** - the native
  libusb-shaped API: open a device, run control/bulk/interrupt/iso transfers, or
  subclass a reusable `Driver`.
- :simple-python: **[pyusb](pyusb.md)** - code already
  written against pyusb drives a virtual device by loading the C libusb wrapper as
  its backend.

</div>

In USB/IP terms this side is the **client**
([which end is the server](../getting-started.md)) - something
must already be serving before any host call can succeed.
