# servo-hub firmware (ESP32 + PCA9685)

A generic, safe servo and input hub for the LabRat bench. It drives up to 16 hobby servos through a PCA9685 at 50 Hz
and reports switch and ADC inputs. It speaks one JSON-lines protocol ([`docs/protocol.md`](../../docs/protocol.md) §4)
on three transports at once:

| transport | where | who uses it |
|---|---|---|
| `Serial` 115200 | the DevKit's USB-UART connector ("UART" on the S3 board) | laptop bridge (`bridge/labrat_bridge/servo_hub.py`), or an Android phone over USB-OTG |
| `USBSerial` (CDC) | the ESP32-S3's native "USB" connector (S3 only) | same as above, either cable works |
| BLE Nordic UART | advertises `labrat-hub` | the phone page (Web Bluetooth) |

The firmware knows nothing about pipettes, probes or phone stands: the cloud DO owns those meanings and their
calibration. Wiring, BOM and power: [`hardware/electronics`](../../hardware/electronics).

## Build, test, flash
```bash
uv tool install platformio          # once (or prefix the commands with `uvx`)
cd firmware/servo-hub
pio test -e native                  # protocol / motion / watchdog / input tests on the host (Unity)
pio run -e esp32s3                  # ESP32-S3-DevKitC-1
pio run -e esp32                    # classic ESP32 DevKitC (WROOM-32)
pio run -e esp32s3 -t upload        # flash (add --upload-port /dev/ttyACM0 if several boards are plugged in)
pio device monitor -b 115200        # type JSON lines, e.g. {"id":1,"cmd":"hello"}
```
If the S3 does not enter the bootloader on its own, hold **BOOT**, tap **RST**, release BOOT, and upload again.
The same bench console works from the bridge: `cd bridge && uv run python -m labrat_bridge hub '{"cmd":"hello"}'`
(it keeps pinging, so the watchdog stays quiet).

## Layout
```
include/board.h            pin map per board (HUB_BOARD_ESP32S3 / HUB_BOARD_ESP32), input + ADC tables
lib/hub_core/src/          hardware-free logic, unit-tested natively
  hal.h                    the HAL interface (PWM, OE, inputs, ADC, NVS) + transports + board description
  hub.h / hub.cpp          protocol, per-channel motions, easing, inputs/debounce, estop, watchdog, cfg
  servo_math.h             PCA9685 prescale / tick math, deg <-> us, smoothstep
  line_assembler.h         byte stream -> lines (256-byte limit)
src/                       ESP32 only
  main.cpp                 transports -> Hub, loop at ~1 kHz
  hal_esp32.*              Hal on Wire / GPIO / analogReadMilliVolts / Preferences
  pca9685.*                minimal PCA9685 driver (re-probed every 1 s, outputs restored after a chip reset)
  ble_nus.*                Nordic UART service (Arduino BLE / Bluedroid), queued MTU-sized notifications
test/test_hub, test_math   Unity tests; test/support/fake_hal.h is the fake HAL
```

## Pin map
| signal | ESP32-S3-DevKitC-1 | ESP32 DevKitC |
|---|---|---|
| I2C SDA / SCL → PCA9685 | GPIO8 / GPIO9 | GPIO21 / GPIO22 |
| PCA9685 OE (optional) | GPIO10 | GPIO23 |
| `syr_home` (NO switch to GND) | GPIO4 | GPIO26 |
| `estop` (**NC** button to GND; jumper to GND if none) | GPIO5 | GPIO27 |
| `in1`, `in2` (switch to GND) | GPIO6, GPIO7 | GPIO25, GPIO33 |
| `a0`, `a1` (ADC1, mV) | GPIO1, GPIO2 | GPIO34, GPIO35 |

Inputs use the internal pull-ups, so they need no resistors. An input reads `1` when its switch is closed. `estop`
reads `1` when it is engaged, that is, when its NC contact is open.

## Protocol examples
Requests carry an integer `id`, and the reply echoes it. Timed commands (`servo`/`pulse` with `ms`) reply when they
finish, and everything else keeps being served meanwhile.
```
> {"id":1,"cmd":"hello"}
< {"id":1,"ok":true,"fw":"servo-hub/1.0.0","board":"esp32-s3-devkitc-1","channels":16,"inputs":["syr_home","estop","in1","in2"],"adc":["a0","a1"],"uptime_ms":5123,"pwm":true,"estop":0}
> {"id":2,"cmd":"servo","ch":2,"deg":120,"ms":800}          eased (smoothstep) over 800 ms
> {"id":3,"cmd":"servo","ch":3,"deg":40,"ms":500}           runs concurrently on another channel
< {"id":3,"ok":true,"deg":40}
< {"id":2,"ok":true,"deg":120}
> {"id":4,"cmd":"pulse","ch":0,"us":1300,"ms":4000,"until":"syr_home"}   drive the plunger down until the switch closes
< {"ev":"in","name":"syr_home","v":1}
< {"id":4,"ok":true,"us":1300,"ms":2410,"stopped_by":"syr_home"}         ch0 is back at rest_us (1500 = neutral)
> {"id":5,"cmd":"servo","ch":1,"deg":10,"ms":1000}
> {"id":6,"cmd":"servo","ch":1,"deg":170}                  a new command on ch1 preempts id 5
< {"id":5,"ok":false,"err":"preempted"}
< {"id":6,"ok":true,"deg":170}
> {"id":7,"cmd":"cfg","ch":4,"kind":"cont","rest_us":1500}  persisted in NVS
< {"id":7,"ok":true,"cfg":{"ch":4,"min_us":500,"max_us":2500,"rest_us":1500,"kind":"cont"}}
> {"id":8,"cmd":"read"}
< {"id":8,"ok":true,"inputs":{"syr_home":1,"estop":0,"in1":0,"in2":0},"adc":{"a0":12,"a1":0},"ch":[1500,2500,1833,944,0,0,0,0,0,0,0,0,0,0,0,0]}
> {"cmd":"ping"}                                            id-less keepalive: no reply
(2 s of silence)
< {"ev":"wd"}                                               every servo relaxed
```

## Behaviour details and choices beyond protocol.md §4
- **Pulse math:** PCA9685 prescale = round(25 MHz / (4096 × 50)) − 1 = 121, giving 50.03 Hz. One tick is 4.88 µs,
  and 1500 µs is 307 ticks. The internal oscillator varies by a few % between chips. If you measure a 1500 µs pulse
  as something else, rebuild with `-DPCA_OSC_HZ_TRIM=...`.
- **Channel defaults** (until `cfg` changes them): ch0 `cont` 700-2300 µs, rest 1500 (FS90R syringe). Every other
  channel is `pos` 500-2500 µs = 0-180°, rest 0 (off). `deg` maps linearly over `min_us..max_us`.
- `servo` on a `cont` channel is refused (`err:"kind"`); continuous servos take `pulse`.
- `servo` with `ms` eases from the current pulse. For a relaxed servo it uses the last pulse it was driven to. A
  servo that was never driven jumps to the target and the reply still comes after `ms`, so "wait until settled"
  semantics hold.
- `pulse`: `us` must be 0 (off) or within the channel's `min_us..max_us` (`err:"range"`). `until` requires `ms`
  (`err:"until_needs_ms"`), so no drive is ever unbounded. If the `until` input already reads 1, the hub does not
  drive at all and replies `{ms:0, stopped_by}`. The reply always carries `us`, the actual `ms`, and `stopped_by`
  (the input name, or `null`).
- `ms` is limited to 0..60000.
- Cancelled motions reply `ok:false` with `err` = `preempted` (new command on the channel, `off ch`, or a changed
  `cfg`), `stopped` (`stop`, or `off` for all channels), `watchdog`, or `estop`.
- `stop` and the watchdog put continuous channels to `rest_us` for 100 ms, then switch everything off. The estop
  switches everything off at once and raises OE.
- `cfg` needs `ch`. With no other fields it only reads the channel. `reset:true` restores the default and erases the
  stored copy. An identical `cfg` (the DO resends it after every restart) neither writes flash nor preempts a motion.
- `estop` engaged: `servo` and `pulse` (except `us:0`) are refused with `err:"estop"`. `hello` also reports
  `"estop":0|1` and `"pwm":true|false`. Motion commands fail with `err:"pwm"` if the PCA9685 does not answer.
- Debounce: 20 ms for switches, 10 ms for the estop. `until` uses the debounced value. Inputs start at their current
  level without an event. An estop that is open at boot reports `{"ev":"estop","v":1}` right after `boot`.
- Watchdog: armed by the first line, trips once per silence (`ev:"wd"`), and re-armed by any line on any transport.
- Errors without a usable request: `{"ok":false,"err":"json"}`, `"too_long"` (over 256 bytes), `"unknown_cmd"`. Bad
  arguments give `"ch"`, `"args"`, `"range"`, `"input"`, `"kind"`. A failed flash write gives `"nvs"`.
- Replies go back on the transport the request came from, and events go to every transport.
- BLE: incoming bytes are queued, and outgoing lines are sent as notifications of (negotiated MTU − 3) bytes,
  one every 4 ms. Advertising restarts after a disconnect.
- Boot window: between a reset and `setup()` (~0.3 s), a PCA9685 that stayed powered keeps its last pulses. The
  firmware then turns everything off. OE has no pull-up resistor (the few-resistors constraint), so wiring OE does not
  cover this gap.
