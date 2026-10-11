"""LabRat FastAPI app: WebSocket hubs, REST API, static web + phone SPAs."""
import asyncio
import json
import time

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, lab, learn
from .config import PHONE_DIR, TOKEN, WEB_DIR, log
from .hub import hub
from .store import store
from .telemetry import SESSION_ID, modelence
from .voice import stt

app = FastAPI(title="LabRat")


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


@app.exception_handler(Exception)
async def _err(_, exc: Exception):
    log.exception("unhandled")
    return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------- static ----------------
for d in (WEB_DIR, PHONE_DIR):
    d.mkdir(parents=True, exist_ok=True)
app.mount("/phone", StaticFiles(directory=str(PHONE_DIR), html=True), name="phone")
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
