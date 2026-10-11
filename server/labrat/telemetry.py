"""Raindrop (AI observability) + Modelence (thin session/event hook). Both never raise."""
import asyncio
import uuid

import httpx

from .config import env, log, warn_once

SESSION_ID = uuid.uuid4().hex[:12]
_raindrop = None
_raindrop_tried = False


def _rd():
    global _raindrop, _raindrop_tried
    if _raindrop_tried:
        return _raindrop
    _raindrop_tried = True
    key = env("RAINDROP_API_KEY")
    if not key:
        warn_once("raindrop", "RAINDROP_API_KEY not set; Raindrop tracking disabled (events still in store)")
        return None
    try:
        import raindrop.analytics as raindrop  # type: ignore

        raindrop.init(key)
        _raindrop = raindrop
        log.info("Raindrop SDK initialised")
    except Exception as e:  # noqa: BLE001
        warn_once("raindrop_sdk", f"raindrop-ai SDK unavailable ({e}); using REST")
        _raindrop = "rest"
    return _raindrop


async def _raindrop_rest(payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(
                "https://api.raindrop.ai/v1/events/track",
                headers={"Authorization": f"Bearer {env('RAINDROP_API_KEY')}"},
                json=[payload],
            )
    except Exception as e:  # noqa: BLE001
        warn_once("raindrop_rest", f"Raindrop REST failed: {e}")


def track(event: str, input: str = "", output: str = "", model: str = "", user_id: str = "labrat-operator", props: dict | None = None) -> None:
    rd = _rd()
    props = {**(props or {}), "session_id": SESSION_ID}
    if rd is not None:
        try:
            if rd == "rest":
                payload = {
                    "event_id": uuid.uuid4().hex, "user_id": user_id, "event": event,
                    "properties": props, "ai_data": {"model": model, "input": input[:4000], "output": output[:4000], "convo_id": SESSION_ID},
                }
                _spawn(_raindrop_rest(payload))
            else:
                rd.track_ai(user_id=user_id, event=event, model=model or "n/a", input=input[:4000], output=output[:4000], convo_id=SESSION_ID, properties=props)
        except Exception as e:  # noqa: BLE001
            warn_once("raindrop_track", f"Raindrop track failed: {e}")
    modelence(event, {"input": input[:1000], "output": output[:1000], "model": model, **props})


def modelence(event: str, data: dict) -> None:
    """Thin hook: POST {session, event, data} JSON to MODELENCE_URL with bearer MODELENCE_KEY. No-op if unset."""
    url = env("MODELENCE_URL")
    if not url:
        warn_once("modelence", "MODELENCE_URL not set; Modelence hook is a no-op")
        return

    async def go():
        try:
            h = {"Authorization": f"Bearer {env('MODELENCE_KEY', '')}"}
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(url, headers=h, json={"app": "labrat", "session": SESSION_ID, "event": event, "data": data})
        except Exception as e:  # noqa: BLE001
            warn_once("modelence_err", f"Modelence hook failed: {e}")

    _spawn(go())


def _spawn(coro) -> None:
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
