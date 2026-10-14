"""Recursive self-improvement (RSI) loop.

After each real run, the operator (UI rating / RGB sample from the phone photo) gives an observation. We:
 1. compare predicted vs observed well colour,
 2. fit simulator parameters (dye absorbance coefficients via ridge-regularised least squares in absorbance space;
    pipette volume gain from observed vs commanded volume when provided),
 3. store a versioned `sim_params` doc (Mongo) and install it in the digital twin only if error improves,
 4. re-score all past variants with the new params,
 5. promote the run's protocol to *procedural* memory only if its re-simulated prediction error improved,
 6. write *semantic* facts and *episodic* memories, and emit a Raindrop `rsi.update` event with before/after metrics.
"""
from __future__ import annotations

import time

import numpy as np

from . import sim
from .config import log
from .store import store
from .telemetry import track

LAMBDA = 0.3  # ridge strength pulling toward previous params
BG = 245.0


def _versions() -> list[dict]:
    return sorted(store.mem["sim_params"].values(), key=lambda d: d["v"])


def current_version() -> dict:
    vs = [v for v in _versions() if v.get("accepted", True)]
    return vs[-1] if vs else ensure_baseline()


def ensure_baseline() -> dict:
    if not store.mem["sim_params"]:
        return store.put("sim_params", {"id": "simparams_v0", "v": 0, "created_at": time.time(), "params": sim.get_params(),
                                        "error_before": None, "error_after": None, "n_runs": 0, "accepted": True, "note": "factory defaults"})
    return _versions()[0]


def apply_latest() -> None:
    try:
        sim.set_params(current_version()["params"])
    except Exception as e:  # noqa: BLE001
        log.warning(f"apply sim_params failed: {e}")


def _pred_rgb(comp: dict[str, float]) -> np.ndarray:
    return np.array(sim.mix_rgb(comp), float)


def _err(obs: list[dict]) -> float | None:
    pts = [o for o in obs if o.get("rgb")]
    if not pts:
        return None
    return float(np.mean([np.linalg.norm(_pred_rgb(o["comp"]) - np.array(o["rgb"], float)) for o in pts]))


def _fit(obs: list[dict], p0: dict) -> dict:
    pts = [o for o in obs if o.get("rgb") and o.get("comp")]
    names = sim.DYE_NAMES
    absorb = {d: np.array(p0["absorbance"][d], float) for d in names}
    if pts:
        C = np.array([[o["comp"].get(d, 0.0) for d in names] for o in pts], float)
        C = C / np.maximum(C.sum(1, keepdims=True), 1e-9)
        T = np.clip(np.array([o["rgb"] for o in pts], float) / BG, 1e-3, 1.0)
        A = -np.log10(T)  # (n,3)
        X0 = np.stack([absorb[d] for d in names])  # (4,3)
        lhs = C.T @ C + LAMBDA * np.eye(len(names))
        rhs = C.T @ A + LAMBDA * X0
        X = np.linalg.solve(lhs, rhs)
        X[names.index("water")] = 0.0
        X = np.clip(X, 0.0, 4.0)
        absorb = {d: X[i] for i, d in enumerate(names)}
    gains = [o["vol_ul"] / o["cmd_vol_ul"] for o in obs if o.get("vol_ul") and o.get("cmd_vol_ul")]
    gain = float(np.clip(np.mean(gains), 0.5, 1.5)) if gains else p0.get("vol_gain", 1.0)
    return {"absorbance": {d: [round(float(x), 4) for x in absorb[d]] for d in names}, "vol_gain": round(gain, 4)}


def _describe(pred, obs) -> str:
    d = np.array(obs, float) - np.array(pred, float)
    words = []
    for i, ch in enumerate(("red", "green", "blue")):
        if abs(d[i]) > 20:
            words.append(f"{'more' if d[i] > 0 else 'less'} {ch}")
    return ", ".join(words) or "close to prediction"


def _rescore_all() -> int:
    n = 0
    for exp in store.mem["experiments"].values():
        changed = False
        for v in exp.get("variants", []):
            if not v.get("sim"):
                continue
            r = sim.simulate(v["protocol"], exp["goal"], record_frames=False)
            r.pop("frames", None)
            v["sim"] = r
            n += 1
            changed = True
        if changed:
            ranked = sorted([v for v in exp["variants"] if v.get("sim", {}).get("ok")], key=lambda v: v["sim"]["score"], reverse=True)
            exp["best_variant_id"] = ranked[0]["id"] if ranked else None
            exp["rescored_with"] = current_version()["v"]
            store.put("experiments", exp)
    return n


async def observe(run_id: str, rating: int | None = None, rgb: list[int] | None = None, well: str | None = None,
                  vol_ul: float | None = None, source: str = "operator") -> dict:
    from . import lab

    run = store.get("runs", run_id)
    if not run:
        raise ValueError(f"no run {run_id}")
    exp = store.get("experiments", run["experiment_id"])
    v = lab.variant(exp, run["variant_id"])
    ensure_baseline()
    base = sim.simulate(v["protocol"], exp["goal"], record_frames=False)
    well = (well or base["best_well"] or "A1").upper()
    w = base["wells"].get(well) or {}
    comp = w.get("comp", {})
    cmd_vol = float(sum(s.get("ul", 0) for s in v["protocol"] if s.get("op") == "dispense" and s.get("dst", "").upper() == well))
    ob = store.put("observations", {"run_id": run_id, "experiment_id": exp["id"], "variant_id": v["id"], "well": well,
                                    "rating": rating, "rgb": [int(x) for x in rgb] if rgb else None, "comp": comp,
                                    "predicted_rgb": w.get("rgb"), "vol_ul": vol_ul, "cmd_vol_ul": cmd_vol, "source": source})
    run["observation_rating"], run["observed_rgb"] = rating, ob["rgb"]
    await lab.emit_run(run)

    obs = list(store.mem["observations"].values())
    prev = current_version()
    single_before = (float(np.linalg.norm(_pred_rgb(comp) - np.array(rgb, float))) if rgb else None)
    err_before = _err(obs)
    new_params = _fit(obs, prev["params"])
    sim.set_params(new_params)
    err_after = _err(obs)
    single_after = (float(np.linalg.norm(_pred_rgb(comp) - np.array(rgb, float))) if rgb else None)
    accepted = err_before is not None and err_after is not None and err_after < err_before - 1e-6
    if not accepted:
        sim.set_params(prev["params"])
    ver = store.put("sim_params", {
        "id": f"simparams_v{len(store.mem['sim_params'])}", "v": len(store.mem["sim_params"]), "created_at": time.time(),
        "params": new_params if accepted else prev["params"], "proposed_params": new_params,
        "error_before": None if err_before is None else round(err_before, 2), "error_after": None if err_after is None else round(err_after, 2),
        "n_runs": len({o["run_id"] for o in obs}), "accepted": accepted, "trigger_run_id": run_id, "parent_v": prev["v"],
    })
    rescored = _rescore_all() if accepted else 0

    # memories
    if rgb:
        fact = (f"{'+'.join(v['params']['dyes'])} at ratios {v['params']['ratios']} ({cmd_vol:.0f} µL) looked rgb{tuple(ob['rgb'])}; "
                f"predicted rgb{tuple(w.get('rgb') or [])}: real result was {_describe(w.get('rgb') or [0, 0, 0], rgb)}.")
        await store.remember(fact, tags=["observation", exp["id"]], type="semantic", source_run_id=run_id, confidence=0.8)
    promoted = False
    if accepted and single_before is not None and single_after is not None and single_after < single_before and (rating or 3) >= 3:
        steps = "; ".join(f"{s['op']} {', '.join(f'{k}={val}' for k, val in s.items() if k != 'op')}" for s in v["protocol"])
        await store.remember(f"PROCEDURE for '{exp['goal']}': {v['params']} → {steps}", tags=["procedure", exp["id"]],
                             meta={"protocol": v["protocol"], "params": v["params"]}, type="procedural", source_run_id=run_id,
                             confidence=round(max(0.1, 1 - single_after / 441.7), 3))
        promoted = True
    metrics = {"v": ver["v"], "accepted": accepted, "error_before": ver["error_before"], "error_after": ver["error_after"],
               "run_error_before": single_before, "run_error_after": single_after, "rescored_variants": rescored,
               "promoted_procedure": promoted, "rating": rating, "source": source}
    store.event("rsi.update", **metrics)
    track("rsi.update", input=f"run {run_id} rating={rating} rgb={rgb}", output=str(metrics), props=metrics)
    return {"observation": ob, "version": ver, **metrics}


def learning() -> dict:
    ensure_baseline()
    return {"versions": [{k: d.get(k) for k in ("v", "created_at", "params", "error_before", "error_after", "n_runs", "accepted", "trigger_run_id")}
                         for d in _versions()], "current_v": current_version()["v"]}


TAXONOMY = [
    "Lab devices: arms → SO-101 gripper arms (so_a, so_b, one each side of the CNC); motion stage → 3018 CNC (cnc, GRBL); liquid handler → syringe pipette (pipette, in the CNC spindle clamp on the spindle axis, 200 µL tips on a luer-to-tip adapter, 12:1 gear train + rack on a continuous-rotation FS90R servo, ESP32/PCA9685 Tappet board tap_a ch 8); camera → iPhone on a static stand above the CNC bed.",
    "Reagents: dyes → red, yellow, blue (subtractive primaries); diluent → water. Stock beakers in column 8 of the beaker matrix: A8 water, B8 red, C8 yellow, D8 blue (4.5 mL each).",
    "Labware: FT-200L-RS tip_rack (8x12, 200 µL tips) at (17,19.25); printed beaker matrix (6x8 beakers of 15.3 x 50 mm on 19 mm, A1 at (153.25,32)), columns 1-7 = mixing beakers A1-F7 (5 mL), column 8 = stocks; waste (280,170); tip eject bar (10,170).",
    "Reagents on hand (not simulated): white vinegar, baking soda, borax, ammonium chloride, pink Himalayan salt, PLA crumbs, food crumbs, the operator's own saliva / mucus / cheek-cell scrapings.",
    "Colours: secondary colours → orange (red+yellow), green (blue+yellow), purple (red+blue); tertiary → brown (all three).",
]


async def seed_taxonomy() -> None:
    have = {m["text"] for m in store.mem["memories"].values() if m.get("type") == "taxonomic"}
    for t in TAXONOMY:
        if t not in have:
            await store.remember(t, tags=["taxonomy"], type="taxonomic", confidence=1.0)
