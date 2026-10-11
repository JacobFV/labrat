"""GRBL 1.1 (3018 CNC) driver: validation, feed caps, soft limits, char-counting streaming."""
from __future__ import annotations

import math
import re
import threading
import time
from collections import deque

from .common import Device, DeviceError, load_json, log

LIMITS = {"x": (0.0, 300.0), "y": (0.0, 180.0), "z": (-45.0, 0.0)}
FEED_XY_MAX, FEED_Z_MAX = 1500.0, 500.0
DEFAULT_FEED = 1000.0
RX_BUF = 127
ALLOWED = {"G0", "G1", "G4", "G90", "G91", "G21", "G28", "M3", "M5", "$H", "$X"}
DEFAULT_CALIB = {"zero_after_home": [0.0, 0.0, 0.0], "home_if_enabled": True}

_WORD = re.compile(r"([A-Z])\s*([-+]?\d*\.?\d+)")


def _norm_code(letter: str, num: str) -> str:
    v = float(num)
    return f"{letter}{int(v)}" if v == int(v) else f"{letter}{v:g}"


def validate_gcode(lines: list[str], pos: tuple[float, float, float], absolute: bool = True):
    """Return (out_lines, segments, end_pos, absolute). Raises DeviceError on anything not allowed
    or out of bounds. G0 is rewritten to G1 with a capped feed; every motion line gets an F."""
    x, y, z = pos
    out, segs = [], []
    feed = DEFAULT_FEED
    for raw in lines:
        line = raw.split(";")[0].split("(")[0].strip().upper()
        if not line:
            continue
        if line in ("$H", "$X"):
            out.append(line)
            segs.append(("sys", line))
            continue
        if line.startswith("$"):
            raise DeviceError(f"gcode not allowed: {raw!r}")
        words = _WORD.findall(line.replace(" ", ""))
        if "".join(a + b for a, b in words) != line.replace(" ", ""):
            raise DeviceError(f"cannot parse gcode: {raw!r}")
        codes = [_norm_code(a, b) for a, b in words if a in "GM"]
        params = {a: float(b) for a, b in words if a not in "GM"}
        for c in codes:
            if c not in ALLOWED:
                raise DeviceError(f"gcode not allowed: {c} in {raw!r}")
        bad = set(params) - set("XYZFPS")
        if bad:
            raise DeviceError(f"gcode params not allowed: {sorted(bad)} in {raw!r}")
        if "G90" in codes:
            absolute = True
        if "G91" in codes:
            absolute = False
        if "F" in params:
            feed = params["F"]
        motion = [c for c in codes if c in ("G0", "G1")]
        if "G4" in codes:
            p = params.get("P", 0)
            if not 0 <= p <= 60:
                raise DeviceError("G4 dwell must be 0..60 s")
            out.append(f"G4 P{p:g}")
            segs.append(("dwell", p))
            continue
        if "G28" in codes:  # go to safe Z then origin, explicitly bounded
            out += [f"G90 G1 Z0 F{FEED_Z_MAX:g}", f"G1 X0 Y0 F{FEED_XY_MAX:g}"]
            segs += [("move", (x, y, 0.0), FEED_Z_MAX), ("move", (0.0, 0.0, 0.0), FEED_XY_MAX)]
            x, y, z = 0.0, 0.0, 0.0
            if not absolute:
                out.append("G91")
            continue
        has_axis = any(a in params for a in "XYZ")
        if motion or (has_axis and not codes):
            nx = params.get("X", x if absolute else 0.0)
            ny = params.get("Y", y if absolute else 0.0)
            nz = params.get("Z", z if absolute else 0.0)
            if not absolute:
                nx, ny, nz = x + nx, y + ny, z + nz
            check_bounds(nx, ny, nz)
            f = cap_feed(feed, (x, y, z), (nx, ny, nz))
            mode = "G90" if absolute else "G91"
            axes = " ".join(f"{a}{params[a]:g}" for a in "XYZ" if a in params)
            extra = [c for c in codes if c not in ("G0", "G1", "G90", "G91")]
            out.append(" ".join(extra + [mode, "G1", axes, f"F{f:g}"]))
            segs.append(("move", (nx, ny, nz), f))
            x, y, z = nx, ny, nz
            continue
        # modal-only lines (G90/G91/G21/M3/M5)
        out.append(" ".join(codes + [f"{k}{v:g}" for k, v in params.items() if k in "SF"]))
        segs.append(("modal", codes))
    return out, segs, (x, y, z), absolute


def check_bounds(x, y, z):
    for a, v in zip("xyz", (x, y, z)):
        lo, hi = LIMITS[a]
        if not lo - 1e-6 <= v <= hi + 1e-6:
            raise DeviceError(f"{a.upper()}={v:g} outside soft limits {lo:g}..{hi:g}")


def cap_feed(f, a, b):
    f = float(f or DEFAULT_FEED)
    if abs(b[2] - a[2]) > 1e-6:
        return min(f, FEED_Z_MAX)
    return min(f, FEED_XY_MAX)


class GrblBase(Device):
    kind = "grbl"
    unlocked = ("stop", "status")

    def __init__(self, dev_id="cnc", mock=False, info=None):
        super().__init__(dev_id, mock, info)
        self.calib = load_json("cnc.json", DEFAULT_CALIB)
        self.st = {"state": "Idle", "wpos": [0.0, 0.0, 0.0], "mpos": [0.0, 0.0, 0.0], "homed": False}
        self._abort = threading.Event()

    @property
    def pos(self):
        return tuple(self.st["wpos"])

    def _run(self, lines):
        out, segs, end, _ = validate_gcode(lines, self.pos)
        self._abort.clear()
        self._execute(out, segs)
        return {"pos": self.st["wpos"], "lines": out}

    def a_move(self, x=None, y=None, z=None, f=DEFAULT_FEED):
        cx, cy, cz = self.pos
        tgt = (cx if x is None else float(x), cy if y is None else float(y), cz if z is None else float(z))
        check_bounds(*tgt)
        # safe ordering: raise Z first if going up, lower last if going down
        lines = []
        if tgt[2] > cz:
            lines.append(f"G90 G21 G1 Z{tgt[2]:g} F{min(f, FEED_Z_MAX):g}")
        if (tgt[0], tgt[1]) != (cx, cy):
            lines.append(f"G90 G21 G1 X{tgt[0]:g} Y{tgt[1]:g} F{min(f, FEED_XY_MAX):g}")
        if tgt[2] < cz:
            lines.append(f"G90 G21 G1 Z{tgt[2]:g} F{min(f, FEED_Z_MAX):g}")
        return self._run(lines or ["G90 G21"])

    def a_jog(self, dx=0, dy=0, dz=0, f=DEFAULT_FEED):
        cx, cy, cz = self.pos
        return self.a_move(cx + float(dx), cy + float(dy), cz + float(dz), f)

    def a_gcode(self, lines):
        if isinstance(lines, str):
            lines = lines.splitlines()
        return self._run(list(lines))

    def a_status(self):
        return dict(self.st)

    def state(self):
        return dict(self.st)


class MockGrbl(GrblBase):
    def __init__(self, dev_id="cnc", info=None, speedup: float | None = None):
        super().__init__(dev_id, True, info)
        import os

        self.speedup = speedup or float(os.environ.get("LABRAT_MOCK_SPEEDUP", "1"))

    def _execute(self, out, segs):
        for s in segs:
            if self._abort.is_set():
                raise DeviceError("stopped")
            if s[0] == "move":
                tgt, f = s[1], s[2]
                a = self.pos
                d = math.dist(a, tgt)
                dur = d / f * 60.0 / self.speedup
                self.st["state"] = "Run"
                t0 = time.monotonic()
                while True:
                    k = min(1.0, (time.monotonic() - t0) / dur) if dur > 0 else 1.0
                    self.st["wpos"] = self.st["mpos"] = [round(a[i] + (tgt[i] - a[i]) * k, 3) for i in range(3)]
                    if k >= 1.0 or self._abort.is_set():
                        break
                    time.sleep(0.02)
                self.st["state"] = "Idle"
            elif s[0] == "dwell":
                time.sleep(s[1] / self.speedup)
            elif s == ("sys", "$H"):
                self.a_home()

    def a_home(self):
        self.st["state"] = "Home"
        time.sleep(0.3 / self.speedup)
        self.st["wpos"] = self.st["mpos"] = [0.0, 0.0, 0.0]
        self.st.update(state="Idle", homed=True)
        return dict(self.st)

    def a_stop(self):
        self._abort.set()
        self.st["state"] = "Idle"
        return dict(self.st)

    def safe_stop(self):
        self.a_stop()


class Grbl(GrblBase):
    def __init__(self, port: str, dev_id="cnc", info=None):
        import serial

        super().__init__(dev_id, False, {"port": port, **(info or {})})
        self.ser = serial.Serial(port, 115200, timeout=0.1)
        self._wlock = threading.Lock()
        self._cv = threading.Condition()
        self.inflight: deque = deque()  # [len, line, result-holder]
        self.info_lines: list[str] = []
        self.alarm: str | None = None
        self._closed = False
        self.homing_enabled = False
        threading.Thread(target=self._reader, daemon=True, name="grbl-rx").start()
        self._connect()
        threading.Thread(target=self._poller, daemon=True, name="grbl-poll").start()

    # --- low level
    def _write(self, b: bytes):
        with self._wlock:
            self.ser.write(b)

    def _reader(self):
        while not self._closed:
            try:
                line = self.ser.readline().decode("ascii", "replace").strip()
            except Exception as e:
                self.error = f"serial: {e}"
                time.sleep(0.5)
                continue
            if not line:
                continue
            if line.startswith("<"):
                self._parse_status(line)
            elif line == "ok" or line.startswith("error"):
                with self._cv:
                    if self.inflight:
                        self.inflight.popleft()[2].append(line)
                    self._cv.notify_all()
            elif line.startswith("ALARM"):
                self.alarm = line
                self.st["state"] = "Alarm"
                log.warning("cnc %s", line)
            elif line.startswith("Grbl"):
                self.info["banner"] = line
                with self._cv:
                    for it in self.inflight:
                        it[2].append("error:reset")
                    self.inflight.clear()
                    self._cv.notify_all()
            else:
                self.info_lines.append(line)
                del self.info_lines[:-50]

    def _parse_status(self, line: str):
        parts = line.strip("<>").split("|")
        self.st["state"] = parts[0].split(":")[0]
        for p in parts[1:]:
            k, _, v = p.partition(":")
            vals = [float(t) for t in v.split(",")[:3]] if v else []
            if k == "MPos":
                self.st["mpos"] = vals
                wco = self.st.get("wco", [0.0, 0.0, 0.0])
                self.st["wpos"] = [round(vals[i] - wco[i], 3) for i in range(3)]
            elif k == "WPos":
                self.st["wpos"] = vals
            elif k == "WCO":
                self.st["wco"] = vals

    def _poller(self):
        while not self._closed:
            try:
                self._write(b"?")
            except Exception:
                pass
            time.sleep(0.2)

    def _send(self, line: str, timeout=30.0) -> str:
        holder: list[str] = []
        with self._cv:
            self.inflight.append([len(line) + 1, line, holder])
        self._write((line + "\n").encode())
        with self._cv:
            self._cv.wait_for(lambda: holder, timeout)
        return holder[0] if holder else "timeout"

    def _connect(self):
        self._write(b"\r\n\r\n")
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        self.info_lines.clear()
        self._send("$$", timeout=3)
        for l in self.info_lines:
            if l.startswith("$22="):
                self.homing_enabled = l.strip().endswith("1")
        self.info["homing_enabled"] = self.homing_enabled
        self._write(b"?")
        time.sleep(0.3)
        if self.st["state"] == "Alarm" or self.alarm:
            log.info("cnc in alarm -> $X")
            self._send("$X", timeout=3)
            self.alarm = None
        self._send("G90 G21", timeout=3)

    def _execute(self, out, segs):
        """Character-counting streaming, then wait for the planner to drain (G4 P0)."""
        if self.alarm or self.st["state"] == "Alarm":
            self._send("$X", timeout=3)
            self.alarm = None
        holders = []
        for line in out + ["G4 P0"]:
            if self._abort.is_set():
                raise DeviceError("stopped")
            n = len(line) + 1
            with self._cv:
                ok = self._cv.wait_for(lambda: sum(i[0] for i in self.inflight) + n <= RX_BUF or self._abort.is_set(), 60)
                if not ok:
                    raise DeviceError("cnc stream timeout")
                h: list[str] = []
                self.inflight.append([n, line, h])
                holders.append((line, h))
            self._write((line + "\n").encode())
        timeout = 600
        with self._cv:
            self._cv.wait_for(lambda: all(h for _, h in holders) or self._abort.is_set(), timeout)
        if self._abort.is_set():
            raise DeviceError("stopped")
        errs = [(l, h[0]) for l, h in holders if h and h[0] != "ok"]
        if errs:
            raise DeviceError(f"grbl errors: {errs}")

    def a_home(self):
        self._abort.clear()
        if self.homing_enabled and self.calib.get("home_if_enabled", True):
            r = self._send("$H", timeout=120)
            if r != "ok":
                raise DeviceError(f"homing failed: {r}")
            zx, zy, zz = self.calib["zero_after_home"]
            self._send(f"G10 L20 P1 X{zx:g} Y{zy:g} Z{zz:g}")
            self.st["homed"] = True
        else:  # homing disabled: assume zeroed; go to work origin via safe Z
            self._execute([f"G90 G21 G1 Z0 F{FEED_Z_MAX:g}", f"G1 X0 Y0 F{FEED_XY_MAX:g}"], [])
        return dict(self.st)

    def a_zero(self, x=0, y=0, z=0):
        """Declare the current position as work (x, y, z), default 0,0,0 (use when homing is disabled).
        Declaring a mid-range point (e.g. x=20, z=-10) leaves room to move left/up from where it sits."""
        x, y, z = float(x), float(y), float(z)
        check_bounds(x, y, z)
        self._send(f"G10 L20 P1 X{x:g} Y{y:g} Z{z:g}")
        time.sleep(0.3)
        return dict(self.st)

    def a_stop(self):
        self._abort.set()
        self._write(b"!")
        time.sleep(0.25)
        self._write(b"\x18")
        with self._cv:
            for it in self.inflight:
                it[2].append("error:stopped")
            self.inflight.clear()
            self._cv.notify_all()
        time.sleep(0.5)
        return dict(self.st)

    def safe_stop(self):
        try:
            self.a_stop()
        except Exception as e:
            log.error("cnc safe stop failed: %s", e)

    def close(self):
        self._closed = True
        try:
            self.ser.close()
        except Exception:
            pass


def probe_grbl(port: str) -> str | None:
    import serial

    try:
        with serial.Serial(port, 115200, timeout=0.2) as s:
            s.write(b"\r\n\r\n")
            deadline = time.monotonic() + 2.5
            buf = b""
            while time.monotonic() < deadline:
                buf += s.read(256)
                if b"Grbl" in buf:
                    i = buf.find(b"Grbl")
                    return buf[i:].split(b"\r")[0].split(b"\n")[0].decode("ascii", "replace")
                if b"ok" in buf or b"<Idle" in buf:  # already awake, no banner
                    s.write(b"$I\n")
            s.write(b"\x18")
            time.sleep(0.5)
            buf += s.read(512)
            if b"Grbl" in buf:
                return "Grbl"
    except Exception:
        return None
    return None
