# macOS

**macOS has no in-box USB/IP client**, and no `vhci-hcd` equivalent to install. One
experimental third-party client exists, but it only runs with System Integrity
Protection turned off - so for most people the practical way to give a Mac a virtual
device is a [hardware client](hw-clients.md), which needs nothing installed on the Mac at
all.

| Option | Works today | Cost |
|---|---|---|
| [Hardware client](hw-clients.md) (**recommended**) | yes | a $6 board; the Mac sees plain USB |
| [`usbip-macos`](#usbip-macos-experimental) | experimental | SIP must be disabled |
| [Serve from macOS](#serve-from-macos) | yes | the *other* machine gets the device |
| [Drive from code](#drive-a-device-from-macos) | yes | no USB stack involvement |

## `usbip-macos` (experimental)

[`carlossless/usbip-macos`](https://github.com/carlossless/usbip-macos) is a USB/IP
client written in Rust. It creates the virtual controller through Apple's
`IOUSBHostControllerInterface` rather than a kext, and its author describes it as
"highly experimental… just enough working to work my current usecases"

!!! warning "It needs SIP disabled"

    `IOUSBHostControllerInterface` is gated behind the
    `com.apple.developer.usb.host-controller-interface` entitlement, which Apple grants
    only on request. Without it the process must run as **root on a Mac with System
    Integrity Protection turned off** (`csrutil disable` from Recovery). That lowers the
    security posture of the whole machine - do it on a scratch Mac or a VM, not on a
    daily driver. Prefer the [hardware client](hw-clients.md) if you can.

Build it with cargo, then attach by busid or by VID:PID:

```bash
cargo build --release

sudo ./target/release/usbip-macos -r 192.168.0.5 list
sudo ./target/release/usbip-macos -r 192.168.0.5 attach --busid 1-1
sudo ./target/release/usbip-macos -r 192.168.0.5 attach --vendor_id 0x1209 --product_id 0x0001
```

`-p/--tcp-port` selects a non-default port; the process stays in the foreground for the
lifetime of the device, and detaching means stopping it. Serve on every interface so the
Mac can reach you - `dev.plug(via=usbip.USBIP("0.0.0.0", 3240))` - since the default
local transport is loopback-only.

## Serve from macOS

The Python library is pure Python and runs anywhere Python does, so a Mac can host the
device server; a Linux or Windows machine then imports it over the network.

```bash
python3 examples/hid_device.py --host 0.0.0.0 --port 3240   # on the Mac
```

```bash
sudo usbip attach -r <mac-ip> -b 1-1                               # on a Linux box
```

(The C libraries also build on macOS - `libusbip-device.0.dylib` / `libusbip-host.0.dylib` -
though that branch of the build is best-effort and not exercised by CI.)

## Drive a device from macOS

The [host API](../host/host-driver.md) speaks USB/IP itself, so it needs no
kernel support and works on macOS exactly as on Linux - see
[libusb-shaped transfers](../host/host-driver.md).
Point the transport at the serving machine: `usbip.USBIP("10.0.0.5", 3240)`.

## Run existing libusb tools

The C `libusb-1.0` [wrapper](../c-reference.md) targets macOS too - best-effort, as the
Darwin branch of the build is untested - so a stock libusb program, or
[pyusb pointed at it](../host/pyusb.md), can drive a served device without any
kernel driver. Use `DYLD_LIBRARY_PATH` (or pass the path to pyusb's backend loader)
rather than Linux's `LD_PRELOAD`.

## Testing a Mac-only USB stack

If the goal is to test how macOS *itself* reacts to a device on an untouched system, use
a [hardware client](hw-clients.md), or a Linux VM that imports the device and passes it
through to the Mac's virtualisation stack.
