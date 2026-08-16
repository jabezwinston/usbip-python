# Firmware Upgrade (DFU)

DFU (Device Firmware Upgrade) is the standard USB protocol for flashing firmware.
Present a DFU target that `dfu-util` can **UPLOAD** from (read out) or **DOWNLOAD** to
(write). Each target is a linear byte store - a file, a flash part, whatever you supply -
described by callbacks; the class runs the DFU 1.1 state machine and stores nothing itself.

## A file-backed target

=== "Python"

    ```python
    import time, usbip
    from usbip.device import USBDevice
    from usbip.classes.device import DFU

    class FileTarget:                      # one DFU target backed by a file
        name, i_string = "fw0", 0
        def __init__(self, path):
            self.f = open(path, "a+b", buffering=0)
            self.length = self.f.seek(0, 2)

        def write(self, off, data):
            self.f.seek(off)
            self.f.write(data)
            self.length = max(self.length, off + len(data))

        def read(self, off, size):
            self.f.seek(off)
            return self.f.read(min(size, max(0, self.length - off)))

        def begin_download(self):
            self.length = 0

        def finish_download(self):
            self.f.truncate(self.length)

    dev = USBDevice(0x1209, 0x000F, product="USBIP DFU")
    dev.add(DFU([FileTarget("firmware.bin")]))
    dev.plug()
    time.sleep(3600)                       # serve (Ctrl-C to stop)
    ```

=== "C"

    ```c
    #include <stdio.h>
    #include <unistd.h>
    #include "usbip-device.h"
    #include "classes/dfu.h"

    static uint32_t fw_len;                       /* bytes stored so far */

    static int wr(void *c, uint32_t off, const uint8_t *d, uint32_t n) {
        FILE *fp = c;
        if (fseek(fp, (long)off, SEEK_SET) != 0 || fwrite(d, 1, n, fp) != n)
            return DFU_STATUS_errWRITE;

        if (off + n > fw_len)
            fw_len = off + n;
        return DFU_STATUS_OK;
    }

    static int rd(void *c, uint32_t off, uint8_t *d, uint32_t n) {
        FILE *fp = c;
        if (off >= fw_len)
            return 0;                             /* end of image */

        if (n > fw_len - off)
            n = fw_len - off;
        if (fseek(fp, (long)off, SEEK_SET) != 0)
            return 0;

        return (int)fread(d, 1, n, fp);
    }

    int main(void) {
        FILE *fp = fopen("firmware.bin", "w+b");
        setvbuf(fp, NULL, _IONBF, 0);             /* reads and writes alternate */

        usbip_device *g = usbip_device_create(0x1209, 0x000F);
        dfu_target tgt = {
            .name  = "fw0",
            .ctx   = fp,                          /* passed back to wr/rd */
            .write = wr,
            .read  = rd,
        };
        dfu_opts opts = {
            .targets   = &tgt,
            .n_targets = 1,
        };
        dfu_add(g, &opts);
        usbip_device_plug(g, NULL);
        for (;;) sleep(1);
    }
    ```

## Flash it with dfu-util

```bash
dfu-util -l                       # lists the targets (each is an "alt setting")
dfu-util -a 0 -U readback.bin     # UPLOAD target 0 (device -> file)
dfu-util -a 0 -D firmware.bin     # DOWNLOAD to target 0 (file -> device)
```

## Targets and alternate settings

Each target maps to a USB **alternate setting** - `dfu-util -a N` selects which one.
The class hands your `write` callback the incoming bytes in `transfer_size` chunks at
increasing offsets; return `0` for success or a `DFU_STATUS_*` code to report an error
to the host.

Full program: `examples/dfu_device.py`; in C, `examples/device/dfu_device.c`. API:
[DFU](../../api/classes-device.md#dfu-device-firmware-upgrade).
