# Disk Storage (MSC)

A mass-storage function is a **block backend** plus the class's SCSI / Bulk-Only
Transport state machine. The class does no filesystem access of its own: you supply the
backend - an image file, a real partition, your own block source - and the OS mounts it
like a USB drive.

## An image-backed drive

=== "Python"

    ```python
    import sys, time, usbip
    from usbip.device import USBDevice
    from usbip.classes.device import MSC

    class ImageStore:                                    # the backend is yours
        block_size = 512

        def __init__(self, path):
            self._f = open(path, "r+b", buffering=0)     # an existing image, as it is
            self.num_blocks = self._f.seek(0, 2) // self.block_size
            if not self.num_blocks:
                raise SystemExit(f"{path}: smaller than one {self.block_size} B block")

        def read(self, lba, count):
            self._f.seek(lba * self.block_size)
            return self._f.read(count * self.block_size)

        def write(self, lba, count, data):
            self._f.seek(lba * self.block_size)
            self._f.write(data)

    store = ImageStore(sys.argv[1])                      # capacity = the image's size
    dev = USBDevice(0x1209, 0x0008, product="USBIP MSC")
    dev.add(MSC(store, vendor="USB-IP", product="DISK"))
    dev.plug()                                           # serve it; -> a drive
    time.sleep(3600)                                     # serve (Ctrl-C to stop)
    ```

=== "C"

    ```c
    #include <stdio.h>
    #include <unistd.h>
    #include "usbip-device.h"
    #include "classes/msc.h"

    #define BLOCK_SIZE 512

    static uint32_t num_of_blocks;      /* the image's size, in blocks */

    static uint32_t get_num_of_blocks(void *u) { 
        (void)u;
        return num_of_blocks; 
    }

    static uint32_t get_block_size(void *u) { 
        (void)u;
        return BLOCK_SIZE; 
    }

    static int disk_read(void *u, uint32_t lba, uint32_t n, uint8_t *b) {
        FILE *fp = u;
        if (fseek(fp, (long)lba * BLOCK_SIZE, SEEK_SET) != 0)
            return -1;

        if (fread(b, 1, n * BLOCK_SIZE, fp) == n * BLOCK_SIZE)
            return  0;
        else 
            return -1;
    }

    static int disk_write(void *u, uint32_t lba, uint32_t n, const uint8_t *b) {
        FILE *fp = u;
        if (fseek(fp, (long)lba * BLOCK_SIZE, SEEK_SET) != 0)
            return -1;

        if (fwrite(b, 1, n * BLOCK_SIZE, fp) == n * BLOCK_SIZE)
            return  0;
        else 
            return -1;
    }

    int main(int argc, char **argv) {
        if (argc != 2) {
            fprintf(stderr, "usage: %s <disk.img>\n", argv[0]);
            return 1;
        }

        FILE *fp = fopen(argv[1], "r+b");                 /* an existing image, as it is */
        if (!fp) {
            perror(argv[1]);
            return 1;
        }

        setvbuf(fp, NULL, _IONBF, 0);                     /* reads and writes alternate */
        fseek(fp, 0, SEEK_END);                           /* capacity = the image's size */
        num_of_blocks = (uint32_t)(ftell(fp) / BLOCK_SIZE);
        if (num_of_blocks == 0) {
            fprintf(stderr, "%s: smaller than one %d B block\n", argv[1], BLOCK_SIZE);
            return 1;
        }

        usbip_device *g = usbip_device_create(0x1209, 0x0008);
        msc_opts opts = {
            .num_blocks = get_num_of_blocks,
            .block_size = get_block_size,
            .read       = disk_read,
            .write      = disk_write,
            .user       = fp,         /* passed back to disk_read/disk_write */
        };
        msc_add(g, &opts);
        usbip_device_plug(g, NULL);                       /* serve it; -> a drive */
        for (;;) sleep(1);
    }
    ```

## Mount it

Once a [client](../../platforms/index.md) has imported the device, the host's in-box
mass-storage driver binds it and it behaves like any other USB drive.

=== "Linux"

    `usb-storage` binds it as `/dev/sd*`:

    ```bash
    lsblk
    sudo mount /dev/sdX1 /mnt
    sudo dd if=/dev/sdX bs=512 count=1 of=first.bin    # read the boot sector
    ```

=== "Windows"

    `USBSTOR` binds it and it gets a drive letter - browse it in Explorer. A
    floppy-declared device (see [Floppy drives](#floppy-drives)) shows up as a floppy
    drive instead of a removable disk.

## Blocks, LBAs and media types

Both take the image path on the command line and serve exactly what is already there:
the capacity is the file's own size, and neither creates, grows nor truncates it, so
pointing either at a prepared image - a formatted disk, an ISO - cannot damage it. Make
a blank image of whatever size you want and format it with your OS's own tools, or just
let the host format the drive after it attaches. (On Linux: `truncate -s 1M disk.img`
then `mkfs.vfat disk.img`.)

A **block** is a fixed-size chunk of storage (here 512 bytes), addressed by its **LBA**
(logical block address). The class translates the host's SCSI read/write commands into
calls to your `read`/`write` over a range of blocks - you never parse SCSI yourself.

Present a write-protected medium with `read_only=True` / `.read_only = 1`. What the
medium *is* comes from one field: `medium="disk"` / `.medium = MSC_MEDIUM_DISK` (the
default), `"cdrom"` / `MSC_MEDIUM_CDROM` for a read-only CD-ROM, or `"floppy"` /
`MSC_MEDIUM_FLOPPY` for a floppy.

## Several drives on one interface (LUNs)

A mass-storage interface can carry up to 16 **logical units** - what a card reader's
slots are, or a disk shipped beside its install CD. They share one pair of bulk pipes
and answer one command at a time; the host picks the unit per command, having asked
`GET_MAX_LUN` how many there are. Each unit is a store of its own, and can be its own
kind of medium:

=== "Python"

    ```python
    disk = ImageStore("disk.img")
    cd = ImageStore("install.iso")
    cd.medium, cd.read_only = "cdrom", True    # a store says what it is
    dev.add(MSC([disk, cd]))                   # two drives, one interface
    ```

    A store may carry `medium`, `read_only`, `product` and `serial`; anything it does
    not carry comes from the `MSC(...)` arguments.

=== "C"

    ```c
    msc_opts opts = {
        .num_blocks = get_num_of_blocks,       /* one backend, called per unit */
        .block_size = get_block_size,
        .read       = disk_read,
        .write      = disk_write,
        .n_luns     = 2,
        .luns = {
            { .user = &disk },                 /* unit 0: a plain read-write disk */
            { .user = &cd, .medium = MSC_MEDIUM_CDROM },   /* unit 1: its install CD */
        },
        .on_command = log_scsi,
        .user       = &app,                    /* on_command's context */
    };
    msc_add(g, &opts);
    ```

    Each `msc_lun` field left zero or `NULL` falls back to the device-wide `msc_opts`
    one, and with `n_luns` unset (the everyday case) there is a single unit backed by
    `opts.user` - which is why every single-LUN program above needs none of this.

The host treats the units as separate drives: Linux gives each its own `/dev/sd*`,
Windows its own drive letter. Both examples take the images on the command line,
comma-separated, and a tag on an entry says what that unit is:

```bash
msc_device --file disk.img,install.iso:cdrom,key.img:ro   # a disk, a CD and a locked key
msc_device --file a.img,b.img --size 8M,64M               # two disks, sized separately
```

The tags are `:cdrom`, `:floppy`, `:disk` and `:ro`; an entry without one takes the
device-wide flags. `--size` and `--block-size` take one value for every image, or one
per image, and only ever size an image that is not there yet - an existing image keeps
its own size, as always.

## Floppy drives

What makes a host treat a disk as a floppy is its geometry. Give the store one of the
standard capacities and the class answers MODE SENSE page 0x05 (Flexible Disk) with
that format's cylinders, heads and sectors, and offers the whole set through READ
FORMAT CAPACITIES - so Windows can format it:

| Format | Code | Blocks (512 B) | C/H/S |
|--------|------|----------------|-------|
| 2.88M  | `FLOPPY_2_88M` / `MSC_FLOPPY_2_88M` | 5760 | 80 / 2 / 36 |
| 1.44M  | `FLOPPY_1_44M` / `MSC_FLOPPY_1_44M` | 2880 | 80 / 2 / 18 |
| 1.2M   | `FLOPPY_1_2M` / `MSC_FLOPPY_1_2M`   | 2400 | 80 / 2 / 15 |
| 720K   | `FLOPPY_720K` / `MSC_FLOPPY_720K`   | 1440 | 80 / 2 / 9  |
| 360K   | `FLOPPY_360K` / `MSC_FLOPPY_360K`   |  720 | 40 / 2 / 9  |

Size the image with `floppy_format_size(FLOPPY_1_44M)` /
`msc_floppy_format_size(MSC_FLOPPY_1_44M)`.
A capacity matching none of these still works - the class reports the real block count
with 1.44M's layout - but no host will call it a standard floppy.

Add `ufi=True` / `.ufi = 1` to declare the **UFI** command set (`bInterfaceSubClass`
0x04, SFF-8070i) instead of SCSI transparent (0x06): the subclass a real USB floppy
drive reports, and what makes Windows show a floppy drive rather than a removable disk.
The transport stays Bulk-Only either way. The UFI commands themselves - FORMAT UNIT,
READ(12)/WRITE(12), VERIFY, SEEK, REZERO UNIT - are answered whatever the subclass.

```python
dev.add(MSC(store, medium="floppy", ufi=True))             # a 1.44 MB USB floppy drive
```

Full program: `examples/msc_device.py`; in C, `examples/device/msc_device.c`. API:
[MSC](../../api/classes-device.md#msc-mass-storage-scsi-bulk-only-transport).
