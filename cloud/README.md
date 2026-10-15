# LabRat cloud

The LabRat coordinator: one Cloudflare Worker, one Durable Object (`Lab`, a single instance named `lab`) and one D1
database. The only outside service is the Anthropic API (the agent's brain); without a key a rule-based agent answers.
The wire contract is [`docs/protocol.md`](../docs/protocol.md).

```
src/
  index.ts        Worker: token check, / -> /console/, /api|/ws|/healthz -> the DO, everything else -> static assets
  do.ts           Lab DO: hibernatable WebSockets (tags ui|phone|bridge), REST route table, boot, device events
  hub.ts          providers, device routing (most recent announcer), pending commands + timeouts, broadcast, frames,
                  probe-arm safety rules
  devices/        DO-side logical devices on the provider's servo `hub` (firmware lines, protocol.md §4)
    base.ts         serialised actions, channel cfg, persistence hooks
    pipette.ts      dead-reckoning syringe on ch0 (FS90R, firmware-timed `pulse`, `until: syr_home`)
    probe.ts        probe arm on ch1 (deploy / retract / set)
    stand.ts        phone pan/tilt on ch2/ch3 (aim, look at deck | operator | home | <beaker>)
  agent.ts        Anthropic tool-use loop + rule-based fallback
  llm.ts          Messages API over fetch
  lab.ts          experiments: create / edit / dry run / repeat, run (approval, execution, abort), results, CSV
  sim.ts          digital twin: deck, samples -> steps, motion timing, liquid accounting, dye mixing, checks, frames
  learn.ts        colour model: ridge refit in absorbance space from every result, kept only if it predicts better
  vision.ts       camera homography + beaker sampling of phone JPEGs (jpeg-js), Claude-vision fallback (measures a plate)
  deck_cal.ts     taught deck points, pipette and tool offsets
  tooling.ts      tool-rail moves (pipette / microscope / probe)
  store.ts        D1 access          linalg.ts  small dense solver          env.ts  Env + shared context
migrations/       D1 schema
test/             vitest in workerd (@cloudflare/vitest-plugin)
```

## Run

```sh
cd cloud
npm install
cp .dev.vars.example .dev.vars          # LABRAT_TOKEN, optional ANTHROPIC_API_KEY
npx wrangler d1 migrations apply labrat --local   # schema changed? rm -rf .wrangler/state/v3/d1 first
npx wrangler dev                        # http://localhost:8787/  (console), /phone/
npm test                                # vitest in the Workers runtime
npm run typecheck
```

Clients: the console at `http://localhost:8787/?token=labrat`, the phone at `/phone/?token=labrat`, the bridge with
`--server ws://localhost:8787`, the CLI with `--server http://localhost:8787`.

Configuration (`wrangler.jsonc` vars, `.dev.vars`, or `wrangler secret put` when deployed):

| name | default | |
|---|---|---|
| `LABRAT_TOKEN` | `labrat` | required on every socket and `/api` call: `?token=`, `Authorization: Bearer`, or `x-labrat-token` |
| `ANTHROPIC_API_KEY` | unset | unset: rule-based agent |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | |
| `LABRAT_AUTO_APPROVE` | `0` | start runs without human approval |
| `LABRAT_AUTO_OBSERVE` | `1` | after a run with a photo step, measure every beaker from the photo |
| `LABRAT_PHOTO_WAIT_S` | `3` | how long the photo step waits for a fresh phone frame |

Before a deploy: `wrangler d1 create labrat`, put its id in `wrangler.jsonc`, apply the migrations with `--remote`, and
set `LABRAT_TOKEN` / `ANTHROPIC_API_KEY` as secrets.

## Routes

Static: `/` and `/console` redirect to `/console/` (`apps/console`); `/phone/` serves `apps/phone`
(`apps/.assetsignore` keeps `cli/` and Python files out).

WebSockets (protocol.md §1-3): `/ws/ui`, `/ws/phone`, `/ws/bridge`. Unauthenticated sockets are closed with 4401.

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | liveness (no token): boot error, providers, LLM on, D1 ok |
| GET | `/api/state` | the `devices` message + UI count, LLM flag, has_frame, active experiments |
| | `/api/experiments*`, `/api/results.csv`, `/api/dry_run`, `/api/notes`, `/api/model`, `/api/chat` | experiments, notebook, model: [`docs/experiments.md`](../docs/experiments.md#rest) |
| POST | `/api/abort` | flag the running experiment, fail in-flight commands, stop cnc / arms / every servo |
| GET | `/api/events?kind=&limit=` | audit log |
| GET / POST | `/api/deck_cal` | deck calibration summary / patch (pipette offset, tools, points; `null` forgets) |
| POST | `/api/deck_cal/teach` `{target, pos?}` | teach a target from the current CNC position (or `pos`) |
| POST | `/api/jog` `{device:"cnc", dx, dy, dz, f}` | relative CNC jog (≤ 50 mm per axis) |
| POST | `/api/device` `{device, action, args, timeout?}` | one action on any device (provider or logical); hub safety rules apply |
| GET / POST | `/api/cal/{pipette\|probe\|stand}` | logical-device calibration (`{reset:true}` restores defaults) |
| GET / POST | `/api/camera_cal` `{corners, image_w, image_h}` | camera → deck homography (beakers A1, A7, F7, F1) |
| GET | `/api/camera_frame` | latest phone frame |

Errors are `{"error": "..."}` with 400 / 401 / 404 / 409 / 500.

## Data

D1 (`migrations/0001_init.sql`):

| table | contents |
|---|---|
| `experiments` | title, status, and the full JSON document (samples, steps, dry-run check and predictions, run log; never frames) |
| `results` | one beaker reading (measured RGB, rating, note, source `operator` / `camera`, what went in, prediction at the time) |
| `notes` + `notes_fts` | lab notebook; FTS5 (porter), bm25 search |
| `model` | colour-model versions; the newest accepted row is installed at DO boot |
| `events` | audit log (`chat`, `tool`, `experiment_*`, `measure`, `model_refit`, `deck_teach`, `provider_hello`, ...), trimmed to 5000 rows at DO boot |
| `config` | `deck_cal`, `camera_cal`, `cal:pipette`, `cal:probe`, `cal:stand` |

Replay frames are not stored: the dry run is deterministic, so `/api/experiments/{id}/replay` re-runs it. DO storage holds the agent's chat history and the logical devices'
last state (dead-reckoned plunger position, probe and stand angles). Providers survive hibernation through their socket
attachments (their `hello`); device state comes back with the next `state` message.

## Logical devices

`pipette`, `probe` and `stand` appear in the `devices` broadcast next to provider devices, with status taken from the
provider's `hub`. Their calibration defaults live in `src/devices/*.ts` (`PIPETTE_DEFAULTS`, `PROBE_DEFAULTS`,
`STAND_DEFAULTS`) and are overridden by the D1 rows. Rules the hub enforces:

- `cnc.probe` is refused unless `probe.deployed`; the probe arm is retracted after every probe cycle.
- Before any CNC move below safe Z (`move` with `z < 0`, `jog` with `dz < 0`, any `gcode`) the arm is retracted unless
  it is known to be retracted.
- An experiment's photo step parks the head, `stand.look {at: "deck"}` (when a hub is connected), sends `{"t":"snap"}` to the
  phones and waits for a frame newer than that.
- A hub `estop` event aborts everything; `wd` / `boot` events reset the drivers' channel config.

`cal:stand` (deck coordinates in mm, z up from the bed; the stand sits front-centre of the CNC):

```json
{ "pan_ch": 2, "tilt_ch": 3, "pan_center_deg": 84.4, "tilt_center_deg": 63.9, "pan_dir": -1, "tilt_dir": 1,
  "pan_limits": [-90.6, 79.4], "tilt_limits": [-28.9, 106.1],
  "pivot": [157.5, -131, 369.3], "deck_center": [150, 90, 0], "operator": [150, -560, 628],
  "poses": { "home": { "pan": 0, "tilt": 0 } }, "rim_h": 51.2, "ms": 600, "settle_ms": 300 }
```

`aim {pan, tilt}` is relative to the reference view (0, 0) = camera on `deck_center`; servo angle =
`center + dir × angle`, clamped to the limits. `look` computes azimuth / elevation differences from `pivot`;
`look operator` turns the phone's screen (its face) toward `operator`, so the camera points away from it.
