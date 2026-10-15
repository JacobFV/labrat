# LabRat wire protocols

The contract between the coordinator (`cloud/`, one Durable Object), the clients (`apps/`), the USB bridge (`bridge/`)
and the servo hub firmware (`firmware/servo-hub/`). When code and this page disagree, fix whichever is wrong in the
same change; nothing else redefines these messages.

```
 apps/console ─┐                                 ┌─ bridge (laptop, USB) ─┬─ so_a, so_b   SO-101 (Feetech bus)
 apps/cli ─────┼─ wss /ws/ui ─┐                  │                        ├─ cnc          3018 GRBL
               │              ▼                  │                        ├─ microscope   UVC camera
               │     ┌──────────────────┐        │                        └─ hub ─USB─┐
 apps/phone ───┴────►│  Lab DO  (cloud) │◄─ /ws/bridge                                │
   /ws/phone         │  + D1            │◄─ /ws/phone (also a device provider) ─ hub ─BLE─┤
                     └──────────────────┘                                   servo hub (ESP32 + PCA9685)
                      pipette / probe / stand drivers live here,              ch0 syringe, ch1 probe arm,
                      on top of the hub's raw servo protocol                  ch2 stand pan, ch3 stand tilt
```

Everything dials out to the Durable Object. Every WebSocket authenticates with `?token=LABRAT_TOKEN` and the DO
closes unauthenticated sockets with code 4401. All frames are JSON text.

## 1. Device providers (`/ws/bridge`, and `/ws/phone` for the BLE hub)

A *provider* is any socket that owns hardware. The laptop bridge is one; the phone page becomes one when it has the
servo hub paired over Web Bluetooth. Several providers may be connected at once. The DO routes each command to the
provider that announced the device id most recently; a provider disconnecting marks its devices `offline` and fails
their pending commands.

| Direction | Message |
|---|---|
| provider → DO | `{"t":"hello","devices":[{"id","kind","status":"ok\|mock\|error","info":{}}]}` (replaces that provider's device set) |
| provider → DO | `{"t":"state","devices":{"<id>":{...}}}` (bridge: every device at 5 Hz; phone: the `hub` state at 1 Hz and after every hub event) |
| DO → provider | `{"t":"cmd","id":"<hex>","device":"<id>","action":"<name>","args":{...}}` |
| provider → DO | `{"t":"result","id":"<hex>","ok":true\|false,"data":{},"error":"..."}` |
| provider → DO | `{"t":"event","device":"<id>","data":{...}}` (unsolicited, e.g. a hub input change; see "Hub events" below) |
| provider → DO | `{"t":"log","level","text"}` (forwarded to the UIs) |

Bridge-side, an action `X` dispatches to the driver method `a_X` (`bridge/labrat_bridge/common.py`). Each device
serialises its actions except those it lists in `unlocked` (`stop` is always unlocked).

### Devices a provider exposes

| id | kind | provided by | actions (args) → data |
|---|---|---|---|
| `so_a`, `so_b` | `so101` | bridge `so101.py` | `joints {q:{shoulder_pan,shoulder_lift,elbow_flex,wrist_flex,wrist_roll,gripper} deg, ms}`, `pose {name: home\|ready\|rest, ms}`, `gripper {open 0..1, ms}`, `torque {on}`, `teleop {leader, on, hz?, record?}`, `read {}`, `stop {}` |
| `cnc` | `grbl` | bridge `grbl.py` | `home {}`, `move {x,y,z,f}` (absolute mm), `jog {dx,dy,dz,f}`, `gcode {lines}` (validated), `status {}` → `{pos,wpos,state}`, `stop {}`, `zero {x?,y?,z?}`, `probe {dz=-20,f=60,retract=true}` → `{hit,z,start_z,target_z}` (G38.3; the DO deploys the probe arm first) |
| `microscope` | `usb_microscope` | bridge `microscope.py` | `snap {width?,height?}` → `{image_b64,mime,w,h,ts}`, `info {}` |
| `hub` | `servo_hub` | bridge `servo_hub.py` (USB serial) **or** phone page (Web Bluetooth) | `line {msg:{...}}` → the firmware's reply object (§4). The provider assigns the firmware `id`, sends the line, and resolves on the matching reply. `stop {}` and `read {}` are shortcuts for the firmware commands. Firmware events (`{"ev":...}`) come up as `{"t":"event","device":"hub","data":{...}}`. While the DO link is up the provider sends `{"cmd":"ping"}` every 1 s; when it drops the provider stops, and the firmware watchdog relaxes every servo |

### Devices the DO implements on top of `hub`

These never appear on a provider. Their drivers and calibration live in `cloud/src/devices/`; the calibration
is stored in D1 (`config` rows `cal:pipette`, `cal:probe`, `cal:stand`) and is editable from the console.

| id | kind | hub channel | actions (args) → state |
|---|---|---|---|
| `pipette` | `servo_syringe` | 0 (FS90R continuous) | `home {}` (slow drive into the bottom stop; done when the `syr_home` input closes, or after the dead-reckoned overtravel if no switch is fitted), `prime {}`, `aspirate {ul}`, `dispense {ul?}`, `blowout {}`, `move {mm,speed?}`, `jog {mm}`, `drive {speed -1..1, ms}`, `zero {mm?}`, `observe {mm,conf?}`, `rest {}`, `stop {}`. Timed drives use the firmware's `pulse` with `ms` so their length does not depend on network latency. State `{pos_mm,stroke_mm,vol_ul,ul_per_mm,homed,moving}` |
| `probe` | `servo_arm` | 1 (SG90 positional) | `deploy {}`, `retract {}`, `set {deg}`. State `{deployed,deg,moving}`. See "Probe-arm interlock" below |
| `stand` | `pan_tilt` | 2 = pan (about Z), 3 = tilt (about X) | `aim {pan,tilt,ms?}` (deg, 0 = looking straight down at the deck centre), `look {at: deck\|operator\|home\|<beaker>\|<stock>}`, `relax {}`, `stop {}` (= relax). State `{pan,tilt,moving,at}`. Every aim waits `settle_ms` before it resolves |

Each logical device sends its channels' `cfg` (kind, `rest_us`) to the hub before its first hub command, and again
after anything that may have reset them: a provider announcing (or dropping) `hub`, or a hub `wd` / `boot` event. The
resend is lazy (it happens just before that device's next hub command); an identical `cfg` neither writes flash nor
preempts a motion.

#### Probe-arm interlock (enforced in the DO, `cloud/src/hub.ts`)

- `cnc.probe` is refused unless `probe.deployed`; the DO retracts the arm after every probe cycle, whether it hit or
  not.
- Before any CNC command that can take the head below safe Z the DO retracts the arm unless it is known to be
  retracted (no-op while no hub is connected). The triggers are `cnc.move` with `z < 0` (`Z_SAFE`), `cnc.jog` with
  `dz < 0`, and any `cnc.gcode`.

#### Hub events

| event (`data` of a `{"t":"event","device":"hub"}`) | what the DO does |
|---|---|
| `{"ev":"estop","v":1}` | aborts everything, as `POST /api/abort` does: every running run is flagged aborted, every pending command fails, `stop` goes to `cnc`, `so_a`, `so_b` and the hub (`{"cmd":"stop"}`). Logged as an error and stored as a `device_event` |
| `{"ev":"wd"}`, `{"ev":"boot"}` | the hub relaxed every servo (or rebooted): the drivers clear `moving` and resend their `cfg` before their next command (above). Logged as a warning |
| `{"ev":"in",...}`, `{"ev":"estop","v":0}` | forwarded to the UIs as a `log` line |

#### Photo step

A run's `photo` step: Z to safe height, park the head out of the camera's view (x 290, y 175), then `stand.look
{at:"deck"}` if a hub is online, then `{"t":"snap"}` to every phone, then wait up to `LABRAT_PHOTO_WAIT_S` (3 s) for a
frame newer than the moment the stand settled. Measuring the plate only uses a frame captured after that point.

## 2. Phone (`/ws/phone?token=…`)

Voice is on-device: the page uses the browser's speech recognition and speech synthesis, so no audio crosses the
wire.

| Direction | Message |
|---|---|
| phone → DO | `{"t":"frame","jpeg":"<b64>","w","h"}`, `{"t":"text","text","final":true}` (recognised speech or typed text) |
| DO → phone | on connect `{"t":"face","mood":"happy"}`; `{"t":"say","text"}` (the phone speaks it), `{"t":"face","mood":"idle\|listening\|thinking\|speaking\|happy\|alert","gaze":[x,y]}`, `{"t":"transcript","role":"user\|agent","text"}`, `{"t":"snap"}` (send a fresh frame now: photo steps and the agent's `look` tool) |
| both | the provider messages of §1, when the phone has the hub paired |

## 3. UI (`/ws/ui?token=…`, console and CLI)

| Direction | Message |
|---|---|
| ui → DO | `{"t":"chat","text"}`, `{"t":"approve","id"?}` (no `id`: the experiment awaiting approval), `{"t":"abort"}` |
| DO → ui | `devices {providers,devices}`, `chat {role,text,meta}`, `experiment {experiment}` (on every change; shape in [experiments.md](experiments.md)), `model`, `note`, `frame`, `log`, `deck_cal`. On connect the DO replays devices and the newest 20 experiments |

REST lives on the same origin (`cloud/src/index.ts` is the route table; [architecture.md](architecture.md#endpoints)
lists it).

## 4. Servo hub firmware (JSON lines; USB CDC at 115200 and BLE)

One protocol over both transports. Lines are UTF-8 JSON ending in `\n`, at most 256 bytes. BLE uses the Nordic UART
service: service `6e400001-b5a3-f393-e0a9-e50e24dcca9e`, RX (host writes) `6e400002-…`, TX (notify) `6e400003-…`;
the device advertises as `labrat-hub`. A line may span several BLE writes/notifications; split on `\n`.

Requests carry an integer `id`; the reply echoes it: `{"id":7,"ok":true,...}` or `{"id":7,"ok":false,"err":"..."}`.
A command that runs over time (`servo` or `pulse` with `ms`) replies when it finishes; other commands keep being
served meanwhile. A new command on a channel cancels that channel's running motion (its reply comes back with
`"ok":false,"err":"preempted"`).

| cmd | args | reply |
|---|---|---|
| `hello` | | `{fw:"servo-hub/1.x", board, channels:16, inputs:[names], uptime_ms}` |
| `ping` | | `{}` (any line feeds the watchdog) |
| `servo` | `ch, deg 0..180, ms?` | moves a positional servo, eased over `ms` (default: jump); `{deg}` |
| `pulse` | `ch, us, ms?` | raw pulse width. With `ms`: hold it for `ms`, then return the channel to its `rest_us` (neutral for a continuous servo, off otherwise) and reply. An input named in `until` (e.g. `"until":"syr_home"`) ends the hold early when it reads 1; the reply says `{stopped_by}` |
| `off` | `ch?` | relaxes one channel, or all of them |
| `stop` | | ends every motion, continuous channels to neutral, then all off |
| `cfg` | `ch, min_us?, max_us?, rest_us?, kind?: "pos"\|"cont"` | per-channel limits, persisted in NVS; `{cfg}` |
| `read` | | `{inputs:{name:0\|1}, adc:{name:mV}, ch:[us,...]}` |

Events (no `id`): `{"ev":"boot"}`, `{"ev":"in","name","v"}` (debounced input change), `{"ev":"wd"}` (watchdog
fired: no line for 2 s, every servo relaxed), `{"ev":"estop","v"}`.

Inputs are switches to ground on GPIOs with the internal pull-up, so they need no resistors: `syr_home` (plunger
bottom-stop microswitch) and `estop` (normally-closed mushroom button; open = stop: the firmware stops every servo
itself, then reports). Pin map and wiring: [`hardware/electronics/`](../hardware/electronics).
