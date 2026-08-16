# Examples

Every class ships a runnable program in **both** languages. Python examples live in
`examples/`; the C examples are built by `make` in the
[C library](https://github.com/jabezwinston/usbip-c) into its `examples/build/`
directory, and are named below without that prefix.

```bash
python3 examples/<name>.py [options]      # Python
<name>                                           # C, from the build directory
```

On Windows that is `python examples\<name>.py` and `<name>.exe`.

Each program then **serves** its device and waits. Import it with a USB/IP client from
another terminal - [Getting Started → Plug it in](getting-started.md) has
the command for each OS.

## Documentation examples

The programs the guide walks through line by line, in both languages. The Python pair
lives in `examples/doc/`, the C pair in the C library's `doc/examples/` (built with
every other C example, so it cannot drift from the API).

| Program | Python | C | Notes |
|---------|--------|---|-------|
| Boot HID keyboard | `doc/boot_keyboard.py` | `doc/boot_keyboard.c` | hand-authored descriptors - no ready-made device class ([Getting Started](getting-started.md)) |
| Host driver | `doc/host_drive.py` | `doc/host_drive.c` | import and drive a device ([Host driver](host/host-driver.md)) |

## Device examples

| Device | Python | C | Notes |
|--------|--------|---|-------|
| HID keyboard/mouse/raw | `hid_device.py` | `hid_device.c` | `--profile keyboard\|mouse\|raw` |
| CDC-ACM serial | `cdc_acm_device.py` | `cdc_acm_device.c` | echoes whatever it receives |
| Composite disk + console | `msc_cdc_device.py` | `msc_cdc_device.c` | one device: a drive **and** a serial console for it |
| Composite serial + DFU | - | `cdc_dfu_device.c` | COM port + DFU; WinUSB scoped to the DFU function (C only) |
| Composite serial + HID | `cdc_hid_device.py` | `cdc_hid_device.c` | one device: a COM port **and** media keys - type `v+`/`m`/`b-` on the port to press them |
| Two devices at once | `multi_device.py` | `multi_device.c` | a keyboard **and** a serial port as separate devices (busids `1-1`, `1-2`) |
| Mass storage | `msc_device.py` | `msc_device.c` | `--file` image(s), comma-separated = one LUN each, `--cdrom`, `--floppy[=1.44M]`, `--ufi` |
| MTP | `mtp_device.py` | `mtp_device.c` | exports a directory tree |
| Audio (UAC) | `audio_device.py` | `audio/` | speaker + mic, `--mic-in`/`--out` WAV |
| Webcam (UVC) | `uvc_device.py` | `video/` | `--format yuyv\|mjpeg\|both` |
| DFU | `dfu_device.py` | `dfu_device.c` | file-backed targets for `dfu-util` |
| Vendor-specific | `vendor_device.py` | `vendor_device.c` | bulk loopback (libusb/pyusb) |
| WebUSB | `webusb_device.py` | `webusb_device.c` | browser-openable device |


## Host examples (C)

| Host | File | Notes |
|------|------|-------|
| CDC-ACM host | `examples/host/cdc_host.c` | drives the CDC device with `usbip_host_*` (no kernel) |
| UVC host | `examples/host/uvc_host.c` | pulls frames over isochronous IN |
| Device probe | `examples/host/host_probe.c` | enumerate + dump descriptors (also against a real `usbipd`) |

Python's host example is `examples/doc/host_drive.py` (above); host **drivers**
are used directly from the library - see
[Host driver](host/host-driver.md) and
[Host drivers](api/classes-host.md).

## Wrappers (C)

The C build also produces drop-in `libusb-1.0` and `libusbK` wrapper libraries
(the C library's `wrapper/`), so existing tools - `dfu-util`, pyusb programs, WinUSB apps - work
unmodified against virtual devices. Build commands and usage:
[C library reference](c-reference.md) ·
[pyusb](host/pyusb.md).
