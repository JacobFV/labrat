# LabRat — integration contract (hackathon, 2026-10-11)

A voice-operated, simulation-first desk-lab agent with persistent memory. It drives:
- 2× **SO-101** arms (Feetech STS3215 bus servos over USB serial, 1 Mbps, ids 1..6: shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper). Arm `so_a` carries the **iPhone** on its tip (the camera eye/mouth). Arm `so_b` is a general manipulator (gripper).
- 1–2× **Tappet ESP32 arms** (our firmware, JSON lines over USB UART 115200, see ../robot-2026-10-11/docs/SPEC.md §4; Python client in ../robot-2026-10-11/host/tappet/client.py). ESP32 PCA9685 **channel 8 = pipette plunger servo** (via `servo_raw {ch:8, us}`; needs `enable`).
- **3018 CNC** (GRBL 1.1, USB serial 115200) carrying the **servo pipette** on its spindle head. Work area X 0..300, Y 0..180, Z 0..-45 (mm, machine coords after homing/zeroing at the top-left-front of the deck, Z0 = safe height).
- **iPhone SPA** (served by the cloud server at `/phone`): realtime camera frames + mic → server; animated eyes + TTS audio ← server.
- **Laptop webapp** (`/`): chat with the agent, experiment gallery (many simulated variants), 3D digital twin (three.js), device status, run approvals.

## Topology
```
iPhone SPA ──wss──┐                       ┌── MongoDB Atlas (+ Voyage embeddings, vector search)
Laptop web ──wss──┼── LabRat server (AWS) ┼── Raindrop (traces/events)
                  │   FastAPI + agent     ├── ElevenLabs (TTS + STT)
Laptop bridge ─wss┘   + simulator         ├── LLM: Anthropic Claude (default) or Fireworks (Llama)
 (USB: SO-101 ×2, ESP32 ×1-2, GRBL CNC)   └── Modelence (app/session telemetry hook)
```
All clients connect OUT to the server. Server = single Python process: `server/` (FastAPI, uvicorn), serves `web/` at `/` and `phone/` at `/phone`.
Config via env (`.env`): ANTHROPIC_API_KEY, LLM_PROVIDER=anthropic|fireworks, FIREWORKS_API_KEY, FIREWORKS_MODEL, MONGODB_URI, VOYAGE_API_KEY, RAINDROP_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, MODELENCE_URL/MODELENCE_KEY, LABRAT_TOKEN (shared secret for bridge/clients). **Every sponsor integration must degrade gracefully** (log + in-memory fallback) when its key is missing, so the demo never blocks.

## WebSocket endpoints (JSON text frames; all carry `?token=LABRAT_TOKEN`)
### `/ws/bridge` (laptop hardware bridge ⇄ server)
- bridge→server `{"t":"hello","devices":[{"id":"so_a","kind":"so101","status":"ok|mock|error","info":{}}, ...]}`
- bridge→server `{"t":"state","devices":{"<id>":{...live state...}}}` (≈5 Hz)
- server→bridge `{"t":"cmd","id":"<uuid>","device":"<id>","action":"<name>","args":{...}}`
- bridge→server `{"t":"result","id":"<uuid>","ok":true|false,"data":{},"error":"..."}`
Device ids/kinds/actions:
| id | kind | actions (args) |
|----|------|---------------|
| `so_a`, `so_b` | so101 | `joints {q:{shoulder_pan,shoulder_lift,elbow_flex,wrist_flex,wrist_roll,gripper} deg, ms}`, `pose {name}` (named poses: home, look_deck, look_user, look_plate), `gripper {open:0..1}`, `torque {on}`, `read {}` |
| `tap_a`, `tap_b` | tappet | `raw {cmd, ...}` passthrough of the Tappet body protocol (joints/move/tap/neck/home/park/enable/relax) |
| `cnc` | grbl | `home {}`, `move {x,y,z,f}` (absolute mm, G90 G1), `jog {dx,dy,dz,f}`, `status {}`, `gcode {lines:[...]}` (validated), `stop {}` (feed hold + soft reset) |
| `pipette` | servo_pipette | `aspirate {ul}`, `dispense {ul}`, `blowout {}`, `set {frac 0..1}` (0 = plunger up/rest, 1 = fully pressed), `eject {}` (tip eject by moving over the eject bar — composite done by server, not bridge) |
Bridge enforces local limits (CNC soft limits & feed caps, joint limits) and a watchdog (stop motion if server link drops > 3 s). Every device has a `--mock` mode.

### `/ws/phone` (iPhone SPA ⇄ server)
- phone→server `{"t":"frame","jpeg":"<base64>","w":..,"h":..}` (≈2 fps, 640px), `{"t":"audio","mime":"audio/webm|audio/mp4","b64":"..."}` (one utterance, push-to-talk or VAD), `{"t":"text","text":"..."}`
- server→phone `{"t":"say","text":"...","audio_b64":"<mp3 base64 or null>"}`, `{"t":"face","mood":"idle|listening|thinking|speaking|happy|alert","gaze":[x,y]}`, `{"t":"transcript","role":"user|agent","text":"..."}`

### `/ws/ui` (laptop webapp ⇄ server)
- server→ui events: `{"t":"devices",...}`, `{"t":"chat","role":"user|agent|tool","text":"...","meta":{}}`, `{"t":"experiment","experiment":{...}}` (created/updated), `{"t":"sim","experiment_id":..,"variant_id":..,"frames":[...]}`, `{"t":"run","run":{...}}`, `{"t":"frame","jpeg":"..."}` (latest phone camera), `{"t":"log",...}`
- ui→server `{"t":"chat","text":"..."}`, `{"t":"approve","run_id":..}`, `{"t":"abort"}`

## REST
`GET /api/state` · `GET /api/experiments` · `GET /api/experiments/{id}` · `POST /api/chat {text}` · `POST /api/experiments/{id}/simulate {n_variants}` · `POST /api/experiments/{id}/run {variant_id}` (requires sim pass) · `POST /api/abort` · `GET /api/memory/search?q=` · `GET /healthz`

## Domain model (Mongo collections: experiments, variants, runs, memories, events)
- **Deck** (CNC bed, mm, CNC machine coords): `tip_rack` (8×12, origin (20,20), pitch 9), `plate` (96-well, origin (140,20), pitch 9, 300 µL), `reservoirs` (4 troughs at x=250, y=20/60/100/140, 20 mL, contents e.g. water/red/blue/yellow dye), `waste` (x=280,y=170), `eject_bar` (x=10,y=170). Heights: tip pick z=-38, well aspirate z=-30, reservoir z=-35, safe z=0.
- **Protocol** = ordered steps: `pick_tip{rack_well}`, `aspirate{src, ul}`, `dispense{dst, ul}`, `mix{well, ul, n}`, `eject_tip{}`, `photo{target}` (phone arm looks + captures), `wait{s}`.
- **Experiment** = goal (natural language, e.g. "make the most orange-looking well using red & yellow"), design space (params), N **variants** (protocols) generated by the agent → each simulated by the digital twin → simulated outcome + score + checks (volumes ≤ well cap, tip used once, no collisions/out-of-bounds, total time) → agent selects best → (approval) → real run → photo + observation → stored as memory (Voyage-embedded) → retrieved for future experiments.
- **Simulator** (`server/labrat/sim.py`): kinematic + liquid model: CNC motion timing (trapezoid), pipette volume accounting, well contents as mixtures of dyes → predicted RGB color (subtractive mixing), contamination check (tip reuse across sources), collision/bounds checks for CNC and SO-101 reach envelopes, returns per-step frames `{t, cnc:{x,y,z}, pipette:{vol_ul, tip}, wells:{A1:{rgb,vol}}}` for the 3D twin replay.

## Agent (server)
LLM tool-use loop. Tools: `list_devices`, `look` (latest phone frame → image), `recall(query)` (Voyage + Atlas vector search; fallback keyword), `design_experiment(goal, n_variants)`, `simulate(experiment_id)`, `select_variant(experiment_id, variant_id, reason)`, `run_variant(experiment_id, variant_id)` (requires sim pass + approval unless auto), `move_arm(device, pose|joints)`, `cnc_move`, `pipette`, `say(text)` (ElevenLabs → phone), `remember(text, tags)`. Every tool call, sim, run → Raindrop event + Mongo `events` + Modelence hook.
