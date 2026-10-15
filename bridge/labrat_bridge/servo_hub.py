"""Servo hub (ESP32 + PCA9685, ../firmware/servo-hub) over USB serial: the `hub` device of docs/protocol.md §1.

The bridge does not know what the servos are for (the pipette, probe arm and stand drivers live in the cloud DO).
It only relays firmware lines (§4):

  * action `line {msg}`: assigns the firmware `id`, sends the line, resolves with the matching reply
    (a firmware `ok:false` reply becomes a failed result that still carries the reply as data);
  * firmware events (`{"ev":...}`) go upward as `{"t":"event","device":"hub","data":{...}}`;
  * while the DO link is up, `{"cmd":"ping"}` every 1 s; when it drops the pings stop and the firmware's 2 s
    watchdog relaxes every servo by itself.

`MockHubFirmware` emulates the firmware protocol (concurrent timed motions, preemption, `until`, inputs, estop,
watchdog, cfg) in-process, for the tests and for the cloud end-to-end run without hardware. Channel 0 drives a
simulated plunger that closes `syr_home` at its bottom stop, so a DO `pipette.home` terminates as on the bench.
"""
from __future__ import annotations

import itertools
import json
import os
import queue
import threading
import time
from typing import Any, Callable

from .common import Device, DeviceError, log

FW_PREFIX = "servo-hub"
BAUD = 115200
MAX_LINE = 256              # firmware limit, bytes including the '\n'
PING_S = 1.0
DEFAULT_TIMEOUT_S = 5.0
MOTION_SLACK_S = 5.0        # reply timeout on top of a timed command's `ms`


# ------------------------------------------------------------------------------------------------ discovery
def _open_quiet(port: str, timeout: float = 0.2):
    """Open a port without pulsing DTR/RTS where the OS allows it (an ESP32 DevKit resets on that)."""
    import serial

    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, BAUD, timeout
    s.dtr = False
    s.rts = False
    s.open()
    return s


def probe_hub(port: str) -> dict | None:
    """The hub's `hello` reply if the port answers with fw `servo-hub...`, else None."""
    try:
        with _open_quiet(port) as s:
            time.sleep(1.2)  # in case the board reset anyway: let it boot
            s.reset_input_buffer()
            for _ in range(2):
                s.write(b'{"id":1,"cmd":"hello"}\n')
                deadline = time.monotonic() + 1.2
                while time.monotonic() < deadline:
                    line = s.readline()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(msg, dict) and str(msg.get("fw", "")).startswith(FW_PREFIX):
                        return msg
    except Exception:
        return None
    return None


# ------------------------------------------------------------------------------------------------ transports
class SerialTransport:
    def __init__(self, port: str):
        self.desc = port
        self.ser = _open_quiet(port)
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


class _Pending:
    __slots__ = ("event", "value")

    def __init__(self):
        self.event = threading.Event()
        self.value: dict | None = None


class HubLink:
    """Request/reply matching over a line transport (anything with readline() -> bytes and write(bytes))."""

    def __init__(self, transport, on_event: Callable[[dict], None] | None = None):
        self.t = transport
        self.on_event = on_event or (lambda ev: None)
        self._ids = itertools.count(1)
        self._wlock = threading.Lock()
        self._plock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self.connected = True
        self._reader = threading.Thread(target=self._read_loop, name="hub-rx", daemon=True)
        self._reader.start()

    def _next_id(self) -> int:
        i = next(self._ids)
        if i >= 2**31 - 1:
            self._ids = itertools.count(2)
            i = 1
        return i

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
                log.debug("hub: stray line %r", raw[:80])
                continue
            if not isinstance(msg, dict):
                continue
            if "ev" in msg:
                try:
                    self.on_event(msg)
                except Exception as e:
                    log.warning("hub event handler: %s", e)
                continue
            rid = msg.get("id")
            with self._plock:
                p = self._pending.pop(rid, None) if isinstance(rid, int) else None
            if p:
                p.value = msg
                p.event.set()
            elif msg.get("ok") is False:
                log.warning("hub: %s", msg)
        self.connected = False
        self._fail_all("hub link closed")

    def _fail_all(self, why: str):
        with self._plock:
            items = list(self._pending.values())
            self._pending.clear()
        for p in items:
            p.value = {"ok": False, "err": "link_down", "_why": why}
            p.event.set()

    def _write(self, obj: dict) -> None:
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        if len(data) > MAX_LINE:
            raise DeviceError(f"hub line too long ({len(data)} > {MAX_LINE} bytes)")
        if not self.connected:
            raise DeviceError("hub not connected")
        with self._wlock:
            try:
                self.t.write(data)
            except Exception as e:
                self.connected = False
                raise DeviceError(f"hub write failed: {e}")

    def request(self, msg: dict, timeout: float = DEFAULT_TIMEOUT_S) -> dict:
        rid = self._next_id()
        line = {**msg, "id": rid}
        p = _Pending()
        with self._plock:
            self._pending[rid] = p
        try:
            self._write(line)
        except DeviceError:
            with self._plock:
                self._pending.pop(rid, None)
            raise
        if not p.event.wait(timeout):
            with self._plock:
                self._pending.pop(rid, None)
            raise DeviceError(f"hub: no reply to {msg.get('cmd')} (id {rid}) within {timeout:.1f}s")
        if p.value and p.value.get("err") == "link_down" and "_why" in p.value:
            raise DeviceError(p.value["_why"])
        return p.value or {}

    def notify(self, msg: dict) -> None:
        """Fire-and-forget line without an id (the keepalive ping)."""
        self._write(msg)

    def close(self):
        self.connected = False
        try:
            self.t.close()
        except Exception:
            pass
        self._fail_all("hub link closed")


def timeout_for(msg: dict) -> float:
    if isinstance(msg.get("timeout_ms"), (int, float)) and msg["timeout_ms"] > 0:
        return msg["timeout_ms"] / 1000.0
    ms = msg.get("ms")
    if isinstance(ms, (int, float)) and ms > 0:
        return ms / 1000.0 + MOTION_SLACK_S
    return DEFAULT_TIMEOUT_S


# ------------------------------------------------------------------------------------------------ device
class ServoHub(Device):
    kind = "servo_hub"
    # Timed motions on different channels run concurrently in the firmware: never serialise them here.
    unlocked = ("line", "stop", "read")

    def __init__(self, dev_id: str = "hub", port: str | None = None, mock: bool = False, info: dict | None = None,
                 firmware: "MockHubFirmware | None" = None):
        super().__init__(dev_id, mock, info)
        self.inputs: dict[str, int] = {}
        self.last_event: str | None = None
        self.link_up = False
        self._stop = threading.Event()
        if mock:
            self.firmware = firmware or MockHubFirmware()
            transport = self.firmware
            self.info.setdefault("port", "mock")
        else:
            if not port:
                raise DeviceError("no servo hub port")
            self.firmware = None
            transport = SerialTransport(port)
            self.info["port"] = port
        self.link = HubLink(transport, on_event=self._on_event)
        hello = None
        deadline = time.monotonic() + 4.0
        while hello is None:
            try:
                hello = self.link.request({"cmd": "hello"}, timeout=1.0)
            except DeviceError:
                if time.monotonic() > deadline:
                    self.link.close()
                    raise
        self.info.update({k: hello[k] for k in ("fw", "board", "channels", "inputs", "adc") if k in hello})
        try:
            r = self.link.request({"cmd": "read"})
            self.inputs = dict(r.get("inputs") or {})
        except DeviceError:
            pass
        self._pinger = threading.Thread(target=self._ping_loop, name="hub-ping", daemon=True)
        self._pinger.start()

    # --- link to the DO -------------------------------------------------------------------------
    def on_link(self, up: bool) -> None:
        self.link_up = up

    def _ping_loop(self):
        while not self._stop.wait(PING_S):
            if self.link_up and self.link.connected:
                try:
                    self.link.notify({"cmd": "ping"})
                except DeviceError as e:
                    log.warning("hub ping: %s", e)

    # --- firmware -> DO ------------------------------------------------------------------------
    def _on_event(self, ev: dict) -> None:
        name = ev.get("ev")
        if name == "in" and "name" in ev:
            self.inputs[ev["name"]] = ev.get("v")
        elif name == "estop":
            self.inputs["estop"] = ev.get("v")
        elif name == "wd":
            log.warning("servo hub watchdog fired: servos relaxed")
        self.last_event = name
        self.emit(ev)

    # --- actions ---------------------------------------------------------------------------------
    def a_line(self, msg: dict | None = None, **_):
        if not isinstance(msg, dict) or not isinstance(msg.get("cmd"), str):
            raise DeviceError("line needs args.msg {cmd, ...}")
        timeout = timeout_for(msg)
        body = {k: v for k, v in msg.items() if k not in ("id", "timeout_ms")}
        reply = self.link.request(body, timeout=timeout)
        if isinstance(reply.get("inputs"), dict):
            self.inputs.update(reply["inputs"])
        if reply.get("ok") is False:
            raise DeviceError(f"hub {msg['cmd']}: {reply.get('err', 'error')}", data=reply)
        return reply

    def a_stop(self):
        return self.a_line({"cmd": "stop"})

    def a_read(self):
        return self.a_line({"cmd": "read"})

    def state(self) -> dict:
        return {"connected": self.link.connected, "inputs": dict(self.inputs), "last_event": self.last_event}

    def safe_stop(self) -> None:
        try:
            self.link.request({"cmd": "stop"}, timeout=1.0)
        except DeviceError:
            pass

    def close(self) -> None:
        self._stop.set()
        self.link.close()


# ------------------------------------------------------------------------------------------------ mock firmware
class MockHubFirmware:
    """In-process emulation of firmware/servo-hub (protocol.md §4), exposed as a line transport."""

    FW = "servo-hub/1.0.0-mock"
    INPUTS = ("syr_home", "estop", "in1", "in2")
    ADCS = ("a0", "a1")
    WATCHDOG_S = 2.0
    SETTLE_S = 0.1
    TICK_S = 0.005
    # simulated plunger on ch0: mm/s per us away from neutral (≈1.4 mm/s at 1640 us), travel 0..stroke
    PLUNGER_MM_PER_S_PER_US = 0.01
    PLUNGER_STROKE_MM = 30.0

    def __init__(self, speedup: float | None = None):
        self.speedup = float(speedup or os.environ.get("LABRAT_MOCK_HUB_SPEEDUP", 1.0))
        self.out: queue.Queue[bytes] = queue.Queue()
        self.lock = threading.RLock()
        self.cfg = [self._default_cfg(ch) for ch in range(16)]
        self.us = [0] * 16
        self.last_us = [0] * 16
        self.motion: list[dict | None] = [None] * 16
        self.inputs = {n: 0 for n in self.INPUTS}
        self.adc = {n: 0 for n in self.ADCS}
        self.plunger_mm = 10.0
        self.wd_count = 0
        self.lines_seen: list[dict] = []
        self.last_line = None
        self.wd_tripped = False
        self._closed = False
        self._buf = b""
        self.t0 = time.monotonic()
        self._emit({"ev": "boot", "fw": self.FW})
        self._thread = threading.Thread(target=self._run, name="mock-hub-fw", daemon=True)
        self._thread.start()

    @staticmethod
    def _default_cfg(ch: int) -> dict:
        if ch == 0:
            return {"min_us": 700, "max_us": 2300, "rest_us": 1500, "kind": "cont"}
        return {"min_us": 500, "max_us": 2500, "rest_us": 0, "kind": "pos"}

    def _now(self) -> float:
        return (time.monotonic() - self.t0) * self.speedup

    # --- transport interface -----------------------------------------------------------------
    def readline(self) -> bytes:
        while not self._closed:
            try:
                return self.out.get(timeout=0.2)
            except queue.Empty:
                continue
        return b""

    def write(self, data: bytes) -> None:
        self._buf += data
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip(b"\r")
            if line:
                self._line(line)

    def close(self) -> None:
        self._closed = True

    # --- test hooks ----------------------------------------------------------------------------
    def set_input(self, name: str, v: int) -> None:
        """Change a (debounced) input as if the switch moved; `estop` 1 = engaged."""
        with self.lock:
            if self.inputs.get(name) == v:
                return
            self.inputs[name] = v
            if name == "estop":
                if v:
                    self._relax_all("estop", settle=False)
                self._emit({"ev": "estop", "v": v})
            else:
                self._emit({"ev": "in", "name": name, "v": v})

    # --- protocol ------------------------------------------------------------------------------
    def _emit(self, obj: dict) -> None:
        self.out.put((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def _reply(self, rid, ok=True, **kw):
        obj = {"id": rid} if rid is not None else {}
        obj["ok"] = ok
        obj.update(kw)
        self._emit(obj)

    def _line(self, raw: bytes) -> None:
        with self.lock:
            self.last_line = self._now()
            self.wd_tripped = False
            if len(raw) + 1 > MAX_LINE:
                return self._reply(None, False, err="too_long")
            try:
                req = json.loads(raw)
                assert isinstance(req, dict)
            except Exception:
                return self._reply(None, False, err="json")
            self.lines_seen.append(req)
            rid = req.get("id") if isinstance(req.get("id"), int) else None
            cmd = req.get("cmd")
            fn = getattr(self, "_c_" + str(cmd), None)
            if cmd == "ping":
                if rid is not None:
                    self._reply(rid)
                return
            if fn is None:
                return self._reply(rid, False, err="unknown_cmd")
            fn(rid, req)

    def _ch(self, req) -> int | None:
        ch = req.get("ch")
        return ch if isinstance(ch, int) and not isinstance(ch, bool) and 0 <= ch < 16 else None

    def _ms(self, req) -> int | None:
        ms = req.get("ms", 0)
        if not isinstance(ms, (int, float)) or ms < 0 or ms > 60000:
            return None
        return int(round(ms))

    def _cancel(self, ch: int, why: str) -> None:
        m = self.motion[ch]
        if m and m.get("rid", "none") != "none":
            self._reply(m["rid"], False, err=why)
        self.motion[ch] = None

    def _set(self, ch: int, us: int) -> None:
        self.us[ch] = int(us)
        if us:
            self.last_us[ch] = int(us)

    def _relax_all(self, why: str, settle: bool) -> None:
        now = self._now()
        for ch in range(16):
            self._cancel(ch, why)
            c = self.cfg[ch]
            if settle and c["kind"] == "cont" and c["rest_us"] and self.us[ch]:
                self._set(ch, c["rest_us"])
                self.motion[ch] = {"mode": "settle", "t0": now, "dur": self.SETTLE_S, "rid": "none"}
            else:
                self._set(ch, 0)

    def _c_hello(self, rid, req):
        self._reply(rid, fw=self.FW, board="mock", channels=16, inputs=list(self.INPUTS), adc=list(self.ADCS),
                    uptime_ms=int(self._now() * 1000), pwm=True, estop=self.inputs["estop"])

    def _c_servo(self, rid, req):
        ch, ms = self._ch(req), self._ms(req)
        deg = req.get("deg")
        if ch is None:
            return self._reply(rid, False, err="ch")
        if not isinstance(deg, (int, float)):
            return self._reply(rid, False, err="args")
        if not 0 <= deg <= 180 or ms is None:
            return self._reply(rid, False, err="range")
        c = self.cfg[ch]
        if c["kind"] == "cont":
            return self._reply(rid, False, err="kind")
        if self.inputs["estop"]:
            return self._reply(rid, False, err="estop")
        self._cancel(ch, "preempted")
        target = c["min_us"] + (c["max_us"] - c["min_us"]) * deg / 180.0
        if not ms:
            self._set(ch, round(target))
            return self._reply(rid, deg=deg)
        frm = self.us[ch] or self.last_us[ch] or round(target)
        self._set(ch, frm)
        self.motion[ch] = {"mode": "ease", "t0": self._now(), "dur": ms / 1000.0, "from": frm, "to": target,
                           "deg": deg, "rid": rid}

    def _c_pulse(self, rid, req):
        ch, ms = self._ch(req), self._ms(req)
        us = req.get("us")
        if ch is None:
            return self._reply(rid, False, err="ch")
        if not isinstance(us, (int, float)):
            return self._reply(rid, False, err="args")
        c = self.cfg[ch]
        if not (us == 0 or c["min_us"] <= us <= c["max_us"]) or ms is None:
            return self._reply(rid, False, err="range")
        until = req.get("until")
        if until is not None:
            if until not in self.inputs:
                return self._reply(rid, False, err="input")
            if not ms:
                return self._reply(rid, False, err="until_needs_ms")
        if self.inputs["estop"] and us:
            return self._reply(rid, False, err="estop")
        self._cancel(ch, "preempted")
        us = int(round(us))
        if not ms:
            self._set(ch, us)
            return self._reply(rid, us=us)
        if until and self.inputs[until]:
            self._set(ch, c["rest_us"])
            return self._reply(rid, us=us, ms=0, stopped_by=until)
        self._set(ch, us)
        self.motion[ch] = {"mode": "hold", "t0": self._now(), "dur": ms / 1000.0, "us": us, "until": until,
                           "rid": rid}

    def _c_off(self, rid, req):
        if req.get("ch") is not None:
            ch = self._ch(req)
            if ch is None:
                return self._reply(rid, False, err="ch")
            self._cancel(ch, "preempted")
            self._set(ch, 0)
        else:
            for ch in range(16):
                self._cancel(ch, "stopped")
                self._set(ch, 0)
        self._reply(rid)

    def _c_stop(self, rid, req):
        self._relax_all("stopped", settle=True)
        self._reply(rid)

    def _c_cfg(self, rid, req):
        ch = self._ch(req)
        if ch is None:
            return self._reply(rid, False, err="ch")
        c = self._default_cfg(ch) if req.get("reset") else dict(self.cfg[ch])
        for k in ("min_us", "max_us", "rest_us"):
            v = req.get(k)
            if v is None:
                continue
            if not isinstance(v, (int, float)) or not ((k == "rest_us" and v == 0) or 400 <= v <= 2600):
                return self._reply(rid, False, err="range")
            c[k] = int(round(v))
        if req.get("kind") is not None:
            if req["kind"] not in ("pos", "cont"):
                return self._reply(rid, False, err="kind")
            c["kind"] = req["kind"]
        if c["min_us"] >= c["max_us"] or (c["rest_us"] and not c["min_us"] <= c["rest_us"] <= c["max_us"]):
            return self._reply(rid, False, err="range")
        if c != self.cfg[ch]:
            self._cancel(ch, "preempted")
            self.cfg[ch] = c
        self._reply(rid, cfg={"ch": ch, **c})

    def _c_read(self, rid, req):
        self._reply(rid, inputs=dict(self.inputs), adc=dict(self.adc), ch=list(self.us))

    # --- the "loop()" --------------------------------------------------------------------------
    def _run(self):
        last = self._now()
        while not self._closed:
            time.sleep(self.TICK_S)
            with self.lock:
                now = self._now()
                self._plunger(now - last)
                last = now
                for ch in range(16):
                    m = self.motion[ch]
                    if not m:
                        continue
                    el = now - m["t0"]
                    if m["mode"] == "hold":
                        hit = m["until"] and self.inputs[m["until"]]
                        if hit or el >= m["dur"]:
                            self.motion[ch] = None
                            self._set(ch, self.cfg[ch]["rest_us"])
                            self._reply(m["rid"], us=m["us"], ms=int(min(el, m["dur"]) * 1000),
                                        stopped_by=m["until"] if hit else None)
                    elif m["mode"] == "ease":
                        if el >= m["dur"]:
                            self.motion[ch] = None
                            self._set(ch, round(m["to"]))
                            self._reply(m["rid"], deg=m["deg"])
                        else:
                            x = el / m["dur"]
                            self._set(ch, round(m["from"] + (m["to"] - m["from"]) * x * x * (3 - 2 * x)))
                    elif m["mode"] == "settle" and el >= m["dur"]:
                        self.motion[ch] = None
                        self._set(ch, 0)
                if self.last_line is not None and not self.wd_tripped and now - self.last_line >= self.WATCHDOG_S:
                    self.wd_tripped = True
                    self.wd_count += 1
                    self._relax_all("watchdog", settle=True)
                    self._emit({"ev": "wd"})

    def _plunger(self, dt: float) -> None:
        c = self.cfg[0]
        us = self.us[0]
        if c["kind"] != "cont" or not us or not c["rest_us"]:
            return
        self.plunger_mm += (us - c["rest_us"]) * self.PLUNGER_MM_PER_S_PER_US * dt
        self.plunger_mm = max(0.0, min(self.PLUNGER_STROKE_MM, self.plunger_mm))
        home = 1 if self.plunger_mm <= 0.0 else 0
        if home != self.inputs["syr_home"]:
            self.set_input("syr_home", home)
