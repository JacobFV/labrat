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
| `so_a`, `so_b` | so101 | `so101.py` | `joints {q:{shoulder_pan,shoulder_lift,elbow_flex,wrist_flex,wrist_roll,gripper} deg, ms}`, `pose {name: home\|look_deck\|look_user\|look_plate, ms}`, `gripper {open 0..1, ms}`, `torque {on}`, `read {}`, `stop {}` |
| `tap_a`, `tap_b` | tappet | `tappet.py` | `raw {cmd, ...}`: passthrough of the Tappet JSON-lines protocol ([tappet/docs/SPEC.md](tappet/docs/SPEC.md)) through the vendored client `tappet_client.py` (copied from [tappet/host/tappet/client.py](tappet/host/tappet/client.py)) |
| `cnc` | grbl | `grbl.py` | `home {}`, `move {x,y,z,f}` (absolute mm), `jog {dx,dy,dz,f}`, `gcode {lines:[...]}` (validated), `status {}` → `{pos/wpos,...}`, `stop {}` (feed hold + soft reset), `zero {}` (real hardware only) |
| `pipette` | servo_pipette | `tappet.py` (PCA9685 **ch 8** via `servo_raw`, `enable` sent first) | `set {frac 0..1}` (0 = rest, 1 = fully pressed), `aspirate {ul}`, `dispense {ul?}`, `blowout {}`, `rest {}`. Tip eject is a CNC move over the eject bar that the server performs (`lab.py: exec_step`) |

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
