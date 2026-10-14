# LabRat architecture

This is the single system-design document for LabRat. It was written from the code in `server/labrat/`, `bridge/labrat_bridge/`, `web/`, `phone/`, `cli/` and `deploy/`. When the code and this doc disagree, the code is right, so fix this doc. The wire protocols are summarised in [../CONTRACT.md](../CONTRACT.md). The Tappet arm firmware protocol is specified in [../tappet/docs/SPEC.md](../tappet/docs/SPEC.md).

## 1. Components and processes

| Process | Code | Runs on | Role |
|---|---|---|---|
| **LabRat server** | `server/labrat/` (FastAPI + uvicorn, one Python process) | cloud (AWS EC2 via Docker) or laptop | agent, simulator, learning loop, memory, WebSocket hub, REST API. Also serves `web/` at `/` and `phone/` at `/phone` |
| **Hardware bridge** | `bridge/labrat_bridge/` (`python -m labrat_bridge`) | laptop with the USB devices | dials out to `/ws/bridge`, drives the serial devices, enforces local limits and runs the watchdog. Every device has a mock mode |
| **Laptop web app** | `web/` (`app.js`, `twin.js` three.js twin, `deck.js` deck calibration, `camera_cal.js`) | browser | chat, experiment gallery, 3D replay, device status, run approval, deck/camera calibration |
| **Phone SPA** | `phone/` (`phone.js`) | iPhone on a static stand looking down at the CNC bed | camera frames and mic audio go up to the server; animated face and TTS audio come back |
| **CLI** | `cli/labrat_cli/` (`uv run labrat --server http://127.0.0.1:8100 --token labrat`) | terminal | REPL over `/ws/ui` and REST |
| **Tappet firmware/host** | `tappet/firmware`, `tappet/host` | ESP32-S3 + PCA9685 | in the demo setup, only drives the syringe servo (PCA9685 ch 8); the Tappet arms are optional. The bridge talks to them through the vendored client `bridge/labrat_bridge/tappet_client.py` (copied from `tappet/host/tappet/client.py`) |

Physical layout: the 3018 CNC (carrying the syringe pipette in its spindle clamp) is in the middle, an SO-101 arm stands on each side of it, the iPhone sits on a fixed stand looking straight down at the CNC bed, and the laptop (bridge + console) is off to the side. Nothing moves the camera.

Hardware, as bridge device ids (`bridge/labrat_bridge/bridge.py: ALL_IDS`):
- `so_a`, `so_b` are SO-101 arms (Feetech STS3215 bus at 1 Mbps): plain manipulators with the stock gripper, `so_a` on one side of the CNC and `so_b` on the other. Named poses are `home`, `ready` and `rest`; the old `look_deck`/`look_user`/`look_plate` names are accepted as aliases for `ready`.
- `tap_a` is the ESP32-S3 + PCA9685 Tappet board that drives the syringe servo (JSON lines over UART at 115200). `tap_b` and the Tappet arms are optional and not part of the demo setup.
- `cnc` is a 3018 CNC running GRBL 1.1 at 115200. Its `probe` action uses the head's capacitive probe (G38.3).
- The head is a small tool changer: a tool rail on its front takes modules (`cad/syringe/syrhead/tools.py`), today a
  USB microscope (`microscope` device, `bridge/labrat_bridge/microscope.py`) and a capacitive liquid-level probe
  (TTP223 → GRBL probe pin). Each tool has an offset from the syringe tip (`deck_cal` `tools`, nominal from
  `cad/syringe/tools.json`); `server/labrat/tooling.py` moves the spindle to `target − offset` and the agent's
  `tool` call does goto / snap / probe.
- `pipette` is a syringe pipette that replaces the spindle motor in the 3018's 42 mm motor holder ([`cad/syringe`](../cad/syringe)): a disposable syringe with a 200 µL tip on a luer-to-tip adapter, its plunger driven through a 12:1 printed gear train and a rack (the rack slides on its own back against a rib behind the mesh) by a continuous-rotation FS90R servo on PCA9685 **channel 8** (`servo_raw`): 0.5 mm of plunger per servo radian. The servo has no position feedback: the bridge dead-reckons the plunger from calibrated speeds, re-zeroes with `home` (bottom stop) and accepts camera/NN estimates through `observe`.

## 2. Topology

```
 iPhone on stand     ──wss /ws/phone──┐
 Laptop web (/)      ──wss /ws/ui ────┤        ┌──────────── LabRat server (FastAPI) ─────────────┐
 CLI (labrat)        ──wss /ws/ui ────┼──────► │ app.py  hub.py  agent.py  lab.py  sim.py         │
 Laptop bridge       ──wss /ws/bridge─┘        │ learn.py vision.py deck_cal.py store.py          │
   │ USB serial                                 │ voice.py embed.py telemetry.py                    │
   ├─ so_a (SO-101 gripper, left of CNC)        └───┬──────┬──────┬───────┬────────┬──────────┬───┘
   ├─ so_b (SO-101 gripper, right of CNC)           │      │      │       │        │          │
   ├─ tap_a (Tappet ESP32 board)                Anthropic Mongo  Voyage  ElevenLabs Raindrop  Modelence
   │    └─ PCA9685 ch 8 → syringe servo          (LLM +  Atlas   (embed)  (TTS/STT) (events)  Mission Control
   └─ cnc (3018 GRBL) carrying the pipette        vision)                                     (/api/events)
```

All clients connect out to the server, so the bridge needs no inbound port. WebSockets check `?token=LABRAT_TOKEN` (default `labrat`) and close unauthenticated sockets with code 4401.

## 3. Data flow (one experiment)

```
voice ─► STT ─► agent ─► recall ─► design ─► simulate ─► approve ─► execute ─► observe(vision) ─► learn ─► memory
```

1. **Voice.** `phone.js` sends `{"t":"audio","b64","mime"}` on `/ws/phone`. Typed text uses `{"t":"text"}`, and the web/CLI chat uses `{"t":"chat"}` on `/ws/ui` or `POST /api/chat`.
2. **STT.** `voice.py:stt` sends the audio to ElevenLabs `scribe_v1`. If no key is set, the server asks the user to send text instead.
3. **Agent.** `agent.py:handle` runs one turn at a time under a lock and keeps the last 12 messages of history.
   - It runs an Anthropic tool-use loop (`_anthropic_loop`). The default model is `ANTHROPIC_MODEL=claude-sonnet-5`. Setting `LLM_PROVIDER=fireworks` uses `_fireworks_loop` instead.
   - If there is no LLM key, or the LLM call errors, a rule-based fallback (`_rules`) answers.
   - Tools: `list_devices`, `look` (latest frame from the static overhead iPhone; nothing moves), `recall`, `design_experiment`, `simulate`, `select_variant`, `run_variant`, `move_arm`, `cnc_move`, `pipette`, `say`, `remember`.
4. **Recall.** `store.py:recall` uses Atlas `$vectorSearch` on `memories.embedding`, with an optional `type` filter. If that is unavailable, it falls back to cosine similarity in Python plus a keyword bonus.
5. **Design.** `lab.py:create_experiment` takes variants from the LLM (`agent._llm_variants`) or from the deterministic generator `sim.generate_variants` (dye ratios × volumes, 12 by default).
6. **Simulate.** `lab.py:simulate_experiment` runs `sim.simulate` on each variant, ranks the variants that pass by score, sets `best_variant_id`, and broadcasts `{"t":"sim"}` frames for the 3D twin.
7. **Approve.** `lab.py:request_run` refuses a variant that was not simulated or failed its checks. It then creates a run with status `awaiting_approval`. A human approves it (`/ws/ui {"t":"approve"}` or `POST /api/runs/{id}/approve`) unless `auto=true` or `LABRAT_AUTO_APPROVE=1`.
8. **Execute.** `lab.py:_execute`/`exec_step` turns each protocol op into bridge commands through `hub.cmd`. The command goes out as `{"t":"cmd",id,device,action,args}`, and a future resolves on `{"t":"result"}` or times out.
9. **Observe (vision).** The `photo` step lifts the pipette, parks the CNC out of the camera view and waits for a phone frame newer than the park (`hub.wait_frame`). No arm moves, because the iPhone is on a static stand; `LABRAT_PHOTO_POSE=[device:]pose` re-enables an arm pose if the phone is ever arm-mounted. If `LABRAT_AUTO_OBSERVE` is on (the default), `vision.py:auto_observe` then takes that fresh frame (never a frame from before the photo step) and estimates the target well's RGB. It uses the homography method if a camera calibration exists, otherwise Claude vision. A human can also observe manually (`POST /api/runs/{id}/observe` or the UI `observe` message).
10. **Learn.** `learn.py:observe` refits the simulator parameters and accepts them only if the error improves (see §7).
11. **Memory.** The server writes typed memories: an *episodic* run summary, a *semantic* "predicted vs observed" fact, and a *procedural* protocol, which is promoted only when it improved. Recall uses these memories on the next goal.

Replies are spoken by `agent.speak` → `voice.py:tts` (ElevenLabs `eleven_flash_v2_5`). The phone falls back to browser `speechSynthesis` when `audio_b64` is null.

## 4. Endpoints (generated from `server/labrat/app.py` and `server/labrat/vision.py`)

### WebSocket

| Path | Peer | Client → server `t` | Server → client `t` |
|---|---|---|---|
| `/ws/bridge` | hardware bridge (one at a time; a new connection replaces the old one) | `hello {devices}`, `state {devices}` (~5 Hz), `result {id, ok, data, error}`, `log` | `cmd {id, device, action, args}` |
| `/ws/phone` | iPhone SPA (many) | `frame {jpeg, w, h}`, `audio {b64, mime}`, `text {text}` | `face {mood, gaze}`, `say {text, audio_b64}`, `transcript {role, text}` |
| `/ws/ui` | web app, CLI (many) | `chat {text}`, `approve {run_id}`, `abort`, `observe {run_id, rating, rgb, well}` | `devices`, `chat {role, text, meta}`, `experiment`, `sim`, `run`, `frame`, `log`, `deck_cal`. On connect the server replays the devices, the last 10 experiments and the last 5 runs |

### REST (21 routes)

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | liveness: session id, bridge connected, LLM on, Mongo on |
| GET | `/api/state` | devices, phone/UI counts, LLM/Mongo flags, has_frame, pending run, last 10 runs |
| GET | `/api/experiments` | last 50 experiments (frames stripped) |
| GET | `/api/experiments/{exp_id}?frames=` | one experiment (`frames=true` includes the sim frames) |
| GET | `/api/experiments/{exp_id}/variants/{vid}/frames` | sim frames and result for one variant (3D replay) |
| POST | `/api/chat` `{text}` | one agent turn; returns `{reply}` |
| POST | `/api/experiments/{exp_id}/simulate` `{n_variants?}` | (re)simulate all variants |
| POST | `/api/experiments/{exp_id}/run` `{variant_id?, auto?}` | request a real run (requires a passing simulation) |
| POST | `/api/runs/{run_id}/approve` | approve a pending run and start execution |
| POST | `/api/runs/{run_id}/observe` `{rating?, rgb?, well?, vol_ul?}` | manual observation, triggers the learning loop |
| GET | `/api/learning` | sim_params version history and the current version |
| POST | `/api/abort` | cancel run tasks; send `stop` to cnc, so_a, so_b |
| GET | `/api/memory/search?q=&k=5&type=` | memory recall |
| GET | `/api/deck_cal` | deck calibration summary |
| POST | `/api/deck_cal` | patch the deck calibration (pipette offset, points) |
| POST | `/api/deck_cal/teach` `{target, pos?}` | teach a deck target from the current CNC position (or an explicit `pos`) |
| POST | `/api/jog` `{device:"cnc", dx, dy, dz, f}` | relative CNC jog (each step must be ≤ 50 mm) |
| POST | `/api/camera_cal` `{corners, image_w, image_h}` | save the camera→deck homography corners (beakers A1, A7, F7, F1) |
| GET | `/api/camera_cal` | read the camera calibration |
| GET | `/api/camera_frame` | latest phone frame |
| POST | `/api/runs/{run_id}/auto_observe?pose=` | vision observation now |

Static mounts: `/phone` → `phone/`, `/` → `web/`. Errors not handled elsewhere return `500 {"error"}`.

## 5. Persistence: MongoDB collections and memory types

`store.py` keeps an in-memory primary copy and writes through to Atlas in a thread pool. If `MONGODB_URI` is unset or Atlas is unreachable, the server runs entirely in memory. At boot it loads 500 memories, 50 experiments, and all `sim_params`/`observations`.

| Collection | Contents |
|---|---|
| `experiments` | goal, variants (protocol, params, sim result), `best_variant_id`, `selected_variant_id`. Variants are written without their frames |
| `variants` | reserved (variants currently live inside experiments) |
| `runs` | status (`awaiting_approval → running → done/failed/aborted`), step log, predicted/observed RGB, vision result |
| `memories` | `{type, text, tags, meta, embedding, embed_model, source_run_id, confidence}`. Vector index `memories_vector` (cosine; 1024-d Voyage, or 256-d when the hashed fallback is used) |
| `events` | audit log (`chat`, `run_*`, `rsi.update`, `vision.observe`, `deck_teach`, `bridge_connected`, …). Capped at about 2000 in memory |
| `sim_params` | versioned simulator parameters `{v, params, proposed_params, error_before, error_after, accepted, trigger_run_id, parent_v}` |
| `observations` | `{run_id, well, rating, rgb, comp, predicted_rgb, vol_ul, cmd_vol_ul, source: operator|vision}` |
| `config` | doc `deck_calibration` (also in `server/data/deck_cal.json`) |
| `camera_cal` | doc `camera_cal` (homography corners) |

**Memory types** (`store.MEMORY_TYPES`):
- **semantic**: facts and notes, including "predicted vs observed" facts.
- **episodic**: one per real run.
- **procedural**: a protocol that improved the prediction.
- **taxonomic**: device, reagent, labware and colour taxonomy. `learn.seed_taxonomy` seeds it at startup.

## 6. Simulator (`server/labrat/sim.py`)

- **Deck** (CNC machine coordinates, mm; source of truth [`cad/deck/deck.json`](../cad/deck), checked by `tests/test_sim.py`):
  - tip rack: FT-200L-RS, 96 × 200 µL, 117 × 81.5 mm box; 8×12 grid with A1 at (17, 19.25), 9 mm pitch.
  - beaker matrix (printed, `DECK-001`): 6 rows A–F × 8 columns of 15.3 × 50 mm beakers on a 19 mm pitch, A1 at
    (153.25, 32). Columns 1–7 are the mixing beakers (the `plate` grid, wells A1–F7, 5 mL each); column 8 holds the
    stocks: A8 water, B8 red, C8 yellow, D8 blue (the reservoirs, 4.5 mL each), E8/F8 free.
  - waste at (280, 170); eject bar at (10, 170).
  - Z heights (the tip end is 55 mm above the bed at z = 0; beaker rims are at 51.2): safe 0, dispense −9 (5 mm inside
    the rim), aspirate / mix −45 (tip end 10 mm above the bed), tip pick-up −45 (the travel limit; see below).
  - bounds: X 0..300, Y 0..180, Z −45..0.
  - Reach: the tip only reaches liquid while a beaker holds about 1.23 mL (`REACH_UL`); aspirating below that is an
    error, and a mix in a beaker with less is skipped with a warning.
  - Tip pick-up does not fit the 3018's 45 mm of Z with the FT-200L-RS (seating needs about 49.5 mm and pulling a tip
    out of the rack about 51 mm): `cad/deck/report.json`. Teach real `tip_rack` points once that is resolved.
- **Ops:** `pick_tip`, `aspirate`, `dispense`, `mix`, `eject_tip`, `photo`, `wait`.
- **Motion:** trapezoidal timing (accel 400 mm/s², 3000 mm/min XY, 600 mm/min Z). The pipette moves 100 µL/s.
- **Liquid:** each well's composition is tracked in µL per dye. Colour is predicted by subtractive (Beer–Lambert) mixing from the per-dye absorbance (`mix_rgb`), with `VOL_GAIN` = actual ÷ commanded volume.
- **Checks (any one fails the variant):**
  - out of bounds;
  - XY travel below safe Z;
  - tip reuse or contamination;
  - tip overfill (200 µL);
  - reservoir underflow, and aspirating below the reachable liquid level;
  - well overflow;
  - aspirate or dispense without a tip;
- **Score:** 1 − (RGB distance to the goal colour) ÷ 441.7, multiplied by 0.1 if the variant failed. The output includes frames for the twin replay.
- **Deck calibration:** coordinates go through `deck_cal` (taught points take priority over nominal ones).

## 7. Learning loop (`server/labrat/learn.py`)

After each observation:
1. Compare the predicted and observed well RGB.
2. Fit the dye absorbance in absorbance space with ridge-regularised least squares (λ = 0.3, pulled toward the previous parameters). Fit the pipette volume gain (clamped to 0.5..1.5) when volumes were measured.
3. Store a new `sim_params` version. It is **accepted** only if the mean error over all observations goes down. Otherwise the previous parameters are restored.
4. If accepted, re-score every past variant and re-rank the best variants.
5. Write a semantic fact. Promote the run's protocol to procedural memory only if the parameters were accepted, this run's own prediction error dropped, and rating ≥ 3.
6. Emit `rsi.update` to the event store, Raindrop and Modelence.

`GET /api/learning` exposes the version history.

## 8. Deck and camera calibration

- **Deck (`server/labrat/deck_cal.py`, UI `web/deck.js`):**
  - Taught points are CNC coordinates of the **pipette tip**, and `spindle = tip − pipette_offset`. The default offset is `{dx: 0, dy: 0, dz: 0}`: the syringe tip is on the spindle axis.
  - Teachable targets are `tip_rack.A1/H12`, `plate.A1/F7` (beaker matrix), the reservoirs, waste and eject bar.
  - When both A1 and the far corner of a grid are taught, pitch and rotation are derived from them. Otherwise the grid uses its nominal, axis-aligned pitch (tip rack 9 mm, beakers 19 mm).
  - Workflow: jog with `POST /api/jog`, then save the position with `POST /api/deck_cal/teach`.
- **Camera (`server/labrat/vision.py`, UI `web/camera_cal.js`):**
  - The operator clicks the A1, A7, F7 and F1 beaker centres on a phone frame. `homography()` maps grid (col, row) to image pixels.
  - The sampler takes the median of a small disk at the projected beaker centre and white-balances it against an empty beaker (scaled to 245).

## 9. Safety invariants

- **No real run without a passing simulation.** `lab.request_run` raises if the variant was not simulated or `sim.ok` is false.
- **Human approval by default.** A run waits in `awaiting_approval` unless `auto` or `LABRAT_AUTO_APPROVE` is set.
- **Abort** (`POST /api/abort`, UI `abort`) cancels the run tasks and sends `stop` to cnc, so_a and so_b.
- **Bridge-side limits** (enforced even if the server is wrong):
  - GRBL soft limits X 0..300, Y 0..180, Z −45..0 (`grbl.py: LIMITS`);
  - feed caps of 1500 mm/min XY and 500 mm/min Z;
  - G-code is validated and moves are segmented through safe Z;
  - SO-101 joints are clamped to their calibrated min/max (`so101.py: clamp`);
  - syringe plunger targets outside 0..`stroke_mm` are rejected, every servo drive ends with a neutral pulse, and `stop` is not queued behind a running move.
- **Watchdog.** If the server link is down for more than 3 s (`bridge.py: WATCHDOG_S`), the bridge calls `safe_stop` on every device: CNC feed hold, arms hold, syringe servo to neutral (and the Tappet firmware relaxes all PWM after 5 s without commands, which also stops a continuous servo). Tappet boards also get a 1 s heartbeat that keeps the firmware's 5 s watchdog fed.
- **Command timeouts.** Bridge commands time out. A bridge disconnect fails every pending command, which fails the run.
- **REST jog** is limited to ≤ 50 mm per axis and only for `cnc`.
- **Graceful degradation.** Every sponsor integration logs a warning once and falls back to the in-memory store, the rules agent, hashed embeddings, or browser TTS, so the demo never blocks.

## 10. Sponsor integration map

| Sponsor | Where (file:function) | Status (2026-10-11 14:59 PDT) |
|---|---|---|
| Anthropic (Claude) | `server/labrat/agent.py:_anthropic_loop`, `_complete_text`, `_llm_variants`; `server/labrat/vision.py:llm_rgb` (vision) | live, returns 200 |
| MongoDB Atlas | `server/labrat/store.py:Store` (write-through, `$vectorSearch` in `recall`, index creation in `_bootstrap`); `deck_cal.py`; `vision.py:get_cal/set_cal` | live, returns 200 |
| Voyage AI | `server/labrat/embed.py:embed` (`voyage-3.5`) | **key not set**, so the hashed bag-of-words fallback (`_hash_embed`, 256-d) is in use |
| ElevenLabs | `server/labrat/voice.py:tts` (`eleven_flash_v2_5`), `voice.py:stt` (`scribe_v1`) | TTS live, returns 200 |
| Raindrop | `server/labrat/telemetry.py:track` (SDK `track_ai`, or REST `POST https://api.raindrop.ai/v1/events/track`); called from agent `chat`/`tool_call`, lab `design_experiment`/`simulate`/`run`, learn `rsi.update`, vision `vision.observe` | live over REST, returns 200 |
| Modelence | `server/labrat/telemetry.py:modelence`. It POSTs `{app, session, event, data}` to `MODELENCE_URL` with `Authorization: Bearer MODELENCE_KEY`. Every `track()` event is mirrored, plus `session_start` | live: Mission Control at https://labrat-mission-mv4c6k4pk66-prod.cloud.modelence.app, ingest `POST /api/events`. The bearer equals the Modelence config `labrat.ingestKey` (= `MODELENCE_KEY`). POST returns 200 |
| Fireworks (optional) | `server/labrat/agent.py:_fireworks_loop` | off unless `LLM_PROVIDER=fireworks` |
| AWS | `deploy/aws_ec2.sh` + `Dockerfile` | script ready, **not yet deployed** |

## 11. Deployment topology

- **Local (current).** Run `cd server && uv run uvicorn labrat.app:app --port 8100` (any port works). The bridge runs on the same laptop with `--server ws://localhost:8100`, and the phone opens `http(s)://<host>/phone`.
- **AWS (scripted, not yet deployed).** `deploy/aws_ec2.sh` provisions one EC2 instance (t3.small, us-west-2 by default) running the `Dockerfile` image (python:3.12-slim, uvicorn on port 8000). Caddy in front provides automatic HTTPS on `https://<ip-dashes>.sslip.io`. The script ships the code as a tarball to a private S3 bucket that the instance fetches through a presigned URL, and uploads `.env` privately as the container `--env-file`. Re-running with `REDEPLOY=1` replaces the instance. The bridge then dials `wss://<ip-dashes>.sslip.io/ws/bridge`.
- **Modelence Mission Control** is a separate hosted app that receives the event stream (see §10).
- **Configuration** comes from the environment (`.env`, loaded from the repo root and `server/`): `LABRAT_TOKEN`, `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `FIREWORKS_API_KEY`, `FIREWORKS_MODEL`, `MONGODB_URI`, `MONGODB_DB`, `VOYAGE_API_KEY`, `RAINDROP_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `MODELENCE_URL`, `MODELENCE_KEY`, `LABRAT_AUTO_APPROVE`, `LABRAT_AUTO_OBSERVE`, `LABRAT_PHOTO_POSE` (unset by default), `LABRAT_PHOTO_WAIT_S` (default 3), `LABRAT_DECK_CAL`. See `.env.example`.
