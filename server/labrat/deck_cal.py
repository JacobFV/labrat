"""Deck calibration: pipette-tip offset (0,0,0 for the coaxial syringe head) + taught deck positions -> spindle (CNC) coordinates.

Taught points are CNC machine coords of the PIPETTE TIP. The CNC moves the spindle, so
spindle = tip - pipette_offset. Untaught targets fall back to the nominal deck (docs/ARCHITECTURE.md §6),
whose numbers are spindle coords (so an uncalibrated deck behaves exactly as before).
If both corners of a grid are taught (A1 + the far corner: tip_rack.H12, plate.F7), pitch and rotation are derived from
them; otherwise the grid's nominal pitch (tip rack 9 mm, beaker matrix 19 mm), axis-aligned, is used from the taught A1.

Persistence: JSON file (server/data/deck_cal.json, or $LABRAT_DECK_CAL) and, when MongoDB is
connected, the `config` collection doc with id "deck_calibration".
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import time
from pathlib import Path

from .config import ROOT, log

DOC_ID = "deck_calibration"
PATH = Path(os.getenv("LABRAT_DECK_CAL") or (ROOT / "server" / "data" / "deck_cal.json"))
ROWS = "ABCDEFGH"
# grid: (rows, cols, nominal pitch). "plate" is the mixing grid: columns 1-7 of the printed beaker matrix (cad/deck,
# DECK-001); its column 8 holds the stock beakers (the reservoirs).
GRIDS = {"tip_rack": (8, 12, 9.0), "plate": (6, 7, 19.0)}
TARGETS = ["tip_rack.A1", "tip_rack.H12", "plate.A1", "plate.F7",
           "reservoir.water", "reservoir.red", "reservoir.blue", "reservoir.yellow", "waste", "eject_bar"]

# Tool-rail modules (cad/syringe/tools.json): tool point relative to the syringe tip, nominal from the CAD.
# Teach them like the pipette offset (jog the tool over a mark, then the syringe tip over the same mark).
TOOLS_NOMINAL = {"microscope": {"dx": -16.0, "dy": -63.0, "dz": 25.0},   # lens front (TM-110 at rail index -16)
                 "probe": {"dx": 11.0, "dy": -42.5, "dz": 0.0}}         # electrode tip (TM-120 at rail index +11)
DEFAULT = {"id": DOC_ID, "pipette_offset": {"dx": 0.0, "dy": 0.0, "dz": 0.0}, "points": {},
           "tools": copy.deepcopy(TOOLS_NOMINAL)}

_cal: dict | None = None


def _db():
    try:
        from .store import store
        return store.db
    except Exception:  # noqa: BLE001
        return None


def load() -> dict:
    global _cal
    cal = None
    db = _db()
    if db is not None:
        try:
            cal = db.config.find_one({"id": DOC_ID}, {"_id": 0})
        except Exception as e:  # noqa: BLE001
            log.warning(f"deck_cal mongo load failed: {e}")
    if cal is None and PATH.exists():
        try:
            cal = json.loads(PATH.read_text())
        except Exception as e:  # noqa: BLE001
            log.warning(f"deck_cal file load failed: {e}")
    _cal = _normalize(cal or {})
    return _cal


def _normalize(c: dict) -> dict:
    out = copy.deepcopy(DEFAULT)
    off = c.get("pipette_offset") or {}
    for k in ("dx", "dy", "dz"):
        if k in off:
            out["pipette_offset"][k] = float(off[k])
    for name, t in (c.get("tools") or {}).items():
        out["tools"].setdefault(name, {"dx": 0.0, "dy": 0.0, "dz": 0.0})
        out["tools"][name].update({k: float(t[k]) for k in ("dx", "dy", "dz") if k in t})
    for name, p in (c.get("points") or {}).items():
        out["points"][name] = {k: float(p[k]) for k in ("x", "y", "z") if k in p}
        if "ts" in p:
            out["points"][name]["ts"] = p["ts"]
    return out


def get() -> dict:
    return _cal if _cal is not None else load()


def save(cal: dict) -> dict:
    global _cal
    _cal = _normalize(cal)
    _cal["ts"] = time.time()
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(_cal, indent=2))
    except Exception as e:  # noqa: BLE001
        log.warning(f"deck_cal file save failed: {e}")
    db = _db()
    if db is not None:
        try:
            db.config.replace_one({"id": DOC_ID}, dict(_cal), upsert=True)
        except Exception as e:  # noqa: BLE001
            log.warning(f"deck_cal mongo save failed: {e}")
    return _cal


def reset(cal: dict | None = None) -> dict:
    """Set in-memory calibration without persisting (tests)."""
    global _cal
    _cal = _normalize(cal or {})
    return _cal


def update(patch: dict) -> dict:
    cal = copy.deepcopy(get())
    if "pipette_offset" in patch:
        cal["pipette_offset"].update({k: float(v) for k, v in patch["pipette_offset"].items() if k in ("dx", "dy", "dz")})
    for name, t in (patch.get("tools") or {}).items():
        cal["tools"].setdefault(name, {"dx": 0.0, "dy": 0.0, "dz": 0.0})
        cal["tools"][name].update({k: float(v) for k, v in t.items() if k in ("dx", "dy", "dz")})
    for name, p in (patch.get("points") or {}).items():
        if p is None:
            cal["points"].pop(name, None)
        else:
            cal["points"][canon(name)] = {k: float(p[k]) for k in ("x", "y", "z") if k in p}
    return save(cal)


def canon(target: str) -> str:
    t = target.strip()
    m = re.fullmatch(r"(tip_rack|plate)\.([A-Ha-h])(\d{1,2})", t)
    if m:
        return f"{m.group(1)}.{m.group(2).upper()}{int(m.group(3))}"
    if t.lower() in ("water", "red", "blue", "yellow"):
        return "reservoir." + t.lower()
    return t


def teach(target: str, spindle: tuple[float, float, float]) -> dict:
    """Record target's tip position = current spindle position + pipette offset."""
    o = get()["pipette_offset"]
    x, y, z = spindle
    tip = {"x": round(x + o["dx"], 3), "y": round(y + o["dy"], 3), "z": round(z + o["dz"], 3), "ts": time.time()}
    cal = copy.deepcopy(get())
    cal["points"][canon(target)] = tip
    save(cal)
    return tip


# ---------------- transforms ----------------
def tip_to_spindle(x: float, y: float, z: float, cal: dict | None = None) -> tuple[float, float, float]:
    o = (cal or get())["pipette_offset"]
    return x - o["dx"], y - o["dy"], z - o["dz"]


def tool_offset(tool: str, cal: dict | None = None) -> tuple[float, float, float]:
    """a tool's point relative to the spindle reference (pipette offset + the tool's offset from the syringe tip)."""
    cal = cal or get()
    p = cal["pipette_offset"]
    if tool in ("pipette", "", None):
        return p["dx"], p["dy"], p["dz"]
    t = cal["tools"].get(tool)
    if t is None:
        raise ValueError(f"unknown tool {tool!r} (known: pipette, {', '.join(cal['tools'])})")
    return p["dx"] + t["dx"], p["dy"] + t["dy"], p["dz"] + t["dz"]


def spindle_for_tool(tool: str, x: float, y: float, z: float, cal: dict | None = None) -> tuple[float, float, float]:
    """spindle position that puts ``tool``'s point at deck point (x, y, z) (pipette-tip coordinates)."""
    o = tool_offset(tool, cal)
    return x - o[0], y - o[1], z - o[2]


def spindle_to_tip(x: float, y: float, z: float, cal: dict | None = None) -> tuple[float, float, float]:
    o = (cal or get())["pipette_offset"]
    return x + o["dx"], y + o["dy"], z + o["dz"]


def grid_frame(grid: str, cal: dict | None = None) -> dict | None:
    """Derived grid frame in tip coords: {a1:(x,y,z), pitch, theta_deg, h12z? (far-corner z)} or None if A1 untaught."""
    cal = cal or get()
    a1 = cal["points"].get(f"{grid}.A1")
    if not a1:
        return None
    rows, cols, nominal = GRIDS[grid]
    h12 = cal["points"].get(f"{grid}.{ROWS[rows - 1]}{cols}")
    pitch, theta = nominal, 0.0
    if h12:
        vx, vy = h12["x"] - a1["x"], h12["y"] - a1["y"]
        nx, ny = (cols - 1) * nominal, (rows - 1) * nominal
        n = math.hypot(nx, ny)
        m = math.hypot(vx, vy)
        if m > 1e-6:
            pitch = nominal * m / n
            theta = math.atan2(vy, vx) - math.atan2(ny, nx)
    return {"a1": (a1["x"], a1["y"], a1.get("z", 0.0)), "h12z": h12.get("z") if h12 else None,
            "pitch": pitch, "theta": theta, "theta_deg": math.degrees(theta), "rows": rows, "cols": cols}


def grid_spindle(grid: str, r: int, c: int, nominal_xy: tuple[float, float], nominal_z: float,
                 cal: dict | None = None) -> tuple[float, float, float]:
    cal = cal or get()
    f = grid_frame(grid, cal)
    if not f:
        return nominal_xy[0], nominal_xy[1], nominal_z
    ax, ay, az = f["a1"]
    u, v = c * f["pitch"], r * f["pitch"]
    ct, st = math.cos(f["theta"]), math.sin(f["theta"])
    x, y = ax + u * ct - v * st, ay + u * st + v * ct
    z = az
    if f["h12z"] is not None:
        k = 0.5 * (r / (f["rows"] - 1) + c / (f["cols"] - 1))
        z = az + k * (f["h12z"] - az)
    return tip_to_spindle(x, y, z, cal)


def point_spindle(target: str, nominal: tuple[float, float, float], cal: dict | None = None) -> tuple[float, float, float]:
    cal = cal or get()
    p = cal["points"].get(canon(target))
    if not p:
        return nominal
    return tip_to_spindle(p["x"], p["y"], p.get("z", nominal[2]), cal)


def summary() -> dict:
    cal = get()
    return {**cal, "targets": TARGETS, "grids": {g: _fmt(grid_frame(g, cal)) for g in GRIDS},
            "spindle": {k: dict(zip("xyz", (round(v, 3) for v in tip_to_spindle(p["x"], p["y"], p.get("z", 0.0), cal))))
                        for k, p in cal["points"].items()}}


def _fmt(f):
    if not f:
        return None
    return {"pitch": round(f["pitch"], 4), "theta_deg": round(f["theta_deg"], 4), "a1": f["a1"]}
