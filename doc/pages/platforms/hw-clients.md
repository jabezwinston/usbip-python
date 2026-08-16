# Hardware clients

Any importer that speaks USB/IP works, including embedded ones that drive a real USB
host controller. This library implements both sides of the wire format.

The board re-presents the device on its own USB port. The machine it plugs into needs no
USB/IP support at all - which is why this is the practical answer on [macOS](macos.md),
where no usable client exists.

Every transfer is a network round trip. A 1 ms `bInterval` will not hold over a congested
link. Declare realistic intervals, and keep timing assumptions out of the device's
behaviour.

[USBIP for microcontrollers](https://github.com/jabezwinston/usbip-for-uc) is a ready-made
importer firmware. The board joins your WiFi, imports a device from a server built on this
library, and **re-presents it on its own USB-OTG port**. The PC it plugs into sees a plain
USB device.

Images for the Raspberry Pi Pico W / Pico 2 W and the ESP32-S2 / S3 are on the project's
[Releases](https://github.com/jabezwinston/usbip-for-uc/releases) page. The same firmware
targets the Pi Zero W / 2 W and - over wired Ethernet instead of WiFi - the STM32F767/F429
and CH32V307.

A release image carries no WiFi credentials. The board comes up as an open SoftAP named
`usbip-<board>-<xxxx>`: join it, open <http://192.168.4.1/>, and enter your WiFi and the
USB/IP server's address.

The board's shell does the same. It runs on the console (UART / USB-CDC), and over telnet
once the board is on the network - `telnet <board-ip>`, or `nc <board-ip> 23`. The board
answers to `usbip-<board>.local`, so you need not hunt for its lease.
```sh
usbip-client@pico_w > wifi connect "my ssid" "my password"
usbip-client@pico_w > sys save
usbip-client@pico_w > dev list 192.168.0.5
  # busid        vid:pid    class    spd if
  0 1-1          1209:0011  00/00/00   2 1
usbip-client@pico_w > dev attach 1-1
attach ok
```

The board's USB port now *is* that device to whatever it is plugged into. `dev detach`
returns it to idle.

The board can acquire its device unaided - useful for a headless dongle:

```sh
usbip-client@pico_w > dev auto add 192.168.0.5:3240
usbip-client@pico_w > dev auto on
usbip-client@pico_w > sys save
```

It sweeps the saved servers - up to 4, one candidate per pass - and imports the first
device it finds, preferring a saved busid. Failures back off from 5 s to 60 s. If your
server exits, the board drops the device and re-presents it when the server returns.
`dev detach` also turns auto-attach off; otherwise the next sweep would import the device
straight back.

Keep the server PC's own 2.4 GHz radio out of the way. Both of these stall transfers, and
both look exactly like a board bug:

- **WiFi power save.** Transfers stall in ~230 ms retransmission timeouts. Turn it off:
  `nmcli connection modify "<name>" 802-11-wireless.powersave 2`.
- **Bluetooth discovery.** On a combo WiFi/BT card, a BT inquiry time-slices the radio
  into 20-100 ms stalls. Stop scanning while a device is attached.
