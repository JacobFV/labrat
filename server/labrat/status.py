"""Honest per-integration status for the UI sponsor pills.

Each integration module calls `mark(name, ok, detail)` after a real call. `services()` combines that with
whether the integration is configured: {name: {ok, mode: "live"|"fallback"|"local", detail, configured, last_ok_ts, last_err}}.
"""
import time

from .config import env

_last: dict[str, dict] = {}

_KEYS = {
    "mongo": ("MONGODB_URI",),
    "voyage": ("VOYAGE_API_KEY",),
    "raindrop": ("RAINDROP_API_KEY", "RAINDROP_WRITE_KEY"),
    "modelence": ("MODELENCE_URL",),
    "elevenlabs": ("ELEVENLABS_API_KEY",),
}


def mark(name: str, ok: bool, detail: str = "") -> None:
    rec = _last.setdefault(name, {"calls": 0, "errors": 0})
    rec["calls"] += 1
    rec["ok"] = bool(ok)
    rec["ts"] = time.time()
    if ok:
        rec["last_ok_ts"] = rec["ts"]
        rec["detail"] = detail or "last call ok"
    else:
        rec["errors"] += 1
        rec["last_err"] = (detail or "error")[:300]
        rec["detail"] = rec["last_err"]


def _llm_configured() -> bool:
    from .config import LLM_PROVIDER

    return bool(env("FIREWORKS_API_KEY") if LLM_PROVIDER == "fireworks" else env("ANTHROPIC_API_KEY"))


def _one(name: str, configured: bool, fallback_detail: str) -> dict:
    rec = _last.get(name) or {}
    if not configured:
        return {"ok": False, "mode": "fallback", "configured": False, "detail": fallback_detail}
    if not rec:
        return {"ok": True, "mode": "live", "configured": True, "detail": "configured; no calls yet"}
    out = {"ok": rec["ok"], "mode": "live" if rec["ok"] else "fallback", "configured": True, "detail": rec.get("detail", ""),
           "calls": rec["calls"], "errors": rec["errors"]}
    for k in ("last_ok_ts", "last_err"):
        if k in rec:
            out[k] = rec[k]
    return out


def services() -> dict:
    from .config import LLM_PROVIDER
    from .store import store

    s = {
        "voyage": _one("voyage", bool(env("VOYAGE_API_KEY")), "no VOYAGE_API_KEY; hashed bag-of-words embeddings"),
        "raindrop": _one("raindrop", any(env(k) for k in _KEYS["raindrop"]), "no RAINDROP_API_KEY; events kept in local store"),
        "modelence": _one("modelence", bool(env("MODELENCE_URL")), "no MODELENCE_URL; hook is a no-op"),
        "elevenlabs": _one("elevenlabs", bool(env("ELEVENLABS_API_KEY")), "no ELEVENLABS_API_KEY; browser speechSynthesis"),
        "llm": _one("llm", _llm_configured(), "no LLM key; rule-based agent"),
    }
    s["llm"]["provider"] = LLM_PROVIDER
    if store.db is not None:
        m = _one("mongo", True, "")
        if not _last.get("mongo"):
            m["detail"] = "connected" + ("; Atlas vector search" if store.vector_ok else "")
        m["vector_search"] = store.vector_ok
        s["mongo"] = m
    else:
        s["mongo"] = {"ok": False, "mode": "fallback", "configured": bool(env("MONGODB_URI")),
                      "detail": "MongoDB unavailable; in-memory store" if env("MONGODB_URI") else "no MONGODB_URI; in-memory store"}
    deploy = (env("LABRAT_DEPLOY", "local") or "local").lower()
    s["aws"] = ({"ok": True, "mode": "live", "detail": "running on AWS"} if deploy == "aws"
                else {"ok": False, "mode": "local", "detail": f"running locally (LABRAT_DEPLOY={deploy})"})
    return s
