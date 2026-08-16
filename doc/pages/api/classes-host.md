# Host drivers

Reusable host-side class drivers - each a [`Driver`][usbip.host.Driver] subclass
opened with `Driver.open(vid, pid, transport=...)` - plus filesystem helpers used by
the mass-storage host.

## HID driver

::: usbip.classes.host.hid
    options:
      members:
        - HIDDriver
        - HIDReport
        - HIDField

## CDC-ACM driver

::: usbip.classes.host.cdc_acm
    options:
      members:
        - CDCDriver

## Mass-storage driver

::: usbip.classes.host.msc
    options:
      members:
        - MSCDriver

## MTP host

::: usbip.classes.host.mtp
    options:
      members:
        - MtpHost
        - MtpError

## Bluetooth driver

::: usbip.classes.host.bluetooth
    options:
      members:
        - BluetoothDriver

## Filesystem helpers (extras)

Utilities the mass-storage / MTP hosts build on: a block-device view, a FAT
filesystem reader, and an ISO-9660 reader.

### Block device

::: usbip.classes.host.extras.blockdev

### FAT filesystem

::: usbip.classes.host.extras.fatfs

### ISO-9660 filesystem

::: usbip.classes.host.extras.isofs
