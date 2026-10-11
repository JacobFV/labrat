# labrat bridge

Laptop process that connects **out** to the LabRat server (`/ws/bridge`, see `../CONTRACT.md`), discovers USB serial
hardware, and executes `cmd` messages. Every device has a mock; anything not found falls back to a mock
(reported as `status:"mock"`, `info.fallback` = reason) unless `--no-fallback` (then `status:"error"`).

## Run
```bash
cd bridge
uv run python -m labrat_bridge --server wss://HOST --token labrat            # real hw, auto-discover
uv run python -m labrat_bridge --server ws://localhost:8000 --mock all        # everything simulated
uv run python -m labrat_bridge --server wss://HOST --mock so_b,tap_b          # partial mock
uv run python -m labrat_bridge --server wss://HOST --so-a /dev/cu.usbmodemA --so-b /dev/cu.usbmodemB
uv run python -m labrat_bridge --server wss://HOST --ports /dev/cu.usbserial-1,/dev/cu.usbmodem2
uv run python -m labrat_bridge scan                                           # just print the port table
uv run pytest -q -s                                                           # all-mock test vs fake server
```
Env: `LABRAT_SERVER`, `LABRAT_TOKEN`, `LOG=DEBUG`, `LABRAT_CALIB_DIR`, `LABRAT_MOCK_SPEEDUP` (mock CNC time scale).

At startup it probes each USB port in parallel: GRBL banner (115200) -> Tappet `hello` (115200, fw `tappet-fw`)
-> Feetech ping ids 1..6 (1 Mbps), then prints a port table and a device table.

## Wiring
| Device | Link | Notes |
|---|---|---|
| SO-101 `so_a` (phone arm), `so_b` (gripper arm) | Waveshare/Feetech bus adapter USB, 1 Mbps, ids 1..6 | 7.4 V (STS3215 7.4 V version) or 12 V servo supply to adapter; common GND |
| Tappet ESP32 `tap_a` (`tap_b`) | USB-UART 115200, JSON lines | bridge pings every 1 s (fw watchdog parks after 5 s) |
| Pipette plunger servo | ESP32 PCA9685 **ch 8** on `tap_a` | `enable` is sent automatically before the first `servo_raw` (this also energizes ch 0..7) |
| 3018 CNC | GRBL 1.1 USB 115200 | pipette on spindle mount; work zero = top-left-front, Z0 = safe height |

Arm port mapping: `--so-a/--so-b` override > `calib/ports.json` (USB serial numbers, persisted automatically) > sorted
port order. If the arms swap, run once with `--so-a/--so-b`; the mapping is saved.

## Actions (per CONTRACT)
- `so_a/so_b`: `joints {q, ms}`, `pose {name, ms?}`, `gripper {open 0..1}`, `torque {on}`, `read {}`, `stop {}`.
  Moves interpolate (smoothstep) at 50 Hz with SYNC_WRITE to goal position (reg 42); targets clamped to calib limits.
- `tap_a/tap_b`: `raw {cmd, ...}` passthrough (motion cmds wait for the `done` event).
- `cnc`: `home`, `move {x,y,z,f}` (Z raised first / lowered last), `jog {dx,dy,dz,f}`, `status`, `gcode {lines}`,
  `stop` (feed hold `!` + soft reset 0x18), `zero` (G10 L20 current = 0,0,0). Soft limits X 0..300, Y 0..180,
  Z -45..0; feed caps XY 1500, Z 500 mm/min. `gcode` allows only G0/G1/G4/G90/G91/G21/G28/M3/M5/$H/$X, bounds-checks
  every move (G90/G91 tracked), rewrites G0 -> G1 with capped F. Streaming uses character counting (127-byte RX buffer),
  then `G4 P0` to wait for motion completion. Alarm on connect/before a job -> `$X`. If `$22=1`, `home` runs `$H` then
  `G10 L20 P1` with `calib/cnc.json:zero_after_home`; otherwise `home` = go to work 0,0,0 via safe Z.
- `pipette`: `set {frac}`, `aspirate {ul}`, `dispense {ul?}`, `blowout {}`, `rest {}`. frac 0 = rest, `first_stop`
  (0.8) = first stop, 1.0 = blowout; `us = rest_us + (full_us-rest_us)*frac`. Aspirate: if empty and not pressed, press
  to first stop (better: server sends `set {frac:0.8}` **above** the liquid, descends, then `aspirate`), then release
  by `ul/max_ul*first_stop`. Dispense presses back (to first stop when emptying).

Watchdog: server link down > 3 s -> CNC feed hold + reset, arms hold present position, Tappets `stop`, pipette to rest.
State is sent at 5 Hz (`so*: q/moving/torque`, `cnc: state/wpos/mpos`, `pipette: frac/vol_ul/us`).

## Calibration
Files in `bridge/calib/` (defaults used when missing):
- `so_a.json` / `so_b.json`: `joints.{name}.{offset (ticks), sign (+-1), min, max (deg)}`, `poses.{home,look_deck,look_user,look_plate}`.
  deg = sign * (ticks - 2048 - offset) * 360/4096.
- `pipette.json`: `{rest_us:1000, full_us:2000, first_stop:0.8, max_ul:200, settle_ms:400}`.
- `cnc.json`: `{zero_after_home:[0,0,0], home_if_enabled:true}`.

SO-101 steps (torque goes OFF, move by hand):
```bash
uv run python -m labrat_bridge calibrate --device so_a --port /dev/cu.usbmodemXXXX
```
1. Put the arm in the centred/zero pose -> Enter (records offsets; `s` skips).
2. Enter, sweep every joint through its safe range, Enter (records min/max deg).
3. For each of home, look_deck, look_user, look_plate: pose the arm -> Enter.
If a joint moves the wrong way, flip its `sign` in the JSON (and redo step 2/3).

Pipette: use `set {frac}` from the server (or edit `pipette.json`) to find the µs where the plunger hits the first
stop / bottom; set `rest_us`, `full_us`, `first_stop`; tune `max_ul` by weighing water (1 mg = 1 µL).
