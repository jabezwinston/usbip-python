# USB Classes

These ready-made classes present complete, host-recognised interfaces on top of the
[device API](../../components/device.md). Each exists in both Python and C with
corresponding entry points - `dev.add(CLASS(...))` in Python, `<class>_add(dev, &opts)`
in C, both returning the same data-plane handle - and each has a recipe page below
showing the smallest code that does something real. For signatures see the [API reference](../../api/classes-device.md);
to write a class of your own see
[Components → Device classes](../../components/classes.md).

| Class | Interface | Python entry | C entry | Recipe |
|-------|-----------|--------------|---------|--------|
| **HID** | 0x03 | `dev.add(HID(...))` | `hid_add()` | [Input Device (HID)](hid.md) |
| **CDC-ACM** | 0x02/0x0A | `dev.add(CDCACM(...))` | `cdc_acm_add()` | [Serial Port](serial.md) |
| **MSC** | 0x08 | `dev.add(MSC(...))` | `msc_add()` | [Disk Storage](storage.md) |
| **MTP** | 0x06 | `dev.add(MTP(...))` | `mtp_add()` | [Media Transfer](mtp.md) |
| **UAC** | 0x01 | `dev.add(UAC(...))` | `uac_add()` | [Sound Card](audio.md) |
| **UVC** | 0x0E | `dev.add(UVC(...))` | `uvc_add()` | [Webcam](video.md) |
| **DFU** | 0xFE | `dev.add(DFU(...))` | `dfu_add()` | [Firmware Upgrade](dfu.md) |
| **Bluetooth** | 0xE0 | `dev.add(Bluetooth(...))` | `bluetooth_add()` | [Bluetooth Dongle](bluetooth.md) |

## HID - Human Interface Device
A *generic* HID interface: hand it a Report descriptor and it presents a keyboard,
mouse, consumer control, or vendor-defined raw device. Ready-made builders
(`keyboard_report_descriptor()`, `mouse_report_descriptor()`, …) cover the common
profiles. → [Recipe](hid.md) · [API](../../api/classes-device.md#hid-human-interface-device)

## CDC-ACM - Virtual serial port
Presents an ordinary serial port, bound by the host's in-box driver. You get callbacks
for open/close, line-coding changes and RX, and transmit with the port's `write()` /
`cdc_acm_send()`.
→ [Recipe](serial.md) · [API](../../api/classes-device.md#cdc-acm-virtual-serial-port)

## MSC - Mass storage
A SCSI / Bulk-Only-Transport drive backed by a block source - an image file, a partition, or
your own. Supports read-only, CD-ROM and floppy media, and several logical units (LUNs) on
one interface, each its own medium and its own drive on the host.
→ [Recipe](storage.md) · [API](../../api/classes-device.md#msc-mass-storage-scsi-bulk-only-transport)

## MTP - Media Transfer Protocol
Exposes directory trees as MTP storages, browsable by any host file manager with no
driver install. The class implements the protocol and object model; you supply a
storage backend.
→ [Recipe](mtp.md) · [API](../../api/classes-device.md#mtp-media-transfer-protocol)

## UAC - USB Audio Class 1.0
A speaker (iso OUT) + microphone (iso IN) sound card, 48 kHz/16-bit. Provide a mic
source and speaker sink, or use the built-in tone.
→ [Recipe](audio.md) · [API](../../api/classes-device.md#uac-usb-audio-class-10)

## UVC - USB Video Class
An isochronous webcam advertising YUYV and/or MJPEG at one resolution. Supply frames,
or use the built-in color-bar generator.
→ [Recipe](video.md) · [API](../../api/classes-device.md#uvc-usb-video-class-webcam)

## DFU - Device Firmware Upgrade
A DFU 1.1 target `dfu-util` can upload from / download to. Each target is a linear byte
store you back with a file or flash.
→ [Recipe](dfu.md) · [API](../../api/classes-device.md#dfu-device-firmware-upgrade)

## Bluetooth - HCI transport
A USB Bluetooth controller transport (HCI commands/events + ACL). You provide the
controller; the bundled examples include a complete from-scratch one.
→ [Recipe](bluetooth.md) · [API](../../api/classes-device.md#bluetooth-hci-transport)

!!! info "Beyond these classes"
    The examples also include **vendor-specific** and **WebUSB** devices 
    - see [Vendor Devices & WebUSB](../vendor.md). The other composites areSeveral functions on one device is a
    [composite device](../composite.md). See [Examples](../../examples.md).
