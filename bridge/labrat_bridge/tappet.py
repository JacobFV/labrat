"""Tappet ESP32 arms (JSON lines @115200) + the rack-and-pinion syringe servo on its PCA9685 channel 8."""
from __future__ import annotations

import json
import math
import os
import threading
import time

from .common import Device, DeviceError, load_json, log, save_json
from .tappet_client import MOTION_CMDS, BodyClient, BodyError

PIPETTE_CH = 8


def probe_tappet(port: str) -> dict | None:
    import serial

    try:
        with serial.Serial(port, 115200, timeout=0.2) as s:
            time.sleep(1.2)  # ESP32 may reset on open
            s.reset_input_buffer()
            for _ in range(2):
                s.write(b'{"id":1,"cmd":"hello"}\n')
                deadline = time.monotonic() + 1.2
                while time.monotonic() < deadline:
                    line = s.readline()
                    if b"tappet-fw" in line:
                        try:
                            return json.loads(line)
                        except ValueError:
                            return {"fw": "tappet-fw"}
    except Exception:
        return None
    return None


class Tappet(Device):
    kind = "tappet"
    unlocked = ("raw",)  # firmware serializes; allow stop/status while a motion runs

    def __init__(self, dev_id: str, port: str | None = None, mock: bool = False, info: dict | None = None):
        super().__init__(dev_id, mock, info)
        self.client: BodyClient | None = None
        self.mock_state = {"enabled": False, "last": None, "busy": False}
        self.info.setdefault("port", port or "mock")
        if not mock and port:
            # heartbeat ping every 1 s keeps the firmware's 5 s watchdog fed
            self.client = BodyClient.connect(port, 115200, heartbeat_s=1.0)
            deadline = time.monotonic() + 6
            while True:
                try:
                    self.info["hello"] = self.client.hello()
                    break
                except BodyError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.5)

    def request(self, cmd: str, **args):
        if self.client is None:
            if cmd in ("enable",):
                self.mock_state["enabled"] = True
            elif cmd in ("relax",):
                self.mock_state["enabled"] = False
            elif cmd == "servo_raw" and not self.mock_state["enabled"]:
                return {"ok": False, "err": "not_enabled", "msg": "send enable first"}
            if cmd in MOTION_CMDS:
                self.mock_state["busy"] = True
                time.sleep(min(float(args.get("ms") or 300), 3000) / 1000.0)
                self.mock_state["busy"] = False
            self.mock_state["last"] = {"cmd": cmd, **args}
            return {"ok": True, "mock": True, "cmd": cmd}
        wait = cmd in MOTION_CMDS
        try:
            return self.client.request(cmd, wait=wait, wait_timeout=60, **args)
        except BodyError as e:
            raise DeviceError(str(e))

    def a_raw(self, cmd: str, **args):
        args.pop("id", None)
        return self.request(cmd, **args)

    def state(self):
        if self.client is None:
            return {"connected": True, **self.mock_state}
        return {"connected": self.client.connected, "status": self.client.last_status}

    def safe_stop(self):
        try:
            self.request("stop")
        except Exception as e:
            log.warning("%s stop failed: %s", self.id, e)

    def close(self):
        if self.client:
            self.client.close()


# Rack-and-pinion syringe (cad/syringe): continuous-rotation 9 g servo -> 12T m1.5 pinion (r 9 mm) -> rack -> plunger.
# A continuous servo has no position feedback: us sets SPEED. Position is dead-reckoned from calibrated plunger
# speeds, re-zeroed by `home` (drive down into the bottom stop) and corrected by `observe` (camera / NN estimate).
# pos_mm = plunger height above its lowest point (0 = pressed, stroke_mm = fully drawn).
DEFAULT_SYR = {
    "neutral_us": 1500,       # servo stops here (trim the servo pot or this value until it does not creep)
    "deadband_us": 40,        # |us - neutral| below this does not turn the servo
    "span_us": 400,           # us above the deadband at full speed
    "dir": 1,                 # +1: us > neutral raises the plunger (aspirates); flip if the rack runs the wrong way
    "move_speed": 0.25,       # fraction of span for normal moves
    "mm_per_s_up": 6.0,       # measured plunger speed at move_speed, raising
    "mm_per_s_down": 6.0,     # ... lowering (load differs: calibrate both)
    "backlash_mm": 0.15,      # extra travel added on direction reversal
    "stroke_mm": 25.0,        # usable rack travel (cad/syringe syringe_stroke)
    "syringe_id_mm": 17.8,    # barrel bore -> ul_per_mm = pi/4 * id^2
    "ul_gain": 1.0,           # actual/geometric volume (air cushion compression, tip leakage), fit by weighing
    "ready_mm": 2.0,          # air cushion kept below the liquid: aspirate starts here, dispense returns here
    "max_ul": 200.0,          # tip capacity
    "home_speed": 0.15,       # slow drive into the bottom stop
    "home_s": 6.0,            # long enough to reach the stop from anywhere, short enough not to cook the servo
    "settle_ms": 120,
    "min_pulse_ms": 25,
}


class Syringe(Device):
    kind = "servo_syringe"
    unlocked = ("stop", "observe")  # stop must not queue behind a running move

    def __init__(self, dev_id: str = "pipette", host: Tappet | None = None, info: dict | None = None):
        mock = host is None or host.client is None
        super().__init__(dev_id, mock, info)
        self.host = host
        self.cal = load_json("syringe.json", DEFAULT_SYR)
        self.info.update(host=host.id if host else None, channel=PIPETTE_CH, ul_per_mm=round(self.ul_per_mm, 2))
        self.pos = 0.0          # dead-reckoned plunger height (mm)
        self.homed = False
        self.vol_ul = 0.0       # liquid in the tip
        self.last_dir = 0
        self.us_now = int(self.cal["neutral_us"])
        self.last_obs: dict | None = None
        self.moving = False
        self._enabled = False
        self._lk = threading.Lock()

    @property
    def ul_per_mm(self) -> float:
        d = float(self.cal["syringe_id_mm"])
        return math.pi / 4 * d * d * float(self.cal["ul_gain"])

    def us_for(self, speed: float) -> int:
        """speed -1..1 (+ = plunger up) -> pulse width."""
        c = self.cal
        speed = max(-1.0, min(1.0, float(speed)))
        if abs(speed) < 1e-6:
            return int(c["neutral_us"])
        mag = c["deadband_us"] + c["span_us"] * abs(speed)
        return int(round(c["neutral_us"] + c["dir"] * math.copysign(mag, speed)))

    def _pulse(self, us: int):
        if not self.mock:
            if not self._enabled:
                self.host.request("enable")
                self._enabled = True
            r = self.host.request("servo_raw", ch=PIPETTE_CH, us=int(us))
            if not r.get("ok", True):
                raise DeviceError(f"servo_raw failed: {r}")
        self.us_now = int(us)

    def _run(self, speed: float, seconds: float):
        """drive at speed for seconds, then always return to neutral (also on errors)."""
        try:
            self.moving = True
            self._pulse(self.us_for(speed))
            time.sleep(max(0.0, seconds) / self._speedup())
        finally:
            self._pulse(self.us_for(0))
            self.moving = False
        time.sleep(self.cal["settle_ms"] / 1000.0 / self._speedup())

    def _speedup(self) -> float:
        return float(os.environ.get("LABRAT_MOCK_SPEEDUP", "1")) if self.mock else 1.0

    def _rate(self, up: bool, speed: float) -> float:
        c = self.cal
        base = c["mm_per_s_up"] if up else c["mm_per_s_down"]
        return max(1e-3, base * speed / c["move_speed"])  # speed ~ linear in pulse offset above the deadband

    def _move_to(self, target: float, speed: float | None = None):
        c = self.cal
        target = float(target)
        if not (-1e-6 <= target <= c["stroke_mm"] + 1e-6):
            raise DeviceError(f"plunger target {target:.2f} mm outside 0..{c['stroke_mm']}")
        d = target - self.pos
        if abs(d) < 1e-4:
            return
        up = d > 0
        sp = float(speed or c["move_speed"])
        dist = abs(d)
        sign = 1 if up else -1
        if self.last_dir and self.last_dir != sign:
            dist += c["backlash_mm"]
        t = dist / self._rate(up, sp)
        if t * 1000 < c["min_pulse_ms"]:  # too short to resolve: slow down instead
            sp = max(0.02, sp * t * 1000 / c["min_pulse_ms"])
            t = dist / self._rate(up, sp)
        self._run(sign * sp, t)
        self.pos = target
        self.last_dir = sign

    # ---- actions ----
    def a_home(self):
        """drive slowly down into the bottom stop (fork on the barrel), then pos = 0."""
        with self._lk:
            self._run(-self.cal["home_speed"], self.cal["home_s"])
            self.pos, self.homed, self.last_dir, self.vol_ul = 0.0, True, -1, 0.0
        return self.state()

    def a_zero(self, mm: float = 0.0):
        """declare the current plunger height (after positioning by hand / drive)."""
        self.pos, self.homed = float(mm), True
        return self.state()

    def a_observe(self, mm: float, conf: float = 1.0):
        """external position estimate (camera + NN reading the rack): blend into the dead-reckoned position."""
        mm, conf = float(mm), max(0.0, min(1.0, float(conf)))
        err = mm - self.pos
        self.pos = max(0.0, min(self.cal["stroke_mm"], self.pos + conf * err))
        self.last_obs = {"mm": round(mm, 3), "conf": conf, "err_mm": round(err, 3), "ts": time.time()}
        return self.state()

    def a_move(self, mm: float, speed: float | None = None):
        with self._lk:
            self._move_to(mm, speed)
        return self.state()

    def a_jog(self, mm: float, speed: float | None = None):
        with self._lk:
            self._move_to(self.pos + float(mm), speed)
        return self.state()

    def a_drive(self, speed: float, ms: float):
        """raw open-loop drive for calibration (position is NOT tracked: home/zero/observe afterwards)."""
        ms = float(ms)
        if not 0 < ms <= 5000:
            raise DeviceError("ms must be in (0, 5000]")
        with self._lk:
            self._run(float(speed), ms / 1000.0)
            self.homed = False
        return self.state()

    def a_prime(self):
        """plunger to ready_mm (do this ABOVE the liquid): the air cushion the aspirate starts from."""
        with self._lk:
            if not self.homed:
                self._run(-self.cal["home_speed"], self.cal["home_s"])
                self.pos, self.homed, self.last_dir = 0.0, True, -1
            self._move_to(self.cal["ready_mm"])
        return self.state()

    def a_aspirate(self, ul: float):
        ul = float(ul)
        if ul <= 0 or self.vol_ul + ul > self.cal["max_ul"] + 1e-6:
            raise DeviceError(f"aspirate {ul} uL exceeds capacity ({self.vol_ul}/{self.cal['max_ul']})")
        with self._lk:
            if self.vol_ul == 0 and abs(self.pos - self.cal["ready_mm"]) > 1e-3:
                raise DeviceError("prime first (plunger not at ready_mm above the liquid)")
            self._move_to(self.pos + ul / self.ul_per_mm)
            self.vol_ul = round(self.vol_ul + ul, 2)
        return self.state()

    def a_dispense(self, ul: float | None = None):
        with self._lk:
            if ul is None or float(ul) >= self.vol_ul - 1e-6:
                self._move_to(self.cal["ready_mm"])
                self.vol_ul = 0.0
            else:
                ul = float(ul)
                self._move_to(max(self.cal["ready_mm"], self.pos - ul / self.ul_per_mm))
                self.vol_ul = round(self.vol_ul - ul, 2)
        return self.state()

    def a_blowout(self):
        """push the air cushion out too (plunger to 0) to clear the last drop."""
        with self._lk:
            self._move_to(0.0)
            self.vol_ul = 0.0
        return self.state()

    def a_rest(self):
        """stop the servo (neutral pulse). The rack holds through the servo gearbox."""
        self._pulse(self.us_for(0))
        return self.state()

    def a_stop(self):
        if self.moving:  # interrupted mid-move: the dead-reckoned position is no longer trustworthy
            self.homed = False
        return self.a_rest()

    def state(self):
        return {"pos_mm": round(self.pos, 3), "stroke_mm": self.cal["stroke_mm"], "vol_ul": self.vol_ul,
                "ul_per_mm": round(self.ul_per_mm, 2), "homed": self.homed, "moving": self.moving, "us": self.us_now, "obs": self.last_obs}

    def safe_stop(self):
        try:
            self._pulse(self.us_for(0))
        except Exception as e:
            log.warning("syringe stop failed: %s", e)
            try:  # no pulses at all also stops a continuous servo
                self.host.request("relax")
            except Exception:
                pass
