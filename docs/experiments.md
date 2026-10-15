# Running experiments

One idea: **an experiment is one plate.** You say what goes into each beaker, LabRat dry-runs it on the digital twin,
you approve, the robot fills the plate, the phone photographs it, and every beaker gets a measured colour next to its
predicted one. To do it again, **Repeat** it (a new experiment, so nothing you recorded is overwritten).

```
 samples ──► dry run ──► run (you approve) ──► photo ──► results ──► CSV
 (µL per      checks,                          every     measured vs
  beaker)     time, predicted colours          beaker    predicted; the colour model refits
```

## Samples

A sample is one beaker: the µL of each stock that goes into it. The stocks are `water`, `red`, `yellow` and `blue`
(column 8 of the beaker rack, 4.5 mL each).

```json
[{"red": 60, "yellow": 140}, {"red": 100, "yellow": 100}, {"red": 100, "yellow": 100, "label": "replicate"}]
```

- `well` is optional (A1–F7); samples without one fill A1, B1 … F1, A2 … in order.
- `label` is optional free text.
- Replicates are just repeated samples.

LabRat turns samples into robot steps: one tip per stock, dispensed into each beaker from above, then a mix with a
fresh tip for any beaker full enough for the tip to reach (about 1.2 mL), then a photo. Advanced: give `steps`
instead of `samples` to write the protocol yourself (ops `pick_tip`, `aspirate`, `dispense`, `mix`, `eject_tip`,
`photo`, `wait`; see `cloud/src/sim.ts`).

An optional `target` colour (`"orange"`, `"#ff8800"` or `[255, 136, 0]`) adds a distance-to-target column and marks the
closest beaker.

## The dry run

Every time an experiment is created or edited it is dry-run with the current colour model. It assumes a clean deck:
empty beakers, full stocks, a full tip rack.

- **draft**: the dry run found a problem (out of reach, overflow, too little stock, a reused tip …). Fix and save.
- **ready**: passed; it can be run.

The prediction is saved with the experiment, so after the run you compare against what was predicted *at the time*.

## Statuses

`draft` → `ready` → `awaiting_approval` → `running` → `done` | `failed` | `aborted`

Only `draft` and `ready` can be edited (notes can always be edited). One experiment runs at a time. Abort (button,
"stop", or the e-stop) stops everything.

## Results

After a run with a photo step the camera reads every beaker (camera calibration on the Deck tab, or Claude vision
without one). You can also enter or correct readings by hand: an RGB colour, a 1–5 rating, a note. The newest reading
of a beaker is its result. New readings refit the colour model; a refit is kept only if it predicts all readings so far
better (Model tab). An auto-note with the results goes into the notebook, where the agent finds it next time.

Download one experiment's table (`/api/experiments/{id}.csv`) or every measured beaker ever (`/api/results.csv`).

## Three ways in

**Console** (`/console/`): New experiment → edit the sample table → Save (dry run) → Run → Approve → results.

**Agent**: "find the most orange mix of red and yellow". It writes the samples, dry-runs them and asks you to approve.

**Python** (`apps/cli`, `uv run python`):

```python
from labrat_cli import Lab

lab = Lab("http://127.0.0.1:8787", token="labrat")
e = lab.new("Orange sweep", target="orange",
            samples=[{"red": r, "yellow": 200 - r} for r in range(20, 200, 20)])
print(e["status"], e["check"]["errors"])
e = lab.run(e["id"], approve=True, wait=True)     # blocks until done
for b in lab.get(e["id"])["beakers"]:
    print(b["well"], b["red"], b["yellow"], b["predicted_rgb"], b["measured_rgb"])
```

See [`experiments/`](../experiments) for scripts.

## REST

Token on every call (`?token=`, `Authorization: Bearer`, or `x-labrat-token`). Bodies are JSON; errors are
`{"error": "..."}` with 400 / 404.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/experiments` | | newest 100 experiments |
| POST | `/api/experiments` | `{title, target?, samples \| steps, notes?}` | the experiment, dry-run (`draft` or `ready`) |
| GET | `/api/experiments/{id}` | | the experiment |
| PATCH | `/api/experiments/{id}` | any of `{title, target, samples, steps, notes}` | the experiment, dry-run again |
| POST | `/api/experiments/{id}/repeat` | | a new copy |
| POST | `/api/experiments/{id}/run` | `{auto?}` | `awaiting_approval` (or `running` with `auto`) |
| POST | `/api/experiments/{id}/approve` | | `running` |
| POST | `/api/experiments/{id}/results` | `{well, rgb?, rating?, note?}` or `{results: [...]}` | `{results, model}` |
| POST | `/api/experiments/{id}/measure` | | read every beaker from the latest phone frame |
| GET | `/api/experiments/{id}/replay` | | `{frames}` for the 3D twin |
| GET | `/api/experiments/{id}.csv` | | one row per beaker |
| GET | `/api/results.csv` | | every measured beaker of every experiment |
| POST | `/api/dry_run` | `{samples \| steps, frames?}` | the dry run, nothing saved |
| GET / POST | `/api/notes` | `?q=&experiment=` / `{text, experiment_id?}` | notebook |
| GET | `/api/model` | | `{current_v, params, versions}` |
| POST | `/api/abort` | | stops everything |

An experiment as returned:

```jsonc
{
  "id": "exp_3f9a1c", "title": "Orange sweep", "status": "done", "source": "operator",
  "target": {"name": "orange", "rgb": [255, 140, 0]},          // or null
  "samples": [{"well": "A1", "red": 20, "yellow": 180}, ...],
  "steps": [{"op": "pick_tip", "rack_well": "A1"}, ...],
  "check": {"ok": true, "errors": [], "warnings": [], "duration_s": 412.3, "tips_used": ["A1", "B1"], "model_v": 3},
  "beakers": [{"well": "A1", "label": null, "water": 0, "red": 20, "yellow": 180, "blue": 0, "vol_ul": 200,
               "predicted_rgb": [240, 205, 4], "measured_rgb": [236, 199, 30], "error": 26.8,
               "predicted_to_target": 66.3, "measured_to_target": 70.1, "rating": null, "note": null, "source": "camera"}],
  "closest_predicted": "E1", "closest_measured": "D1",           // only with a target
  "notes": "", "log": [{"i": 0, "op": "pick_tip", "msg": "picked tip A1", "t": 3.1}], "step": 41, "error": null,
  "created": 1760500000.1, "started": 1760500100.0, "finished": 1760500512.4
}
```

WebSocket `/ws/ui` pushes `{"t":"experiment","experiment":{...}}` on every change (and the newest 20 on connect),
`{"t":"model",...}` after a refit and `{"t":"note","note":{...}}`; send `{"t":"approve","id"?}` or `{"t":"abort"}`.
