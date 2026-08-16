# Sound Card (UAC)

A UAC1 audio function is a stereo **speaker** (isochronous OUT) plus a mono
**microphone** (isochronous IN), 48 kHz / 16-bit. Provide a microphone source and a
speaker sink - or omit `mic_source` and the class plays a built-in 440 Hz tone.

## A tone-generator sound card

=== "Python"

    ```python
    import time, usbip
    from usbip.device import USBDevice
    from usbip.classes.device import UAC

    def spk_sink(data):          # PCM the host played to the speaker
        pass

    dev = USBDevice(0x1209, 0x0007, product="USBIP Audio")
    dev.add(UAC(spk_sink=spk_sink))        # mic = built-in tone
    dev.set_iso_pacing(True)               # play at real time (see below)
    dev.plug()                             # -> an ALSA card
    time.sleep(3600)                       # serve (Ctrl-C to stop)
    ```

=== "C"

    ```c
    #include <unistd.h>
    #include "usbip-device.h"
    #include "classes/uac.h"

    static void spk(void *u, const uint8_t *d, int n) { 
        (void)u; (void)d; (void)n; 
    }

    int main(void) {
        usbip_device *g = usbip_device_create(0x1209, 0x0007);
        uac_opts opts = {
            .spk_sink = spk,     /* mic_source NULL -> 440 Hz tone */
        };
        uac_add(g, &opts);
        usbip_device_set_iso_pacing(g, 1);    /* play at real time */
        usbip_device_plug(g, NULL);           /* -> an ALSA card */
        for (;;) sleep(1);
    }
    ```

## Record and play

Once a [client](../../platforms/index.md) has imported the device, the host's in-box
audio driver binds it and it becomes an ordinary sound device - recording from it
yields the microphone stream (the 440 Hz tone unless you supply one), and playing to it
lands in `spk_sink()`.

=== "Linux"

    `snd-usb-audio` binds it as an ALSA card:

    ```bash
    aplay -l                                      # find the card
    arecord -D plughw:CARD -d 3 mic.wav           # record the device's mic stream
    aplay   -D plughw:CARD song.wav               # play into spk_sink()
    ```

=== "Windows"

    `usbaudio.sys` binds it. Pick it under Settings → Sound, then record with Voice
    Recorder or play anything to it.

!!! warning "Enable isochronous pacing for audio"
    Without `dev.set_iso_pacing(True)` / `usbip_device_set_iso_pacing(g, 1)` audio
    free-runs many times too fast - USB/IP has
    [no bus frame clock](../../platforms/hw-clients.md).

Full program: `examples/audio_device.py` (with `--mic-in`/`--out` WAV); in C,
`examples/device/audio/`. API: [UAC](../../api/classes-device.md#uac-usb-audio-class-10).
