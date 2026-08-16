# Windows

Windows has no in-box USB/IP client; install one. 
E.g. [usbip-win2](https://github.com/vadimgrn/usbip-win2) for Windows 10/11, or the older
[usbip-win](https://github.com/cezanne/usbip-win).

From an **Administrator** prompt:

```sh
usbip.exe attach -r <server-ip> -b 1-1
```

or from usbip-win2's GUI, which lists what the server offers and attaches the bus id
you pick:

![usbip-win2 attaching bus id 1-1 from a server on localhost:3240](../img/usbip-win2-gui.png)

