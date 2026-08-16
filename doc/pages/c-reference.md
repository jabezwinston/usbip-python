# C library reference

USBIP ships a byte-compatible **C library** (`usbip_host_*` host · `usbip_device_*` device ·
`usbip_*` transport) alongside the Python library. It is published separately as
[usbip-c](https://github.com/jabezwinston/usbip-c) and documented with Doxygen.

[Open the C library reference :material-arrow-right:](https://jabezwinston.github.io/usbip-c/){ .md-button .md-button--primary }

The two libraries mirror each other, so the [Use Cases](device/index.md) on this
site show **both** Python and C snippets side by side - switch the tab on any code
block to see the C equivalent.

## Wrappers

The C build also produces **wrapper** libraries (drop-in `libusb-1.0` / `libusbK`)
that let stock binaries drive a virtual device. They are C artifacts: follow the
button above and pick **Wrappers** from the guide list for the full documentation.
