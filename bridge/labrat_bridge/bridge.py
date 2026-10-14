"""Discovery, device construction, and the websocket loop to the server's /ws/bridge."""
from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

from .common import Device, load_json, log, save_json
from .grbl import Grbl, MockGrbl, probe_grbl
from .so101 import So101, probe_feetech
from .microscope import Microscope
from .tappet import Syringe, Tappet, probe_tappet

ALL_IDS = ["so_a", "so_b", "tap_a", "tap_b", "cnc", "pipette", "microscope"]
# extra syringe modules on the tool rail: calib/tools.json {"syringes": [{"id": "pipette2", "channel": 9}, ...]}
DEFAULT_TOOLS = {"syringes": []}
WATCHDOG_S = 3.0
STATE_HZ = 5.0


def list_candidate_ports(spec: str = "auto") -> list[dict]:
    from serial.tools import list_ports

    if spec and spec != "auto":
        return [{"device": p, "serial": None, "desc": ""} for p in spec.split(",") if p]
    out = []
    for p in list_ports.comports():
        name = p.device
        if any(s in name for s in ("Bluetooth", "debug-console", "wlan")) or (p.vid is None and "usb" not in name.lower()):
            continue
        if name.startswith("/dev/tty.") and name.replace("/dev/tty.", "/dev/cu.") in [q.device for q in list_ports.comports()]:
            continue  # macOS: prefer cu.*
        out.append({"device": name, "serial": p.serial_number, "desc": p.description or ""})
    return out


def classify_port(p: dict) -> dict:
    port = p["device"]
    banner = probe_grbl(port)
    if banner:
        return {**p, "kind": "grbl", "detail": banner}
    hello = probe_tappet(port)
    if hello:
        return {**p, "kind": "tappet", "detail": hello.get("fw", "tappet-fw")}
    ids = probe_feetech(port)
    if ids:
        return {**p, "kind": "so101", "detail": f"ids {ids}"}
    return {**p, "kind": "unknown", "detail": ""}


def discover(spec: str = "auto", skip: set[str] | None = None) -> list[dict]:
    ports = [p for p in list_candidate_ports(spec) if p["device"] not in (skip or set())]
    if not ports:
        return []
    with ThreadPoolExecutor(max_workers=len(ports)) as ex:
        return list(ex.map(classify_port, ports))


def build_devices(mock: set[str], ports: str = "auto", so_a: str | None = None, so_b: str | None = None,
                  fallback_mock: bool = True) -> dict[str, Device]:
    found: list[dict] = []
    if mock != set(ALL_IDS):
        found = discover(ports)
    print_table("Serial ports", [(f["device"], f["kind"], str(f["detail"]), f.get("serial") or "") for f in found]
                or [("(none)", "", "", "")], ("port", "kind", "detail", "usb serial"))

    by_kind: dict[str, list[dict]] = {}
    for f in found:
        by_kind.setdefault(f["kind"], []).append(f)
    devs: dict[str, Device] = {}

    def make(dev_id, factory, mock_factory, reason="not found"):
        if dev_id in mock:
            devs[dev_id] = mock_factory()
            return
        try:
            devs[dev_id] = factory()
        except Exception as e:
            log.error("%s: %s", dev_id, e)
            d = mock_factory()
            if not fallback_mock:
                d.mock = False
                d.error = str(e)
            else:
                d.info["fallback"] = str(e)
            devs[dev_id] = d

    # --- SO-101 arms: explicit override > persisted usb-serial mapping > sorted order
    pm = load_json("ports.json", {"so_a": None, "so_b": None})
    arms = by_kind.get("so101", [])
    assign: dict[str, str | None] = {"so_a": so_a, "so_b": so_b}
    for key in ("so_a", "so_b"):
        if assign[key] is None and pm.get(key):
            match = [a for a in arms if pm[key] in (a.get("serial"), a["device"])]
            if match:
                assign[key] = match[0]["device"]
    rest = sorted(a["device"] for a in arms if a["device"] not in assign.values())
    for key in ("so_a", "so_b"):
        if assign[key] is None and rest:
            assign[key] = rest.pop(0)
    ser_of = {a["device"]: a.get("serial") or a["device"] for a in arms}
    if any(assign.values()):
        save_json("ports.json", {k: ser_of.get(v, v) for k, v in assign.items()})
    for key in ("so_a", "so_b"):
        port = assign[key]
        make(key, (lambda k=key, p=port: So101(k, port=p) if p else _raise("not found")),
             lambda k=key: So101(k, mock=True, info={"reason": "mock"}))

    taps = sorted(f["device"] for f in by_kind.get("tappet", []))
    for i, key in enumerate(("tap_a", "tap_b")):
        port = taps[i] if i < len(taps) else None
        make(key, (lambda k=key, p=port: Tappet(k, port=p) if p else _raise("not found")),
             lambda k=key: Tappet(k, mock=True))

    grbls = [f["device"] for f in by_kind.get("grbl", [])]
    make("cnc", lambda: Grbl(grbls[0]) if grbls else _raise("not found"), lambda: MockGrbl())

    host = next((d for k, d in devs.items() if k.startswith("tap_") and isinstance(d, Tappet) and d.client), None)
    if "pipette" in mock or host is None:
        devs["pipette"] = Syringe("pipette", None)
    else:
        devs["pipette"] = Syringe("pipette", host)
    for sy in load_json("tools.json", DEFAULT_TOOLS).get("syringes", []):   # more syringe modules, own channel + calib
        sid = sy["id"]
        devs[sid] = Syringe(sid, None if (sid in mock or host is None) else host, channel=int(sy["channel"]),
                            calib=sy.get("calib", f"{sid}.json"))
    devs["microscope"] = Microscope("microscope", mock="microscope" in mock)

    print_table("Devices", [(d.id, d.kind, d.status, str(d.info.get("port", d.info.get("host", "")))) for d in devs.values()],
                ("id", "kind", "status", "port/host"))
    return devs


def _raise(msg):
    raise RuntimeError(msg)


def print_table(title, rows, header):
    rows = [tuple(header)] + [tuple(r) for r in rows]
    w = [max(len(str(r[i])) for r in rows) for i in range(len(header))]
    print(f"\n== {title} ==")
    for n, r in enumerate(rows):
        print("  " + "  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))
        if n == 0:
            print("  " + "  ".join("-" * x for x in w))
    print(flush=True)


def bridge_url(server: str, token: str) -> str:
    s = server.rstrip("/")
    if s.startswith("http"):
        s = "ws" + s[4:]
    if not s.startswith("ws"):
        s = "wss://" + s
    if not s.endswith("/ws/bridge"):
        s += "/ws/bridge"
    return s + "?" + urlencode({"token": token})


class Bridge:
    def __init__(self, devices: dict[str, Device], url: str):
        self.devices = devices
        self.url = url
        self.connected = False
        self.last_link = time.monotonic()
        self.safe_done = False
        self._send_lock = asyncio.Lock()
        self.stop_event = asyncio.Event()

    async def _send(self, ws, obj):
        async with self._send_lock:
            await ws.send(json.dumps(obj, default=str))

    async def _handle(self, ws, msg):
        rid = msg.get("id")
        dev = self.devices.get(msg.get("device"))
        try:
            if dev is None:
                raise RuntimeError(f"unknown device {msg.get('device')!r}")
            if dev.error:
                raise RuntimeError(f"{dev.id} unavailable: {dev.error}")
            data = await dev.handle(msg.get("action", ""), msg.get("args") or {})
            res = {"t": "result", "id": rid, "ok": True, "data": data if isinstance(data, dict) else {"value": data}}
        except Exception as e:
            log.warning("cmd %s %s.%s failed: %s", rid, msg.get("device"), msg.get("action"), e)
            res = {"t": "result", "id": rid, "ok": False, "data": {}, "error": str(e)}
        try:
            await self._send(ws, res)
        except Exception:
            pass

    async def _state_loop(self, ws):
        while True:
            st = {}
            for k, d in self.devices.items():
                try:
                    st[k] = d.state()
                except Exception as e:
                    st[k] = {"error": str(e)}
            await self._send(ws, {"t": "state", "devices": st})
            await asyncio.sleep(1.0 / STATE_HZ)

    async def _watchdog(self):
        while True:
            await asyncio.sleep(0.25)
            if not self.connected and not self.safe_done and time.monotonic() - self.last_link > WATCHDOG_S:
                self.safe_done = True
                log.warning("server link down > %.0fs: stopping CNC, holding arms, syringe servo to neutral", WATCHDOG_S)
                await asyncio.gather(*(asyncio.to_thread(d.safe_stop) for d in self.devices.values()),
                                     return_exceptions=True)

    async def run(self):
        from websockets.asyncio.client import connect

        wd = asyncio.create_task(self._watchdog())
        backoff = 0.5
        try:
            while not self.stop_event.is_set():
                state_task = None
                try:
                    async with connect(self.url, ping_interval=1.0, ping_timeout=2.0, max_size=8 * 2**20) as ws:
                        self.connected, self.safe_done, backoff = True, False, 0.5
                        log.info("connected to %s", self.url.split("?")[0])
                        await self._send(ws, {"t": "hello", "devices": [d.describe() for d in self.devices.values()]})
                        state_task = asyncio.create_task(self._state_loop(ws))
                        async for raw in ws:
                            self.last_link = time.monotonic()
                            try:
                                msg = json.loads(raw)
                            except ValueError:
                                continue
                            if msg.get("t") == "cmd":
                                asyncio.create_task(self._handle(ws, msg))
                except Exception as e:
                    log.warning("link error: %s", e)
                finally:
                    if self.connected:
                        self.last_link = time.monotonic()
                    self.connected = False
                    if state_task:
                        state_task.cancel()
                if self.stop_event.is_set():
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)
        finally:
            wd.cancel()
