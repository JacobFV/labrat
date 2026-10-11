"""LabRat FastAPI app: WebSocket hubs, REST API, static web + phone SPAs."""
import asyncio
import json
import time

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, deck_cal, lab, learn
from .config import PHONE_DIR, TOKEN, WEB_DIR, log
from .hub import hub
from .store import store
from .telemetry import SESSION_ID, modelence
from .voice import stt

app = FastAPI(title="LabRat")

from .vision import router as _vision_router  # noqa: E402

app.include_router(_vision_router)


def _authed(ws: WebSocket) -> bool:
    return ws.query_params.get("token") == TOKEN


def _bg(coro) -> None:
    async def wrap():
        try:
            await coro
        except Exception:  # noqa: BLE001
            log.exception("background task failed")
    asyncio.create_task(wrap())


@app.on_event("startup")
async def _startup() -> None:
    modelence("session_start", {"session": SESSION_ID})
    learn.ensure_baseline()
    learn.apply_latest()
    store.on_loaded = learn.apply_latest
    _bg(learn.seed_taxonomy())
    log.info(f"LabRat up; session {SESSION_ID}; LLM={'on' if agent.llm_available() else 'rules'}")


# ---------------- websockets ----------------
@app.websocket("/ws/bridge")
async def ws_bridge(ws: WebSocket) -> None:
    if not _authed(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    if hub.bridge is not None:
        try:
            await hub.bridge.close()
        except Exception:  # noqa: BLE001
            pass
    hub.bridge = ws
    store.event("bridge_connected")
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            await hub.on_bridge_msg(msg)
    except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
        pass
    finally:
        if hub.bridge is ws:
            hub.bridge_gone()
            store.event("bridge_disconnected")
            await hub.push_devices()


@app.websocket("/ws/phone")
async def ws_phone(ws: WebSocket) -> None:
    if not _authed(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.phones.add(ws)
    await ws.send_text(json.dumps({"t": "face", "mood": "happy", "gaze": [0, 0]}))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            t = msg.get("t")
            if t == "frame":
                hub.latest_frame = {"jpeg": msg.get("jpeg"), "w": msg.get("w"), "h": msg.get("h"), "ts": time.time()}
                await hub.ui({"t": "frame", "jpeg": msg.get("jpeg"), "w": msg.get("w"), "h": msg.get("h")})
            elif t == "text":
                _bg(agent.handle(msg.get("text", ""), "phone"))
            elif t == "audio":
                _bg(_phone_audio(msg))
    except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
        pass
    finally:
        hub.phones.discard(ws)


async def _phone_audio(msg: dict) -> None:
    await hub.phone({"t": "face", "mood": "thinking", "gaze": [0, 0]})
    text = await stt(msg.get("b64", ""), msg.get("mime", "audio/webm"))
    if not text:
        await hub.phone({"t": "say", "text": "Sorry, I couldn't hear that.", "audio_b64": None})
        await hub.phone({"t": "face", "mood": "idle", "gaze": [0, 0]})
        return
    await agent.handle(text, "phone-voice")


@app.websocket("/ws/ui")
async def ws_ui(ws: WebSocket) -> None:
    if not _authed(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.uis.add(ws)
    await ws.send_text(json.dumps(hub.devices_msg(), default=str))
    for exp in reversed(store.list("experiments", 10)):
        await ws.send_text(json.dumps({"t": "experiment", "experiment": lab._strip(exp)}, default=str))
    for run in reversed(store.list("runs", 5)):
        await ws.send_text(json.dumps({"t": "run", "run": run}, default=str))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            t = msg.get("t")
            if t == "chat":
                _bg(agent.handle(msg.get("text", ""), "ui"))
            elif t == "approve":
                _bg(lab.approve(msg.get("run_id") or (lab.pending_run() or {}).get("id", "")))
            elif t == "abort":
                _bg(lab.abort())
            elif t == "observe":
                _bg(learn.observe(msg.get("run_id", ""), msg.get("rating"), msg.get("rgb"), msg.get("well")))
    except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
        pass
    finally:
        hub.uis.discard(ws)


# ---------------- REST ----------------
class ChatIn(BaseModel):
    text: str


class SimIn(BaseModel):
    n_variants: int | None = None


class RunIn(BaseModel):
    variant_id: str | None = None
    auto: bool = False


@app.get("/healthz")
async def healthz():
    return {"ok": True, "session": SESSION_ID, "bridge": hub.bridge is not None, "llm": agent.llm_available(),
            "mongo": store.db is not None}


@app.get("/api/state")
async def state():
    return {**hub.devices_msg(), "phones": len(hub.phones), "uis": len(hub.uis), "llm": agent.llm_available(),
            "mongo": store.db is not None, "has_frame": hub.latest_frame is not None,
            "pending_run": lab.pending_run(), "runs": store.list("runs", 10)}


@app.get("/api/experiments")
async def experiments():
    return [lab._strip(e) for e in store.list("experiments", 50)]


@app.get("/api/experiments/{exp_id}")
async def experiment(exp_id: str, frames: bool = False):
    e = store.get("experiments", exp_id)
    if not e:
        raise HTTPException(404, "not found")
    return e if frames else lab._strip(e)


@app.get("/api/experiments/{exp_id}/variants/{vid}/frames")
async def variant_frames(exp_id: str, vid: str):
    e = store.get("experiments", exp_id)
    if not e:
        raise HTTPException(404, "not found")
    try:
        v = lab.variant(e, vid)
    except ValueError as err:
        raise HTTPException(404, str(err)) from err
    return {"experiment_id": exp_id, "variant_id": vid, "frames": v.get("frames", []), "sim": v.get("sim")}


@app.post("/api/chat")
async def chat(body: ChatIn):
    return {"reply": await agent.handle(body.text, "rest")}


@app.post("/api/experiments/{exp_id}/simulate")
async def simulate(exp_id: str, body: SimIn | None = None):
    try:
        e = await lab.simulate_experiment(exp_id, (body.n_variants if body else None))
    except ValueError as err:
        raise HTTPException(404, str(err)) from err
    return lab._strip(e)


@app.post("/api/experiments/{exp_id}/run")
async def run(exp_id: str, body: RunIn | None = None):
    try:
        return await lab.request_run(exp_id, body.variant_id if body else None, auto=bool(body and body.auto))
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


@app.post("/api/runs/{run_id}/approve")
async def approve(run_id: str):
    try:
        return await lab.approve(run_id)
    except ValueError as err:
        raise HTTPException(404, str(err)) from err


class ObserveIn(BaseModel):
    rating: int | None = None
    rgb: list[int] | None = None
    well: str | None = None
    vol_ul: float | None = None


@app.post("/api/runs/{run_id}/observe")
async def observe(run_id: str, body: ObserveIn):
    try:
        return await learn.observe(run_id, body.rating, body.rgb, body.well, body.vol_ul)
    except ValueError as err:
        raise HTTPException(404, str(err)) from err


@app.get("/api/learning")
async def learning():
    return learn.learning()


@app.post("/api/abort")
async def abort():
    await lab.abort()
    return {"ok": True}


@app.get("/api/memory/search")
async def memory_search(q: str, k: int = 5, type: str | None = None):
    return {"query": q, "results": await store.recall(q, k, type)}


# ---------------- deck calibration ----------------
class JogIn(BaseModel):
    device: str = "cnc"
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0
    f: float = 600.0


class TeachIn(BaseModel):
    target: str
    pos: list[float] | None = None  # optional explicit spindle xyz (else read from the CNC)


async def _cnc_pos() -> list[float]:
    try:
        st = await hub.cmd("cnc", "status", {}, timeout=5)
        p = st.get("wpos") or st.get("pos")
        if p:
            return [float(v) for v in p]
    except Exception as e:  # noqa: BLE001
        log.warning(f"cnc status failed: {e}")
    p = ((hub.devices.get("cnc") or {}).get("state") or {}).get("wpos")
    if not p:
        raise HTTPException(409, "no CNC position available (bridge connected?)")
    return [float(v) for v in p]


def _sync_pos(p: list[float]) -> None:
    lab._pos.update({"x": round(p[0], 2), "y": round(p[1], 2), "z": round(p[2], 2)})


@app.get("/api/deck_cal")
async def get_deck_cal():
    return deck_cal.summary()


@app.post("/api/deck_cal")
async def post_deck_cal(body: dict):
    deck_cal.update(body)
    await hub.ui({"t": "deck_cal", "deck_cal": deck_cal.summary()})
    return deck_cal.summary()


@app.post("/api/deck_cal/teach")
async def teach(body: TeachIn):
    p = body.pos if body.pos else await _cnc_pos()
    tip = deck_cal.teach(body.target, (p[0], p[1], p[2]))
    store.event("deck_teach", target=body.target, spindle=p, tip=tip)
    out = {"target": deck_cal.canon(body.target), "spindle": p, "tip": tip, "deck_cal": deck_cal.summary()}
    await hub.ui({"t": "deck_cal", "deck_cal": out["deck_cal"]})
    return out


@app.post("/api/jog")
async def jog(body: JogIn):
    if body.device != "cnc":
        raise HTTPException(400, "only cnc jog supported")
    for v in (body.dx, body.dy, body.dz):
        if abs(v) > 50:
            raise HTTPException(400, "jog step too large (max 50 mm)")
    try:
        r = await hub.cmd("cnc", "jog", {"dx": body.dx, "dy": body.dy, "dz": body.dz, "f": body.f}, timeout=30)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"jog failed: {e}") from e
    p = r.get("pos") if isinstance(r, dict) else None
    if p:
        _sync_pos(p)
    return {"ok": True, "pos": p}


@app.exception_handler(Exception)
async def _err(_, exc: Exception):
    log.exception("unhandled")
    return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------- static ----------------
for d in (WEB_DIR, PHONE_DIR):
    d.mkdir(parents=True, exist_ok=True)
app.mount("/phone", StaticFiles(directory=str(PHONE_DIR), html=True), name="phone")
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
