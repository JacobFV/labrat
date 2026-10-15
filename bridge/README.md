# labrat bridge

Laptop process that connects **out** to the LabRat coordinator (`/ws/bridge`, see [`../docs/protocol.md`](../docs/protocol.md) §1),
discovers USB serial hardware, and executes `cmd` messages. Every device has a mock; anything not found falls back to a
mock (reported as `status:"mock"`, `info.fallback` = reason) unless `--no-fallback` (then `status:"error"`).

Devices: `so_a`, `so_b` (SO-101 arms), `cnc` (3018 GRBL), `microscope` (UVC), `hub` (the ESP32 + PCA9685 servo hub,
[`../firmware/servo-hub`](../firmware/servo-hub)). The pipette, the probe arm and the phone stand are **not** bridge
devices: the cloud DO drives them through `hub` (`cloud/src/devices`), and owns their calibration.

## Run
```bash
cd bridge
uv run python -m labrat_bridge --server wss://HOST --token labrat            # real hw, auto-discover
uv run python -m labrat_bridge --server ws://localhost:8787 --mock all        # everything simulated
uv run python -m labrat_bridge --server wss://HOST --mock so_b,hub            # partial mock
uv run python -m labrat_bridge --server wss://HOST --so-a /dev/ttyACM0 --so-b /dev/ttyACM1
uv run python -m labrat_bridge --server wss://HOST --ports /dev/ttyUSB0,/dev/ttyACM2
uv run python -m labrat_bridge scan                                           # just print the port table
uv run python -m labrat_bridge hub '{"cmd":"hello"}' '{"cmd":"servo","ch":2,"deg":90,"ms":800}'   # hub bench console
uv run python -m labrat_bridge hub --port mock                                # ... against the emulated firmware (stdin lines)
uv run --group dev pytest -q                                                  # all-mock tests vs a fake server
```
Env: `LABRAT_SERVER`, `LABRAT_TOKEN`, `LOG=DEBUG`, `LABRAT_CALIB_DIR`, `LABRAT_MOCK_SPEEDUP` (mock CNC time scale),
`LABRAT_MOCK_HUB_SPEEDUP` (mock hub firmware clock).

At startup it probes each USB port in parallel: GRBL banner (115200) -> servo hub `hello` (115200, reply `fw` starts
with `servo-hub`) -> Feetech ping ids 1..6 (1 Mbps), then prints a port table and a device table.

## Wiring
Full low-voltage wiring, pin map, BOM and power budget: [`../hardware/electronics`](../hardware/electronics).

| Device | Link | Notes |
|---|---|---|
| SO-101 `so_a`, `so_b` | Feetech/Waveshare bus adapter USB, 1 Mbps, ids 1..6 | one supply per arm into its adapter: **5 V** for the 7.4 V STS3215 (upstream BOM), **12 V** only for the 12 V variant; never mix variants on one bus ([`../hardware/so101`](../hardware/so101)); common GND |
| 3018 CNC `cnc` | GRBL 1.1 USB 115200 | syringe head in the spindle clamp; work zero = top-left-front, Z0 = safe height |
| Capacitive probe (TTP223 on the tool rail) | 3018 board probe header: GND, 5 V, OUT -> probe pin (A5) | `$6=1` once (TTP223 OUT is active HIGH); `cnc.probe` runs G38.3. The DO deploys the probe arm (hub ch1) first |
| USB microscope | laptop USB (UVC) | `uv sync --extra microscope`; `calib/microscope.json` `{"index": N}` |
| Servo hub `hub` | USB serial 115200 (DevKit UART port, or the S3's native USB port) | JSON lines, protocol.md §4. ch0 syringe FS90R, ch1 probe arm, ch2/ch3 stand pan/tilt. The phone can drive the same hub over BLE instead |

## Actions
- `so_a/so_b`: `joints {q, ms}`, `pose {name: home|ready|rest, ms?}` (legacy `look_*` names alias to `ready`),
  `gripper {open 0..1}`, `torque {on}`, `read {}`, `stop {}`. Moves interpolate (smoothstep) at 50 Hz with SYNC_WRITE
  to goal position (reg 42); targets clamped to calib limits.
- `cnc`: `home`, `move {x,y,z,f}` (Z raised first / lowered last), `jog {dx,dy,dz,f}`, `status`, `gcode {lines}`,
  `probe {dz,f,retract}` (G38.3), `stop` (feed hold `!` + soft reset 0x18), `zero`. Soft limits X 0..300, Y 0..180,
  Z -45..0; feed caps XY 1500, Z 500 mm/min. `gcode` allows only G0/G1/G4/G90/G91/G21/G28/M3/M5/$H/$X, bounds-checks
  every move (G90/G91 tracked), rewrites G0 -> G1 with capped F. Alarm on connect/before a job -> `$X`.
- `microscope`: `snap {width?,height?}`, `info {}`.
- `hub` (`servo_hub.py`): `line {msg}` passes one firmware request through. The bridge assigns the firmware `id`
  (any `id` in `msg` is replaced; an optional `timeout_ms` is ours and is stripped), waits for the matching reply
  (timeout `ms + 5 s` for timed commands, else 5 s) and returns it as `data`. A firmware `ok:false` reply fails the
  result with `error` = `"hub <cmd>: <err>"` and still returns the reply as `data`. `stop {}` and `read {}` are
  shortcuts. `line`, `stop`, `read` are not serialised, so timed motions on different channels run concurrently
  (the firmware replies to each when it finishes, or with `err:"preempted"` when a newer command takes its channel).
  Firmware events (`{"ev":"in"|"estop"|"wd"|"boot",...}`) go up as `{"t":"event","device":"hub","data":{...}}`.
  State: `{connected, inputs, last_event}`.

Watchdogs: while the DO link is up the bridge sends `{"cmd":"ping"}` to the hub every 1 s; when it drops the pings
stop at once and the hub firmware relaxes every servo 2 s later by itself (`ev:"wd"`). Independently, server link
down > 3 s -> CNC feed hold + reset, arms hold present position, hub `stop`.
State is sent at 5 Hz.

`MockHubFirmware` (in `servo_hub.py`) emulates the firmware protocol, including a plunger on ch0 that closes
`syr_home` at its bottom stop, so the DO's `pipette.home` ends as it does on the bench.

## Calibration
Files in `bridge/calib/` (defaults used when missing):
- `so_a.json` / `so_b.json`: `joints.{name}.{offset (ticks), sign (+-1), min, max (deg)}`, `poses.{home,ready,rest}`.
  deg = sign * (ticks - 2048 - offset) * 360/4096.
- `ports.json`: which USB serial number is `so_a` / `so_b` (written automatically).
- `cnc.json`: `{zero_after_home:[0,0,0], home_if_enabled:true}`.

Pipette, probe and stand calibration live in the cloud (D1 `config` rows `cal:pipette`, `cal:probe`, `cal:stand`).
Hub channel limits (`cfg`: `min_us`, `max_us`, `rest_us`, `kind`) live in the hub's flash; the DO sends them.

SO-101 steps (torque goes OFF, move by hand):
```bash
uv run python -m labrat_bridge calibrate --device so_a --port /dev/ttyACM0
```
1. Put the arm in the centred/zero pose -> Enter (records offsets; `s` skips).
2. Enter, sweep every joint through its safe range, Enter (records min/max deg).
3. For each of home, ready, rest: pose the arm -> Enter.
If a joint moves the wrong way, flip its `sign` in the JSON (and redo step 2/3).

Leader/follower teleop (records `calib/teleop_<follower>_<time>.json`):
```bash
uv run python -m labrat_bridge teleop --leader-port /dev/ttyACM1 --follower-port /dev/ttyACM0
```
