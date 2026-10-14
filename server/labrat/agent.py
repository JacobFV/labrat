"""LabRat agent: LLM tool-use loop (Anthropic default, Fireworks optional) + rule-based fallback."""
import asyncio
import json
import re
import time
from typing import Any

import httpx

from . import lab, sim
from .config import ANTHROPIC_MODEL, FIREWORKS_MODEL, LLM_PROVIDER, env, log, warn_once
from .hub import BridgeError, hub
from .store import store
from .status import mark
from .telemetry import track
from .voice import tts

SYSTEM = """You are LabRat, a friendly voice-operated desk-lab robot agent. Physical setup: a 3018 CNC (cnc) carries a syringe
pipette in place of its spindle (pipette: a disposable syringe with a 200 µL tip on a luer adapter, plunger driven by a
12:1 gear train and rack on a continuous-rotation servo, channel 8 of the ESP32/PCA9685 Tappet board tap_a; the tip is on the
spindle axis). Two SO-101 arms with stock
grippers, so_a and so_b, stand on either side of the CNC for pick-and-place (named poses: home, ready, rest). An iPhone on a
static stand looks straight down at the CNC bed; it is your eye, ear and mouth (look() returns its latest frame; it does not
move). The operator's laptop sits to the side. Deck (mm): tip_rack 8x12 at (20,20) pitch 9, 96-well plate at (140,20) pitch 9 (300 µL wells),
reservoirs water/red/blue/yellow at x=250 y=20/60/100/140, eject bar (10,170). Work is SIMULATION-FIRST: for any wet-lab goal,
recall relevant memories, design_experiment (many variants), simulate, select the best variant with a reason, then run_variant
(this requests human approval). Never move hardware into unsafe positions. Keep spoken replies short (1-2 sentences) since
they are read aloud on the phone. Use say() only for interim spoken updates; your final text reply is spoken automatically."""

TOOLS: list[dict] = [
    {"name": "list_devices", "description": "List connected hardware devices and their live state.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "look", "description": "Get the latest camera frame from the static overhead iPhone looking down at the CNC bed (no motion).", "input_schema": {"type": "object", "properties": {}}},
    {"name": "recall", "description": "Search long-term memory (past experiments, observations, notes).",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "design_experiment", "description": "Create an experiment for a goal with N protocol variants (e.g. dye ratios/volumes).",
     "input_schema": {"type": "object", "properties": {"goal": {"type": "string"}, "n_variants": {"type": "integer", "default": 12}}, "required": ["goal"]}},
    {"name": "simulate", "description": "Run the digital twin on all variants of an experiment; returns checks and scores.",
     "input_schema": {"type": "object", "properties": {"experiment_id": {"type": "string"}}, "required": ["experiment_id"]}},
    {"name": "select_variant", "description": "Select a variant to run, with a reason.",
     "input_schema": {"type": "object", "properties": {"experiment_id": {"type": "string"}, "variant_id": {"type": "string"}, "reason": {"type": "string"}},
                      "required": ["experiment_id", "variant_id"]}},
    {"name": "run_variant", "description": "Request a real hardware run of a simulated, passing variant (needs human approval).",
     "input_schema": {"type": "object", "properties": {"experiment_id": {"type": "string"}, "variant_id": {"type": "string"}}, "required": ["experiment_id"]}},
    {"name": "move_arm", "description": "Move an SO-101 gripper arm (so_a left of the CNC, so_b right) to a named pose (home, ready, rest) or joint angles (deg).",
     "input_schema": {"type": "object", "properties": {"device": {"type": "string"}, "pose": {"type": "string"}, "joints": {"type": "object"}, "ms": {"type": "integer"}},
                      "required": ["device"]}},
    {"name": "cnc_move", "description": "Move the CNC syringe head (absolute mm) or home it (home=true).",
     "input_schema": {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}, "f": {"type": "number"}, "home": {"type": "boolean"}}}},
    {"name": "pipette", "description": "Syringe pipette action: home (drive plunger to its bottom stop), prime (plunger to the ready air cushion; do it above the liquid), aspirate/dispense (ul), blowout, move (plunger height mm, 0..stroke).",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["home", "prime", "aspirate", "dispense", "blowout", "move"]}, "ul": {"type": "number"}, "mm": {"type": "number"}},
                      "required": ["action"]}},
    {"name": "say", "description": "Speak a short sentence through the phone.", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "remember", "description": "Store a note in long-term memory.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
                      "type": {"type": "string", "enum": ["semantic", "episodic", "procedural", "taxonomic"]}}, "required": ["text"]}},
]


def llm_available() -> bool:
    if LLM_PROVIDER == "fireworks":
        return bool(env("FIREWORKS_API_KEY"))
    return bool(env("ANTHROPIC_API_KEY"))


def _model() -> str:
    return FIREWORKS_MODEL if LLM_PROVIDER == "fireworks" else ANTHROPIC_MODEL


# ---------------- speech / UI helpers ----------------
async def face(mood: str) -> None:
    await hub.phone({"t": "face", "mood": mood, "gaze": [0, 0]})


async def speak(text: str) -> None:
    await hub.phone({"t": "face", "mood": "speaking", "gaze": [0, 0]})
    audio = await tts(text)
    await hub.phone({"t": "say", "text": text, "audio_b64": audio})


async def chat_ui(role: str, text: str, **meta: Any) -> None:
    await hub.ui({"t": "chat", "role": role, "text": text, "meta": meta})


# ---------------- tools ----------------
def _exp_summary(exp: dict, top: int = 5) -> dict:
    vs = [v for v in exp["variants"] if v.get("sim")]
    vs.sort(key=lambda v: v["sim"]["score"], reverse=True)
    return {
        "experiment_id": exp["id"], "goal": exp["goal"], "status": exp["status"], "n_variants": len(exp["variants"]),
        "sim_summary": exp.get("sim_summary"), "best_variant_id": exp.get("best_variant_id"),
        "top": [{"variant_id": v["id"], "params": v.get("params"), "score": v["sim"]["score"], "ok": v["sim"]["ok"],
                 "predicted_rgb": v["sim"]["predicted_rgb"], "duration_s": v["sim"]["duration_s"], "errors": v["sim"]["errors"][:2]} for v in vs[:top]],
    }


async def _llm_variants(goal: str, n: int) -> list[dict] | None:
    """Ask the LLM for variant parameters; build protocols deterministically. None on any failure."""
    if not llm_available():
        return None
    prompt = (f"Goal: {goal}\nReservoir dyes available: water, red, yellow, blue. Propose {n} diverse variants as JSON only: "
              '[{"dyes":["red","yellow"],"ratios":[0.2,0.8],"total_ul":200}] . total_ul between 60 and 280.')
    try:
        txt = await asyncio.wait_for(_complete_text(prompt), 25)
        m = re.search(r"\[.*\]", txt, re.S)
        items = json.loads(m.group(0)) if m else []
        out = []
        for i, it in enumerate(items[:n]):
            dyes = [d for d in it["dyes"] if d in sim.DYES]
            ratios = [float(r) for r in it["ratios"]][: len(dyes)]
            if not dyes or len(ratios) != len(dyes):
                continue
            total = max(60.0, min(280.0, float(it.get("total_ul", 200))))
            well = f"{sim.ROWS[i % 8]}{i // 8 + 1}"
            out.append({"id": f"v{i + 1:02d}", "params": {"dyes": dyes, "ratios": ratios, "total_ul": total, "well": well},
                        "protocol": sim.protocol_from_ratios(dyes, ratios, total, well)})
        return out or None
    except Exception as e:  # noqa: BLE001
        warn_once("llm_variants", f"LLM variant design failed ({e}); deterministic generator used")
        return None


async def run_tool(name: str, args: dict) -> Any:
    t0 = time.time()
    try:
        out = await _run_tool(name, args)
        ok = True
    except (BridgeError, ValueError, KeyError) as e:
        out, ok = {"error": str(e)}, False
    store.event("tool", name=name, args=args, ok=ok)
    short = json.dumps(out, default=str)[:600] if not (isinstance(out, dict) and out.get("_image")) else "<image>"
    track("tool_call", input=f"{name} {json.dumps(args, default=str)}", output=short, model=_model() if llm_available() else "rules",
          props={"tool": name, "ok": ok, "ms": int((time.time() - t0) * 1000)})
    await chat_ui("tool", f"{name}({json.dumps(args, default=str)[:200]}) → {short[:300]}", tool=name, ok=ok)
    return out


async def _run_tool(name: str, a: dict) -> Any:
    if name == "list_devices":
        return {"bridge": hub.bridge is not None, "devices": [{k: d.get(k) for k in ("id", "kind", "status", "state")} for d in hub.devices.values()]}
    if name == "look":
        f = hub.latest_frame
        if not f:
            return {"error": "no camera frame yet (phone not connected?)"}
        return {"_image": f["jpeg"], "w": f.get("w"), "h": f.get("h"), "age_s": round(time.time() - f["ts"], 1)}
    if name == "recall":
        return {"memories": [{k: m.get(k) for k in ("type", "text", "tags", "score", "confidence")} for m in await store.recall(a["query"])]}
    if name == "design_experiment":
        n = int(a.get("n_variants") or 12)
        vs = await _llm_variants(a["goal"], n)
        source = "llm" if vs else "deterministic"
        if vs and len(vs) < n:
            extra = sim.generate_variants(a["goal"], n)[len(vs):]
            for i, v in enumerate(extra):
                v["id"] = f"v{len(vs) + i + 1:02d}"
            vs += extra
        exp = await lab.create_experiment(a["goal"], n, vs, source)
        return {"experiment_id": exp["id"], "n_variants": len(exp["variants"]), "source": source,
                "dyes": exp["variants"][0]["params"]["dyes"]}
    if name == "simulate":
        exp = await lab.simulate_experiment(a["experiment_id"])
        return _exp_summary(exp)
    if name == "select_variant":
        exp = await lab.select_variant(a["experiment_id"], a["variant_id"], a.get("reason", ""))
        return {"selected": exp["selected_variant_id"]}
    if name == "run_variant":
        run = await lab.request_run(a["experiment_id"], a.get("variant_id"))
        return {"run_id": run["id"], "status": run["status"], "note": "waiting for human approval in the web UI or by voice ('approve')"
                if run["status"] == "awaiting_approval" else ""}
    if name == "move_arm":
        dev = a["device"]
        if a.get("pose"):
            return await hub.cmd(dev, "pose", {"name": a["pose"]})
        return await hub.cmd(dev, "joints", {"q": a.get("joints") or {}, "ms": a.get("ms", 1500)})
    if name == "cnc_move":
        if a.get("home"):
            return await hub.cmd("cnc", "home", {}, timeout=90)
        x, y, z = (float(a.get(k, 0)) for k in "xyz")
        for ax, v in zip("xyz", (x, y, z)):
            lo, hi = sim.BOUNDS[ax]
            if not lo <= v <= hi:
                raise ValueError(f"{ax}={v} outside [{lo},{hi}]")
        return await hub.cmd("cnc", "move", {"x": x, "y": y, "z": z, "f": float(a.get("f", 2000))}, timeout=60)
    if name == "pipette":
        act = a["action"]
        args = {"ul": a["ul"]} if act in ("aspirate", "dispense") else ({"mm": float(a.get("mm", 0))} if act == "move" else {})
        return await hub.cmd("pipette", act, args)
    if name == "say":
        await speak(a["text"])
        await chat_ui("agent", a["text"], spoken=True)
        return {"ok": True}
    if name == "remember":
        m = await store.remember(a["text"], a.get("tags"), type=a.get("type") or "semantic")
        return {"memory_id": m["id"]}
    raise ValueError(f"unknown tool {name}")


# ---------------- LLM backends ----------------
_history: list[dict] = []  # text-only turns {"role","content"}
_lock = asyncio.Lock()


def _anthropic():
    from anthropic import AsyncAnthropic

    ws = env("ANTHROPIC_WORKSPACE_ID")
    return AsyncAnthropic(default_headers={"anthropic-workspace-id": ws} if ws else None)


async def _complete_text(prompt: str) -> str:
    if LLM_PROVIDER == "fireworks":
        r = await _fireworks([{"role": "user", "content": prompt}], tools=False)
        return r["choices"][0]["message"].get("content") or ""
    c = _anthropic()
    msg = await c.messages.create(model=ANTHROPIC_MODEL, max_tokens=2000, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if b.type == "text")


async def _anthropic_loop(text: str) -> str:
    c = _anthropic()
    msgs = _history[-12:] + [{"role": "user", "content": text}]
    final = ""
    for _ in range(10):
        resp = await c.messages.create(model=ANTHROPIC_MODEL, max_tokens=1500, system=SYSTEM, tools=TOOLS, messages=msgs)
        msgs.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
        texts = [b.text for b in resp.content if b.type == "text" and b.text.strip()]
        if texts:
            final = "\n".join(texts)
        if resp.stop_reason != "tool_use":
            break
        if texts:
            await chat_ui("agent", final, interim=True)
        results = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            out = await run_tool(b.name, b.input or {})
            if isinstance(out, dict) and out.get("_image"):
                content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": out["_image"]}},
                           {"type": "text", "text": f"camera frame {out.get('w')}x{out.get('h')}, {out.get('age_s')} s old"}]
            else:
                content = json.dumps(out, default=str)[:8000]
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": content})
        msgs.append({"role": "user", "content": results})
    return final or "Done."


async def _fireworks(messages: list[dict], tools: bool = True) -> dict:
    body: dict[str, Any] = {"model": FIREWORKS_MODEL, "messages": messages, "max_tokens": 1500, "temperature": 0.3}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in TOOLS]
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.fireworks.ai/inference/v1/chat/completions",
                         headers={"Authorization": f"Bearer {env('FIREWORKS_API_KEY')}"}, json=body)
        r.raise_for_status()
        return r.json()


async def _fireworks_loop(text: str) -> str:
    msgs: list[dict] = [{"role": "system", "content": SYSTEM}] + _history[-12:] + [{"role": "user", "content": text}]
    final = ""
    for _ in range(10):
        data = await _fireworks(msgs)
        m = data["choices"][0]["message"]
        msgs.append({k: v for k, v in m.items() if k in ("role", "content", "tool_calls")})
        if m.get("content"):
            final = m["content"]
        calls = m.get("tool_calls") or []
        if not calls:
            break
        for tc in calls:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            out = await run_tool(tc["function"]["name"], args)
            if isinstance(out, dict) and out.get("_image"):
                out = {"note": "image captured (vision not available on this provider)", "w": out.get("w"), "h": out.get("h")}
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(out, default=str)[:8000]})
    return final or "Done."


# ---------------- rule-based fallback ----------------
def _latest_exp() -> dict | None:
    exps = store.list("experiments", 1)
    return exps[0] if exps else None


async def _rules(text: str) -> str:
    t = text.lower().strip()
    if re.search(r"\b(approve|go ahead|yes,? (run|go|do it)|confirm)\b", t):
        r = lab.pending_run()
        if not r:
            return "There is no run waiting for approval."
        await lab.approve(r["id"])
        return f"Approved. Running variant {r['variant_id']} now."
    if re.search(r"\b(abort|stop|halt|emergency)\b", t):
        await lab.abort()
        return "Stopping everything."
    if re.search(r"\bhome\b", t):
        msgs = []
        for dev in ("so_a", "so_b"):
            if dev in hub.devices:
                r = await run_tool("move_arm", {"device": dev, "pose": "home"})
                msgs.append(f"{dev} {'ok' if 'error' not in r else r['error']}")
        if "cnc" in hub.devices and ("cnc" in t or "all" in t or "everything" in t):
            r = await run_tool("cnc_move", {"home": True})
            msgs.append(f"cnc {'ok' if 'error' not in r else r['error']}")
        return "Homing: " + (", ".join(msgs) if msgs else "no devices connected") + "."
    if re.search(r"\b(look|see|camera)\b", t):
        r = await run_tool("look", {})
        if "error" in r:
            return "I can't see anything yet. Is the phone connected?"
        return f"I grabbed a {r['w']}x{r['h']} camera frame. Vision needs an LLM key to describe it."
    if re.search(r"\b(devices?|status)\b", t):
        r = await run_tool("list_devices", {})
        return f"Bridge {'connected' if r['bridge'] else 'offline'}; devices: " + (", ".join(f"{d['id']} ({d['status']})" for d in r["devices"]) or "none") + "."
    if t.startswith("remember"):
        await run_tool("remember", {"text": text.split(" ", 1)[1] if " " in text else text})
        return "Got it, I'll remember that."
    if re.search(r"\b(recall|what do you remember|memory|memories)\b", t):
        r = await run_tool("recall", {"query": text})
        ms = r["memories"]
        return "I remember: " + " | ".join(m["text"][:120] for m in ms[:3]) if ms else "I don't remember anything relevant yet."
    if re.search(r"\b(simulate|sim)\b", t) and not sim.target_rgb(t):
        exp = _latest_exp()
        if not exp:
            return "No experiment yet. Tell me a goal, like 'make orange'."
        r = await run_tool("simulate", {"experiment_id": exp["id"]})
        return _sim_sentence(r)
    if re.search(r"\b(run|execute|do it)\b", t) and not sim.target_rgb(t):
        exp = _latest_exp()
        if not exp:
            return "No experiment to run yet."
        if exp["status"] != "simulated":
            await run_tool("simulate", {"experiment_id": exp["id"]})
        r = await run_tool("run_variant", {"experiment_id": exp["id"]})
        if "error" in r:
            return f"Can't run: {r['error']}"
        return f"Run {r['run_id']} ready for variant {store.get('runs', r['run_id'])['variant_id']}. Say approve or press approve to start."
    if sim.target_rgb(t) or re.search(r"\b(make|mix|experiment)\b", t):
        n = int(m.group(1)) if (m := re.search(r"(\d+)\s*variants?", t)) else 12
        mem = await store.recall(text, 2)
        r = await run_tool("design_experiment", {"goal": text, "n_variants": n})
        s = await run_tool("simulate", {"experiment_id": r["experiment_id"]})
        prior = f" I recalled {len(mem)} related memories." if mem else ""
        return _sim_sentence(s) + prior + " Say 'run best' to run it for real."
    return ("I can: make a color (e.g. 'make orange'), simulate, run best, approve, abort, home arms, look, status, "
            "remember ..., recall ... (" + ("LLM call failed, so I'm using simple commands this turn" if llm_available() else "LLM key not configured, using simple commands") + ").")


def _sim_sentence(r: dict) -> str:
    if "error" in r:
        return f"Simulation failed: {r['error']}"
    ss = r.get("sim_summary") or {}
    if not r["top"]:
        return "No variant passed the safety checks."
    b = r["top"][0]
    return (f"Simulated {ss.get('n')} variants in {ss.get('sim_ms')} ms, {ss.get('passing')} passed checks. "
            f"Best is {b['variant_id']} with ratios {b['params']['ratios']} of {'/'.join(b['params']['dyes'])}, "
            f"predicted color {b['predicted_rgb']}, score {b['score']:.2f}.")


# ---------------- entry point ----------------
async def handle(text: str, source: str = "ui") -> str:
    text = (text or "").strip()
    if not text:
        return ""
    await chat_ui("user", text, source=source)
    await hub.phone({"t": "transcript", "role": "user", "text": text})
    async with _lock:
        await face("thinking")
        t0 = time.time()
        use_llm = llm_available()
        try:
            if use_llm:
                reply = await (_fireworks_loop(text) if LLM_PROVIDER == "fireworks" else _anthropic_loop(text))
                mark("llm", True, f"{LLM_PROVIDER} {_model()} ok")
            else:
                warn_once("llm", "No LLM key configured; using rule-based agent")
                reply = await _rules(text)
        except Exception as e:  # noqa: BLE001
            log.exception("agent error")
            if use_llm:
                mark("llm", False, f"{LLM_PROVIDER} failed: {e}")
            warn_once("llm_fail", f"LLM failed ({e}); falling back to rules for this turn")
            try:
                reply = await _rules(text)
            except Exception as e2:  # noqa: BLE001
                reply = f"Sorry, something went wrong: {e2}"
        _history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
        del _history[:-24]
    store.event("chat", source=source, text=text, reply=reply)
    track("chat", input=text, output=reply, model=_model() if use_llm else "rules", props={"source": source, "ms": int((time.time() - t0) * 1000)})
    await chat_ui("agent", reply)
    await hub.phone({"t": "transcript", "role": "agent", "text": reply})
    await speak(reply[:600])
    await face("idle")
    return reply
