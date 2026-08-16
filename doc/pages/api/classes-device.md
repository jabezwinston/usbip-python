# Device classes

Ready-made, reusable USB device classes - each an [`Interface`][usbip.function.Interface]
or [`Function`][usbip.function.Function] subclass built only on the public device API,
and each added the one way anything is added: `dev.add(CLASS(...))`, which returns the
class's data-plane handle. Only the user-facing entry points are shown; see
[Device Classes](../device/classes/index.md) for usage.

## HID - Human Interface Device

The generic `HID` interface plus ready-made Report-descriptor builders.

::: usbip.classes.device.hid
    options:
      members:
        - HID
        - keyboard_report_descriptor
        - mouse_report_descriptor
        - consumer_report_descriptor
        - vendor_report_descriptor

## CDC-ACM - Virtual serial port

`dev.add(CDCACM(...))` attaches a port and returns it; transmit with the port's `write()`.

::: usbip.classes.device.cdc_acm
    options:
      members:
        - CDCACM
        - CDCData

## MSC - Mass storage (SCSI / Bulk-Only Transport)

The disk is backed by an application-supplied block store (see the `FileStore` in
`examples/msc_device.py`); the class itself does no filesystem access. Pass a list of
stores for a multi-LUN disk - one logical unit each, each its own drive on the host.

::: usbip.classes.device.msc
    options:
      members:
        - MSC
        - Lun

## MTP - Media Transfer Protocol

`dev.add(MTP(...))` attaches one or more storages, each an application-supplied backend (see the
`FilesystemStore` in `examples/mtp_device.py`).

::: usbip.classes.device.mtp
    options:
      members:
        - MTP

## UAC - USB Audio Class 1.0

`dev.add(UAC(...))` attaches a speaker + microphone audio interface.

::: usbip.classes.device.uac
    options:
      members:
        - UAC

## UVC - USB Video Class (webcam)

`dev.add(UVC(...))` attaches a camera (YUYV and/or MJPEG).

::: usbip.classes.device.uvc
    options:
      members:
        - UVC

## DFU - Device Firmware Upgrade

`dev.add(DFU(...))` attaches a DFU target table.

::: usbip.classes.device.dfu
    options:
      members:
        - DFU
        - DFUError

## Bluetooth - HCI transport

`dev.add(Bluetooth(...))` attaches a USB Bluetooth controller transport.

::: usbip.classes.device.bluetooth
    options:
      members:
        - Bluetooth
        - BluetoothInterface
