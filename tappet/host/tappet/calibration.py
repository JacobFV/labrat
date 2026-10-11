"""Screen calibration (SPEC §6): homographies, default calibration from tray + device profile,
touch-probe auto-calibration, and camera-corner homography.

Calibration object (JSON, shared with the web UI / Android head)::

    {"version": 1,
     "device": {name, screen_w, screen_l, screen_px_w, screen_px_h, surface_z},
     "H_sw": 3x3  [u,v,1] -> world [x,y,1] (mm),
     "surface_z": mm,
     "H_ic": null | 3x3  camera pixel -> [u,v],
     "camera_size": null | [w,h],
     "source": "default" | "auto" | "manual",
     "rms_mm": null | float,
     "touch_size": null | [w,h]}
"""
from __future__ import annotations

import copy
import json
import math
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from .config import load_config

# ---------------------------------------------------------------------------
# Homography (normalized DLT, least squares)
# ---------------------------------------------------------------------------


def _normalize(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    c = pts.mean(axis=0)
    d = np.sqrt(((pts - c) ** 2).sum(axis=1)).mean()
    s = math.sqrt(2) / d if d > 1e-12 else 1.0
    T = np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1.0]])
    ph = np.c_[pts, np.ones(len(pts))] @ T.T
    return ph, T


def homography_dlt(src: Sequence[Sequence[float]], dst: Sequence[Sequence[float]]) -> np.ndarray:
    """Least-squares homography H with dst ~ H @ src (Hartley-normalized DLT, >= 4 points)."""
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 2 or len(src) < 4:
        raise ValueError("need >= 4 matching 2-D point pairs")
    sn, Ts = _normalize(src)
    dn, Td = _normalize(dst)
    A = []
    for (x, y, _), (u, v, _) in zip(sn, dn):
        A.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        A.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, _, Vt = np.linalg.svd(np.asarray(A))
    Hn = Vt[-1].reshape(3, 3)
    H = np.linalg.inv(Td) @ Hn @ Ts
    if abs(H[2, 2]) < 1e-12:
        raise ValueError("degenerate homography")
    return H / H[2, 2]


def apply_h(H, pts) -> np.ndarray:
    """Apply homography to an (N,2) array (or a single point) -> (N,2) / (2,)."""
    H = np.asarray(H, dtype=float)
    p = np.asarray(pts, dtype=float)
    single = p.ndim == 1
    p = np.atleast_2d(p)
    ph = np.c_[p, np.ones(len(p))] @ H.T
    out = ph[:, :2] / ph[:, 2:3]
    return out[0] if single else out


def h_to_list(H) -> list[list[float]]:
    return [[float(round(v, 10)) for v in row] for row in np.asarray(H, dtype=float)]


def rigid_screen_h(center_x: float, center_y: float, screen_w: float, screen_l: float,
                   rot_deg: float = 0.0) -> np.ndarray:
    """H_sw for a screen lying screen-up, portrait, top edge toward +y, rotated by rot_deg (CCW).

    u (0 left -> 1 right as seen by a person facing the screen from the robot side) maps to +x,
    v (0 top -> 1 bottom) maps to -y.
    """
    c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    # local (lx, ly) = ((u-0.5)*w, (0.5-v)*l); world = R @ local + center
    A = np.array([[screen_w, 0, -screen_w / 2], [0, -screen_l, screen_l / 2], [0, 0, 1.0]])
    R = np.array([[c, -s, center_x], [s, c, center_y], [0, 0, 1.0]])
    return R @ A


# ---------------------------------------------------------------------------
# Calibration object
# ---------------------------------------------------------------------------


def default_calibration(cfg: dict | None = None, device: dict | None = None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    dev = dict(cfg["default_device"])
    if device:
        dev.update(device)
    tray = cfg["tray"]
    H = rigid_screen_h(tray["center_x"], tray["center_y"], dev["screen_w"], dev["screen_l"])
    return {
        "version": 1,
        "device": {k: dev[k] for k in ("name", "screen_w", "screen_l", "screen_px_w",
                                        "screen_px_h", "surface_z")},
        "H_sw": h_to_list(H),
        "surface_z": float(dev["surface_z"]),
        "H_ic": None,
        "camera_size": None,
        "source": "default",
        "rms_mm": None,
        "touch_size": None,
    }


def uv_to_world(cal: dict, u: float, v: float) -> tuple[float, float]:
    x, y = apply_h(cal["H_sw"], [u, v])
    return float(x), float(y)


def world_to_uv(cal: dict, x: float, y: float) -> tuple[float, float]:
    u, v = apply_h(np.linalg.inv(np.asarray(cal["H_sw"], dtype=float)), [x, y])
    return float(u), float(v)


def uv_error_mm(cal: dict, u0: float, v0: float, u1: float, v1: float) -> tuple[float, float, float]:
    """(dx_mm, dy_mm, err_mm) between two screen points, in screen axes (right, down)."""
    w = cal["device"]["screen_w"]
    l = cal["device"]["screen_l"]
    dx = (u1 - u0) * w
    dy = (v1 - v0) * l
    return dx, dy, math.hypot(dx, dy)


def validate_calibration(cal: dict) -> dict:
    for k in ("H_sw", "surface_z", "device"):
        if k not in cal:
            raise ValueError(f"calibration missing {k}")
    H = np.asarray(cal["H_sw"], dtype=float)
    if H.shape != (3, 3) or not np.all(np.isfinite(H)) or abs(np.linalg.det(H)) < 1e-9:
        raise ValueError("H_sw must be an invertible 3x3 matrix")
    if cal.get("H_ic") is not None:
        Hc = np.asarray(cal["H_ic"], dtype=float)
        if Hc.shape != (3, 3) or not np.all(np.isfinite(Hc)):
            raise ValueError("H_ic must be a 3x3 matrix")
    float(cal["surface_z"])
    return cal


def merge_calibration(cal: dict, patch: dict, cfg: dict | None = None) -> dict:
    """Apply a POST /api/calibration body to ``cal`` and return the new calibration."""
    if patch.get("reset"):
        return default_calibration(cfg)
    new = copy.deepcopy(cal)
    if "camera_corners" in patch:
        corners = patch["camera_corners"]
        new["H_ic"] = h_to_list(camera_homography(corners))
        if patch.get("camera_size"):
            new["camera_size"] = list(patch["camera_size"])
    for k, v in patch.items():
        if k in ("camera_corners", "reset"):
            continue
        if k == "device" and isinstance(v, dict):
            new["device"].update(v)
        else:
            new[k] = v
    if "H_sw" in patch or "surface_z" in patch:
        new["source"] = patch.get("source", "manual")
    validate_calibration(new)
    return new


def camera_homography(corners_px: Sequence[Sequence[float]]) -> np.ndarray:
    """H_ic from the 4 screen corners clicked in a camera image (TL, TR, BR, BL) -> (u,v)."""
    if len(corners_px) != 4:
        raise ValueError("need 4 corners (TL, TR, BR, BL)")
    return homography_dlt(corners_px, [[0, 0], [1, 0], [1, 1], [0, 1]])


def load_calibration(path: str | Path, cfg: dict | None = None) -> dict:
    p = Path(path)
    if p.is_file():
        try:
            return validate_calibration(json.loads(p.read_text()))
        except Exception:
            pass
    return default_calibration(cfg)


def save_calibration(cal: dict, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cal, indent=2))


# ---------------------------------------------------------------------------
# Touch-probe auto-calibration
# ---------------------------------------------------------------------------


class CalibrationError(Exception):
    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg


def grid_uv(n: int = 3, margin: float = 0.15) -> list[tuple[float, float]]:
    vals = [margin + (1 - 2 * margin) * i / (n - 1) for i in range(n)]
    return [(u, v) for v in vals for u in vals]


def auto_calibrate(client, touch_log, cal: dict, arm: str = "auto", n: int = 3,
                   margin: float = 0.15, timeout: float = 4.0, hold_ms: int = 80,
                   log: Callable[[str], None] | None = None) -> dict:
    """Touch-probe auto-calibration.

    Taps an n x n grid of world points (predicted from the current calibration), records where
    the touchpad page saw each touch, and fits H_sw (touched uv -> commanded world xy) by DLT.
    Returns ``{"calibration", "points", "rms_mm", "max_mm"}``.
    """
    log = log or (lambda s: None)
    z = float(cal["surface_z"])
    points = []
    for (u, v) in grid_uv(n, margin):
        x, y = uv_to_world(cal, u, v)
        mark = touch_log.last_seq
        client.tap(x, y, z, arm=arm, hold_ms=hold_ms, wait=True, timeout=30)
        ev = touch_log.wait_for(mark, timeout)
        if ev is None:
            log(f"  no touch for ({u:.2f},{v:.2f}) -> ({x:.1f},{y:.1f})")
            continue
        points.append({"u": u, "v": v, "x": x, "y": y, "tu": ev["u"], "tv": ev["v"],
                       "touch_size": [ev["w"], ev["h"]]})
        log(f"  ({u:.2f},{v:.2f}) world ({x:.1f},{y:.1f}) -> touch ({ev['u']:.3f},{ev['v']:.3f})")
    if len(points) < 4:
        raise CalibrationError("no_touch", f"only {len(points)} of {n * n} probe taps registered; "
                               "is touchpad.html open full-screen on the target phone?")
    src = [[p["tu"], p["tv"]] for p in points]
    dst = [[p["x"], p["y"]] for p in points]
    H = homography_dlt(src, dst)
    new = copy.deepcopy(cal)
    new["H_sw"] = h_to_list(H)
    new["source"] = "auto"
    new["touch_size"] = points[-1]["touch_size"]
    pred = apply_h(H, src)
    res = np.hypot(pred[:, 0] - np.asarray(dst)[:, 0], pred[:, 1] - np.asarray(dst)[:, 1])
    # error of the *old* calibration at each probe = where it touched vs where it meant to
    for p in points:
        p["err_mm"] = uv_error_mm(cal, p["u"], p["v"], p["tu"], p["tv"])[2]
        del p["touch_size"]
    new["rms_mm"] = float(np.sqrt((res ** 2).mean()))
    new["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return {"calibration": new, "points": points,
            "rms_mm": new["rms_mm"], "max_mm": float(max(p["err_mm"] for p in points))}
