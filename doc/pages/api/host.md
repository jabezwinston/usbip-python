# Host API

Drive USB devices over USB/IP. [`open`][usbip.host.open] / [`attach`][usbip.host.attach]
return a [`Handle`][usbip.host.Handle] with libusb-shaped control / bulk / interrupt /
isochronous transfers, or subclass [`Driver`][usbip.host.Driver] for a reusable host
driver that binds by class code (use it via `Driver.open(vid, pid, transport=...)`).
A [`Connection`][usbip.host.Connection] is the underlying imported-device session
both are built on.

::: usbip.host
    options:
      members:
        - open
        - attach
        - Handle
        - Driver
        - Connection
