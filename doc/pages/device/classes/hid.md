# Input Device (HID)

A HID (Human Interface Device) is "generic": you hand the class a **Report descriptor**
and it presents a proper keyboard, mouse, consumer control, or vendor-defined raw
device. Ready-made Report-descriptor builders cover the common profiles.

## HID terms in 60 seconds

- **Report** - the little packet of data a HID device exchanges. A keyboard's input
  report is 8 bytes (modifiers + up to 6 keycodes); a mouse report is buttons + X/Y.
- **Report descriptor** - a small byte "program" that tells the host *how to read* those
  reports (which bits are which buttons, axes, etc.). You rarely write one by hand -
  `keyboard_report_descriptor()`, `mouse_report_descriptor()` and friends generate the
  standard ones. The class serves it to the host on request.
- **GET_REPORT / SET_REPORT** - the host can also fetch or push a report over the control
  endpoint (not just the interrupt pipe). Keyboard LEDs (Caps/Num Lock), for instance,
  arrive as a `SET_REPORT(Output)` - you receive them via the `on_output` callback.
- **Boot vs. report protocol** - keyboards and mice support a stripped-down "boot
  protocol" a BIOS can use before drivers load; `GET_PROTOCOL`/`SET_PROTOCOL` switch
  between it and the full "report protocol". Advertising it is just `subclass=SUBCLASS_BOOT`
  plus `protocol=PROTOCOL_KEYBOARD` or `PROTOCOL_MOUSE` (`HID_SUBCLASS_BOOT` /
  `HID_PROTOCOL_KEYBOARD` / `HID_PROTOCOL_MOUSE` in C), as below.

The class answers all of these HID control requests for you - you supply the Report
descriptor and react through callbacks. (New to control requests? See
[USB concepts](../../concepts.md).)

## A mouse

A complete program that nudges the pointer once a second. The Python builder generates
the Report descriptor; in C the same descriptor is written with the `HID_*` macros.

=== "Python"

    ```python
    import time, usbip
    from usbip.device import USBDevice
    from usbip.classes.device import HID, hid

    dev = USBDevice(0x1209, 0x0011, product="USBIP Mouse")
    mouse = dev.add(HID(hid.mouse_report_descriptor(),
                        subclass=hid.SUBCLASS_BOOT,
                        protocol=hid.PROTOCOL_MOUSE))
    dev.plug()
    mouse.send_report(bytes([0, 12, 0, 0]))   # buttons, dx, dy, wheel -> move right
    time.sleep(3600)                          # serve (Ctrl-C to stop)
    ```

=== "C"

    ```c
    #include <unistd.h>
    #include "usbip-device.h"
    #include "classes/hid.h"

    static const uint8_t mouse[] = {              /* report: buttons, dx, dy, wheel */
      HID_USAGE_PAGE(0x01), HID_USAGE(0x02),           /* Generic Desktop, Mouse */
      HID_COLLECTION(0x01),                            /*   Application */
        HID_USAGE(0x01),                               /*   Pointer */
        HID_COLLECTION(0x00),                          /*     Physical */
          HID_USAGE_PAGE(0x09),                        /*     Buttons */
          HID_USAGE_MIN(1), HID_USAGE_MAX(3),
          HID_LOGICAL_MIN(0), HID_LOGICAL_MAX(1),
          HID_REPORT_COUNT(3), HID_REPORT_SIZE(1), HID_INPUT(0x02),    /* 3 buttons */
          HID_REPORT_COUNT(1), HID_REPORT_SIZE(5), HID_INPUT(0x03),    /* 5 bits padding */
          HID_USAGE_PAGE(0x01),                        /*     Generic Desktop */
          HID_USAGE(0x30), HID_USAGE(0x31), HID_USAGE(0x38),           /* X, Y, Wheel */
          HID_LOGICAL_MIN(-127), HID_LOGICAL_MAX(127),
          HID_REPORT_SIZE(8), HID_REPORT_COUNT(3), HID_INPUT(0x06),    /* relative X/Y/wheel */
      HID_END_COLLECTION,
      HID_END_COLLECTION,
    };

    int main(void) {
        usbip_device *dev = usbip_device_create(0x1209, 0x0011);
        hid_opts opts = {
            .report_desc     = mouse,
            .report_desc_len = sizeof(mouse),
            .subclass        = HID_SUBCLASS_BOOT,
            .protocol        = HID_PROTOCOL_MOUSE,
        };
        hid_iface *f = hid_add(dev, &opts);
        usbip_device_plug(dev, NULL);
        hid_send_report(f, (uint8_t[4]){ 0, 12, 0, 0 }, 4);  /* move right */
        for (;;) sleep(1);
    }
    ```

## Keyboard, raw, and more

Only the Report descriptor and `hid_opts` change:

- **Keyboard** - `hid.keyboard_report_descriptor()` (Python) / the `HID_*` macros (C);
  set `subclass=SUBCLASS_BOOT, protocol=PROTOCOL_KEYBOARD`. Send 8-byte reports `[modifiers, 0, keycode, …]`, and
  handle Caps/Num-Lock LEDs through the `on_output` callback.
- **Raw / vendor** - `hid.vendor_report_descriptor(8, 8)`; add an interrupt-OUT endpoint
  (`out_ep=0x01` / `.out_ep = 0x01`) plus an `on_output` callback to receive host data.
  No class driver claims it, so your own program reads and writes the reports.

The bundled multi-profile program covers all three:
`examples/hid_device.py`; in C, `examples/device/hid_device.c`.

## Verify it

A HID device needs no driver install on any OS. Once a
[client](../../platforms/index.md) has imported it, a mouse moves the pointer and a
keyboard types into whatever window has focus - that much is the same everywhere. A
raw device is reached through the host's own raw-HID interface:

=== "Linux"

    `usbhid` binds it, and a raw device shows up as `/dev/hidraw*`:

    ```bash
    xxd < /dev/hidraw0       # watch the reports a raw device sends
    ```

=== "Windows"

    `hidclass` binds it - `kbdhid` / `mouhid` on top for a keyboard or mouse. A raw
    device is reachable through the Windows HID API (`CreateFile` on the device
    interface path, then the `HidD_*` functions).

See the [HID API](../../api/classes-device.md#hid-human-interface-device).
