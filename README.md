# LabRat 🐀🧪

**A voice-operated, simulation-first desk-lab agent with persistent memory that improves itself.**
Built for the Harness Engineering & Model Wrangling Hackathon (SF, 2026-10-11).

Talk to it through an iPhone mounted on a robot arm (its eyes, ears and voice), or chat with it on the
laptop. It designs an experiment, **simulates many variants in a 3D digital twin**, picks the best one,
asks for approval, and then runs it for real. The hardware is two SO-101 arms, Tappet ESP32 arms and a
3018 CNC carrying a servo pipette. It then compares the result with its prediction, **refits its own
simulator**, and stores what it learned in typed memory (semantic / episodic / procedural / taxonomic).

```
iPhone SPA (on SO-101 arm) ──wss──┐                         ┌─ MongoDB Atlas  (typed memory + vector search)
Laptop web app (chat/twin) ─wss──┼── LabRat server on AWS ──┼─ Voyage AI      (embeddings)
Laptop USB bridge ─────────wss──┘   agent + simulator       ├─ Raindrop       (traces, rsi.update events)
  SO-101 ×2 · Tappet ESP32 ×1-2     + learning loop         ├─ ElevenLabs     (TTS + STT on the phone)
  3018 CNC (GRBL) · servo pipette                           ├─ Modelence      (session/event hook)
                                                            └─ Claude / Fireworks Llama (agent LLM)
```

## The loop (why it's a harness, not a demo)
1. **Ask:** by voice or chat, e.g. "make the most orange well from red and yellow".
2. **Recall:** Voyage + Atlas vector search over prior episodic, semantic and procedural memories.
3. **Design and simulate:** N protocol variants are run in the digital twin (CNC motion, pipette
   volumes, tip tracking, dye mixing, safety checks), then scored.
4. **Select and approve:** the best passing variant needs explicit approval before real actuation.
5. **Execute:** the bridge drives the hardware with local limits, a gcode whitelist and a watchdog.
6. **Observe:** a phone photo and/or your rating.
7. **Self-improve:** `learn.py` refits the simulator parameters and adopts them only if prediction
   error drops. It re-scores past variants, promotes the protocol to procedural memory, and logs
   `rsi.update` to Raindrop. The Learning tab charts error over versions.

## Repo
| Dir | What |
|---|---|
| [`CONTRACT.md`](CONTRACT.md) | Integration contract: WebSocket and REST protocol, devices, deck, domain model |
| [`server/`](server) | FastAPI server: hubs, agent (LLM tool use + rule fallback), simulator, learning loop, sponsor integrations |
| [`web/`](web) | Laptop console: chat, three.js digital twin, experiment gallery, Learning and Memory tabs, approvals |
| [`phone/`](phone) | iPhone head: animated eyes, camera frames, push-to-talk / VAD voice, TTS playback |
| [`bridge/`](bridge) | USB bridge: SO-101 (Feetech STS), Tappet ESP32, GRBL CNC, servo pipette, each with a mock |
| [`cad/`](cad) | Servo pipette mount for the 3018 spindle (build123d to STL/3MF, cam travel table) |
| [`deploy/`](deploy) | `aws_ec2.sh`: EC2 + Docker + Caddy HTTPS (`<ip>.sslip.io`, needed for iPhone camera/mic) |

## Run it
```bash
cp .env.example .env    # fill in keys; anything blank falls back to a local mock
cd server && uv run uvicorn labrat.app:app --host 0.0.0.0 --port 8000
cd bridge && uv run python -m labrat_bridge --server ws://localhost:8000 --token labrat      # add --mock all without hardware
open "http://localhost:8000/?token=labrat"          # laptop console
# iPhone: https://<server>/phone/?token=labrat      (HTTPS is required for camera/mic)
./deploy/aws_ec2.sh                                 # deploy to AWS, prints the https URL
```

Hardware sister project, with the Tappet ESP32 arms and firmware: [JacobFV/tappet](https://github.com/JacobFV/tappet).
Sponsor-to-file mapping: [server/README.md](server/README.md#sponsor-integrations).
