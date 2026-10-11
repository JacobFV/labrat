"""Firmware simulator: implements the body protocol of SPEC §4 over TCP (default port 7777).

* Same JSON shapes, error codes, queue semantics (depth 16, immediate ``queued`` response,
  ``done``/``error`` events), min-jerk interpolation at 100 Hz, Cartesian ``move`` / swipe stroke
  with IK per tick, ``tap``/``swipe`` sequences, auto arm selection + parking of the other arm,
  watchdog, status stream, button and brownout fault injection.
* Time runs in *simulated* milliseconds: one 10 ms tick per ``0.01/speed`` real seconds.
  (The watchdog is measured in simulated time too.)
* A :class:`VirtualTouchscreen` watches the (noisy) actual stylus tips and records a touch every
  time a tip goes below the true screen surface inside the true screen outline, so calibration
  and the tap test run fully in simulation.
"""
from __future__ import annotations

import copy
import json
import math
import random
import socketserver
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

from .calibration import apply_h, rigid_screen_h
from .config import ARM_JOINTS, ARM_NAMES, JOINT_NAMES, load_config
from .kinematics import Kinematics, KinematicsError

FW_NAME = "tappet-fw"
FW_VER = "0.1.0-sim"
PROTO = 1
TICK_MS = 10
QUEUE_DEPTH = 16
PARK_CLEAR_MM = 60.0
MIN_SEG_MS = 60
CART_SPEED = 150.0  # mm/s average for Cartesian segments without explicit ms
JOINT_FIELDS = ("ch", "gear", "dir", "s_zero", "neutral", "min", "max", "home", "park", "max_vel")
GEOM_FIELDS = ("base_x", "base_y", "shoulder_h", "r0", "L1", "L2", "tool_dx", "tool_dz")


class CmdError(Exception):
    def __init__(self, err: str, msg: str = "", **extra):
        super().__init__(msg or err)
        self.err = err
        self.msg = msg or err
        self.extra = extra


def minjerk(tau: float) -> float:
    tau = min(max(tau, 0.0), 1.0)
    return tau ** 3 * (10 - 15 * tau + 6 * tau * tau)


def _r(v: float, n: int = 2) -> float:
    v = round(float(v), n)
    return 0.0 if v == 0 else v


# ---------------------------------------------------------------------------
# Virtual touchscreen
# ---------------------------------------------------------------------------


class VirtualTouchscreen:
    """The target phone as the simulator sees it: a rectangle at ``surface_z``.

    ``offset`` (dx,dy mm) and ``rot_deg`` displace the *true* screen from the nominal
    tray/device geometry (to exercise auto-calibration).  Touches are reported in CSS points like
    touchpad.html would (``x = u*w, y = v*h``).
    """

    def __init__(self, cfg: dict, offset=(0.0, 0.0), rot_deg: float = 0.0,
                 surface_dz: float = 0.0, points_size=(393.0, 852.0)):
        dev = cfg["default_device"]
        tray = cfg["tray"]
        self.screen_w = float(dev["screen_w"])
        self.screen_l = float(dev["screen_l"])
        self.surface_z = float(dev["surface_z"]) + surface_dz
        self.H = rigid_screen_h(tray["center_x"] + offset[0], tray["center_y"] + offset[1],
                                self.screen_w, self.screen_l, rot_deg)
        self.Hinv = np.linalg.inv(self.H)
        self.w, self.h = points_size
        self.touches: list[dict] = []
        self._down = {a: False for a in ARM_NAMES}
        self.listeners: list[Callable[[dict], None]] = []
        self.hysteresis = 0.3

    def world_to_uv(self, x: float, y: float) -> tuple[float, float]:
        u, v = apply_h(self.Hinv, [x, y])
        return float(u), float(v)

    def update(self, arm: str, tip, t_ms: float) -> None:
        x, y, z = tip
        if not self._down[arm]:
            if z <= self.surface_z:
                self._down[arm] = True
                u, v = self.world_to_uv(x, y)
                if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
                    ev = {"arm": arm, "u": u, "v": v, "x": u * self.w, "y": v * self.h,
                          "w": self.w, "h": self.h, "t": t_ms, "wx": x, "wy": y}
                    self.touches.append(ev)
                    for cb in list(self.listeners):
                        try:
                            cb(ev)
                        except Exception:
                            pass
        elif z > self.surface_z + self.hysteresis:
            self._down[arm] = False


# ---------------------------------------------------------------------------
# Motion segments
# ---------------------------------------------------------------------------


class SegError(Exception):
    def __init__(self, err: str, msg: str):
        super().__init__(msg)
        self.err = err
        self.msg = msg


class Seg:
    def start(self, sim: "FirmwareSim") -> None:
        raise NotImplementedError

    def step(self, sim: "FirmwareSim") -> bool:  # True when finished
        raise NotImplementedError


class HoldSeg(Seg):
    def __init__(self, ms: float):
        self.ms = max(0.0, float(ms))

    def start(self, sim):
        self.t = 0.0

    def step(self, sim):
        self.t += TICK_MS
        return self.t >= self.ms


class JointSeg(Seg):
    def __init__(self, target: dict, ms: Optional[float] = None):
        self.target = dict(target)
        self.ms = ms

    def start(self, sim):
        for n in self.target:
            sim.raw_us.pop(sim.kin.channel_of(n), None)
        self.q0 = {n: sim.q[n] for n in self.target}
        if self.ms is None:
            self.ms = sim.joint_ms(self.q0, self.target)
        self.ms = max(float(self.ms), TICK_MS)
        self.t = 0.0
        sim.resample_noise(self.target.keys())

    def step(self, sim):
        self.t += TICK_MS
        s = minjerk(self.t / self.ms)
        for n, q1 in self.target.items():
            sim.q[n] = self.q0[n] + s * (q1 - self.q0[n])
        return self.t >= self.ms


class CartSeg(Seg):
    def __init__(self, arm: str, p1, ms: Optional[float] = None):
        self.arm = arm
        self.p1 = np.asarray(p1, dtype=float)
        self.ms = ms

    def start(self, sim):
        self.p0 = np.asarray(sim.kin.fk_joints(self.arm, sim.q), dtype=float)
        if self.ms is None:
            dist = float(np.linalg.norm(self.p1 - self.p0))
            ms_cart = 1000.0 * dist / CART_SPEED
            try:
                q1 = sim.kin.ik(self.arm, *self.p1)
                names = sim.kin.arm_joint_names(self.arm)
                ms_j = sim.joint_ms({n: sim.q[n] for n in names}, dict(zip(names, q1))) * 1.2
            except KinematicsError:
                ms_j = 0.0
            self.ms = math.ceil(max(ms_cart, ms_j, MIN_SEG_MS) / TICK_MS) * TICK_MS
        self.ms = max(float(self.ms), TICK_MS)
        self.t = 0.0
        sim.resample_noise(sim.kin.arm_joint_names(self.arm))

    def step(self, sim):
        self.t += TICK_MS
        s = minjerk(self.t / self.ms)
        p = self.p0 + s * (self.p1 - self.p0)
        try:
            q = sim.kin.ik(self.arm, *p)
        except KinematicsError as e:
            raise SegError(e.code, f"{self.arm} path ({p[0]:.1f},{p[1]:.1f},{p[2]:.1f}): {e.msg}")
        for n, v in zip(sim.kin.arm_joint_names(self.arm), q):
            sim.q[n] = v
        return self.t >= self.ms


class Job:
    def __init__(self, id_, cmd: str, planner: Callable[["FirmwareSim"], list],
                 on_done: Optional[Callable[["FirmwareSim"], None]] = None):
        self.id = id_
        self.cmd = cmd
        self.planner = planner
        self.on_done = on_done
        self.segs: Optional[deque] = None
        self.cur: Optional[Seg] = None


# ---------------------------------------------------------------------------
# Firmware simulator core
# ---------------------------------------------------------------------------


class FirmwareSim:
    def __init__(self, cfg: dict | None = None, speed: float = 1.0, noise_deg: float = 0.0,
                 backlash_deg: float = 0.0, seed: int | None = 0,
                 touchscreen: VirtualTouchscreen | None = None, autostart: bool = True):
        self.cfg_defaults = copy.deepcopy(cfg if cfg is not None else load_config())
        self.cfg = copy.deepcopy(self.cfg_defaults)
        self.kin = Kinematics(self.cfg)
        self.speed = float(speed)
        self.noise_deg = float(noise_deg)
        self.backlash_deg = float(backlash_deg)
        self.rng = random.Random(seed)
        self.touchscreen = touchscreen or VirtualTouchscreen(self.cfg)
        self.lock = threading.RLock()
        self.listeners: list[Callable[[dict], None]] = []  # event sinks (broadcast)
        self.nvs: dict = {}

        self.now_ms = 0.0
        self.enabled = False
        self.q = self.kin.pose("home")
        self.q_act = dict(self.q)
        self.noise = {n: 0.0 for n in JOINT_NAMES}
        self.raw_us: dict[int, float] = {}
        self.queue: deque[Job] = deque()
        self.job: Optional[Job] = None
        self.watchdog_ms = int(self.cfg["link"]["watchdog_ms"])
        self.last_cmd_ms = 0.0
        self.stream_hz = 0.0
        self.next_stream_ms = 0.0
        self.vservo = 5.02
        self.vin = 9.0
        self.faults: list[str] = []
        self.phone_power = True
        self.ticks = 0

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if autostart:
            self.start()

    # --- lifecycle -------------------------------------------------------
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="tappet-sim", daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        period = TICK_MS / 1000.0 / max(self.speed, 1e-6)
        nxt = time.monotonic()
        while not self._stop.is_set():
            nxt += period
            with self.lock:
                self.tick()
            d = nxt - time.monotonic()
            if d > 0:
                time.sleep(d)
            elif d < -0.5:
                nxt = time.monotonic()

    def run_ticks(self, n: int):
        """Advance the simulation synchronously (for autostart=False use)."""
        with self.lock:
            for _ in range(n):
                self.tick()

    # --- events ------------------------------------------------------------
    def emit(self, ev: dict):
        for cb in list(self.listeners):
            try:
                cb(ev)
            except Exception:
                pass

    # --- helpers -----------------------------------------------------------
    def joint_ms(self, q0: dict, q1: dict) -> float:
        """Min-jerk duration so the peak velocity (1.875x mean) stays under max_vel."""
        t = 0.0
        for n, v1 in q1.items():
            mv = float(self.cfg["joints"][n]["max_vel"])
            t = max(t, 1.875 * abs(v1 - q0[n]) / mv * 1000.0)
        t = max(t, MIN_SEG_MS)
        return math.ceil(t / TICK_MS) * TICK_MS

    def resample_noise(self, names):
        if self.noise_deg > 0:
            for n in names:
                self.noise[n] = self.rng.gauss(0.0, self.noise_deg)

    def tool(self, arm: str):
        return self.kin.fk_joints(arm, self.q)

    def tool_actual(self, arm: str):
        return self.kin.fk_joints(arm, self.q_act)

    def status_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "busy": self.job is not None or bool(self.queue),
            "queue": len(self.queue) + (1 if self.job else 0),
            "joints": {n: _r(self.q[n]) for n in JOINT_NAMES},
            "tools": {a: [_r(c) for c in self.tool(a)] for a in ARM_NAMES},
            "vservo": _r(self.vservo), "vin": _r(self.vin),
            "faults": list(self.faults),
        }

    # --- tick ----------------------------------------------------------------
    def tick(self):
        self.now_ms += TICK_MS
        self.ticks += 1
        if (self.enabled and self.watchdog_ms > 0 and
                self.now_ms - self.last_cmd_ms > self.watchdog_ms and
                not any(j.cmd == "_watchdog" for j in ([self.job] if self.job else []) + list(self.queue))):
            self._abort_all("watchdog")
            self.queue.append(Job(None, "_watchdog", lambda s: s._plan_park("both"),
                                  on_done=self._watchdog_done))
        if self.enabled and "brownout" not in self.faults:
            self._step_motion()
        self._update_actual()
        if self.stream_hz > 0 and self.now_ms >= self.next_stream_ms:
            self.next_stream_ms = self.now_ms + 1000.0 / self.stream_hz
            ev = {"evt": "status"}
            ev.update(self.status_dict())
            self.emit(ev)

    def _watchdog_done(self, sim):
        self.enabled = False
        self.raw_us.clear()
        self.emit({"evt": "watchdog"})

    def _step_motion(self):
        while True:
            if self.job is None:
                if not self.queue:
                    return
                self.job = self.queue.popleft()
                try:
                    self.job.segs = deque(self.job.planner(self))
                except KinematicsError as e:
                    self._fail_job(e.code, e.msg)
                    continue
                except (SegError, CmdError) as e:
                    self._fail_job(e.err, e.msg)
                    continue
            job = self.job
            try:
                if job.cur is None:
                    if not job.segs:
                        self._finish_job()
                        continue
                    job.cur = job.segs.popleft()
                    job.cur.start(self)
                if job.cur.step(self):
                    job.cur = None
                    if not job.segs:
                        self._finish_job()
                return
            except SegError as e:
                self._fail_job(e.err, e.msg)
                return

    def _finish_job(self):
        job = self.job
        self.job = None
        if job.on_done:
            job.on_done(self)
        if job.id is not None:
            self.emit({"evt": "done", "id": job.id})

    def _fail_job(self, err, msg):
        job = self.job
        self.job = None
        if job.id is not None:
            self.emit({"evt": "error", "id": job.id, "err": err, "msg": msg})

    def _abort_all(self, why: str):
        jobs = ([self.job] if self.job else []) + list(self.queue)
        self.job = None
        self.queue.clear()
        for j in jobs:
            if j.id is not None:
                self.emit({"evt": "error", "id": j.id, "err": "fault", "msg": f"cancelled ({why})"})

    def _update_actual(self):
        b = self.backlash_deg / 2.0
        for n in JOINT_NAMES:
            cmd = self.q[n] + self.noise[n]
            if b > 0:
                a = self.q_act[n]
                if cmd - a > b:
                    a = cmd - b
                elif a - cmd > b:
                    a = cmd + b
                self.q_act[n] = a
            else:
                self.q_act[n] = cmd
        if self.enabled:
            for arm in ARM_NAMES:
                self.touchscreen.update(arm, self.tool_actual(arm), self.now_ms)

    # --- external stimuli -----------------------------------------------------
    def press_button(self, action: str = "short"):
        """Simulate the BOOT button: short = toggle enable/relax, long = (enable +) home."""
        with self.lock:
            self.emit({"evt": "button", "action": action})
            self.last_cmd_ms = self.now_ms
            if action == "long":
                self.enabled = True
                self.queue.append(Job(None, "home", lambda s: [JointSeg(s.kin.pose("home"))]))
            elif self.enabled:
                self._relax()
            else:
                self.enabled = True

    def set_vservo(self, v: float):
        """Inject a servo-rail voltage (brownout below 4.3 V pauses motion)."""
        with self.lock:
            self.vservo = float(v)
            if v < 4.3 and "brownout" not in self.faults:
                self.faults.append("brownout")
                self.emit({"evt": "fault", "code": "brownout"})
            elif v >= 4.5 and "brownout" in self.faults:
                self.faults.remove("brownout")

    def _relax(self):
        self._abort_all("relax")
        self.enabled = False
        self.raw_us.clear()

    # --- planning ---------------------------------------------------------------
    def _plan_park(self, which: str) -> list:
        arms = ARM_NAMES if which == "both" else (which,)
        target = {}
        for a in arms:
            for n in self.kin.arm_joint_names(a):
                target[n] = float(self.cfg["joints"][n]["park"])
        return [JointSeg(target)]

    def _check_points(self, arm: str, pts):
        worst = None
        for p in pts:
            _, err = self.kin.try_ik(arm, *p)
            if err == "unreachable":
                return ("unreachable", f"arm {arm} cannot reach ({p[0]:.1f},{p[1]:.1f},{p[2]:.1f})")
            if err == "limit" and worst is None:
                worst = ("limit", f"arm {arm} joint limit at ({p[0]:.1f},{p[1]:.1f},{p[2]:.1f})")
        return worst

    def _resolve_arm(self, arm: str, x: float, pts) -> str:
        if arm in ARM_NAMES:
            e = self._check_points(arm, pts)
            if e:
                raise CmdError(*e)
            return arm
        if arm != "auto":
            raise CmdError("bad_args", "arm must be L, R or auto")
        first = "L" if x < 0 else "R"
        other = "R" if first == "L" else "L"
        e1 = self._check_points(first, pts)
        if not e1:
            return first
        if not self._check_points(other, pts):
            return other
        raise CmdError(*e1)

    def _approach_segs(self, arm: str, x: float, y: float, z_up: float, ms) -> list:
        """Park the other arm if near the target, lift this arm if low, go above (x,y,z_up)."""
        segs: list = []
        other = "R" if arm == "L" else "L"
        ox, oy, _ = self.tool(other)
        if math.hypot(ox - x, oy - y) < PARK_CLEAR_MM:
            segs += self._plan_park(other)
        cx, cy, cz = self.tool(arm)
        if cz < z_up - 0.5 and self.kin.reachable(arm, cx, cy, z_up):
            segs.append(CartSeg(arm, (cx, cy, z_up)))
        q = self.kin.ik(arm, x, y, z_up)
        segs.append(JointSeg(dict(zip(self.kin.arm_joint_names(arm), q)), ms))
        return segs

    # --- protocol -----------------------------------------------------------------
    def handle_line(self, line: str) -> Optional[dict]:
        """Process one request line; returns the response (events go to ``listeners``)."""
        line = line.strip()
        if not line:
            return None
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            return {"id": None, "ok": False, "err": "bad_json", "msg": "invalid JSON"}
        rid = req.get("id")
        with self.lock:
            self.last_cmd_ms = self.now_ms
            cmd = req.get("cmd")
            fn = getattr(self, f"_cmd_{cmd}", None) if isinstance(cmd, str) else None
            if fn is None:
                return {"id": rid, "ok": False, "err": "unknown_cmd", "msg": f"unknown cmd {cmd!r}"}
            try:
                extra = fn(rid, req) or {}
            except CmdError as e:
                out = {"id": rid, "ok": False, "err": e.err, "msg": e.msg}
                out.update(e.extra)
                return out
            except (KeyError, TypeError, ValueError) as e:
                return {"id": rid, "ok": False, "err": "bad_args", "msg": f"bad args: {e}"}
            out = {"id": rid, "ok": True}
            out.update(extra)
            return out

    @staticmethod
    def _num(req, k, default=None, required=False):
        if k not in req or req[k] is None:
            if required:
                raise CmdError("bad_args", f"missing {k}")
            return default
        v = req[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise CmdError("bad_args", f"{k} must be a number")
        return float(v)

    def _ms(self, req, default=None):
        ms = self._num(req, "ms", default)
        if ms is not None and ms <= 0:
            raise CmdError("bad_args", "ms must be > 0")
        return ms

    def _need_enabled(self):
        if not self.enabled:
            raise CmdError("not_enabled", "servos are relaxed; send enable first")

    def _enqueue(self, rid, cmd, planner, **extra) -> dict:
        self._need_enabled()
        if len(self.queue) + (1 if self.job else 0) >= QUEUE_DEPTH:
            raise CmdError("queue_full", f"motion queue full ({QUEUE_DEPTH})")
        self.queue.append(Job(rid, cmd, planner))
        out = {"queued": True}
        out.update(extra)
        return out

    # commands ------------------------------------------------------------------
    def _cmd_hello(self, rid, req):
        return {"fw": FW_NAME, "ver": FW_VER, "proto": PROTO, "servos": 8,
                "uptime_ms": int(self.now_ms), "sim": True}

    def _cmd_ping(self, rid, req):
        return {"t": int(self.now_ms)}

    def _cmd_enable(self, rid, req):
        self.enabled = True
        return {}

    def _cmd_relax(self, rid, req):
        self._relax()
        return {}

    def _cmd_stop(self, rid, req):
        self._abort_all("stop")
        return {}

    def _cmd_status(self, rid, req):
        return self.status_dict()

    def _cmd_joints(self, rid, req):
        q = req.get("q")
        if not isinstance(q, dict) or not q:
            raise CmdError("bad_args", "q must be an object {NAME: deg}")
        target = {}
        for n, v in q.items():
            if n not in JOINT_NAMES:
                raise CmdError("bad_args", f"unknown joint {n}")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise CmdError("bad_args", f"{n} must be a number")
            target[n] = self.kin.clamp_joint(n, float(v))
        lk = self.cfg["linkage"]
        for a in ARM_NAMES:
            names = self.kin.arm_joint_names(a)
            if names[1] in target or names[2] in target:
                rel = target.get(names[1], self.q[names[1]]) - target.get(names[2], self.q[names[2]])
                if not (lk["rel_min"] <= rel <= lk["rel_max"]):
                    raise CmdError("limit", f"{a}: theta1-theta2={rel:.1f} outside linkage limits")
        ms = self._ms(req)
        return self._enqueue(rid, "joints", lambda s: [JointSeg(target, ms)])

    def _cmd_move(self, rid, req):
        arm = req.get("arm")
        if arm not in ARM_NAMES:
            raise CmdError("bad_args", "arm must be L or R")
        x, y, z = (self._num(req, k, required=True) for k in "xyz")
        try:
            self.kin.ik(arm, x, y, z)
        except KinematicsError as e:
            raise CmdError(e.code, e.msg)
        ms = self._ms(req)
        return self._enqueue(rid, "move", lambda s: [CartSeg(arm, (x, y, z), ms)])

    def _tool_args(self, req):
        t = self.cfg["tool"]
        press = self._num(req, "press_mm", t["default_press_mm"])
        approach = self._num(req, "approach_mm", t["default_approach_mm"])
        if press < 0 or press > t["stylus_travel"] or approach < 0:
            raise CmdError("bad_args", "press_mm must be within stylus travel, approach_mm >= 0")
        return press, approach

    def _cmd_tap(self, rid, req):
        x, y, z = (self._num(req, k, required=True) for k in "xyz")
        hold = self._num(req, "hold_ms", self.cfg["tool"]["default_hold_ms"])
        if hold < 0:
            raise CmdError("bad_args", "hold_ms must be >= 0")
        press, approach = self._tool_args(req)
        ms = self._ms(req)
        arm = req.get("arm") or "auto"
        z_up, z_dn = z + approach, z - press
        self._need_enabled()
        arm = self._resolve_arm(arm, x, [(x, y, z_up), (x, y, z_dn)])

        def plan(s: FirmwareSim):
            segs = s._approach_segs(arm, x, y, z_up, ms)
            segs += [CartSeg(arm, (x, y, z_dn)), HoldSeg(hold), CartSeg(arm, (x, y, z_up))]
            return segs
        return self._enqueue(rid, "tap", plan, arm=arm)

    def _cmd_swipe(self, rid, req):
        x0, y0, x1, y1, z = (self._num(req, k, required=True) for k in ("x0", "y0", "x1", "y1", "z"))
        ms = self._ms(req, 400.0)
        press, approach = self._tool_args(req)
        arm = req.get("arm") or "auto"
        z_up, z_dn = z + approach, z - press
        pts = [(x0, y0, z_up), (x1, y1, z_up)]
        for i in range(11):
            f = i / 10
            pts.append((x0 + f * (x1 - x0), y0 + f * (y1 - y0), z_dn))
        self._need_enabled()
        arm = self._resolve_arm(arm, (x0 + x1) / 2, pts)

        def plan(s: FirmwareSim):
            segs = s._approach_segs(arm, x0, y0, z_up, None)
            segs += [CartSeg(arm, (x0, y0, z_dn)), CartSeg(arm, (x1, y1, z_dn), ms),
                     CartSeg(arm, (x1, y1, z_up))]
            return segs
        return self._enqueue(rid, "swipe", plan, arm=arm)

    def _cmd_neck(self, rid, req):
        target = {}
        for k, n in (("pan", "NECK_PAN"), ("tilt", "NECK_TILT")):
            v = self._num(req, k)
            if v is not None:
                target[n] = self.kin.clamp_joint(n, v)
        if not target:
            raise CmdError("bad_args", "pan and/or tilt required")
        ms = self._ms(req)
        return self._enqueue(rid, "neck", lambda s: [JointSeg(target, ms)])

    def _cmd_home(self, rid, req):
        ms = self._ms(req)
        return self._enqueue(rid, "home", lambda s: [JointSeg(s.kin.pose("home"), ms)])

    def _cmd_park(self, rid, req):
        which = req.get("arm") or "both"
        if which not in ("L", "R", "both"):
            raise CmdError("bad_args", "arm must be L, R or both")
        return self._enqueue(rid, "park", lambda s: s._plan_park(which))

    def _cmd_servo_raw(self, rid, req):
        ch = self._num(req, "ch", required=True)
        us = self._num(req, "us", required=True)
        if ch != int(ch) or not 0 <= ch <= 15:
            raise CmdError("bad_args", "ch must be 0..15")
        if not 400 <= us <= 2600:
            raise CmdError("bad_args", "us must be 400..2600")
        self._need_enabled()
        self.raw_us[int(ch)] = us
        n = self.kin.joint_of_channel(int(ch))
        if n:
            self.q[n] = self.kin.us_to_joint(n, us)
        return {}

    def _cmd_calib_get(self, rid, req):
        return {"joints": {n: {f: self.cfg["joints"][n][f] for f in JOINT_FIELDS}
                           for n in JOINT_NAMES}}

    def _cmd_calib_set(self, rid, req):
        joints = req.get("joints")
        if not isinstance(joints, dict):
            raise CmdError("bad_args", "joints must be an object")
        for n, fields in joints.items():
            if n not in JOINT_NAMES or not isinstance(fields, dict):
                raise CmdError("bad_args", f"unknown joint {n}")
            for f, v in fields.items():
                if f not in JOINT_FIELDS or isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise CmdError("bad_args", f"bad field {n}.{f}")
        for n, fields in joints.items():
            self.cfg["joints"][n].update(fields)
        if req.get("save"):
            nj = self.nvs.setdefault("joints", {})
            for n, fields in joints.items():
                nj.setdefault(n, {}).update(fields)
        return {"saved": bool(req.get("save"))}

    def _cmd_geom_get(self, rid, req):
        return {"arms": {a: {f: self.cfg["arms"][a][f] for f in GEOM_FIELDS} for a in ARM_NAMES},
                "linkage": {k: self.cfg["linkage"][k] for k in ("rel_min", "rel_max")}}

    def _cmd_geom_set(self, rid, req):
        arms = req.get("arms") or {}
        linkage = req.get("linkage") or {}
        if not isinstance(arms, dict) or not isinstance(linkage, dict):
            raise CmdError("bad_args", "arms/linkage must be objects")
        for a, g in arms.items():
            if a not in ARM_NAMES or not isinstance(g, dict):
                raise CmdError("bad_args", f"unknown arm {a}")
            for f, v in g.items():
                if f not in GEOM_FIELDS or isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise CmdError("bad_args", f"bad field {a}.{f}")
        for f, v in linkage.items():
            if f not in ("rel_min", "rel_max") or isinstance(v, bool) or not isinstance(v, (int, float)):
                raise CmdError("bad_args", f"bad linkage field {f}")
        for a, g in arms.items():
            self.cfg["arms"][a].update(g)
        self.cfg["linkage"].update(linkage)
        if req.get("save"):
            for a, g in arms.items():
                self.nvs.setdefault("arms", {}).setdefault(a, {}).update(g)
            self.nvs.setdefault("linkage", {}).update(linkage)
        return {"saved": bool(req.get("save"))}

    def _cmd_reset_calib(self, rid, req):
        for k in ("joints", "arms", "linkage"):
            self.cfg[k] = copy.deepcopy(self.cfg_defaults[k])
        self.nvs.clear()
        return {}

    def _cmd_fk(self, rid, req):
        arm = req.get("arm")
        if arm not in ARM_NAMES:
            raise CmdError("bad_args", "arm must be L or R")
        x, y, z = self.tool(arm)
        return {"x": _r(x, 3), "y": _r(y, 3), "z": _r(z, 3)}

    def _cmd_ik(self, rid, req):
        arm = req.get("arm")
        if arm not in ARM_NAMES:
            raise CmdError("bad_args", "arm must be L or R")
        x, y, z = (self._num(req, k, required=True) for k in "xyz")
        try:
            q = self.kin.ik(arm, x, y, z)
        except KinematicsError as e:
            extra = {}
            if e.q is not None:
                extra["q"] = dict(zip(ARM_JOINTS, (_r(v, 4) for v in e.q)))
            raise CmdError(e.code, e.msg, **extra)
        return {"q": dict(zip(ARM_JOINTS, (_r(v, 4) for v in q)))}

    def _cmd_watchdog(self, rid, req):
        ms = self._num(req, "ms", required=True)
        if ms < 0:
            raise CmdError("bad_args", "ms must be >= 0")
        self.watchdog_ms = int(ms)
        return {"ms": self.watchdog_ms}

    def _cmd_stream(self, rid, req):
        hz = self._num(req, "hz", required=True)
        if hz < 0 or hz > 20:
            raise CmdError("bad_args", "hz must be 0..20")
        self.stream_hz = hz
        self.next_stream_ms = self.now_ms
        return {"hz": hz}

    def _cmd_phone_power(self, rid, req):
        on = req.get("on")
        if not isinstance(on, bool):
            raise CmdError("bad_args", "on must be true/false")
        self.phone_power = on
        return {"on": on}


# ---------------------------------------------------------------------------
# TCP server
# ---------------------------------------------------------------------------


class _Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.wlock = threading.Lock()
        self.alive = True
        self.server.sim.listeners.append(self.send)
        self.send({"evt": "boot", "fw": FW_NAME, "ver": FW_VER})

    def send(self, obj: dict):
        if not self.alive:
            return
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        try:
            with self.wlock:
                self.wfile.write(data)
                self.wfile.flush()
        except OSError:
            self.alive = False

    def handle(self):
        sim: FirmwareSim = self.server.sim
        while True:
            try:
                raw = self.rfile.readline(8192)
            except OSError:
                break
            if not raw:
                break
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                self.send({"id": None, "ok": False, "err": "bad_json", "msg": "not UTF-8"})
                continue
            resp = sim.handle_line(line)
            if resp is not None:
                self.send(resp)

    def finish(self):
        self.alive = False
        try:
            self.server.sim.listeners.remove(self.send)
        except ValueError:
            pass
        try:
            super().finish()
        except OSError:
            pass


class SimServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, sim: FirmwareSim, host: str = "127.0.0.1", port: int = 7777):
        self.sim = sim
        super().__init__((host, port), _Handler)
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"tcp://{self.server_address[0]}:{self.port}"

    def start(self) -> "SimServer":
        self._thread = threading.Thread(target=self.serve_forever, name="tappet-sim-tcp", daemon=True)
        self._thread.start()
        return self

    def close(self):
        self.shutdown()
        self.server_close()
        self.sim.close()


def start_sim(port: int = 7777, host: str = "127.0.0.1", offset=(0.0, 0.0), rot_deg: float = 0.0,
              cfg: dict | None = None, **kw) -> SimServer:
    """Start a simulator + TCP server in background threads (port 0 = ephemeral)."""
    cfg = cfg if cfg is not None else load_config()
    ts = VirtualTouchscreen(cfg, offset=offset, rot_deg=rot_deg)
    sim = FirmwareSim(cfg=cfg, touchscreen=ts, **kw)
    return SimServer(sim, host, port).start()
