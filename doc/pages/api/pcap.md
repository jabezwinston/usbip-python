# Capture (pcap)

Record every transfer to a **PCAPNG** file that Wireshark opens with its usual
USB dissectors. Capture normally turns on by itself via the `USBIP_PCAPNG`
environment variable ([Capturing traffic](../tools/capture.md)); this module
is the in-code alternative. `submit()` / `complete()` are called by the
transport internals - applications only need `open()`, `close()` and
`is_enabled()`.

::: usbip.pcap
    options:
      members:
        - open
        - close
        - is_enabled
