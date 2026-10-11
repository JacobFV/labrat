"""Locate and load ``config/robot.json`` (the single source of truth).

Search order:
1. ``$TAPPET_CONFIG`` (path to a robot.json file),
2. ``config/robot.json`` in any parent directory of this package (repo checkout),
3. ``config/robot.json`` in the current directory or its parents.
"""
from __future__ import annotations

import copy
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

ARM_NAMES = ("L", "R")
ARM_JOINTS = ("YAW", "SHOULDER", "ELBOW")
JOINT_NAMES = (
    "L_YAW", "L_SHOULDER", "L_ELBOW",
    "R_YAW", "R_SHOULDER", "R_ELBOW",
    "NECK_PAN", "NECK_TILT",
)


def _candidates() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("TAPPET_CONFIG")
    if env:
        out.append(Path(env))
    for base in [Path(__file__).resolve().parent, Path.cwd().resolve()]:
        for p in [base, *base.parents]:
            out.append(p / "config" / "robot.json")
    return out


def find_config_path() -> Path:
    for c in _candidates():
        if c.is_file():
            return c
    raise FileNotFoundError(
        "config/robot.json not found; set TAPPET_CONFIG=/path/to/robot.json")


def repo_root() -> Path:
    """Repository root (the directory containing config/)."""
    return find_config_path().resolve().parent.parent


def web_root() -> Path:
    env = os.environ.get("TAPPET_WEB")
    if env:
        return Path(env)
    return repo_root() / "web"


@lru_cache(maxsize=8)
def _load_cached(path: str, mtime: float) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Return a deep copy of robot.json as a dict (safe to mutate)."""
    p = Path(path) if path else find_config_path()
    return copy.deepcopy(_load_cached(str(p), p.stat().st_mtime))


def arm_joint_name(arm: str, j: str) -> str:
    return f"{arm}_{j}"


def default_screen(cfg: dict) -> dict:
    """Device + tray parameters used for the default screen calibration."""
    dev = cfg["default_device"]
    tray = cfg["tray"]
    return {
        "name": dev["name"],
        "screen_w": dev["screen_w"],
        "screen_l": dev["screen_l"],
        "screen_px_w": dev["screen_px_w"],
        "screen_px_h": dev["screen_px_h"],
        "surface_z": dev["surface_z"],
        "center_x": tray["center_x"],
        "center_y": tray["center_y"],
    }
