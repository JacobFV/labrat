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
| SO-101 `so_a`, `so_b` (gripper arms, one each side of the CNC) | Waveshare/Feetech bus adapter USB, 1 Mbps, ids 1..6 | 7.4 V (STS3215 7.4 V version) or 12 V servo supply to adapter; common GND |
| Tappet ESP32 board `tap_a` (drives the syringe servo; `tap_b`/arms optional) | USB-UART 115200, JSON lines | bridge pings every 1 s (fw watchdog parks after 5 s) |
| Syringe servo (continuous-rotation 9 g, rack and pinion on the plunger) | ESP32 PCA9685 **ch 8** on `tap_a` | `enable` is sent automatically before the first `servo_raw` (this also energizes ch 0..7) |
| 3018 CNC | GRBL 1.1 USB 115200 | syringe head in the spindle clamp (tip on the spindle axis); work zero = top-left-front, Z0 = safe height |

Arm port mapping: `--so-a/--so-b` override > `calib/ports.json` (USB serial numbers, persisted automatically) > sorted
port order. If the arms swap, run once with `--so-a/--so-b`; the mapping is saved.

## Actions (per CONTRACT)
- `so_a/so_b`: `joints {q, ms}`, `pose {name: home|ready|rest, ms?}` (legacy `look_*` names alias to `ready`), `gripper {open 0..1}`, `torque {on}`, `read {}`, `stop {}`.
  Moves interpolate (smoothstep) at 50 Hz with SYNC_WRITE to goal position (reg 42); targets clamped to calib limits.
- `tap_a/tap_b`: `raw {cmd, ...}` passthrough (motion cmds wait for the `done` event).
- `cnc`: `home`, `move {x,y,z,f}` (Z raised first / lowered last), `jog {dx,dy,dz,f}`, `status`, `gcode {lines}`,
  `stop` (feed hold `!` + soft reset 0x18), `zero` (G10 L20 current = 0,0,0). Soft limits X 0..300, Y 0..180,
  Z -45..0; feed caps XY 1500, Z 500 mm/min. `gcode` allows only G0/G1/G4/G90/G91/G21/G28/M3/M5/$H/$X, bounds-checks
  every move (G90/G91 tracked), rewrites G0 -> G1 with capped F. Streaming uses character counting (127-byte RX buffer),
  then `G4 P0` to wait for motion completion. Alarm on connect/before a job -> `$X`. If `$22=1`, `home` runs `$H` then
  `G10 L20 P1` with `calib/cnc.json:zero_after_home`; otherwise `home` = go to work 0,0,0 via safe Z.
- `pipette` (kind `servo_syringe`, [`cad/syringe`](../cad/syringe)): a continuous-rotation servo turns a 12T m1.5 pinion
  (pitch radius 9 mm) on the rack that holds the plunger. `us` sets **speed**, not angle:
  `us = neutral_us + dir*sign*(deadband_us + span_us*|speed|)`. The plunger height `pos_mm` (0 = lowest, `stroke_mm` =
  fully drawn) is dead-reckoned: a move of `d` mm runs for `d / mm_per_s_{up,down}` (scaled by speed, + `backlash_mm` on
  reversal) and always ends with a neutral pulse. `ul_per_mm = pi/4 * syringe_id_mm^2 * ul_gain`.
  - `home {}`: drive slowly down for `home_s` into the bottom stop, then `pos_mm = 0`. `zero {mm?}` declares the position.
  - `prime {}`: (homes if needed) plunger to `ready_mm`, the air cushion. The server does this **above** the liquid.
  - `aspirate {ul}`: needs a prime when the tip is empty; raises by `ul/ul_per_mm`. `dispense {ul?}`: lowers again
    (to `ready_mm` when emptying). `blowout {}`: to 0, pushing the cushion out.
  - `move {mm, speed?}`, `jog {mm, speed?}`: plunger height, rejected outside 0..`stroke_mm`.
  - `drive {speed, ms}`: raw open-loop drive for calibration; marks the position untrusted (`homed=false`).
  - `observe {mm, conf?}`: an external estimate (the camera + NN reading the rack) blended into `pos_mm`
    (`pos += conf*(mm-pos)`); the last one is reported as `obs`. This is the hook for visual closed-loop control.
  - `rest {}` / `stop {}`: neutral pulse. `stop` is not queued behind a running move (an interrupted move clears `homed`).

Watchdog: server link down > 3 s -> CNC feed hold + reset, arms hold present position, Tappets `stop`, syringe servo to neutral.
State is sent at 5 Hz (`so*: q/moving/torque`, `cnc: state/wpos/mpos`, `pipette: pos_mm/stroke_mm/vol_ul/ul_per_mm/homed/moving/us/obs`).

## Calibration
Files in `bridge/calib/` (defaults used when missing):
- `so_a.json` / `so_b.json`: `joints.{name}.{offset (ticks), sign (+-1), min, max (deg)}`, `poses.{home,ready,rest}` (legacy `look_deck`/`look_user`/`look_plate` requests fall back to `ready` unless the file defines them).
  deg = sign * (ticks - 2048 - offset) * 360/4096.
- `syringe.json`: `neutral_us, deadband_us, span_us, dir, move_speed, mm_per_s_up, mm_per_s_down, backlash_mm, stroke_mm,
  syringe_id_mm, ul_gain, ready_mm, max_ul, home_speed, home_s, settle_ms, min_pulse_ms` (defaults in `tappet.py: DEFAULT_SYR`).
- `cnc.json`: `{zero_after_home:[0,0,0], home_if_enabled:true}`.

SO-101 steps (torque goes OFF, move by hand):
```bash
uv run python -m labrat_bridge calibrate --device so_a --port /dev/cu.usbmodemXXXX
```
1. Put the arm in the centred/zero pose -> Enter (records offsets; `s` skips).
2. Enter, sweep every joint through its safe range, Enter (records min/max deg).
3. For each of home, ready, rest: pose the arm -> Enter.
If a joint moves the wrong way, flip its `sign` in the JSON (and redo step 2/3).

Syringe (send the actions with `POST /api/device {device:"pipette", action, args}`):
1. **Neutral:** `drive {speed: 0, ms: 2000}` sends `neutral_us`. If the rack creeps, trim the servo's pot (or `neutral_us`)
   until it stands still; raise `deadband_us` until small speeds just start moving it.
2. **Direction:** `drive {speed: 0.2, ms: 500}` must **raise** the plunger. If it lowers, set `dir: -1`.
3. **Speed:** `home`, then `drive {speed: <move_speed>, ms: 2000}` up and measure the plunger travel (the rack has a
   1.5π mm tooth pitch, or use the camera); `mm_per_s_up = mm / 2`. Repeat downward for `mm_per_s_down`.
4. **Backlash:** reverse after a move and find the extra travel before the rack moves; set `backlash_mm`.
5. **Volume:** `syringe_id_mm` from the syringe datasheet; then tune `ul_gain` by weighing water (1 mg = 1 µL).
6. `home_s` must be long enough to reach the stop from the top (`stroke_mm / (mm_per_s_down*home_speed/move_speed)` + margin).
Resolution note: with the 20.2 mm barrel, 1 mm of plunger is ~250 µL, so a 50 µL aspirate is a 0.2 mm move. Open-loop
timing is not good enough for that; use `observe` (camera reading the rack) or a smaller syringe (`syringe_id_mm`).
