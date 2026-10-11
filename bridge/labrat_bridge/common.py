"""Shared bits: Device base class, calibration JSON helpers, logging."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("labrat_bridge")

CALIB_DIR = Path(os.environ.get("LABRAT_CALIB_DIR", Path(__file__).resolve().parent.parent / "calib"))


def load_json(name: str, default: dict) -> dict:
    p = CALIB_DIR / name
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        return json.loads(json.dumps(default))
    except Exception as e:  # corrupt file -> defaults
        log.warning("bad calib %s: %s (using defaults)", p, e)
        return json.loads(json.dumps(default))
    merged = json.loads(json.dumps(default))
    _deep_merge(merged, data)
    return merged


def save_json(name: str, data: dict) -> Path:
    CALIB_DIR.mkdir(parents=True, exist_ok=True)
    p = CALIB_DIR / name
    p.write_text(json.dumps(data, indent=2) + "\n")
    return p


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


class DeviceError(Exception):
    pass


class Device:
    """A bridge device. Actions are sync methods named ``a_<action>(**args)`` run in a worker
    thread, serialized per device (except those in ``unlocked``, e.g. ``stop``)."""

    kind = "?"
    unlocked: tuple[str, ...] = ()

    def __init__(self, dev_id: str, mock: bool, info: dict | None = None):
        self.id = dev_id
        self.mock = mock
        self.info = dict(info or {})
        self.error: str | None = None
        self._lock: asyncio.Lock | None = None

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        return "mock" if self.mock else "ok"

    def describe(self) -> dict:
        info = dict(self.info)
        if self.error:
            info["error"] = self.error
        return {"id": self.id, "kind": self.kind, "status": self.status, "info": info}

    async def handle(self, action: str, args: dict | None) -> Any:
        fn = getattr(self, "a_" + action, None)
        if fn is None:
            raise DeviceError(f"{self.id}: unknown action '{action}'")
        args = dict(args or {})
        if action in self.unlocked:
            return await asyncio.to_thread(fn, **args)
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            return await asyncio.to_thread(fn, **args)

    def state(self) -> dict:
        return {}

    def safe_stop(self) -> None:
        """Called by the watchdog when the server link is lost."""

    def close(self) -> None:
        pass
