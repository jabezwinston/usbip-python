# Capturing traffic

Every transfer can be recorded to a **PCAPNG** file and opened in Wireshark, which
applies its usual USB and class dissectors - handy for seeing exactly what the host and
device exchanged. Capture only **observes** traffic; it never alters it.

Capture is controlled by the `USBIP_PCAPNG` environment variable - one of the four
`USBIP_*` variables listed in
[Getting Started](env.md).

## Recording a capture

=== "Linux / macOS"

    ```bash
    USBIP_PCAPNG=traffic.pcapng python3 examples/hid_device.py
    USBIP_PCAPNG=traffic.pcapng hid_device              # the C build of the same example
    wireshark traffic.pcapng
    ```

=== "Windows"

    ```bat
    set USBIP_PCAPNG=traffic.pcapng
    python examples\hid_device.py
    wireshark traffic.pcapng
    ```

    `hid_device.exe` is the C build of the same example. One `set` per line - see
    [Environment variables](env.md).

The value is a file name, with two conveniences:

- `1`, `on`, `yes`, `true`, `auto` or an empty value auto-name the file after the
  program (`hid_device.pcapng`), in the **current directory**.
- `%p` anywhere in the value expands to the process id - `USBIP_PCAPNG=cap-%p.pcapng`.

It works on **both sides of the wire**: set it on the server to capture what the
device sees, or on the client to capture what the importer sent. Either side records
each transfer as what it is - control, bulk, interrupt or isochronous.

## Starting and stopping capture in code

=== "Python"

    ```python
    import time, usbip
    from usbip import pcap
    from usbip.device import USBDevice
    from usbip.classes.device import CDCACM

    pcap.open("traffic.pcapng")               # record every transfer
    dev = USBDevice(0x1209, 0x0001, product="USBIP CDC-ACM")
    dev.add(CDCACM())
    dev.plug()
    time.sleep(3600)                          # pcap.close() on exit
    ```

    `usbip.pcap` is the in-code capture API: `pcap.open(path)` starts recording,
    `pcap.close()` finishes the file, and `pcap.is_enabled()` reports whether a
    capture (from code or from `USBIP_PCAPNG`) is active.

=== "C"

    In C the capture is driven entirely by the environment - no code, no rebuild:

    ```sh
    USBIP_PCAPNG=traffic.pcapng ./my_device          # Linux / macOS
    ```

    ```bat
    set USBIP_PCAPNG=traffic.pcapng
    my_device.exe
    ```

Each transfer is written as a Linux *usbmon* submit/complete pair, so Wireshark treats
the file exactly like a capture from real hardware - including the HID, CDC, MSC and
other class dissectors.

## Logging transfers without a capture

When a host sends something the device rejects, a capture shows *that* it STALLed;
`USBIP_DEBUG` names the request as it happens, and logs each data transfer alongside
it - enough to answer "is anything moving at all?" without opening Wireshark:

```bash
USBIP_DEBUG=1 python3 examples/doc/boot_keyboard.py   # Windows: set USBIP_DEBUG=1 first
[usbip_device] ctrl type=0x80 req=0x06 val=0x0200 idx=0x0000 len=64 -> ok
[usbip_device] ctrl type=0x81 req=0x06 val=0x2200 idx=0x0000 len=128 -> ok
[usbip_device] ctrl type=0x21 req=0x09 val=0x0200 idx=0x0000 len=1 -> ok
[usbip_device] IN  ep=0x81 interrupt len=8                # a key report went out
[usbip_device] IN  ep=0x81 interrupt len=8                # the release report
```

A data line carries the direction, the endpoint address, the endpoint's transfer type
and the bytes that actually moved. Isochronous lines add the packet count
(`len=14362 16 pkts`, the de-padded total). An IN URB with no data yet logs
`-> parked, no data yet`, then logs again with the real length once the device
writes.

A `-> STALL` line is the device saying "no": either a request you chose not to answer
(a Device Qualifier, say, which a full-speed device must STALL) or a bug in a handler.
The Python core prints these to stdout, the C core to stderr; the format is identical.

See also [USB concepts](../concepts.md) to make sense of the SETUP packets and transfer
types Wireshark shows.
