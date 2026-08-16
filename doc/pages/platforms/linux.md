# Linux

Linux has a built-in USB/IP client - the **`vhci-hcd`** virtual host controller - so a
USBIP virtual device imported into the kernel behaves exactly like real hardware.

Your program is the [USB/IP **server**](index.md) here: it listens, and `usbip attach`
is the client that connects to it.

```bash
sudo modprobe vhci-hcd                       # load the virtual HCD (once per boot)
sudo usbip attach -r 127.0.0.1 -b 1-1        # import the device served on :3240
```
