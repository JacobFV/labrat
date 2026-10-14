"""LabRat digital twin: deck model, CNC trapezoid timing, pipette/tip/liquid accounting,
subtractive dye mixing -> RGB, safety checks, frames for 3D replay, and goal scoring."""
from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

from . import deck_cal

# ---------------- deck ----------------
ROWS = "ABCDEFGH"
TIP_RACK = {"name": "tip_rack", "origin": (20.0, 20.0), "pitch": 9.0, "rows": 8, "cols": 12}
PLATE = {"name": "plate", "origin": (140.0, 20.0), "pitch": 9.0, "rows": 8, "cols": 12, "cap_ul": 300.0}
RESERVOIRS = {"water": (250.0, 20.0), "red": (250.0, 60.0), "blue": (250.0, 100.0), "yellow": (250.0, 140.0)}
RES_CAP_UL = 20000.0
WASTE = (280.0, 170.0)
EJECT_BAR = (10.0, 170.0)
Z_SAFE, Z_TIP, Z_WELL, Z_RES = 0.0, -38.0, -30.0, -35.0
BOUNDS = {"x": (0.0, 300.0), "y": (0.0, 180.0), "z": (-45.0, 0.0)}
TIP_CAP_UL = 200.0
FEED_XY, FEED_Z = 3000.0, 600.0  # mm/min
ACCEL = 400.0  # mm/s^2
PIPETTE_UL_PER_S = 100.0
# rack-and-pinion syringe head (cad/syringe, bridge/calib syringe.json defaults): plunger height for the twin
SYR_UL_PER_MM = math.pi / 4 * 17.8 ** 2  # 20.2 mm OD barrel, ~17.8 mm bore
SYR_READY_MM, SYR_STROKE_MM = 2.0, 25.0

# SO-101 reach envelope (base positions in deck coords, mm): one arm on each side of the 3018 CNC (left/right of the bed).
# The iPhone camera is on a static stand above the bed, so photos need no arm.
SO101_BASES = {"so_a": (-80.0, 90.0), "so_b": (380.0, 90.0)}
SO101_REACH = 400.0

# Dye absorbance (per channel R,G,B) at stock concentration (≈ OD per unit path)
DYES = {
    "water": np.array([0.0, 0.0, 0.0]),
    "red": np.array([0.05, 1.6, 1.2]),
    "yellow": np.array([0.02, 0.06, 1.8]),
    "blue": np.array([1.6, 0.7, 0.05]),
}
DYE_NAMES = list(DYES)
_ABS = np.stack([DYES[d] for d in DYE_NAMES])  # (4,3)
VOL_GAIN = 1.0  # actual/commanded pipette volume (fitted by learn.py)


def get_params() -> dict:
    return {"absorbance": {d: [round(float(x), 4) for x in DYES[d]] for d in DYE_NAMES}, "vol_gain": round(VOL_GAIN, 4)}


def set_params(p: dict) -> None:
    """Install fitted simulator parameters (used by the RSI loop)."""
    global _ABS, VOL_GAIN
    for d, v in (p.get("absorbance") or {}).items():
        if d in DYES:
            DYES[d] = np.array(v, dtype=float)
    _ABS = np.stack([DYES[d] for d in DYE_NAMES])
    VOL_GAIN = float(p.get("vol_gain", VOL_GAIN))

TARGETS = {
    "orange": (255, 140, 0), "green": (40, 170, 60), "purple": (128, 40, 160), "brown": (130, 75, 30),
    "pink": (255, 150, 180), "red": (220, 20, 30), "yellow": (250, 230, 20), "blue": (30, 70, 220),
    "teal": (0, 150, 150), "black": (20, 20, 20), "lime": (160, 230, 40),
}


def well_xy(name: str, grid: dict) -> tuple[float, float]:
    m = re.fullmatch(r"([A-Ha-h])(\d{1,2})", name.strip())
    if not m:
        raise ValueError(f"bad well {name}")
    r, c = ROWS.index(m.group(1).upper()), int(m.group(2)) - 1
    if not (0 <= c < grid["cols"]):
        raise ValueError(f"bad well {name}")
    ox, oy = grid["origin"]
    nom = (ox + c * grid["pitch"], oy + r * grid["pitch"])
    if grid.get("name") in deck_cal.GRIDS:
        x, y, _ = deck_cal.grid_spindle(grid["name"], r, c, nom, 0.0)
        return x, y
    return nom


def well_xyz(name: str, grid: dict, z_nom: float) -> tuple[float, float, float]:
    """Calibrated spindle (x, y, z_work) for a grid well (taught z overrides the nominal work height)."""
    m = re.fullmatch(r"([A-Ha-h])(\d{1,2})", name.strip())
    if not m:
        raise ValueError(f"bad well {name}")
    r, c = ROWS.index(m.group(1).upper()), int(m.group(2)) - 1
    if not (0 <= c < grid["cols"]):
        raise ValueError(f"bad well {name}")
    ox, oy = grid["origin"]
    return deck_cal.grid_spindle(grid["name"], r, c, (ox + c * grid["pitch"], oy + r * grid["pitch"]), z_nom)


def eject_xyz() -> tuple[float, float, float]:
    return deck_cal.point_spindle("eject_bar", (EJECT_BAR[0], EJECT_BAR[1], -20.0))


def loc(name: str) -> tuple[str, float, float, float]:
    """Resolve a src/dst name to (kind, x, y, z_work)."""
    n = name.strip().lower().replace("reservoir:", "").replace("res_", "")
    if n in RESERVOIRS:
        x, y = RESERVOIRS[n]
        return ("reservoir", *deck_cal.point_spindle("reservoir." + n, (x, y, Z_RES)))
    if n == "waste":
        return ("waste", *deck_cal.point_spindle("waste", (WASTE[0], WASTE[1], -10.0)))
    return ("well", *well_xyz(name, PLATE, Z_WELL))


def mix_rgb(comp: dict[str, float]) -> list[int]:
    """Subtractive mixing: Beer–Lambert on concentration-weighted absorbance."""
    tot = sum(comp.values())
    if tot <= 0:
        return [235, 235, 235]
    c = np.array([comp.get(d, 0.0) / tot for d in DYE_NAMES])
    a = c @ _ABS
    a = np.maximum(a, 0.0)
    t = np.power(10.0, -a)
    rgb = 245.0 * t  # white plate background, slight grey
    return [int(round(v)) for v in rgb]


def move_time(d: float, feed_mm_min: float, accel: float = ACCEL) -> float:
    if d <= 0:
        return 0.0
    v = feed_mm_min / 60.0
    d_acc = v * v / accel
    if d < d_acc:
        return 2.0 * math.sqrt(d / accel)
    return d / v + v / accel


def target_rgb(goal: str) -> tuple[str, tuple[int, int, int]] | None:
    g = goal.lower()
    m = re.search(r"#([0-9a-f]{6})", g)
    if m:
        h = m.group(1)
        return "#" + h, (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    for name, rgb in TARGETS.items():
        if re.search(rf"\b{name}", g) and name not in ("red", "yellow", "blue"):
            return name, rgb
    for name in ("red", "yellow", "blue"):
        if re.search(rf"\b{name}", g):
            return name, TARGETS[name]
    return None


def color_dist(a, b) -> float:
    return float(np.linalg.norm(np.array(a, float) - np.array(b, float)))


class Sim:
    def __init__(self, record_frames: bool = True) -> None:
        self.t = 0.0
        self.pos = [0.0, 0.0, 0.0]
        self.tip: dict | None = None  # {"id","vol","comp":{}, "touched": set}
        self.used_tips: set[str] = set()
        self.wells: dict[str, dict] = {}
        self.res = {k: {"vol": RES_CAP_UL * 0.5, "comp": {k: 1.0}} for k in RESERVOIRS}
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.frames: list[dict] = []
        self.record = record_frames
        self.dist = 0.0

    # --- helpers ---
    def _frame(self, step: int, label: str, changed: list[str] | None = None) -> None:
        if not self.record:
            return
        wells = {}
        for w in changed or []:
            if w in self.wells:
                st = self.wells[w]
                wells[w] = {"rgb": mix_rgb(st["comp"]), "vol": round(st["vol"], 1)}
        self.frames.append({
            "t": round(self.t, 2), "step": step, "label": label,
            "cnc": {"x": round(self.pos[0], 1), "y": round(self.pos[1], 1), "z": round(self.pos[2], 1)},
            "pipette": {"vol_ul": round(self.tip["vol"], 1) if self.tip else 0.0, "tip": self.tip["id"] if self.tip else None,
                        "pos_mm": round(SYR_READY_MM + (self.tip["vol"] / SYR_UL_PER_MM if self.tip else 0.0), 3),
                        "stroke_mm": SYR_STROKE_MM},
            "wells": wells,
        })

    def _goto(self, x: float, y: float, z: float, step: int, label: str) -> None:
        for ax, v in zip("xyz", (x, y, z)):
            lo, hi = BOUNDS[ax]
            if not (lo - 1e-6 <= v <= hi + 1e-6):
                self.errors.append(f"step {step}: {ax}={v} out of bounds [{lo},{hi}]")
        # Retract to safe Z, travel XY, plunge.
        if self.pos[2] < Z_SAFE and (abs(self.pos[0] - x) > 0.01 or abs(self.pos[1] - y) > 0.01):
            d = Z_SAFE - self.pos[2]
            self.t += move_time(d, FEED_Z)
            self.dist += d
            self.pos[2] = Z_SAFE
            self._frame(step, label + ":up")
        dxy = math.hypot(x - self.pos[0], y - self.pos[1])
        if dxy > 0:
            if self.pos[2] < Z_SAFE - 0.5:
                self.errors.append(f"step {step}: XY travel below safe Z (collision risk)")
            self.t += move_time(dxy, FEED_XY)
            self.dist += dxy
            self.pos[0], self.pos[1] = x, y
            self._frame(step, label + ":xy")
        dz = abs(z - self.pos[2])
        if dz > 0:
            self.t += move_time(dz, FEED_Z)
            self.dist += dz
            self.pos[2] = z
            self._frame(step, label + ":z")

    def _container(self, name: str) -> dict:
        kind, *_ = loc(name)
        if kind == "reservoir":
            return self.res[name.strip().lower().replace("reservoir:", "").replace("res_", "")]
        key = name.strip().upper()
        return self.wells.setdefault(key, {"vol": 0.0, "comp": {}})

    # --- steps ---
    def step(self, i: int, s: dict) -> None:
        op = s.get("op")
        try:
            if op == "pick_tip":
                rw = s.get("rack_well", "A1").upper()
                if self.tip:
                    self.errors.append(f"step {i}: pick_tip while holding a tip")
                if rw in self.used_tips:
                    self.errors.append(f"step {i}: tip {rw} already used")
                x, y, zt = well_xyz(rw, TIP_RACK, Z_TIP)
                self._goto(x, y, zt, i, "pick_tip")
                self.t += 0.5
                self.tip = {"id": rw, "vol": 0.0, "comp": {}, "sources": set()}
                self.used_tips.add(rw)
                self._goto(x, y, Z_SAFE, i, "pick_tip")
            elif op == "aspirate":
                ul, src = float(s["ul"]), s["src"]
                if not self.tip:
                    self.errors.append(f"step {i}: aspirate without tip")
                    return
                kind, x, y, z = loc(src)
                c = self._container(src)
                if self.tip["sources"] and src.lower() not in self.tip["sources"] and kind == "reservoir":
                    self.errors.append(f"step {i}: contamination — tip {self.tip['id']} reused for {src} after {sorted(self.tip['sources'])}")
                if self.tip["vol"] + ul > TIP_CAP_UL + 1e-6:
                    self.errors.append(f"step {i}: tip overfill {self.tip['vol'] + ul:.0f} > {TIP_CAP_UL:.0f} µL")
                if ul > c["vol"] + 1e-6:
                    self.errors.append(f"step {i}: aspirate {ul} µL but {src} has {c['vol']:.0f} µL")
                    ul = c["vol"]
                self._goto(x, y, z, i, "aspirate")
                take = _take(c, ul * VOL_GAIN)
                _add(self.tip, take, ul * VOL_GAIN)
                self.tip["sources"].add(src.lower())
                self.t += ul / PIPETTE_UL_PER_S + 0.3
                self._frame(i, "aspirate", [src.upper()] if kind == "well" else [])
            elif op == "dispense":
                dst = s["dst"]
                if not self.tip:
                    self.errors.append(f"step {i}: dispense without tip")
                    return
                ul = float(s.get("ul", self.tip["vol"]))
                if ul > self.tip["vol"] + 1e-6:
                    self.errors.append(f"step {i}: dispense {ul} µL > tip holds {self.tip['vol']:.0f} µL")
                    ul = self.tip["vol"]
                kind, x, y, z = loc(dst)
                c = self._container(dst)
                if kind == "well" and c["vol"] + ul > PLATE["cap_ul"] + 1e-6:
                    self.errors.append(f"step {i}: well {dst} overflow {c['vol'] + ul:.0f} > {PLATE['cap_ul']:.0f} µL")
                self._goto(x, y, z, i, "dispense")
                take = _take(self.tip, ul)
                _add(c, take, ul)
                self.tip["sources"].add(dst.lower())
                self.t += ul / PIPETTE_UL_PER_S + 0.3
                self._frame(i, "dispense", [dst.upper()] if kind == "well" else [])
            elif op == "mix":
                w, ul, n = s["well"], float(s.get("ul", 50)), int(s.get("n", 3))
                if not self.tip:
                    self.errors.append(f"step {i}: mix without tip")
                    return
                c = self._container(w)
                if ul > c["vol"]:
                    self.warnings.append(f"step {i}: mix volume {ul} > well volume {c['vol']:.0f}")
                _, x, y, z = loc(w)
                self._goto(x, y, z, i, "mix")
                self.t += n * 2 * (ul / PIPETTE_UL_PER_S + 0.2)
                self.tip["sources"].add(w.lower())
                self._frame(i, "mix", [w.upper()])
            elif op == "eject_tip":
                if not self.tip:
                    self.warnings.append(f"step {i}: eject without tip")
                    return
                ex, ey, ez = eject_xyz()
                self._goto(ex, ey, ez, i, "eject")
                self._goto(ex, ey + 8, ez, i, "eject")
                self.tip = None
                self._goto(ex, ey + 8, Z_SAFE, i, "eject")
            elif op == "photo":  # static overhead phone: just lift the pipette out of the way and wait for a fresh frame
                if self.pos[2] < Z_SAFE:
                    self._goto(self.pos[0], self.pos[1], Z_SAFE, i, "photo")
                self.t += 2.0
                self._frame(i, "photo")
            elif op == "wait":
                self.t += float(s.get("s", 1))
                self._frame(i, "wait")
            else:
                self.errors.append(f"step {i}: unknown op {op}")
        except (KeyError, ValueError) as e:
            self.errors.append(f"step {i}: {op}: {e}")


def _take(c: dict, ul: float) -> dict[str, float]:
    if c["vol"] <= 0 or ul <= 0:
        return {}
    frac = min(1.0, ul / c["vol"])
    out = {k: v * frac for k, v in c["comp"].items()}
    for k in c["comp"]:
        c["comp"][k] -= out[k]
    c["vol"] = max(0.0, c["vol"] - ul)
    return out


def _add(c: dict, comp: dict[str, float], ul: float) -> None:
    # comp is stored as absolute µL of each dye stock
    if not comp and ul > 0:
        comp = {}
    for k, v in comp.items():
        c["comp"][k] = c["comp"].get(k, 0.0) + v
    c["vol"] += ul


def simulate(protocol: list[dict], goal: str = "", record_frames: bool = True) -> dict[str, Any]:
    sim = Sim(record_frames)
    # Reservoir comps as absolute µL: scale stock fraction by volume.
    for k, r in sim.res.items():
        r["comp"] = {k: r["vol"]}
    sim._frame(-1, "start")
    for i, s in enumerate(protocol):
        sim.step(i, s)
    if sim.tip:
        sim.warnings.append("protocol ends holding a tip")
    wells = {w: {"rgb": mix_rgb(st["comp"]), "vol": round(st["vol"], 1),
                 "comp": {k: round(v, 1) for k, v in st["comp"].items() if v > 0.01}}
             for w, st in sim.wells.items() if st["vol"] > 0}
    score, best_well, tgt = 0.0, None, target_rgb(goal) if goal else None
    if tgt and wells:
        best_well = min(wells, key=lambda w: color_dist(wells[w]["rgb"], tgt[1]))
        d = color_dist(wells[best_well]["rgb"], tgt[1])
        score = max(0.0, 1.0 - d / 441.7)
    elif wells:
        best_well = next(iter(wells))
        score = 0.5
    ok = not sim.errors
    if not ok:
        score *= 0.1
    return {
        "ok": ok, "errors": sim.errors, "warnings": sim.warnings,
        "duration_s": round(sim.t, 1), "travel_mm": round(sim.dist, 1),
        "wells": wells, "best_well": best_well,
        "predicted_rgb": wells[best_well]["rgb"] if best_well else None,
        "target": {"name": tgt[0], "rgb": list(tgt[1])} if tgt else None,
        "score": round(score, 4), "frames": sim.frames,
        "tips_used": sorted(sim.used_tips),
    }


# ---------------- deterministic variant generator ----------------
def dyes_for_goal(goal: str) -> list[str]:
    g = goal.lower()
    named = [d for d in ("red", "yellow", "blue", "water") if re.search(rf"\b{d}", g)]
    tgt = target_rgb(goal)
    tname = tgt[0] if tgt else ""
    default = {"orange": ["red", "yellow"], "green": ["blue", "yellow"], "purple": ["red", "blue"],
               "brown": ["red", "yellow", "blue"], "pink": ["red", "water"], "teal": ["blue", "yellow", "water"],
               "lime": ["yellow", "blue"], "black": ["red", "yellow", "blue"]}.get(tname)
    if named and (tname in named or not default or set(named) >= set(default)):
        return named if len(named) > 1 else named + ["water"]
    if default:
        return default if not named else list(dict.fromkeys(named + default))
    return named or ["red", "yellow"]


def protocol_from_ratios(dyes: list[str], ratios: list[float], total_ul: float, well: str, tip_start: int = 0, mix: bool = True) -> list[dict]:
    steps: list[dict] = []
    tip_i = tip_start
    s = sum(ratios) or 1.0
    vols = [round(total_ul * r / s) for r in ratios]
    active = [(d, v) for d, v in zip(dyes, vols) if v >= 5]
    for j, (d, v) in enumerate(active):
        tip = f"{ROWS[tip_i % 8]}{tip_i // 8 + 1}"
        tip_i += 1
        steps.append({"op": "pick_tip", "rack_well": tip})
        remaining = v
        while remaining > 0:
            chunk = min(remaining, TIP_CAP_UL)
            steps.append({"op": "aspirate", "src": d, "ul": chunk})
            steps.append({"op": "dispense", "dst": well, "ul": chunk})
            remaining -= chunk
        if mix and j == len(active) - 1:
            steps.append({"op": "mix", "well": well, "ul": min(100, total_ul / 2), "n": 3})
        steps.append({"op": "eject_tip"})
    steps.append({"op": "photo", "target": "plate"})
    return steps


def generate_variants(goal: str, n: int = 12, seed: int = 0) -> list[dict]:
    """Grid + seeded random over dye ratios and total volume. One well per variant."""
    rng = np.random.default_rng(seed)
    dyes = dyes_for_goal(goal)
    k = len(dyes)
    out = []
    grid_n = max(1, n // 2)
    for i in range(n):
        if k == 2 and i < grid_n:
            f = (i + 1) / (grid_n + 1)
            ratios = [f, 1 - f]
        else:
            ratios = list(rng.dirichlet(np.ones(k)))
        total = float(rng.choice([120, 150, 180, 200, 240]))
        well = f"{ROWS[i % 8]}{i // 8 + 1}"
        proto = protocol_from_ratios(dyes, ratios, total, well, tip_start=0)
        out.append({
            "id": f"v{i + 1:02d}",
            "params": {"dyes": dyes, "ratios": [round(float(r), 3) for r in ratios], "total_ul": total, "well": well},
            "protocol": proto,
        })
    return out
