# LabRat: wire contract

This page holds only the protocol tables. Everything else lives in **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**, the single system-design doc. That includes components, data flow, the REST and WebSocket endpoint tables, Mongo collections, the simulator, the learning loop, calibration, safety and sponsors. The tables below are checked against `server/labrat/hub.py` and `bridge/labrat_bridge/*.py`.

## Bridge link: `/ws/bridge?token=LABRAT_TOKEN` (JSON text frames)

| Direction | Message |
|---|---|
| bridge → server | `{"t":"hello","devices":[{"id","kind","status":"ok\|mock\|error","info":{}}]}` |
| bridge → server | `{"t":"state","devices":{"<id>":{...}}}` (~5 Hz) |
| server → bridge | `{"t":"cmd","id":"<hex>","device":"<id>","action":"<name>","args":{...}}` |
| bridge → server | `{"t":"result","id":"<hex>","ok":true\|false,"data":{},"error":"..."}` |
| bridge → server | `{"t":"log",...}` (forwarded to the UIs) |

An action named `X` is dispatched to the driver method `a_X` (`common.py: Device.handle`). Each device serialises its actions except the ones it lists in `unlocked`.

| id | kind | driver | actions (args) |
|---|---|---|---|
| `so_a`, `so_b` | so101 | `so101.py` | `joints {q:{shoulder_pan,shoulder_lift,elbow_flex,wrist_flex,wrist_roll,gripper} deg, ms}`, `pose {name: home\|ready\|rest, ms}` (legacy `look_deck\|look_user\|look_plate` are aliases for `ready`), `gripper {open 0..1, ms}`, `torque {on}`, `read {}`, `stop {}` |
| `tap_a` (`tap_b` optional) | tappet | `tappet.py` | ESP32-S3 + PCA9685 board; in the demo setup `tap_a` only drives the syringe servo (ch 8) and its arms are unused. `raw {cmd, ...}`: passthrough of the Tappet JSON-lines protocol ([tappet/docs/SPEC.md](tappet/docs/SPEC.md)) through the vendored client `tappet_client.py` (copied from [tappet/host/tappet/client.py](tappet/host/tappet/client.py)) |
| `cnc` | grbl | `grbl.py` | `home {}`, `move {x,y,z,f}` (absolute mm), `jog {dx,dy,dz,f}`, `gcode {lines:[...]}` (validated), `status {}` → `{pos/wpos,...}`, `stop {}` (feed hold + soft reset), `zero {}` (real hardware only), `probe {dz=-20, f=60, retract=true}` → `{hit, z, start_z, target_z}` (G38.3 straight down with the tool-rail capacitive probe on the GRBL probe pin, `$6=1`; no alarm when nothing triggers; retracts to the start height) |
| `pipette` | servo_syringe | `tappet.py: Syringe` (continuous-rotation servo on PCA9685 **ch 8** via `servo_raw`, `enable` sent first; 12:1 printed gear train, rack on a carriage that slides on a square bar moulded on the head body, disposable syringe in the spindle clamp, [cad/syringe](cad/syringe)) | `home {}` (slow drive into the bottom stop, pos = 0), `prime {}` (plunger to `ready_mm` air cushion; do it above the liquid), `aspirate {ul}`, `dispense {ul?}` (back to `ready_mm` when emptying), `blowout {}` (to 0), `move {mm, speed?}` / `jog {mm}` (plunger height, 0..stroke), `drive {speed -1..1, ms}` (raw, invalidates position), `zero {mm?}`, `observe {mm, conf?}` (camera/NN position estimate, blended in), `rest {}` / `stop {}` (neutral pulse). State `{pos_mm, stroke_mm, vol_ul, ul_per_mm, homed, moving, us, obs}`. Tip eject is a CNC move over the eject bar that the server performs (`lab.py: exec_step`). More syringe modules (tool rail) come from `calib/tools.json` `{"syringes": [{"id": "pipette2", "channel": 9}]}`, each with the same actions and its own calibration file |
| `microscope` | usb_microscope | `microscope.py` (UVC camera via OpenCV, optional extra `microscope`; mock frames without it) | `snap {width?, height?}` → `{image_b64, mime, w, h, ts}`, `info {}`. Calibration `calib/microscope.json` `{index, width, height}` (set `index`: the laptop's own webcam is usually 0) |

Bridge-side limits:
- CNC soft limits X 0..300, Y 0..180, Z −45..0 mm.
- Feed caps of 1500 mm/min XY and 500 mm/min Z.
- SO-101 joints are clamped to their calibration.
- Watchdog: if the server link is down for more than 3 s, every device runs `safe_stop`.

## Phone link: `/ws/phone?token=...`

| Direction | Message |
|---|---|
| phone → server | `{"t":"frame","jpeg":"<b64>","w","h"}`, `{"t":"audio","b64","mime"}`, `{"t":"text","text"}` |
| server → phone | `{"t":"say","text","audio_b64"\|null}`, `{"t":"face","mood":"idle\|listening\|thinking\|speaking\|happy\|alert","gaze":[x,y]}`, `{"t":"transcript","role":"user\|agent","text"}` |

## UI link: `/ws/ui?token=...` (web app and CLI)

| Direction | Message |
|---|---|
| ui → server | `{"t":"chat","text"}`, `{"t":"approve","run_id"}`, `{"t":"abort"}`, `{"t":"observe","run_id","rating","rgb","well"}` |
| server → ui | `devices`, `chat`, `experiment`, `sim`, `run`, `frame`, `log`, `deck_cal` |

REST endpoints: see [docs/ARCHITECTURE.md §4](docs/ARCHITECTURE.md#4-endpoints-generated-from-serverlabratapppy-and-serverlabratvisionpy).
