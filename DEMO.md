# LabRat: 2-minute demo script

> **Status (2026-10-11, 14:59 PDT):** Anthropic, MongoDB Atlas, Raindrop, ElevenLabs TTS and Modelence
> are live (verified HTTP 200). Voyage is not set yet (hashed-embedding fallback). Hardware is not yet
> connected, so rehearse with `--mock all`. AWS is not yet deployed: use the laptop server URL.
> The Modelence Mission Control dashboard (https://labrat-mission-mv4c6k4pk66-prod.cloud.modelence.app)
> can be shown in place of, or next to, the Raindrop tab. System design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Setup before going on stage.** Server is up (laptop `localhost:8000`, or the AWS URL once deployed). Laptop console is open at `/?token=labrat`. The iPhone is on `so_a` with `/phone/?token=labrat` open and the eyes showing. The bridge is running (`--mock all` if there is no hardware). Reservoirs hold water, red, yellow and blue. A fresh tip rack and an empty plate are on the deck.
Calibrate the camera once: click **Calibrate cam** in the top bar, click the centres of wells A1, A12, H12 and H1, then click **Save**.
Have a second browser tab open on the Raindrop dashboard and one on the Atlas `memories` collection.

| t | You say / type | Screen shows |
|---|---|---|
| 0:00 | (to audience) "This is LabRat, a desk-lab agent. The iPhone on the arm is its eyes, ears and voice. It never touches a liquid before it has simulated the move." | Console: device pills are green (so_a, so_b, tap_a, cnc, pipette), the phone eyes are idle, and the cam thumbnail is live. |
| 0:12 | (to the phone, push-to-talk) **"Make the most orange-looking well using red and yellow."** | The phone face switches to listening and then thinking. The chat shows the transcript plus `recall` and `design_experiment` tool calls. |
| 0:25 | (point at screen) "It recalled what it learned last time, then designed 12 protocol variants and is running each one through the digital twin." | Experiments tab: a gallery of 12 variants with predicted colour swatches, scores and check badges (volume, tip reuse, bounds). The twin replays the best variant: the CNC head moves, tips are picked up, wells fill. |
| 0:40 | "Every variant is checked for volumes, tip reuse and collisions. The failing ones are filtered out and the best one goes up for approval." | The phone says something like "Variant v3 looks best, predicted orange. Approve?". An approval card appears in the console. |
| 0:50 | Click **Approve** (or say **"approve"**) | The run starts. The CNC moves and the pipette aspirates and dispenses. The step log streams (`→ cnc.move …`, `→ pipette.aspirate …`). |
| 1:15 | "Last step: the photo. The arm looks at the plate, finds the well through the camera calibration, white-balances against an empty well and reads the real colour. Nobody has to type anything." | `so_a` moves to `look_plate`. The log shows `vision: saw C5 rgb(…) via homography`. |
| 1:25 | Open the **Learning** tab | A new `sim_params` version appears with error before → after (for example 39 → 12 RGB units), accepted, and 12 variants re-scored. |
| 1:35 | "It refit its own simulator. It only adopts the new parameters if prediction error goes down. It then re-scored every past variant and promoted this protocol to procedural memory." | Memory tab: search "orange" to show a semantic fact ("real result was more red…"), an episodic run record and a PROCEDURE entry. |
| 1:48 | Switch to the Raindrop tab | `rsi.update` and `vision.observe` events with their before/after metrics. |
| 1:55 | "Next time someone asks for orange, it starts from a better simulator. That's the loop." | Done. |

## Fallbacks
- **No hardware, or a device misbehaves:** restart the bridge with `--mock all`. Device pills read `mock`, and the run, twin and logs behave the same way. Say: "this is the same code path with mocked drivers."
- **No phone frame or auto-observe skipped** (the log says `vision: auto-observe skipped`): in the console, rate the run and enter the observed RGB, or run
  `curl -XPOST "$URL/api/runs/<run_id>/observe?token=labrat" -H 'content-type: application/json' -d '{"rating":4,"rgb":[225,95,25]}'`.
  The Learning tab updates in the same way.
- **Calibration is off** (the wrong well gets sampled): click **Calibrate cam**, then **Reload frame**, and click the four wells again. This takes about 10 s. If there is no time, use the manual observe described above.
- **Voice or STT fails:** type the same sentence into the console chat box. The rule-based agent also handles `make orange`, `simulate`, `run best`, `approve` and `abort` without an LLM key.
- **Something moves wrong:** click **Abort** in the top bar (feed hold and soft reset). Pulling the laptop's network trips the bridge watchdog within 3 s.
- **Re-run auto-observe by hand:** `curl -XPOST "$URL/api/runs/<run_id>/auto_observe?token=labrat&pose=true"`.
