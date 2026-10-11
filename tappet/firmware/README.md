# Tappet body firmware (ESP32-S3)

This firmware runs on an ESP32-S3-DevKitC-1 driving a PCA9685 servo board. It implements the
body protocol in `docs/SPEC.md` §4, which is newline-delimited JSON. The same protocol works over
two transports:

* **UART0** at 115200 baud, on the DevKit's "UART" USB port (the CP210x/CH343 bridge). Use this
  for the host CLI, calibration and bench work.
* **AOA (Android Open Accessory)** on the S3's native USB OTG port (GPIO19/20), with the ESP32
  acting as USB *host*. This is how the Android head talks to the body (§5).

Responses go back on the transport the request came from. Events (`boot`, `done`, `error`,
`status`, `button`, `fault`, `watchdog`) go to both transports.

## Build, flash, test

```sh
pip install platformio            # or use the VS Code extension
cd firmware
pio run -e esp32s3                # build
pio run -e esp32s3 -t upload      # flash through the "UART" port
pio device monitor -e esp32s3     # JSON console @115200
pio test -e native                # host-side unit tests (no hardware needed)
python3 tools/gen_config.py       # regenerate include/robot_config.h after editing config/robot.json
python3 tools/gen_config.py --check
```

The build uses the pioarduino platform (Arduino-ESP32 3.3 / ESP-IDF 5.5), which ships the
ESP-IDF USB Host Library. The first build downloads about 1 GB of toolchain. TinyUSB/USB-CDC is
turned off (`ARDUINO_USB_CDC_ON_BOOT=0`) so that `Serial` is UART0 and the OTG PHY is free for
host mode.

Quick smoke test over the serial monitor:

```json
{"id":1,"cmd":"hello"}
{"id":2,"cmd":"enable"}
{"id":3,"cmd":"neck","pan":20}
{"id":4,"cmd":"tap","x":0,"y":160,"z":13.8}
{"id":5,"cmd":"relax"}
```

## Layout

| path | contents |
|------|----------|
| `include/robot_config.h` | **Generated** from `config/robot.json` (joints, geometry, pins, AOA strings). Do not edit by hand. |
| `lib/tappet_core/` | Code with no hardware dependencies, so it builds both for the ESP32 and natively for tests. |
| `  calibration.*` | Runtime calibration and geometry, validation, a CRC-protected NVS blob. |
| `  joint_map.*` | Joint angle → servo angle → pulse width (SPEC §2), with clamping. |
| `  kinematics.*` | Arm FK/IK (SPEC §3), computed in double precision, plus limit and linkage checks. |
| `  minjerk.h` | Minimum-jerk profile and default durations. |
| `  motion.*` | Motion queue (depth 16), the 100 Hz engine, and the tap/swipe/park sequences. |
| `  robot.*` | Protocol dispatcher, watchdog, streaming, fault supervision, button and LED state. |
| `  hal.h` | Hardware interface (`Hal`, `LineSink`). |
| `src/main.cpp` | ESP32 HAL (PCA9685, ADC, VBUS switch, NVS, LED, button), plus the UART and AOA plumbing. |
| `src/pca9685.*` | Minimal PCA9685 driver. Re-initialises the chip if it reappears after a reset. |
| `src/aoa_host.*` | USB host client: AOA handshake, claim of the accessory interface, bulk I/O tasks. |
| `test/` | Unity suites run with `pio test -e native`. |

## Behaviour notes

These are the points that matter when you compare the firmware with `host/tappet/sim.py`.

* **Motion.** Motion runs at 100 Hz with minimum-jerk interpolation. Joint moves interpolate in
  joint space. `move`, the descend/rise legs of a tap or swipe, and the swipe stroke move the tool
  tip along a straight line in Cartesian space, solving IK on every tick. The transit to a point
  above the target is a joint-space move.
* **Default `ms`.**
  * Joint moves: `max(60, 1.875·|Δq|max/max_vel·1000)`, rounded up to a 10 ms tick.
  * Cartesian legs: also capped at 150 mm/s average, and at the joint velocity limits along the
    path.
  * An explicit `ms` is always used exactly as given.
* **Validation at enqueue.**
  * `move`, `tap` and `swipe` are IK-checked when they are queued, sampling every 2 mm along each
    straight leg. A failure returns `unreachable` or `limit` straight away.
  * `tap`/`swipe` with `arm` set to `auto` (or omitted) pick the arm on the same side as `x`, or
    as the swipe midpoint. If that arm can't reach, they fall back to the other arm.
  * `joints` clamps each target to its joint limits but rejects linkage violations with `limit`.
  * Motion commands return `not_enabled` while the servos are relaxed.
* **Other arm.** If the other arm's tool is within 60 mm (xy) of the target, it lifts by
  `approach_mm` and then moves to `park`.
* **Cancellation.**
  * `stop`, `relax`, the watchdog and the long-press "home" cancel all running and queued
    motions. Each one is reported as `{"evt":"error","id":N,"err":"fault","msg":"cancelled (<why>)"}`.
  * After `stop` the servos stay energised and hold the current position.
* **Watchdog.**
  * Defaults to `link.watchdog_ms` (5000). Any request counts as activity.
  * When it expires while enabled: cancel everything, park both arms, relax, then emit
    `{"evt":"watchdog"}`. `watchdog` with `ms:0` turns it off.
* **Faults.**
  * `brownout`: the servo rail stays below 4.3 V for 3 consecutive samples (taken every 50 ms).
    Motion pauses, and resumes once the rail is back at 4.5 V or more.
  * `pwm`: the PCA9685 doesn't ACK. The firmware probes it every second and relaxes the servos if
    it disappears. `enable` fails with `fault` while it is missing.
  * `phone_vbus`: the head's load switch reports a fault (active low).
  * A `fault{code, active}` event is sent when a fault is raised and when it clears.
  * Building with `-DTAPPET_IGNORE_VSERVO` skips the servo rail sensing. Use it only on a bare
    DevKit with no divider fitted; otherwise 0 V reads as a brownout.
* **`servo_raw`.**
  * Writes a pulse (400–2600 µs) directly to a channel and holds it until the next motion command.
  * If the channel belongs to a joint, that joint's commanded angle is updated to match, so the
    next move starts from where the servo actually is.
* **Persistence.**
  * `calib_set` / `geom_set` with `save:true` write a versioned, CRC-checked blob to NVS
    (namespace `tappet`, key `calib`). It is loaded at boot.
  * If the blob is invalid or stale, the firmware falls back to the compiled defaults.
  * `reset_calib` restores the defaults and erases the blob.
* **Lines.** Requests and responses are at most 2048 bytes. A longer request returns `bad_json`
  with the message "line too long".
* **BOOT button (GPIO0).**
  * Short press toggles enable/relax.
  * A long press (>1.5 s) enables the servos if needed and homes all joints.
* **Status LED (GPIO48 RGB).**

  | colour | state |
  |--------|-------|
  | blue, breathing | no link |
  | cyan | link up, servos relaxed |
  | green | enabled |
  | green, flickering | moving |
  | orange, fast blink | watchdog tripped |
  | red, blinking | fault |

## Calibration (direction conventions)

The firmware passes joint angles straight through `s = s_zero + dir·gear·(q − neutral)`. Each
joint's `dir` has to be set so that its motion matches the conventions in SPEC §2–§4. In
particular:

* **Positive neck pan** turns the head CCW seen from above, toward the robot's left (−x).
* **Negative tilt** looks down.
* Arm yaw and shoulder/elbow angles follow SPEC §3.

To calibrate a joint:

1. Find its neutral pulse with `servo_raw`.
2. Set `s_zero` with `calib_set`.
3. Command a small positive angle and check which way it moves. If it moves the wrong way, flip
   `dir`.
4. Save with `{"cmd":"calib_set","joints":{...},"save":true}`.

## AOA details

When a device enumerates, the firmware checks its IDs:

* If it is already `18d1:2d00` or `18d1:2d01`, the firmware claims interface 0 and uses its bulk
  IN/OUT endpoints. Data is streamed in 512-byte transfers, the IN transfer is always re-armed,
  and OUT is fed from an 8 KB queue.
* Otherwise it runs the handshake:
  1. `GET_PROTOCOL` (51).
  2. Six `SEND_STRING` (52) requests carrying `link.aoa` from `robot.json` (manufacturer
     `Tappet`, model `TappetBody`).
  3. `START` (53).
  4. Close the device and wait for it to re-enumerate as an accessory.

`phone_power` switches the head's VBUS (GPIO7), which forces a re-enumeration. When the device
goes away, in-flight transfers are flushed before the interface is released.

The USB host path compiles and is structured per ESP-IDF's examples, but it has **not yet been
verified against a real phone**. All other logic is covered by the native tests.
