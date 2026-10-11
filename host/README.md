# tappet — host tooling

Python package for the Tappet robot: kinematics, a client for the body, a firmware simulator, a web
bridge that lets a PC stand in for the Android head, screen calibration and the v0 tap test.
Everything follows [`docs/SPEC.md`](../docs/SPEC.md) and reads geometry from
[`config/robot.json`](../config/robot.json). Set `TAPPET_CONFIG` to use a different robot.json.

```bash
cd host
uv run tappet --help                  # CLI (deps: pyserial, numpy)
uv run --extra dev pytest -q          # tests
uv run --extra plot tappet workspace  # also writes a PNG (matplotlib)
```

Global options can go before or after the subcommand: `--body` (default `$TAPPET_BODY` or `sim`), `--baud`
and `--calib` (screen calibration JSON, default `~/.tappet/calibration.json`, or `$TAPPET_CALIB`).
`--body` accepts any of these:

| value | link |
|---|---|
| `sim` | `tcp://127.0.0.1:7777`, which is `tappet sim` |
| `tcp://host:port` | simulator, or anything else that speaks the protocol over TCP |
| `/dev/ttyUSB0`, `/dev/cu.usbserial-*`, `COM3`, `serial:///dev/...` | the ESP32 DevKit UART port at 115200 baud |

## Quick start (no hardware)

```bash
uv run tappet sim -v &                 # firmware simulator on :7777
uv run tappet home && uv run tappet tap --uv 0.5 0.5
uv run tappet bridge --sim             # web UI on http://<pc>:8080, PIN printed on the console
uv run tappet taptest --sim --speed 40 --noise 0.3 --backlash 0.5
uv run tappet autocal --sim --offset 3,-4 --rot 2     # finds a displaced or rotated phone
```

## Commands

| command | what it does |
|---|---|
| `sim [--port 7777] [--speed F] [--noise DEG] [--backlash DEG] [--offset DX,DY] [--rot DEG] [-v]` | Firmware simulator over TCP. Press Enter for a short BOOT press, type `l` for a long press, `b` to toggle a brownout. |
| `bridge [--port 8080] [--pin NNNNNN] [--sim ...]` | SPEC §6 web API plus the `../web` UI, backed by `--body` or an in-process simulator. |
| `ping`, `status`, `enable`, `relax`, `stop`, `home`, `park [L\|R\|both]` | Basic body commands. |
| `tap X Y [Z]` / `tap --uv U V` | Tap in world mm or in screen coordinates (uses the screen calibration). |
| `swipe X0 Y0 X1 Y1 [Z]` / `swipe --uv ...` | Swipe. |
| `neck PAN TILT` | Move the neck. |
| `jog [--arm R]` | Interactive keyboard jog: `1`/`2` choose the arm, `a`/`d` move x, `w`/`s` move y, `r`/`f` move z, `[`/`]` change the step, `n`/`m` and `k`/`j` move the neck, `t` taps here, `q` quits. |
| `calib [--joint NAME]` | Servo trim wizard. For each joint it drives `servo_raw` until the joint sits at its `neutral` angle, computes `s_zero`, then calls `calib_set` with `save`. |
| `autocal [--sim ...] [--grid 3]` | Touch-probe screen calibration. On a real robot it starts a touchpad receiver and asks you to open `touchpad.html` on the target phone. |
| `taptest [-n 100] [--autocal] [--sim ...] [--min-rate R]` | The v0 acceptance test. Writes CSV and JSON to `host/out/`. |
| `workspace` | Screen reachability for each arm. Writes `host/out/workspace.{svg,png,json}`. |
| `gen-vectors [--check]` | Regenerates `config/kinematics_vectors.json` and reproduces it byte for byte. |

## Modules

* `tappet.config` finds and loads robot.json.
* `tappet.kinematics` has FK/IK exactly as in SPEC §3 (elbow-up, joint and linkage limits), the joint ↔ servo ↔ µs
  mapping, workspace grids and the shared test vectors (`random.Random(7)`: 10 fixed points plus 20
  random points per arm).
* `tappet.client.BodyClient` speaks JSON lines over serial or TCP. It assigns ids, matches responses to requests,
  and `wait=True` blocks until the `done` event (an `error` event raises `BodyError`). It also handles
  event callbacks (`on_event`) and runs a heartbeat thread that pings to keep the watchdog fed.
* `tappet.sim.FirmwareSim` / `SimServer` implement every SPEC §4 command and event:
  * a 16-deep queue with immediate `queued` responses and `done`/`error` events;
  * 100 Hz min-jerk motion, with Cartesian moves and swipe strokes solved by IK on every tick;
  * the tap and swipe sequences, `auto` arm choice with fallback, and parking the other arm when its tool is within 60 mm of the target;
  * the watchdog (park both arms, relax, then emit `watchdog`), the status stream, button and brownout injection, and in-memory NVS.

  Time is simulated: `speed` makes it run faster than real time, and the watchdog counts simulated
  milliseconds. `VirtualTouchscreen` records a touch whenever the actual stylus tip, including injected
  servo noise and backlash, goes below the true screen surface inside the true screen outline. That
  outline can be offset or rotated relative to the nominal tray.
* `tappet.calibration` provides:
  * normalized least-squares DLT homography;
  * the default `H_sw` from the tray and device profile;
  * touch-probe auto-calibration on a 3×3 grid, fitting touched (u,v) → commanded world (x,y);
  * the camera-corner homography `H_ic`;
  * the calibration JSON format shared with the web UI.
* `tappet.bridge.Bridge` is the HTTP API on stdlib `ThreadingHTTPServer`. It handles PIN pairing and tokens, serves `../web`
  statically, and keeps the touchpad event log. `sim_bridge()` connects the simulator's virtual touchscreen to
  that log, as if `touchpad.html` were open on the phone.
* `tappet.taptest` runs the acceptance test and writes its report.
* `tappet.workspace` computes coverage and draws the plots.
* `tappet.keyboard` is an approximate iOS keyboard layout used by `/api/type`.

## Bridge notes (PC vs Android head)

* There is no camera and no agent on the PC. `GET /api/camera.jpg` and `POST /api/chat` return `501
  {"ok":false,"err":"not_implemented"}`, and `/api/say` prints the text to the console.
* `POST /api/cmd` returns the body's response with HTTP 200, even when that response has `ok:false`. The other endpoints map
  error codes to HTTP status: `400` for unreachable, limit and bad_args; `409` for not_enabled; `429` for queue_full; `503` for link_down;
  `504` for timeout; `422` for no_touch.
* `/api/tap`, `/api/swipe`, `/api/type` and `/api/jog` return only after the motion is done. `/api/calibrate/auto` and
  `/api/taptest` enable the servos if needed and block until they finish, which takes about 0.7–0.9 s per tap.
* Tap-test reports also go to `host/out/taptest-*.{csv,json}`.

## Simulator protocol details that SPEC leaves open

* `stop`, `relax` and the watchdog cancel motions that are queued or running. Each cancelled id gets
  `{"evt":"error","id":N,"err":"fault","msg":"cancelled (stop)"}`.
* Motion commands need `enable` first, otherwise they fail with `not_enabled`.
* `joints` clamps angles to their limits. It rejects a target that breaks the linkage (θ1−θ2) with `limit`.
* `move` checks only the end point when queued. If the straight path crosses a limit, the move fails at run time
  with an `error` event.
* When `ms` is omitted, the duration is `1.875·|Δq|/max_vel` for the slowest joint, so peak min-jerk velocity stays at or below max_vel.
  The minimum is 60 ms. Cartesian segments also cap the average speed at 150 mm/s.
* Some responses, such as `calib_get` with 8 joints, are longer than the 512-byte guideline.
