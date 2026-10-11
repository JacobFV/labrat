"""PC stand-in for the Android head: the SPEC §6 web API over HTTP, backed by a BodyClient
(serial or simulator), serving ../web statically.

No camera and no agent on the PC: ``/api/camera.jpg`` and ``/api/chat`` return 501,
``/api/say`` prints to the console.
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import keyboard
from .calibration import (CalibrationError, auto_calibrate, default_calibration, load_calibration,
                          merge_calibration, save_calibration, uv_to_world)
from .client import BodyClient, BodyError
from .config import load_config, web_root
from .taptest import run_taptest, write_report
from .touch import TouchLog
from .workspace import out_dir

HTTP_FOR_ERR = {"unreachable": 400, "limit": 400, "bad_args": 400, "bad_json": 400,
                "unknown_cmd": 400, "not_enabled": 409, "queue_full": 429, "fault": 500,
                "timeout": 504, "link_down": 503, "no_touch": 422, "not_implemented": 501,
                "unauthorized": 401, "bad_pin": 403, "not_found": 404}

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


class ApiError(Exception):
    def __init__(self, err: str, msg: str = "", status: int | None = None):
        super().__init__(msg or err)
        self.err = err
        self.msg = msg or err
        self.status = status or HTTP_FOR_ERR.get(err, 400)


class Bridge:
    def __init__(self, body_url: str | None = "sim", *, client_factory: Callable[[], BodyClient] | None = None,
                 host: str = "0.0.0.0", port: int = 8080, pin: str | None = None,
                 calib_path: str | Path | None = None, web: str | Path | None = None,
                 cfg: dict | None = None, touch_log: TouchLog | None = None,
                 report_dir: str | Path | None = None, log: Callable[[str], None] = print):
        self.cfg = cfg if cfg is not None else load_config()
        self.body_url = body_url
        self.client_factory = client_factory or (lambda: BodyClient.connect(body_url, heartbeat_s=1.0))
        self.pin = pin or f"{secrets.randbelow(10 ** 6):06d}"
        self.tokens: set[str] = set()
        self.calib_path = Path(calib_path) if calib_path else None
        self.calibration = (load_calibration(self.calib_path, self.cfg) if self.calib_path
                            else default_calibration(self.cfg))
        self.web = Path(web) if web else web_root()
        self.touch_log = touch_log or TouchLog()
        self.report_dir = Path(report_dir) if report_dir else out_dir()
        self.log = log
        self.clock: Callable[[], float] = time.monotonic  # cycle-time clock (sim: simulated)
        self.client: Optional[BodyClient] = None
        self.link_error: Optional[str] = None
        self._link_lock = threading.Lock()
        self._op_lock = threading.Lock()
        self._stop = threading.Event()
        self.httpd = ThreadingHTTPServer((host, port), _make_handler(self))
        self.httpd.daemon_threads = True
        self._threads: list[threading.Thread] = []

    # --- lifecycle --------------------------------------------------------------
    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def start(self) -> "Bridge":
        self.ensure_link()
        t = threading.Thread(target=self._link_watch, daemon=True, name="tappet-bridge-link")
        t.start()
        h = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="tappet-bridge-http")
        h.start()
        self._threads += [t, h]
        return self

    def serve_forever(self):
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self):
        self._stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.client:
            self.client.close()

    def ensure_link(self) -> Optional[BodyClient]:
        with self._link_lock:
            if self.client and self.client.connected:
                return self.client
            if self.client:
                self.client.close()
                self.client = None
            try:
                self.client = self.client_factory()
                self.link_error = None
                self.log(f"[bridge] body link up: {self.client.desc}")
            except Exception as e:
                self.client = None
                self.link_error = str(e)
            return self.client

    def _link_watch(self):
        while not self._stop.wait(2.0):
            if not (self.client and self.client.connected):
                self.ensure_link()

    def body(self) -> BodyClient:
        c = self.client if (self.client and self.client.connected) else self.ensure_link()
        if c is None:
            raise ApiError("link_down", f"body not connected ({self.link_error})")
        return c

    # --- auth ----------------------------------------------------------------------
    def pair(self, pin: str) -> str:
        if not isinstance(pin, str) or not secrets.compare_digest(pin.strip(), self.pin):
            raise ApiError("bad_pin", "wrong PIN")
        tok = secrets.token_hex(16)
        self.tokens.add(tok)
        return tok

    def check_token(self, tok: Optional[str]) -> bool:
        return bool(tok) and any(secrets.compare_digest(tok, t) for t in self.tokens)

    # --- calibration ---------------------------------------------------------------
    def set_calibration(self, cal: dict):
        self.calibration = cal
        if self.calib_path:
            save_calibration(cal, self.calib_path)

    # --- API implementations ----------------------------------------------------------
    def api_status(self) -> dict:
        c = self.client if (self.client and self.client.connected) else None
        body = None
        if c:
            try:
                body = c.status()
            except BodyError:
                body = c.last_status
        return {
            "link": {"connected": bool(c), "transport": c.desc if c else (self.body_url or ""),
                     **({"error": self.link_error} if self.link_error and not c else {})},
            "body": body,
            "calib": self.calibration,
            "agent": {"busy": False, "available": False},
            "camera": {"available": False},
            "touchpad": {"last_seq": self.touch_log.last_seq},
            "host": "python-bridge",
        }

    def api_cmd(self, req: dict) -> dict:
        if not isinstance(req, dict) or not isinstance(req.get("cmd"), str):
            raise ApiError("bad_args", "body must be a protocol request {cmd, ...}")
        args = {k: v for k, v in req.items() if k not in ("cmd", "id")}
        return self.body().request(req["cmd"], check=False, **args)

    def _uv(self, req, *keys):
        out = []
        for k in keys:
            v = req.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ApiError("bad_args", f"{k} must be a number")
            out.append(float(v))
        return out

    def api_tap(self, req: dict) -> dict:
        u, v = self._uv(req, "u", "v")
        x, y = uv_to_world(self.calibration, u, v)
        z = float(self.calibration["surface_z"])
        r = self.body().tap(x, y, z, arm=req.get("arm") or "auto", hold_ms=req.get("hold_ms"),
                            wait=True, timeout=30)
        return {"ok": True, "arm": r.get("arm"), "x": round(x, 2), "y": round(y, 2), "z": z}

    def api_swipe(self, req: dict) -> dict:
        u0, v0, u1, v1 = self._uv(req, "u0", "v0", "u1", "v1")
        x0, y0 = uv_to_world(self.calibration, u0, v0)
        x1, y1 = uv_to_world(self.calibration, u1, v1)
        z = float(self.calibration["surface_z"])
        r = self.body().swipe(x0, y0, x1, y1, z, arm=req.get("arm") or "auto", ms=req.get("ms"),
                              wait=True, timeout=30)
        return {"ok": True, "arm": r.get("arm")}

    def api_type(self, req: dict) -> dict:
        text = req.get("text")
        if not isinstance(text, str):
            raise ApiError("bad_args", "text must be a string")
        taps, skipped = keyboard.plan_taps(text)
        b = self.body()
        z = float(self.calibration["surface_z"])
        for (u, v) in taps:
            x, y = uv_to_world(self.calibration, u, v)
            b.tap(x, y, z, wait=True, timeout=30)
        return {"ok": True, "taps": len(taps), "skipped": skipped}

    def api_neck(self, req: dict) -> dict:
        pan, tilt = req.get("pan"), req.get("tilt")
        if pan is None and tilt is None:
            raise ApiError("bad_args", "pan and/or tilt required")
        self.body().request("neck", pan=pan, tilt=tilt, ms=req.get("ms"))
        return {"ok": True}

    def api_jog(self, req: dict) -> dict:
        arm = req.get("arm")
        if arm not in ("L", "R"):
            raise ApiError("bad_args", "arm must be L or R")
        dx, dy, dz = (float(req.get(k) or 0.0) for k in ("dx", "dy", "dz"))
        b = self.body()
        x, y, z = b.fk(arm)
        tgt = (x + dx, y + dy, z + dz)
        b.move(arm, *tgt, ms=req.get("ms"), wait=True, timeout=10)
        x, y, z = b.fk(arm)
        return {"ok": True, "x": x, "y": y, "z": z}

    def api_calibration_post(self, req: dict) -> dict:
        if not isinstance(req, dict):
            raise ApiError("bad_args", "calibration must be an object")
        try:
            cal = merge_calibration(self.calibration, req, self.cfg)
        except (ValueError, TypeError, KeyError) as e:
            raise ApiError("bad_args", str(e))
        self.set_calibration(cal)
        return cal

    def api_autocal(self, req: dict) -> dict:
        if not self._op_lock.acquire(blocking=False):
            raise ApiError("fault", "another calibration/test is running", 409)
        try:
            b = self.body()
            self._ensure_enabled(b)
            try:
                res = auto_calibrate(b, self.touch_log, self.calibration, arm=req.get("arm") or "auto",
                                     log=self.log)
            except CalibrationError as e:
                raise ApiError(e.code, e.msg)
            self.set_calibration(res["calibration"])
            return {"ok": True, **res}
        finally:
            self._op_lock.release()

    def api_taptest(self, req: dict) -> dict:
        n = req.get("n", 100)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 1000:
            raise ApiError("bad_args", "n must be an integer 1..1000")
        if not self._op_lock.acquire(blocking=False):
            raise ApiError("fault", "another calibration/test is running", 409)
        try:
            b = self.body()
            self._ensure_enabled(b)
            res = run_taptest(b, self.touch_log, self.calibration, n=n, arm=req.get("arm") or "auto",
                              seed=req.get("seed"), log=self.log, clock=self.clock)
            try:
                csv_p, json_p = write_report(res, self.report_dir)
                res["files"] = [str(csv_p), str(json_p)]
            except OSError:
                pass
            return {"ok": True, "results": res}
        finally:
            self._op_lock.release()

    def _ensure_enabled(self, b: BodyClient):
        st = b.status()
        if not st.get("enabled"):
            b.enable()


def _make_handler(bridge: Bridge):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TappetBridge/0.1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet
            pass

        # --- helpers
        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj):
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _err(self, e: ApiError):
            self._json(e.status, {"ok": False, "err": e.err, "msg": e.msg})

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                raise ApiError("bad_args", "body too large", 413)
            raw = self.rfile.read(n) if n else b""
            if not raw.strip():
                return {}
            try:
                return json.loads(raw)
            except ValueError:
                raise ApiError("bad_json", "invalid JSON body")

        def _token(self, q) -> Optional[str]:
            return self.headers.get("X-Tappet-Token") or (q.get("token") or [None])[0]

        # --- verbs
        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Tappet-Token")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def _dispatch(self, method: str):
            url = urlparse(self.path)
            path = url.path
            q = parse_qs(url.query)
            try:
                if not path.startswith("/api/"):
                    if method != "GET":
                        raise ApiError("not_found", "no such endpoint", 404)
                    return self._static(path)
                public = path in ("/api/pair", "/api/touchpad/event")
                if not public and not bridge.check_token(self._token(q)):
                    raise ApiError("unauthorized", "pair first (POST /api/pair {pin})")
                body = self._body() if method == "POST" else {}
                fn = ROUTES.get((method, path))
                if fn is None:
                    raise ApiError("not_found", f"no endpoint {method} {path}", 404)
                status, obj = fn(self, body, q)
                if isinstance(obj, (bytes, bytearray)):
                    self._send(status, bytes(obj), "image/jpeg")
                else:
                    self._json(status, obj)
            except ApiError as e:
                self._err(e)
            except BodyError as e:
                self._err(ApiError(e.err, e.msg))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # pragma: no cover
                traceback.print_exc()
                self._err(ApiError("fault", f"{type(e).__name__}: {e}", 500))

        def _static(self, path: str):
            rel = unquote(path).lstrip("/") or "index.html"
            root = bridge.web.resolve()
            p = (root / rel).resolve()
            if root not in p.parents and p != root:
                raise ApiError("not_found", "not found", 404)
            if p.is_dir():
                p = p / "index.html"
            if not p.is_file():
                raise ApiError("not_found", f"{path} not found", 404)
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith(("json", "javascript", "xml")):
                ctype += "; charset=utf-8"
            self._send(200, p.read_bytes(), ctype)

    def r_pair(h, b, q):
        return 200, {"token": bridge.pair(str(b.get("pin", "")))}

    def r_status(h, b, q):
        return 200, bridge.api_status()

    def r_cmd(h, b, q):
        return 200, bridge.api_cmd(b)

    def r_tap(h, b, q):
        return 200, bridge.api_tap(b)

    def r_swipe(h, b, q):
        return 200, bridge.api_swipe(b)

    def r_type(h, b, q):
        return 200, bridge.api_type(b)

    def r_neck(h, b, q):
        return 200, bridge.api_neck(b)

    def r_jog(h, b, q):
        return 200, bridge.api_jog(b)

    def r_camera(h, b, q):
        raise ApiError("not_implemented", "no camera on the Python bridge", 501)

    def r_say(h, b, q):
        bridge.log(f"[say] {b.get('text', '')}")
        return 200, {"ok": True}

    def r_chat(h, b, q):
        raise ApiError("not_implemented", "no agent on the Python bridge (use the Android head)", 501)

    def r_cal_get(h, b, q):
        return 200, bridge.calibration

    def r_cal_post(h, b, q):
        return 200, bridge.api_calibration_post(b)

    def r_autocal(h, b, q):
        return 200, bridge.api_autocal(b)

    def r_tp_event(h, b, q):
        try:
            ev = bridge.touch_log.add(float(b["x"]), float(b["y"]), float(b["w"]), float(b["h"]),
                                      b.get("t"))
        except (KeyError, TypeError, ValueError):
            raise ApiError("bad_args", "need numeric x, y, w, h")
        return 200, {"ok": True, "seq": ev["seq"]}

    def r_tp_events(h, b, q):
        try:
            since = int((q.get("since") or ["0"])[0])
        except ValueError:
            raise ApiError("bad_args", "since must be an integer")
        return 200, {"events": bridge.touch_log.since(since), "last": bridge.touch_log.last_seq}

    def r_taptest(h, b, q):
        return 200, bridge.api_taptest(b)

    ROUTES = {
        ("POST", "/api/pair"): r_pair,
        ("GET", "/api/status"): r_status,
        ("POST", "/api/cmd"): r_cmd,
        ("POST", "/api/tap"): r_tap,
        ("POST", "/api/swipe"): r_swipe,
        ("POST", "/api/type"): r_type,
        ("POST", "/api/neck"): r_neck,
        ("POST", "/api/jog"): r_jog,
        ("GET", "/api/camera.jpg"): r_camera,
        ("POST", "/api/say"): r_say,
        ("POST", "/api/chat"): r_chat,
        ("GET", "/api/calibration"): r_cal_get,
        ("POST", "/api/calibration"): r_cal_post,
        ("POST", "/api/calibrate/auto"): r_autocal,
        ("POST", "/api/touchpad/event"): r_tp_event,
        ("GET", "/api/touchpad/events"): r_tp_events,
        ("POST", "/api/taptest"): r_taptest,
    }
    return Handler


def sim_bridge(port: int = 8080, sim_port: int = 0, speed: float = 1.0, noise_deg: float = 0.0,
               backlash_deg: float = 0.0, offset=(0.0, 0.0), rot_deg: float = 0.0, **kw):
    """Start an in-process simulator and a bridge wired to it; the simulator's virtual
    touchscreen feeds the bridge's touch log (as if touchpad.html were open on the target)."""
    from .sim import start_sim

    srv = start_sim(port=sim_port, speed=speed, noise_deg=noise_deg, backlash_deg=backlash_deg,
                    offset=offset, rot_deg=rot_deg)
    hb = min(1.0, 0.5 / max(speed, 1e-3))  # keep the (simulated-time) watchdog fed
    url = srv.url
    br = Bridge(url, port=port, client_factory=lambda: BodyClient.connect(url, heartbeat_s=hb), **kw)
    br.clock = lambda: srv.sim.now_ms / 1000.0
    srv.sim.touchscreen.listeners.append(
        lambda ev: br.touch_log.add(ev["x"], ev["y"], ev["w"], ev["h"], time.time() * 1000.0, sim=True))
    return srv, br
