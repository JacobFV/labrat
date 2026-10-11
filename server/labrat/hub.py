"""WebSocket hub: bridge command dispatch (futures/timeouts), UI + phone broadcast."""
import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import WebSocket

from .config import log


class BridgeError(Exception):
    pass


class Hub:
    def __init__(self) -> None:
        self.bridge: WebSocket | None = None
        self.devices: dict[str, dict] = {}  # id -> {id, kind, status, info, state}
        self.pending: dict[str, asyncio.Future] = {}
        self.uis: set[WebSocket] = set()
        self.phones: set[WebSocket] = set()
        self.latest_frame: dict | None = None  # {"jpeg","w","h","ts"}
        self._last_dev_bcast = 0.0

    # ---------- broadcast ----------
    async def _send(self, ws: WebSocket, msg: dict) -> bool:
        try:
            await ws.send_text(json.dumps(msg, default=str))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def ui(self, msg: dict) -> None:
        dead = [ws for ws in list(self.uis) if not await self._send(ws, msg)]
        for ws in dead:
            self.uis.discard(ws)

    async def phone(self, msg: dict) -> None:
        dead = [ws for ws in list(self.phones) if not await self._send(ws, msg)]
        for ws in dead:
            self.phones.discard(ws)

    def devices_msg(self) -> dict:
        return {"t": "devices", "bridge": self.bridge is not None, "devices": list(self.devices.values())}

    async def push_devices(self, force: bool = True) -> None:
        now = time.time()
        if not force and now - self._last_dev_bcast < 0.2:
            return
        self._last_dev_bcast = now
        await self.ui(self.devices_msg())

    # ---------- bridge ----------
    async def on_bridge_msg(self, msg: dict) -> None:
        t = msg.get("t")
        if t == "hello":
            self.devices = {}
            for d in msg.get("devices", []):
                self.devices[d["id"]] = {**d, "state": d.get("state", {})}
            log.info(f"bridge hello: {[d['id'] for d in msg.get('devices', [])]}")
            await self.push_devices()
        elif t == "state":
            for did, st in (msg.get("devices") or {}).items():
                self.devices.setdefault(did, {"id": did, "kind": "?", "status": "ok", "info": {}})["state"] = st
            await self.push_devices(force=False)
        elif t == "result":
            fut = self.pending.pop(msg.get("id", ""), None)
            if fut and not fut.done():
                fut.set_result(msg)
        elif t == "log":
            await self.ui({"t": "log", "src": "bridge", **{k: v for k, v in msg.items() if k != "t"}})

    def bridge_gone(self) -> None:
        self.bridge = None
        for d in self.devices.values():
            d["status"] = "offline"
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(BridgeError("bridge disconnected"))
        self.pending.clear()

    async def cmd(self, device: str, action: str, args: dict | None = None, timeout: float = 30.0) -> dict[str, Any]:
        if self.bridge is None:
            raise BridgeError("no hardware bridge connected")
        cid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.pending[cid] = fut
        msg = {"t": "cmd", "id": cid, "device": device, "action": action, "args": args or {}}
        await self.ui({"t": "log", "src": "server", "level": "info", "text": f"→ {device}.{action} {json.dumps(args or {})}"})
        try:
            await self.bridge.send_text(json.dumps(msg))
            res = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as e:
            raise BridgeError(f"{device}.{action} timed out after {timeout}s") from e
        finally:
            self.pending.pop(cid, None)
        if not res.get("ok"):
            raise BridgeError(f"{device}.{action} failed: {res.get('error')}")
        return res.get("data") or {}


hub = Hub()
