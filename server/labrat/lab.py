"""Experiments, simulation orchestration and real-run execution on hardware via the bridge."""
import asyncio
import time
import uuid

from . import sim
from .config import AUTO_APPROVE, env, log
from .hub import BridgeError, hub
from .store import store
from .telemetry import track

F_XY, F_Z = 3000, 600


def flat_variant(var: dict) -> dict:
    """Variant for UI/REST: no frames; sim results flattened to score/predicted_rgb/checks/pass/time_s."""
    v = {k: x for k, x in var.items() if k != "frames"}
    s = var.get("sim")
    if s:
        v.update({"score": s.get("score"), "predicted_rgb": s.get("predicted_rgb"), "pass": bool(s.get("ok")),
                  "time_s": s.get("duration_s"), "checks": sim_checks(s), "has_frames": bool(var.get("frames")) or bool(var.get("protocol"))})
    return v


def sim_checks(s: dict) -> list[dict]:
    errs, warns = s.get("errors") or [], s.get("warnings") or []
    out = [{"name": "safety", "ok": not errs, "msg": "; ".join(map(str, errs[:3]))},
           {"name": "clean", "ok": not warns, "msg": "; ".join(map(str, warns[:3]))}]
    if s.get("target"):
        out.append({"name": "colour", "ok": (s.get("score") or 0) >= 0.8, "msg": f"target {s['target'].get('name')} score {s.get('score')}"})
    return out


def _strip(exp: dict) -> dict:
    """Experiment for UI/REST (no frames)."""
    e = {k: v for k, v in exp.items() if k != "variants"}
    e["variants"] = [flat_variant(var) for var in exp.get("variants") or []]
    return e


def ensure_frames(exp: dict, var: dict) -> list:
    """Frames aren't persisted to Mongo; re-run the (deterministic) sim from the protocol when missing."""
    if not var.get("frames") and var.get("protocol"):
        r = sim.simulate(var["protocol"], exp.get("goal", ""))
        var["frames"] = r.pop("frames")
        var.setdefault("sim", r)
    return var.get("frames") or []


async def emit_experiment(exp: dict) -> None:
    await hub.ui({"t": "experiment", "experiment": _strip(exp)})


async def create_experiment(goal: str, n_variants: int = 12, variants: list[dict] | None = None, source: str = "deterministic") -> dict:
    n_variants = max(1, min(int(n_variants or 12), sim.PLATE["rows"] * sim.PLATE["cols"]))
    vs = variants or sim.generate_variants(goal, n_variants, seed=int(time.time()) % 10000)
    exp = store.put("experiments", {
        "id": "exp_" + uuid.uuid4().hex[:6], "goal": goal, "status": "designed", "source": source,
        "variants": vs, "best_variant_id": None, "selected_variant_id": None, "created": time.time(),
    })
    for v in vs:
        store.put("variants", {"id": f"{exp['id']}:{v['id']}", "experiment_id": exp["id"], **{k: v[k] for k in ("params", "protocol") if k in v}})
    store.event("experiment_created", experiment_id=exp["id"], goal=goal, n=len(vs))
    track("design_experiment", input=goal, output=f"{len(vs)} variants ({source})", props={"experiment_id": exp["id"]})
    await emit_experiment(exp)
    return exp


async def simulate_experiment(exp_id: str, n_variants: int | None = None) -> dict:
    exp = store.get("experiments", exp_id)
    if not exp:
        raise ValueError(f"no experiment {exp_id}")
    if n_variants and n_variants > len(exp["variants"]):
        extra = sim.generate_variants(exp["goal"], n_variants, seed=len(exp["variants"]) + 7)[len(exp["variants"]):]
        exp["variants"].extend(extra)
    t0 = time.perf_counter()
    exp.setdefault("variants", [])
    for v in exp["variants"]:
        r = sim.simulate(v["protocol"], exp["goal"])
        v["frames"] = r.pop("frames")
        v["sim"] = r
        store.put("variants", {"id": f"{exp_id}:{v['id']}", "experiment_id": exp_id, "params": v.get("params"), "protocol": v["protocol"], "sim": r})
    dt = time.perf_counter() - t0
    passing = [v for v in exp["variants"] if v["sim"]["ok"]]
    ranked = sorted(passing, key=lambda v: v["sim"]["score"], reverse=True)
    exp["best_variant_id"] = ranked[0]["id"] if ranked else None
    exp["status"] = "simulated"
    exp["sim_summary"] = {"n": len(exp["variants"]), "passing": len(passing), "sim_ms": round(dt * 1000, 1),
                          "best_score": ranked[0]["sim"]["score"] if ranked else None}
    store.put("experiments", exp)
    store.event("simulated", experiment_id=exp_id, **exp["sim_summary"])
    track("simulate", input=exp["goal"], output=str(exp["sim_summary"]), props={"experiment_id": exp_id})
    await emit_experiment(exp)
    if ranked:
        b = ranked[0]
        _spawn(store.remember(
            f"Simulated {len(exp['variants'])} variants for {exp['goal']!r}: best {b['id']} {b.get('params')} predicted rgb{tuple(b['sim']['predicted_rgb'] or [])} score {b['sim']['score']}.",
            tags=["sim", exp_id], type="episodic", confidence=0.5, meta={"experiment_id": exp_id}))
    for v in sorted(exp["variants"], key=lambda v: v["id"] != exp["best_variant_id"]):
        await hub.ui({"t": "sim", "experiment_id": exp_id, "variant_id": v["id"], "score": v["sim"]["score"],
                      "ok": v["sim"]["ok"], "predicted_rgb": v["sim"]["predicted_rgb"], "variant": flat_variant(v), "frames": v["frames"]})
    return exp


def _spawn(coro) -> None:
    async def wrap():
        try:
            await coro
        except Exception as e:  # noqa: BLE001
            log.warning(f"background: {e}")
    asyncio.get_running_loop().create_task(wrap())


def variant(exp: dict, vid: str) -> dict:
    for v in exp["variants"]:
        if v["id"] == vid:
            return v
    raise ValueError(f"no variant {vid} in {exp['id']}")


async def select_variant(exp_id: str, vid: str, reason: str = "") -> dict:
    exp = store.get("experiments", exp_id)
    if not exp:
        raise ValueError(f"no experiment {exp_id}")
    variant(exp, vid)
    exp["selected_variant_id"] = vid
    exp["selection_reason"] = reason
    store.put("experiments", exp)
    await emit_experiment(exp)
    return exp


# ---------------- runs ----------------
_tasks: dict[str, asyncio.Task] = {}


async def emit_run(run: dict) -> None:
    store.put("runs", run)
    await hub.ui({"t": "run", "run": run})


async def request_run(exp_id: str, vid: str | None = None, auto: bool = False) -> dict:
    exp = store.get("experiments", exp_id)
    if not exp:
        raise ValueError(f"no experiment {exp_id}")
    vid = vid or exp.get("selected_variant_id") or exp.get("best_variant_id")
    if not vid:
        raise ValueError("no variant selected and no simulated best variant; simulate first")
    v = variant(exp, vid)
    if not v.get("sim"):
        raise ValueError(f"variant {vid} has not been simulated")
    if not v["sim"]["ok"]:
        raise ValueError(f"variant {vid} failed simulation checks: {v['sim']['errors'][:3]}")
    run = {"id": "run_" + uuid.uuid4().hex[:6], "experiment_id": exp_id, "variant_id": vid, "status": "awaiting_approval",
           "step": 0, "n_steps": len(v["protocol"]), "log": [], "created": time.time(), "predicted_rgb": v["sim"]["predicted_rgb"]}
    await emit_run(run)
    store.event("run_requested", run_id=run["id"], experiment_id=exp_id, variant_id=vid)
    if auto or AUTO_APPROVE:
        await approve(run["id"])
    return run


def pending_run() -> dict | None:
    for r in store.list("runs", 20):
        if r["status"] == "awaiting_approval":
            return r
    return None


async def approve(run_id: str) -> dict:
    run = store.get("runs", run_id)
    if not run:
        raise ValueError(f"no run {run_id}")
    if run["status"] != "awaiting_approval":
        return run
    run["status"] = "running"
    run["started"] = time.time()
    await emit_run(run)
    _tasks[run_id] = asyncio.create_task(_execute(run))
    return run


async def abort() -> None:
    for rid, t in list(_tasks.items()):
        if not t.done():
            t.cancel()
    for dev, act in (("cnc", "stop"), ("so_a", "stop"), ("so_b", "stop")):
        if dev in hub.devices or dev == "cnc":
            try:
                await hub.cmd(dev, act, {}, timeout=5)
            except Exception as e:  # noqa: BLE001
                log.warning(f"abort: {dev}.{act} failed: {e}")
    store.event("abort")



_pos = {"x": 0.0, "y": 0.0, "z": 0.0}


async def _mv(x=None, y=None, z=None, f=F_XY):
    """Absolute CNC move always sending full x,y,z (tracked position fills gaps)."""
    if x is not None:
        _pos["x"] = round(float(x), 2)
    if y is not None:
        _pos["y"] = round(float(y), 2)
    if z is not None:
        _pos["z"] = round(float(z), 2)
    await hub.cmd("cnc", "move", {**_pos, "f": f}, timeout=60)


async def _goto(x, y, z):
    await _mv(z=sim.Z_SAFE, f=F_Z)
    await _mv(x=x, y=y, f=F_XY)
    await _mv(z=z, f=F_Z)


async def exec_step(s: dict, run: dict | None = None) -> str:
    op = s["op"]
    if op == "pick_tip":
        x, y, zt = sim.well_xyz(s["rack_well"], sim.TIP_RACK, sim.Z_TIP)  # calibrated deck (spindle coords)
        await _goto(x, y, zt)
        await _mv(z=sim.Z_SAFE, f=F_Z)
        return f"picked tip {s['rack_well']}"
    if op == "aspirate":
        _, x, y, z = sim.loc(s["src"])
        await _goto(x, y, sim.Z_SAFE)  # above the liquid
        await hub.cmd("pipette", "prime", {}, timeout=15)  # plunger to its air-cushion ready height while above liquid
        await _mv(z=z, f=F_Z)
        await hub.cmd("pipette", "aspirate", {"ul": s["ul"]}, timeout=20)
        await _mv(z=sim.Z_SAFE, f=F_Z)
        return f"aspirated {s['ul']} µL from {s['src']}"
    if op == "dispense":
        _, x, y, z = sim.loc(s["dst"])
        await _goto(x, y, z)
        await hub.cmd("pipette", "dispense", {"ul": s.get("ul")}, timeout=20)
        await hub.cmd("pipette", "blowout", {}, timeout=10)
        await _mv(z=sim.Z_SAFE, f=F_Z)
        return f"dispensed {s.get('ul')} µL into {s['dst']}"
    if op == "mix":
        _, x, y, z = sim.loc(s["well"])
        ul = min(float(s.get("ul", 50)), 190.0)
        await _goto(x, y, sim.Z_SAFE)
        await hub.cmd("pipette", "prime", {}, timeout=15)
        await _mv(z=z, f=F_Z)
        for _ in range(int(s.get("n", 3))):
            await hub.cmd("pipette", "aspirate", {"ul": ul}, timeout=20)
            await hub.cmd("pipette", "dispense", {"ul": ul}, timeout=20)
        await hub.cmd("pipette", "blowout", {}, timeout=10)
        await _mv(z=sim.Z_SAFE, f=F_Z)
        return f"mixed {s['well']}"
    if op == "eject_tip":
        ex, ey, ez = sim.eject_xyz()
        await _goto(ex, ey, ez)
        await _mv(y=ey + 8, f=600)
        await _mv(z=sim.Z_SAFE, f=F_Z)
        await hub.cmd("pipette", "rest", {}, timeout=10)
        return "ejected tip"
    if op == "photo":
        # The iPhone is on a static stand above the bed: no arm moves. Park the pipette out of view, then require a
        # phone frame captured after the park finished (LABRAT_PHOTO_POSE re-enables an arm pose for arm-mounted phones).
        from . import vision

        await _mv(z=sim.Z_SAFE, f=F_Z)
        await _mv(x=290, y=175, f=F_XY)  # park out of camera view
        await vision.photo_pose()
        t_ready = time.time()
        frame = await hub.wait_frame(t_ready, float(env("LABRAT_PHOTO_WAIT_S", "3") or 3))
        if run is not None:
            run["photo_after"] = t_ready
            if frame:
                run["photo_ts"] = frame.get("ts")
        return "photo taken" if frame else "photo requested (no fresh phone frame)"
    if op == "wait":
        await asyncio.sleep(float(s.get("s", 1)))
        return f"waited {s.get('s', 1)} s"
    raise ValueError(f"unknown op {op}")


async def _execute(run: dict) -> None:
    exp = store.get("experiments", run["experiment_id"])
    v = variant(exp, run["variant_id"])
    try:
        for i, s in enumerate(v["protocol"]):
            run["step"] = i
            msg = await exec_step(s, run)
            run["log"].append({"i": i, "op": s["op"], "msg": msg, "t": round(time.time() - run["started"], 1)})
            await emit_run(run)
        run["status"] = "done"
        if hub.latest_frame and "photo_ts" not in run:
            run["photo_ts"] = hub.latest_frame.get("ts")
        if any(st.get("op") == "photo" for st in v["protocol"]) and env("LABRAT_AUTO_OBSERVE", "1") not in ("0", "false", "no"):
            from . import vision  # auto observation: photo -> well RGB -> learn.observe(source="vision")

            # only a frame captured after the photo step's park counts (never a stale pre-run frame)
            _spawn(vision.auto_observe(run["id"], pose=False, settle_s=0.0, fresh_after=run.get("photo_after")))
        obs = (f"Ran {exp['goal']!r} variant {v['id']} params={v.get('params')} predicted_rgb={v['sim']['predicted_rgb']} "
               f"score={v['sim']['score']} in {round(time.time() - run['started'])} s on real hardware.")
        run["observation"] = obs
        await store.remember(obs, tags=["run", exp["id"]], meta={"run_id": run["id"]}, type="episodic",
                             source_run_id=run["id"], confidence=0.9)
    except asyncio.CancelledError:
        run["status"] = "aborted"
    except (BridgeError, ValueError) as e:
        run["status"] = "failed"
        run["error"] = str(e)
    finally:
        run["finished"] = time.time()
        await emit_run(run)
        store.event("run_" + run["status"], run_id=run["id"], error=run.get("error"))
        track("run", input=f"{run['experiment_id']}:{run['variant_id']}", output=run["status"] + " " + str(run.get("error", "")),
              props={"run_id": run["id"]})
        _tasks.pop(run["id"], None)
