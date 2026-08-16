# Environment variables

None of the `USBIP_*` variables is required, and none changes the USB behaviour of a
device - they turn on diagnostics, or tell the C *wrappers* where to connect.

| Variable      | Read by | Default | Effect |
|---------------|---------|---------|--------|
| `USBIP_PCAPNG`| the Python library **and** both C libraries | unset - off | write every transfer to a PCAPNG capture file |
| `USBIP_DEBUG` | the device core (Python and C) | unset - off | log every control request (with the verdict) and every data transfer (Python prints to stdout, C to stderr) |
| `USBIP_HOST` | the C `libusb-1.0` / `libusbK` wrappers **only** | `127.0.0.1` | which USB/IP server to connect to |
| `USBIP_PORT` | the C `libusb-1.0` / `libusbK` wrappers **only** | `3240`      | its TCP port |

=== "Linux / macOS"

    ```bash
    USBIP_PCAPNG=traffic.pcapng python3 examples/doc/boot_keyboard.py   # capture
    USBIP_DEBUG=1 python3 examples/doc/boot_keyboard.py                 # log requests
    ```

=== "Windows"

    ```bat
    set USBIP_PCAPNG=traffic.pcapng
    python examples\doc\boot_keyboard.py
    ```

`USBIP_PCAPNG=1` (or `on`/`yes`/`true`/`auto`) auto-names the file after the program, in
the current directory; `%p` in the value expands to the process id. Details in
[Capturing traffic](capture.md).

These pages write their examples for a POSIX shell. Only the syntax differs:

| | Linux / macOS (`sh`) | Windows `cmd` | PowerShell |
|---|---|---|---|
| for one command only | `USBIP_DEBUG=1 prog` | *not supported* | *not supported* |
| for the rest of the session | `export USBIP_DEBUG=1` | `set USBIP_DEBUG=1` | `$env:USBIP_DEBUG = "1"` |
| unset it again | `unset USBIP_DEBUG` | `set USBIP_DEBUG=` | `Remove-Item Env:USBIP_DEBUG` |

!!! warning "One `set` per line"
    In `cmd`, `set USBIP_PORT=4000 && prog` makes the space before `&&` part of the
    value, and the connection then fails with nothing obviously wrong in the command.

Substituting the [`libusb-1.0` / `libusbK` wrapper](../c-reference.md) for the system
library is the one step that is not an environment variable on Windows:

| | Linux | macOS | Windows |
|---|---|---|---|
| load the wrapper, not the system libusb | `LD_PRELOAD=/path/to/libusb-1.0.so.0` | `DYLD_LIBRARY_PATH` (SIP blocks `DYLD_INSERT_LIBRARIES`) | copy `libusb-1.0.dll` into the program's own directory, which Windows searches first |
| find your own shared library at run time | `LD_LIBRARY_PATH` | `DYLD_LIBRARY_PATH` | beside the `.exe`, or on `PATH` |

[pyusb](../host/pyusb.md) needs neither: it takes the backend path as an argument.

!!! warning "`USBIP_HOST` / `USBIP_PORT` are not read by the Python host"
    A Python program picks its server in code -
    `usbip.host.open(..., transport=usbip.USBIP(host, port))`. The variables exist
    because a stock libusb or libusbK binary loading a
    [C wrapper](../c-reference.md) has no place to pass one; they matter to Python
    only when you point **pyusb** at that wrapper (see
    [pyusb](../host/pyusb.md)). The wrappers read them once,
    inside `libusb_init()` - set them before it runs.
