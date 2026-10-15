# LabRat

**A voice-operated, simulation-first desk-lab robot that learns from its own results.**

You talk to it through a phone on a pan/tilt stand (its face, eyes, ears and voice), chat with it from the laptop
console, or script it from Python. Every experiment is dry-run on a digital twin before it touches hardware, a human
approves the real run, and the measured results go into a CSV, the lab notebook and the colour model.

The hardware is a 3018 desktop CNC carrying a geared syringe pipette (plus a USB microscope and a capacitive
liquid-level probe on a tool rail) in place of its spindle, a printed beaker matrix, and an SO-101 arm on each side.

LabRat started at the Harness Engineering & Model Wrangling Hackathon (SF, October 2026), where it took 2nd place.

![The hackathon bench: the 3018 with a tip rack and beakers, an SO-101 arm at each front corner, the phone front-centre and the laptop on the right](docs/images/bench_photo.png)

| Digital twin (console) | Tool head on the 3018 (Cycles render) |
|---|---|
| ![three.js twin of the bench](docs/images/twin_wide.png) | ![tool head, deck and laptop](hardware/tool-head/renders/bench_full.png) |

## How it works

An **experiment is one plate**: you say what goes into each beaker (µL of water, red, yellow, blue), LabRat dry-runs
it on the digital twin (checks, time, predicted colour per beaker), you approve, the robot fills the plate, the phone
photographs it and every beaker gets a measured colour next to its prediction. Results export to CSV and refit the
colour model. Full guide: **[docs/experiments.md](docs/experiments.md)**.

Three ways in:

- **Console** (`/console/`): new experiment, edit the sample table, run, approve, read the plate.
- **Voice or chat**: "find the most orange mix of red and yellow". The agent (Claude, or simple rules without an API
  key) writes the samples, dry-runs them and asks you to approve.
- **Python**: `from labrat_cli import Lab`; scripts live in [`experiments/`](experiments).

## Repo map

```
                      ┌──────────── cloud/ ─────────────┐
 apps/console ─ws/ui─►│ Worker ─► Lab Durable Object    │◄─ws/bridge─ bridge/ ─USB─┬─ SO-101 arms so_a, so_b
 apps/cli ─────ws/ui─►│   experiments · sim · agent     │                          ├─ 3018 CNC (GRBL)
 apps/phone ─ws/phone►│   pipette / probe / stand       │                          ├─ USB microscope
   (face, voice,      │   drivers ─► D1                 │                          └─ servo hub ─┐
    camera, BLE) ─────┼─────────────────────────────────┼──── BLE (alternative) ──────────────►│
                      └──────────── Anthropic API ──────┘          firmware/servo-hub (ESP32 + PCA9685)
```

| Dir | What |
|---|---|
| [`cloud/`](cloud) | Coordinator: Cloudflare Worker + one Durable Object + D1 (hub, REST, experiments, simulator, agent, device drivers); serves `apps/`. [README](cloud/README.md) |
| [`apps/`](apps) | Clients: `console/` (laptop UI + three.js twin), `phone/` (face, camera, voice, BLE hub link), `cli/` (`labrat` REPL + `labrat_cli.Lab` Python client) |
| [`bridge/`](bridge) | Laptop USB bridge: SO-101 arms, GRBL CNC, USB microscope, servo hub; every device has a mock. [README](bridge/README.md) |
| [`firmware/`](firmware) | `servo-hub/`: ESP32 + PCA9685 firmware (PlatformIO), JSON lines over USB and BLE. [README](firmware/servo-hub/README.md) |
| [`hardware/`](hardware) | CAD (build123d), BOMs, renders and print plates: tool head, deck, 3018 model, phone stand, SO-101 notes, electronics, print. [Index](hardware/README.md) |
| [`docs/`](docs) | [`architecture.md`](docs/architecture.md) (system design, endpoints), [`protocol.md`](docs/protocol.md) (wire contract), [`experiments.md`](docs/experiments.md) (how to run experiments) |
| [`experiments/`](experiments) | Experiment scripts using `labrat_cli.Lab` |

## Quickstart

Needs Node 22+ (cloud), [uv](https://docs.astral.sh/uv/) (bridge, CLI) and PlatformIO (firmware, only for the real
hub). Everything runs locally; nothing needs a Cloudflare account until you deploy.

```bash
# 1. Coordinator (local Worker + DO + D1)
cd cloud
npm i
cp .dev.vars.example .dev.vars           # LABRAT_TOKEN=labrat; optionally ANTHROPIC_API_KEY
npx wrangler d1 migrations apply labrat --local   # schema changed? rm -rf .wrangler/state/v3/d1 first
npx wrangler dev                         # http://127.0.0.1:8787
```

- Console: <http://127.0.0.1:8787/console/> (add `?token=...` if you changed `LABRAT_TOKEN`).
- Phone: `/phone/` on the same origin. Camera, microphone and Web Bluetooth need a secure context, so a real phone
  needs HTTPS (a deployed Worker or a tunnel). Web Bluetooth works in Chrome on Android, not on iOS.

```bash
# 2. Bridge (another terminal; --mock all runs without any hardware)
cd bridge
uv run python -m labrat_bridge --server ws://127.0.0.1:8787 --token labrat --mock all
uv run python -m labrat_bridge --server ws://127.0.0.1:8787 --token labrat      # real hardware, auto-discovered

# 3. CLI
cd apps/cli
uv run labrat --server http://127.0.0.1:8787 --token labrat

# 4. Servo hub firmware
cd firmware/servo-hub
pio test -e native                       # host-side unit tests
pio run                                  # build (default env esp32s3; -e esp32 for a classic DevKit)
pio run -e esp32s3 -t upload             # flash
```

Tests: `cd cloud && npm test && npm run typecheck`, `cd bridge && uv run --group dev pytest -q`,
`cd apps/cli && uv run pytest`, `cd apps/phone && npm test`, `cd firmware/servo-hub && pio test -e native`,
`cd hardware/electronics && python3 gen_wiring.py` (checks the pin map against the firmware).

Deploying: see [`cloud/README.md`](cloud/README.md) (create the D1 database, apply migrations with `--remote`, set
`LABRAT_TOKEN` and `ANTHROPIC_API_KEY` as secrets, `npx wrangler deploy`).

## Credits

- SO-101 arm (URDF and decimated meshes in `apps/console/models/so101/`):
  [TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100), Apache-2.0.
- 3018 model (`apps/console/models/cnc3018.glb`): original, generated by `hardware/cnc3018/cnc3018.py`.
- Laptop, bench and labware in the twin are procedural (`apps/console/twin.js`, `apps/console/twin/`); the laptop
  screen texture (`apps/console/models/laptop_screen.jpg`) is a screenshot of the console.

License: [MIT](LICENSE).
