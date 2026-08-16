# Getting Started

Everything you need to run your first virtual USB device and plug it into a real
operating system: install the library, write the device, import it with a USB/IP
client.

=== "Python"

    ```bash
    pip install usbip          # the pure-Python library, no C dependency
    ```

    From a checkout instead (the examples come with it):

    ```bash
    git clone https://github.com/jabezwinston/usbip-python
    cd usbip-python
    pip install -e .
    ```

    Python 3.8 or newer. Check the installed version with
    `python -c "import usbip; print(usbip.__version__)"`.

=== "C"

    The only prebuilt package is for Windows - headers, both role libraries in 32-
    and 64-bit, the examples as `.exe`, attached to every
    [release](https://github.com/jabezwinston/usbip-c/releases). Everywhere else,
    build from source:

    ```bash
    git clone https://github.com/jabezwinston/usbip-c
    make -C usbip-c            # examples + both role libraries
    ```

    On Windows, build natively from `cmd` with `mingw32-make -C usbip-c` (mingw-w64
    on `PATH`), or cross-build from Linux with `make -C usbip-c OS=Windows_NT`.

    See the [C library reference](c-reference.md) for the gcc/clang link lines and
    the C walkthrough.

USB/IP inverts the everyday words: the side that **provides** the device is the
USB/IP **server**, and the side that **uses** it is the USB/IP **client**.

| Your side | USB role | USB/IP role | Socket |
|-----------|----------|-------------|--------|
| a program on the device API (this library) | device | **server** | listens on TCP 3240 |
| a USB/IP client (the OS's own importer) | host | **client** | connects |
| a program on the [host API](host/host-driver.md) | host | **client** | connects |

So `dev.plug()` does not plug into anything - it starts **serving** the device and
returns. Nothing enumerates it until a client imports it.

A USB **Boot-protocol HID keyboard** written as an `Interface` of your own:
hand-authored descriptors and one control handler, with no ready-made device class
involved. This is the whole surface a simple device needs - `USBDevice`, an
`Interface` with an endpoint and a control handler, `plug()`.

```python title="examples/doc/boot_keyboard.py"
--8<-- "examples/doc/boot_keyboard.py"
```

Run it (`python examples\doc\boot_keyboard.py` on Windows):

```bash
python3 examples/doc/boot_keyboard.py       # serves 1209:0011 on TCP :3240
```

Nothing happens yet - the program is now the USB/IP **server**, waiting for a
client.

The keyboard above is deliberately raw - it shows the whole core API. In practice a
ready-made [device class](device/classes/index.md) authors the descriptors and runs the class
protocol for you, and the same keyboard shrinks to the snippet on the
[introduction page](index.md): one `dev.add(HID(...))` call replaces
all the descriptors and the control handler. To serve on the network instead of the
context-manager form, pass a transport:

```python
transport = usbip.USBIP("0.0.0.0", 3240)
dev.plug(via=transport)                               # serve on TCP :3240
```

See [Device](device/index.md) for one recipe per class.

Nothing enumerates until a USB/IP **client** imports what you are serving. Every
example page in these docs assumes this step; how you take it depends on the OS.

=== "Linux"

    The client is built into the kernel - the `vhci-hcd` virtual host controller:

    ```bash
    sudo modprobe vhci-hcd                       # once per boot
    sudo usbip attach -r 127.0.0.1 -b 1-1        # import the served device
    ```

    Detach with `sudo usbip detach -p 00`. See
    [Platforms → Linux](platforms/linux.md).

=== "Windows"

    There is no in-box client - install one (`usbip-win2` for Windows 10/11, or the
    older `usbip-win`), then:

    ```text
    usbip.exe attach -r 127.0.0.1 -b 1-1
    ```

    Windows binds its own in-box driver afterwards. See
    [Platforms → Windows](platforms/windows.md).

=== "macOS"

    macOS has no in-box client, and the one experimental third-party client
    (`usbip-macos`) only runs with System Integrity Protection disabled. The practical
    way to get the keyboard into a Mac's USB stack is a
    [hardware client](platforms/hw-clients.md) - a board that imports the device and
    re-presents it on its own USB port, so the Mac sees plain USB. A Mac can also
    *serve* a device for another machine to import, and *drive* one with the
    [host API](host/host-driver.md). See [Platforms → macOS](platforms/macos.md).

Once imported, the keyboard types its text into whatever window has focus - the host
binds its ordinary HID driver, because as far as it is concerned this is a real
keyboard.

Embedded importers and the no-client options are covered under
[Platforms](platforms/index.md).

!!! tip "No root, no kernel"
    For tests you don't need `vhci` at all: drive your virtual device with a USBIP
    **host** in the same or another process - see
    [Host driver](host/host-driver.md).

Two environment variables turn on diagnostics for any program on either library:
`USBIP_PCAPNG=file.pcapng` records every transfer for Wireshark, and `USBIP_DEBUG=1`
logs every control request and data transfer. The full table - including the
`USBIP_HOST`/`USBIP_PORT` pair the C wrappers read, and how to set a variable in `cmd`
and PowerShell - is under [Environment variables](tools/env.md); the capture workflow is
under [Capturing traffic](tools/capture.md).

The repository ships runnable device and host examples for every class - see the
[Examples](examples.md) page for the full list and commands.

```bash
python3 examples/hid_device.py --profile keyboard --type "hello"
# or the C build - `make` in the C library, then run hid_device from its build directory:
hid_device
```

(On Windows: `python examples\hid_device.py …`, and the C build is `hid_device.exe`.)

- **[Device](device/index.md)** - a recipe per device class, plus vendor/WebUSB and composite devices
- **[Host](host/index.md)** - drive a device from your own code, pyusb, or a stock binary
- **[Platforms](platforms/index.md)** - importing the device on Linux, Windows, macOS or embedded hardware
