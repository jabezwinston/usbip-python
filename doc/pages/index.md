# Introduction

The **USBIP Python library** lets you create **virtual USB devices** and write **host drivers** for
them - entirely in software. There is no hardware and, for the common case, no
kernel module to write: a device you build appears to the operating system as if it
were plugged into a real port.

USB/IP is the transport underneath, but the API vocabulary is pure USB - *device,
interface, endpoint, descriptor, transfer* - and the transport is **local by
default**, named only when you go remote.

```python
import usbip
from usbip.device import USBDevice
from usbip.classes.device import HID, hid

dev = USBDevice(0x1209, 0x0011, product="Virtual Keyboard", manufacturer="USB over IP")
kbd = dev.add(HID(hid.keyboard_report_descriptor(),
                  subclass=hid.SUBCLASS_BOOT, 
                  protocol=hid.PROTOCOL_KEYBOARD))

with dev.plug():                      # appears on the local machine as a real keyboard
    kbd.send_report(bytes([0, 0, 0x04, 0, 0, 0, 0, 0]))   # press 'a'
    kbd.send_report(bytes(8))                             # release
```

## Which end is the server

USB/IP inverts the everyday words: the side that **provides** the device is the
USB/IP **server**, and the side that **uses** it is the **client** - so `dev.plug()`
starts *serving* the device, and nothing enumerates it until a client imports it.
[Getting Started](getting-started.md) has the full role table.

## Two mirrored libraries

The same design exists in two implementations that speak one wire protocol, so you can
pick the language and even mix them (a Python device driven by a C host, or vice-versa):

- **Python** (`usbip/`) - pure Python, object-oriented, no C dependency.
  *This site.*
- **C** ([usbip-c](https://github.com/jabezwinston/usbip-c), `include/`) - `usbip_device_*` device, `usbip_host_*` host (libusb-shaped), `usbip_*`
  transport. See the [C library reference](c-reference.md).

Because they mirror each other, the [Device](device/index.md) recipes show **both**
languages side by side.

## What you can build

- **Device side** - emulate a keyboard/mouse (HID), a serial port (CDC-ACM), a USB
  drive (MSC), a webcam (UVC), a sound card (UAC), a phone (MTP), a DFU target, or a
  Bluetooth dongle. Ready-made [device classes](device/classes/index.md) cover all of these; a new
  class is just an `Interface` subclass.
- **Host side** - drive any USB device with a libusb-shaped API
  ([`Handle`][usbip.host.Handle]), or write a reusable [`Driver`][usbip.host.Driver].
- **Both at once** - connect a virtual device to your own host driver with no kernel
  and no root, ideal for tests.

## Where it runs

- **Linux** - the kernel's `vhci-hcd` imports the device; everything is local over
  loopback. See [Platforms → Linux](platforms/linux.md).
- **Windows** - via an installed USB/IP client; see [Platforms → Windows](platforms/windows.md).
- **macOS** - no in-box client, and the one experimental third-party client needs SIP
  disabled; a [hardware client](platforms/hw-clients.md) is the practical route, and macOS
  can always serve a device or drive one with the host API. See
  [Platforms → macOS](platforms/macos.md).
- **Embedded / hardware importers** - anything that speaks the protocol; see
  [Platforms → Hardware clients](platforms/hw-clients.md).
- **Remote** - point a host at a device on another machine with one argument; see
  [Going remote](tools/remote.md).

## Next steps

<div class="grid cards" markdown>

- :material-rocket-launch: **[Getting Started](getting-started.md)** - install and run
  your first virtual device.
- :material-lightbulb: **[Device](device/index.md)** - task-focused device recipes
  with snippets; **[Host](host/index.md)** - drive one from your own code.
- :material-puzzle: **[Components](components/index.md)** - how the pieces fit together.
- :material-book-open-variant: **[API Reference](api/core.md)** - the curated API.

</div>
