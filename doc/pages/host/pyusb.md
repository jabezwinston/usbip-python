# pyusb

[pyusb](https://github.com/pyusb/pyusb) is the usual way to talk to USB from Python,
and it can drive a **virtual** device from this library without a kernel module, root,
or `vhci` - by loading the C `libusb-1.0` [wrapper](../c-reference.md) as its backend
instead of the system libusb.

This is worth doing when the code that should drive the device is *already* written
against pyusb. If you are writing the host side from scratch, the native
[host API](host-driver.md) is simpler - no C build, no environment variables.

## What you need

| Piece | How you get it |
|-------|----------------|
| pyusb | `pip install pyusb` - stock, unpatched |
| the wrapper | `make libusb` in the C library (`mingw32-make libusb` on Windows) → `libusb-1.0.so.0` (Linux), `libusb-1.0.0.dylib` (macOS) or `libusb-1.0.dll` (Windows), in its platform build directory |
| a device to drive | any device example, in either language |

pyusb loads its libusb backend from a path *you* choose, so pointing it at the wrapper
needs no `LD_PRELOAD`, no copying a DLL, and no change to the rest of the program.

## A complete session

Serve the vendor example - a bulk loopback that echoes whatever it receives:

```bash
python3 examples/vendor_device.py     # 1209:0004 on :3240
```

Then drive it. With the variables set in `os.environ` rather than the shell, this script
is identical on every OS:

```python
import os
import usb.core, usb.util, usb.backend.libusb1

# the wrapper reads these in libusb_init(), i.e. inside get_backend()
os.environ["USBIP_HOST"] = "127.0.0.1"
os.environ["USBIP_PORT"] = "3240"

# Linux  : libusb-1.0.so.0
# macOS  : libusb-1.0.0.dylib   
# Windows: libusb-1.0.dll
backend = usb.backend.libusb1.get_backend(
    find_library=lambda _: "/path/to/libusb-1.0.so.0")

dev = usb.core.find(idVendor=0x1209, idProduct=0x0004, backend=backend)
if dev is None:
    raise SystemExit("device not found - is vendor_device.py running?")

dev.set_configuration()                         # standard requests go over USB/IP

dev.write(0x01, b"hi")                          # bulk OUT
print(bytes(dev.read(0x81, 64)))                # bulk IN  -> b"hi"

usb.util.dispose_resources(dev)
```

!!! warning "Set the environment before `get_backend()`"
    A stock binary has no way to pass a server address, so the wrapper reads
    `USBIP_HOST` / `USBIP_PORT` from the
    [environment](../tools/env.md) - **once**, inside
    `libusb_init()`, which pyusb calls from `get_backend()`. Setting them afterwards
    has no effect. They default to `127.0.0.1:3240`.

