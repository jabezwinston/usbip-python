# Webcam (UVC)

A UVC camera streams frames over an **isochronous** endpoint - the transfer type USB
uses for time-sensitive media (it trades guaranteed delivery for guaranteed timing).
Advertise a resolution and pixel format, then supply frames - or omit the source and
the class generates animated color bars.

## A color-bar webcam

=== "Python"

    ```python
    import time, usbip
    from usbip.device import USBDevice
    from usbip.classes.device import UVC

    dev = USBDevice(0x1209, 0x0006, product="USBIP Camera")
    dev.add(UVC(width=320, height=240, fps=15, formats=("yuyv",)))   # color bars
    dev.plug()                                                       # serve it; -> a camera
    time.sleep(3600)                                                 # serve (Ctrl-C to stop)
    ```

=== "C"

    ```c
    #include <unistd.h>
    #include "usbip-device.h"
    #include "classes/uvc.h"

    int main(void) {
        usbip_device *g = usbip_device_create(0x1209, 0x0006);
        usbip_device_set_strings(g, "USB over IP", "USBIP Camera", "0006");

        uvc_opts opts = {
            .width   = 320,
            .height  = 240,
            .fps     = 15,
            .formats = UVC_HAS_YUYV,
        };
        uvc_add(g, &opts);             /* next_frame NULL -> built-in color bars */
        usbip_device_plug(g, NULL);            /* serve it; -> a camera */
        for (;;) sleep(1);
    }
    ```

## Grab a frame

Once a [client](../../platforms/index.md) has imported the device, the host's in-box
UVC driver binds it and it becomes an ordinary camera - every app that lists cameras
will offer it, browsers included.

=== "Linux"

    `uvcvideo` binds it as `/dev/video*`; `ffmpeg` and any V4L2 app can capture from
    it:

    ```bash
    v4l2-ctl --list-devices
    ffmpeg -f v4l2 -i /dev/videoN -frames:v 1 shot.png
    ```

=== "Windows"

    `usbvideo.sys` binds it. Open the Camera app, or pick it from any app's camera
    list.

## Supplying real frames

To send real pixels, pass a frame source: a callback that fills a buffer with one frame
in **YUYV** (a raw packed format, `width*height*2` bytes) or **MJPEG** (a JPEG per
frame) and returns its length.

!!! note "Isochronous & timing"
    USB/IP has [no bus frame clock](../../platforms/hw-clients.md), so iso
    transfers complete as fast as the host asks. For a webcam this is usually fine;
    audio needs real-time pacing ([Sound Card](audio.md)).

Full program: `examples/uvc_device.py`; in C, `examples/device/video/`. API:
[UVC](../../api/classes-device.md#uvc-usb-video-class-webcam).
