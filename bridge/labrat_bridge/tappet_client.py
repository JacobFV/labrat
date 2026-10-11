# Vendored from tappet/host/tappet/client.py (in this repo) (stdlib + pyserial only).
"""BodyClient: talks the SPEC §4 JSON-lines protocol over serial (pyserial) or TCP (simulator).

    with BodyClient.connect("tcp://127.0.0.1:7777") as body:
        body.enable()
        body.tap(0, 160, 13.8)          # waits for the done event
"""
from __future__ import annotations

import itertools
import json
import socket
import threading
import time
from typing import Any, Callable, Optional

DEFAULT_TIMEOUT = 3.0
MOTION_CMDS = ("joints", "move", "tap", "swipe", "neck", "home", "park")


class BodyError(Exception):
    def __init__(self, err: str, msg: str = "", resp: dict | None = None):
        super().__init__(f"{err}: {msg}" if msg else err)
        self.err = err
        self.msg = msg
        self.resp = resp or {}


class LinkError(BodyError):
    def __init__(self, msg: str):
        super().__init__("link_down", msg)


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class TcpTransport:
    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self.desc = f"tcp://{host}:{port}"
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(None)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._rf = self.sock.makefile("rb")

    def readline(self) -> bytes:
        return self._rf.readline()

    def write(self, data: bytes) -> None:
        self.sock.sendall(data)

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class SerialTransport:
    def __init__(self, port: str, baud: int = 115200):
        import serial  # pyserial

        self.desc = f"serial://{port}@{baud}"
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self._buf = b""
        self._closed = False

    def readline(self) -> bytes:
        while not self._closed:
            try:
                chunk = self.ser.readline()
            except Exception:
                return b""
            if chunk:
                self._buf += chunk
                if self._buf.endswith(b"\n"):
                    line, self._buf = self._buf, b""
                    return line
        return b""

    def write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def close(self) -> None:
        self._closed = True
        try:
            self.ser.close()
        except Exception:
            pass


def open_transport(url: str, baud: int = 115200):
    """``tcp://host:port``, ``sim`` (= tcp://127.0.0.1:7777), ``serial:///dev/ttyX`` or a bare
    device path / COM port."""
    if url in ("sim", "tcp", ""):
        url = "tcp://127.0.0.1:7777"
    if url.startswith("tcp://"):
        hp = url[len("tcp://"):]
        host, _, port = hp.rpartition(":")
        return TcpTransport(host or "127.0.0.1", int(port or 7777))
    if url.startswith("serial://"):
        url = url[len("serial://"):]
    return SerialTransport(url, baud)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class _Pending:
    __slots__ = ("event", "value")

    def __init__(self):
        self.event = threading.Event()
        self.value: Optional[dict] = None


class BodyClient:
    def __init__(self, transport, heartbeat_s: float | None = 1.0):
        self.t = transport
        self._ids = itertools.count(1)
        self._wlock = threading.Lock()
        self._plock = threading.Lock()
        self._responses: dict[int, _Pending] = {}
        self._motions: dict[int, _Pending] = {}
        self._callbacks: list[Callable[[dict], None]] = []
        self.connected = True
        self.last_status: Optional[dict] = None
        self.boot_info: Optional[dict] = None
        self._closing = False
        self._reader = threading.Thread(target=self._read_loop, name="tappet-client-rx", daemon=True)
        self._reader.start()
        self._hb_stop = threading.Event()
        self._hb: Optional[threading.Thread] = None
        if heartbeat_s:
            self.start_heartbeat(heartbeat_s)

    @classmethod
    def connect(cls, url: str = "sim", baud: int = 115200, heartbeat_s: float | None = 1.0
                ) -> "BodyClient":
        return cls(open_transport(url, baud), heartbeat_s=heartbeat_s)

    @property
    def desc(self) -> str:
        return getattr(self.t, "desc", "?")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def close(self):
        self._closing = True
        self._hb_stop.set()
        self.connected = False
        try:
            self.t.close()
        except Exception:
            pass
        self._fail_all("client closed")

    # --- events ----------------------------------------------------------------
    def on_event(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        """Register a callback for every unsolicited event; returns an unsubscribe function."""
        self._callbacks.append(cb)
        return lambda: self._callbacks.remove(cb) if cb in self._callbacks else None

    def _read_loop(self):
        while True:
            try:
                raw = self.t.readline()
            except Exception:
                raw = b""
            if not raw:
                break
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if "evt" in msg:
                self._on_event(msg)
            elif "id" in msg:
                with self._plock:
                    p = self._responses.pop(msg.get("id"), None)
                if p:
                    p.value = msg
                    p.event.set()
        self.connected = False
        self._fail_all("link closed")

    def _on_event(self, ev: dict):
        name = ev.get("evt")
        if name in ("done", "error") and ev.get("id") is not None:
            with self._plock:
                p = self._motions.pop(ev["id"], None)
            if p:
                p.value = ev
                p.event.set()
        elif name == "status":
            self.last_status = {k: v for k, v in ev.items() if k != "evt"}
        elif name == "boot":
            self.boot_info = ev
        for cb in list(self._callbacks):
            try:
                cb(ev)
            except Exception:
                pass

    def _fail_all(self, why: str):
        with self._plock:
            items = list(self._responses.values()) + list(self._motions.values())
            self._responses.clear()
            self._motions.clear()
        for p in items:
            p.value = {"ok": False, "evt": "error", "err": "link_down", "msg": why}
            p.event.set()

    # --- requests ---------------------------------------------------------------
    def send_raw(self, obj: dict) -> None:
        if not self.connected:
            raise LinkError("not connected")
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        with self._wlock:
            try:
                self.t.write(data)
            except Exception as e:
                self.connected = False
                raise LinkError(str(e))

    def request(self, cmd: str, timeout: float = DEFAULT_TIMEOUT, check: bool = True,
                wait: bool = False, wait_timeout: float = 60.0, **args) -> dict:
        """Send ``cmd`` and return its response. For motion commands with ``wait=True`` also
        block until the ``done`` event (raising BodyError on an ``error`` event)."""
        rid = next(self._ids)
        req = {"id": rid, "cmd": cmd}
        req.update({k: v for k, v in args.items() if v is not None})
        p = _Pending()
        m = _Pending() if wait else None
        with self._plock:
            self._responses[rid] = p
            if m:
                self._motions[rid] = m
        try:
            self.send_raw(req)
        except LinkError:
            with self._plock:
                self._responses.pop(rid, None)
                self._motions.pop(rid, None)
            raise
        if not p.event.wait(timeout):
            with self._plock:
                self._responses.pop(rid, None)
                self._motions.pop(rid, None)
            raise BodyError("timeout", f"no response to {cmd} within {timeout}s")
        resp = p.value or {}
        if not resp.get("ok"):
            with self._plock:
                self._motions.pop(rid, None)
            if check:
                raise BodyError(resp.get("err", "error"), resp.get("msg", ""), resp)
            return resp
        if m:
            if not m.event.wait(wait_timeout):
                with self._plock:
                    self._motions.pop(rid, None)
                raise BodyError("timeout", f"{cmd} did not finish within {wait_timeout}s")
            ev = m.value or {}
            if ev.get("evt") != "done":
                raise BodyError(ev.get("err", "fault"), ev.get("msg", ""), ev)
        return resp

    def wait_done(self, rid: int, timeout: float = 60.0) -> dict:
        """Wait for a done/error event of an id sent with ``wait=False`` via :meth:`send_motion`."""
        with self._plock:
            m = self._motions.get(rid)
        if m is None:
            raise KeyError(rid)
        if not m.event.wait(timeout):
            raise BodyError("timeout", f"id {rid} did not finish")
        ev = m.value or {}
        if ev.get("evt") != "done":
            raise BodyError(ev.get("err", "fault"), ev.get("msg", ""), ev)
        return ev

    def send_motion(self, cmd: str, timeout: float = DEFAULT_TIMEOUT, **args) -> dict:
        """Queue a motion and return the response (with ``id``) without waiting; use
        :meth:`wait_done` later."""
        rid = next(self._ids)
        req = {"id": rid, "cmd": cmd}
        req.update({k: v for k, v in args.items() if v is not None})
        p, m = _Pending(), _Pending()
        with self._plock:
            self._responses[rid] = p
            self._motions[rid] = m
        self.send_raw(req)
        if not p.event.wait(timeout):
            raise BodyError("timeout", f"no response to {cmd}")
        resp = p.value or {}
        if not resp.get("ok"):
            with self._plock:
                self._motions.pop(rid, None)
            raise BodyError(resp.get("err", "error"), resp.get("msg", ""), resp)
        return resp

    # --- heartbeat --------------------------------------------------------------
    def start_heartbeat(self, interval_s: float = 1.0):
        self._hb_stop.clear()

        def loop():
            while not self._hb_stop.wait(interval_s):
                if not self.connected:
                    break
                try:
                    self.request("ping", timeout=2.0)
                except BodyError:
                    pass
        self._hb = threading.Thread(target=loop, name="tappet-heartbeat", daemon=True)
        self._hb.start()

    def stop_heartbeat(self):
        self._hb_stop.set()

    # --- convenience --------------------------------------------------------------
    def hello(self) -> dict:
        return self.request("hello")

    def ping(self) -> dict:
        return self.request("ping")

    def status(self) -> dict:
        r = self.request("status")
        st = {k: v for k, v in r.items() if k not in ("id", "ok")}
        self.last_status = st
        return st

    def enable(self):
        return self.request("enable")

    def relax(self):
        return self.request("relax")

    def stop(self):
        return self.request("stop")

    def joints(self, q: dict, ms: float | None = None, wait: bool = True, timeout: float = 60):
        return self.request("joints", q=q, ms=ms, wait=wait, wait_timeout=timeout)

    def move(self, arm: str, x: float, y: float, z: float, ms: float | None = None,
             wait: bool = True, timeout: float = 60):
        return self.request("move", arm=arm, x=x, y=y, z=z, ms=ms, wait=wait, wait_timeout=timeout)

    def tap(self, x: float, y: float, z: float, arm: str = "auto", hold_ms: float | None = None,
            press_mm: float | None = None, approach_mm: float | None = None,
            ms: float | None = None, wait: bool = True, timeout: float = 60):
        return self.request("tap", arm=arm, x=x, y=y, z=z, hold_ms=hold_ms, press_mm=press_mm,
                            approach_mm=approach_mm, ms=ms, wait=wait, wait_timeout=timeout)

    def swipe(self, x0, y0, x1, y1, z, arm: str = "auto", ms: float | None = None,
              press_mm: float | None = None, approach_mm: float | None = None,
              wait: bool = True, timeout: float = 60):
        return self.request("swipe", arm=arm, x0=x0, y0=y0, x1=x1, y1=y1, z=z, ms=ms,
                            press_mm=press_mm, approach_mm=approach_mm, wait=wait,
                            wait_timeout=timeout)

    def neck(self, pan: float | None = None, tilt: float | None = None, ms: float | None = None,
             wait: bool = True, timeout: float = 30):
        return self.request("neck", pan=pan, tilt=tilt, ms=ms, wait=wait, wait_timeout=timeout)

    def home(self, ms: float | None = None, wait: bool = True, timeout: float = 30):
        return self.request("home", ms=ms, wait=wait, wait_timeout=timeout)

    def park(self, arm: str = "both", wait: bool = True, timeout: float = 30):
        return self.request("park", arm=arm, wait=wait, wait_timeout=timeout)

    def servo_raw(self, ch: int, us: float):
        return self.request("servo_raw", ch=ch, us=us)

    def calib_get(self) -> dict:
        return self.request("calib_get")["joints"]

    def calib_set(self, joints: dict, save: bool = False):
        return self.request("calib_set", joints=joints, save=save)

    def geom_get(self) -> dict:
        r = self.request("geom_get")
        return {"arms": r["arms"], "linkage": r["linkage"]}

    def geom_set(self, arms: dict | None = None, linkage: dict | None = None, save: bool = False):
        return self.request("geom_set", arms=arms, linkage=linkage, save=save)

    def reset_calib(self):
        return self.request("reset_calib")

    def fk(self, arm: str) -> tuple[float, float, float]:
        r = self.request("fk", arm=arm)
        return (r["x"], r["y"], r["z"])

    def ik(self, arm: str, x: float, y: float, z: float) -> dict:
        return self.request("ik", arm=arm, x=x, y=y, z=z)["q"]

    def watchdog(self, ms: int):
        return self.request("watchdog", ms=ms)

    def stream(self, hz: float):
        return self.request("stream", hz=hz)

    def phone_power(self, on: bool):
        return self.request("phone_power", on=on)

    def wait_idle(self, timeout: float = 30.0, poll: float = 0.05) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            st = self.status()
            if not st.get("busy"):
                return st
            if time.monotonic() > deadline:
                raise BodyError("timeout", "body still busy")
            time.sleep(poll)
