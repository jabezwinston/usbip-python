# Media Transfer (MTP)

MTP is the protocol phones and cameras use to expose files to a computer. Export one or
more directory trees as MTP storages and any host file manager can browse, download and
(unless read-only) upload, delete and rename them. The class implements the protocol and
object model and touches no filesystem itself; you supply a **storage backend** (a
directory-backed store like the one in the example below).

## Export a directory tree

=== "Python"

    ```python
    import os, time, usbip
    from usbip.device import USBDevice
    from usbip.classes.device import MTP

    class DirStore:                                 # the backend is yours; "" is the root
        read_only, description = False, "USB over IP"

        def __init__(self, root):
            self.root = os.path.abspath(root)
            os.makedirs(self.root, exist_ok=True)

        def _real(self, path):
            return os.path.join(self.root, *[p for p in path.split("/") if p])

        def listdir(self, path):
            return sorted(os.listdir(self._real(path)))

        def stat(self, path):                       # (is_dir, size, mtime)
            st = os.stat(self._real(path))
            is_dir = os.path.isdir(self._real(path))
            return (is_dir, 0 if is_dir else st.st_size, st.st_mtime)

        def read(self, path, off=0, size=None):
            with open(self._real(path), "rb") as f:
                f.seek(off)
                return f.read() if size in (None, 0xFFFFFFFF) else f.read(size)

        # write / mkdir / remove / rename / disk_usage complete a read-write store -
        # see examples/mtp_device.py for the full FilesystemStore

    store = DirStore("shared")                      # export ./shared
    dev = USBDevice(0x1209, 0x0009, product="USBIP MTP")
    dev.add(MTP(store, name="USBIP MTP", manufacturer="USB over IP"))
    dev.plug()
    time.sleep(3600)                                # serve (Ctrl-C to stop)
    ```

=== "C"

    ```c
    #include <unistd.h>
    #include "usbip-device.h"
    #include "classes/mtp.h"

    static int info(void *s, const char *path, int *dir, uint64_t *sz, uint32_t *mt) {
        (void)s; (void)sz; (void)mt;
        if (path[0])
            return -1;    /* only the (empty) root exists */
        *dir = 1;
        return 0;
    }
    static void ls(void *s, const char *p, mtp_emit e, void *c) {
        (void)s; (void)p; (void)e; (void)c;
        /* the root is empty - no files or folders */
    }

    int main(void) {
        usbip_device *g = usbip_device_create(0x1209, 0x0009);
        mtp_opts opts = {
            .name      = "USBIP MTP",
            .read_only = 1,
            .n_stores  = 1,
            .stores    = { (void *)"" },
            .listdir   = ls,
            .get_info  = info,
        };
        mtp_add(g, &opts);
        usbip_device_plug(g, NULL);
        for (;;) sleep(1);
    }
    ```

## Browse it

MTP needs no driver install: the class advertises the Microsoft OS *Compatible ID* that
makes hosts load their own portable-device driver. Once a
[client](../../platforms/index.md) has imported the device, the storages you exported
show up as browsable folders.

=== "Linux"

    There is no kernel driver - MTP is spoken from userspace. A desktop auto-mounts it
    through gvfs; headless, use libmtp:

    ```bash
    gio mount -li               # desktop: the gvfs mount
    mtp-detect ; mtp-files      # headless (libmtp)
    ```

=== "Windows"

    WPD binds it: Explorer shows it as a portable device with no driver install -
    browse, copy in and out, rename and delete.

!!! note "MTP on a desktop"
    gvfs claims an MTP device the moment it enumerates, so `mtp-detect` reports it as
    busy. Browse it through Files / `gio` instead, or stop gvfs first.

## Files, not blocks

Unlike mass storage (which exposes raw blocks), MTP works at the level of **files and
folders**: the host asks for a listing or a named object and the class calls your
backend's `listdir` / `read` / `write`. Set `read_only=True` / `.read_only = 1` to
reject host modifications.

Full program: `examples/mtp_device.py`; in C, `examples/device/mtp_device.c`. API:
[MTP](../../api/classes-device.md#mtp-media-transfer-protocol).
