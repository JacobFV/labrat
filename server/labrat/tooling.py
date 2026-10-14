"""Tool-rail tools on the CNC head (cad/syringe/syrhead/tools.py): put any tool over any deck target, snap the USB
microscope, probe a liquid surface with the capacitive probe.

Every tool has an offset from the syringe tip (deck_cal tools, nominal from cad/syringe/tools.json); the CNC moves
the spindle so the tool's point lands on the target: spindle = target(tip coords) - tool offset.
"""
from __future__ import annotations

from typing import Any

from . import deck_cal, sim
from .hub import hub

TOOL_NAMES = ("pipette", "microscope", "probe")
FOCUS_Z = {"microscope": -20.0}   # default work z for the lens over a target (teach your own: tools.microscope.dz)


def target_xy(target: str | None = None, x: float | None = None, y: float | None = None) -> tuple[float, float]:
    """deck point (pipette-tip coordinates) of a named target (well / stock / waste ...) or explicit x, y."""
    if target:
        _, sx, sy, sz = sim.loc(target)
        tx, ty, _ = deck_cal.spindle_to_tip(sx, sy, sz)
        return tx, ty
    if x is None or y is None:
        raise ValueError("give a target (e.g. 'B3', 'red') or x and y")
    return float(x), float(y)


def plan(tool: str, target: str | None = None, x: float | None = None, y: float | None = None,
         z: float | None = None) -> dict:
    """spindle move that puts ``tool`` over the target, checked against the CNC's bounds."""
    tx, ty = target_xy(target, x, y)
    tz = float(z) if z is not None else FOCUS_Z.get(tool, sim.Z_SAFE)
    sx, sy, sz = deck_cal.spindle_for_tool(tool, tx, ty, tz)
    sz = min(sz, sim.Z_SAFE)
    bad = [f"{ax}={v:.1f} outside [{lo:g},{hi:g}]" for ax, v in zip("xyz", (sx, sy, sz))
           for lo, hi in [sim.BOUNDS[ax]] if not lo - 1e-6 <= v <= hi + 1e-6]
    return {"tool": tool, "target": target or [tx, ty], "deck_xy": [round(tx, 2), round(ty, 2)],
            "spindle": [round(sx, 2), round(sy, 2), round(sz, 2)], "reachable": not bad, "why": "; ".join(bad)}


async def goto(tool: str, target: str | None = None, x: float | None = None, y: float | None = None,
               z: float | None = None) -> dict:
    p = plan(tool, target, x, y, z)
    if not p["reachable"]:
        raise ValueError(f"{tool} cannot reach {p['target']}: {p['why']}")
    sx, sy, sz = p["spindle"]
    await hub.cmd("cnc", "move", {"z": sim.Z_SAFE, "f": 600}, timeout=60)          # up, across, down
    await hub.cmd("cnc", "move", {"x": sx, "y": sy, "f": 2000}, timeout=60)
    await hub.cmd("cnc", "move", {"z": sz, "f": 600}, timeout=60)
    return p


async def snap(target: str | None = None, x: float | None = None, y: float | None = None,
               z: float | None = None) -> dict[str, Any]:
    p = await goto("microscope", target, x, y, z) if (target or x is not None) else None
    img = await hub.cmd("microscope", "snap", {}, timeout=20)
    return {"_image": img["image_b64"], "_mime": img.get("mime", "image/jpeg"), "w": img.get("w"), "h": img.get("h"),
            "at": p}


async def probe_level(target: str, depth: float = 45.0) -> dict:
    """lower the capacitive probe into a beaker until it touches liquid; return the surface height and the volume it
    implies (beaker geometry from the deck)."""
    p = await goto("probe", target, z=sim.Z_SAFE)
    r = await hub.cmd("cnc", "probe", {"dz": -abs(depth), "f": 60}, timeout=120)
    out = {"at": p, "hit": r.get("hit")}
    if r.get("hit"):
        dz = deck_cal.tool_offset("probe")[2]
        h = sim.SAFE_TIP_H + dz + float(r["z"])           # electrode tip above the bed at the trigger
        out.update(surface_h_mm=round(h, 2), est_ul=round(max(0.0, h - sim.BEAKER_FLOOR_H) * sim.BEAKER_UL_PER_MM))
    return out
