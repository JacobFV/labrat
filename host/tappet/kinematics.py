"""Arm kinematics exactly per docs/SPEC.md section 3, plus joint <-> servo <-> pulse mapping.

Angles in degrees, lengths in mm.  ``q`` for an arm is ``(psi, theta1, theta2)`` =
(``<A>_YAW``, ``<A>_SHOULDER``, ``<A>_ELBOW``).
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from .config import ARM_JOINTS, ARM_NAMES, JOINT_NAMES, load_config

UNREACHABLE = "unreachable"
LIMIT = "limit"


class KinematicsError(Exception):
    """IK failure. ``code`` is ``unreachable`` or ``limit``; ``q`` is set for limit errors."""

    def __init__(self, code: str, msg: str = "", q: tuple[float, float, float] | None = None):
        super().__init__(msg or code)
        self.code = code
        self.msg = msg or code
        self.q = q


@dataclass(frozen=True)
class ArmGeom:
    base_x: float
    base_y: float
    shoulder_h: float
    r0: float
    L1: float
    L2: float
    tool_dx: float
    tool_dz: float

    @classmethod
    def from_dict(cls, d: dict) -> "ArmGeom":
        return cls(**{k: float(d[k]) for k in cls.__dataclass_fields__})


def fk_geom(g: ArmGeom, psi: float, t1: float, t2: float) -> tuple[float, float, float]:
    """Forward kinematics (SPEC §3)."""
    p, a, b = math.radians(psi), math.radians(t1), math.radians(t2)
    r = g.r0 + g.L1 * math.cos(a) + g.L2 * math.cos(b) + g.tool_dx
    z = g.shoulder_h + g.L1 * math.sin(a) + g.L2 * math.sin(b) - g.tool_dz
    x = g.base_x - r * math.sin(p)
    y = g.base_y + r * math.cos(p)
    return (x, y, z)


def ik_geom(g: ArmGeom, x: float, y: float, z: float) -> tuple[float, float, float]:
    """Elbow-up IK (SPEC §3). Raises KinematicsError('unreachable')."""
    dx = x - g.base_x
    dy = y - g.base_y
    psi = math.atan2(-dx, dy)
    r = math.hypot(dx, dy) - g.r0 - g.tool_dx
    zp = z - g.shoulder_h + g.tool_dz
    D = math.hypot(r, zp)
    if not (abs(g.L1 - g.L2) <= D <= g.L1 + g.L2) or D == 0.0:
        raise KinematicsError(UNREACHABLE, f"target ({x:.1f},{y:.1f},{z:.1f}) out of reach")
    phi = math.atan2(zp, r)
    ca = (g.L1 ** 2 + D ** 2 - g.L2 ** 2) / (2 * g.L1 * D)
    cb = (g.L2 ** 2 + D ** 2 - g.L1 ** 2) / (2 * g.L2 * D)
    alpha = math.acos(max(-1.0, min(1.0, ca)))
    beta = math.acos(max(-1.0, min(1.0, cb)))
    return (math.degrees(psi), math.degrees(phi + alpha), math.degrees(phi - beta))


class Kinematics:
    """Kinematics + joint mapping bound to a robot.json dict (joints/arms/linkage may be updated)."""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg if cfg is not None else load_config()

    # --- accessors ------------------------------------------------------
    @property
    def joints(self) -> dict:
        return self.cfg["joints"]

    @property
    def linkage(self) -> dict:
        return self.cfg["linkage"]

    def geom(self, arm: str) -> ArmGeom:
        if arm not in ARM_NAMES:
            raise ValueError(f"bad arm {arm!r}")
        return ArmGeom.from_dict(self.cfg["arms"][arm])

    @staticmethod
    def arm_joint_names(arm: str) -> tuple[str, str, str]:
        return tuple(f"{arm}_{j}" for j in ARM_JOINTS)  # type: ignore[return-value]

    # --- FK / IK ----------------------------------------------------------
    def fk(self, arm: str, q: Sequence[float]) -> tuple[float, float, float]:
        return fk_geom(self.geom(arm), q[0], q[1], q[2])

    def fk_joints(self, arm: str, joints: dict) -> tuple[float, float, float]:
        return self.fk(arm, [joints[n] for n in self.arm_joint_names(arm)])

    def limit_violation(self, arm: str, q: Sequence[float]) -> str | None:
        """Return a description of the first violated limit, or None."""
        for name, v in zip(self.arm_joint_names(arm), q):
            j = self.joints[name]
            if v < j["min"] - 1e-9 or v > j["max"] + 1e-9:
                return f"{name}={v:.1f} outside [{j['min']},{j['max']}]"
        rel = q[1] - q[2]
        if rel < self.linkage["rel_min"] - 1e-9 or rel > self.linkage["rel_max"] + 1e-9:
            return (f"theta1-theta2={rel:.1f} outside linkage "
                    f"[{self.linkage['rel_min']},{self.linkage['rel_max']}]")
        return None

    def ik(self, arm: str, x: float, y: float, z: float, check_limits: bool = True
           ) -> tuple[float, float, float]:
        """IK with limit check. Raises KinematicsError(unreachable|limit)."""
        q = ik_geom(self.geom(arm), x, y, z)
        if check_limits:
            v = self.limit_violation(arm, q)
            if v:
                raise KinematicsError(LIMIT, v, q)
        return q

    def try_ik(self, arm: str, x: float, y: float, z: float):
        """Return ``(q, err)``; err is None, 'unreachable' or 'limit' (q set for limit)."""
        try:
            return self.ik(arm, x, y, z), None
        except KinematicsError as e:
            return e.q, e.code

    def reachable(self, arm: str, x: float, y: float, z: float) -> bool:
        return self.try_ik(arm, x, y, z)[1] is None

    # --- joint <-> servo <-> pulse -----------------------------------------
    def clamp_joint(self, name: str, q: float) -> float:
        j = self.joints[name]
        return min(max(q, j["min"]), j["max"])

    def joint_to_servo(self, name: str, q: float) -> float:
        j = self.joints[name]
        q = self.clamp_joint(name, q)
        s = j["s_zero"] + j["dir"] * j["gear"] * (q - j["neutral"])
        return min(max(s, 0.0), 180.0)

    def servo_to_joint(self, name: str, s: float) -> float:
        j = self.joints[name]
        return j["neutral"] + (s - j["s_zero"]) / (j["dir"] * j["gear"])

    def servo_to_us(self, s: float) -> float:
        sv = self.cfg["servo"]
        return sv["us_min"] + s * (sv["us_max"] - sv["us_min"]) / sv["deg_range"]

    def us_to_servo(self, us: float) -> float:
        sv = self.cfg["servo"]
        return (us - sv["us_min"]) * sv["deg_range"] / (sv["us_max"] - sv["us_min"])

    def joint_to_us(self, name: str, q: float) -> float:
        return self.servo_to_us(self.joint_to_servo(name, q))

    def us_to_joint(self, name: str, us: float) -> float:
        return self.servo_to_joint(name, self.us_to_servo(us))

    def channel_of(self, name: str) -> int:
        return int(self.joints[name]["ch"])

    def joint_of_channel(self, ch: int) -> str | None:
        for n, j in self.joints.items():
            if int(j["ch"]) == ch:
                return n
        return None

    def pose(self, which: str) -> dict[str, float]:
        """'home' or 'park' joint dict for all joints."""
        return {n: float(self.joints[n][which]) for n in JOINT_NAMES}

    # --- workspace -------------------------------------------------------
    def reach_grid(self, arm: str, xs: Iterable[float], ys: Iterable[float], z: float) -> np.ndarray:
        """Boolean array [len(ys), len(xs)] of reachability (incl. limits) at height z."""
        xs = list(xs)
        ys = list(ys)
        out = np.zeros((len(ys), len(xs)), dtype=bool)
        for i, y in enumerate(ys):
            for k, x in enumerate(xs):
                out[i, k] = self.reachable(arm, x, y, z)
        return out


# ---------------------------------------------------------------------------
# Shared test vectors (config/kinematics_vectors.json)
# ---------------------------------------------------------------------------
VECTORS_NOTE = ("Generated from docs/SPEC.md section 3 using config/robot.json. q=[yaw,theta1,theta2] deg. "
                "err present => IK must report that error (limit errors still report q). "
                "fk(q) must equal xyz within 1e-3 mm when q present.")

FIXED_POINTS = [
    (0, 160, 13.8), (-30, 95, 13.8), (30, 225, 13.8), (-30, 225, 13.8), (30, 95, 13.8),
    (0, 160, 40), (-85, 40, 300), (0, 400, 13.8), (-60, 120, 21.8), (60, 200, 11.3),
]


def _r6(v: float) -> float:
    v = round(v, 6)
    return 0.0 if v == 0 else v


def generate_vectors(cfg: dict | None = None, seed: int = 7, n_random: int = 20) -> dict:
    """Recreate config/kinematics_vectors.json (deterministic)."""
    kin = Kinematics(cfg)
    rng = random.Random(seed)
    rand_pts = []
    for _ in range(n_random):
        x = rng.uniform(-40, 40)
        y = rng.uniform(80, 240)
        z = rng.uniform(10.8, 33.8)
        rand_pts.append((x, y, z))
    pts = [tuple(float(c) if isinstance(c, float) else c for c in p) for p in FIXED_POINTS] + rand_pts
    vectors = []
    for arm in ARM_NAMES:
        for i, (x, y, z) in enumerate(pts):
            shown = [x, y, z] if i < len(FIXED_POINTS) else [round(x, 4), round(y, 4), round(z, 4)]
            v: dict = {"arm": arm, "xyz": shown}
            q, err = kin.try_ik(arm, x, y, z)
            if q is not None:
                qr = [_r6(a) for a in q]
                v["q"] = qr
                v["fk"] = [_r6(c) for c in kin.fk(arm, q)]
            if err:
                v["err"] = err
            vectors.append(v)
    return {"note": VECTORS_NOTE, "vectors": vectors}


def dump_vectors(data: dict) -> str:
    return json.dumps(data, indent=1)
