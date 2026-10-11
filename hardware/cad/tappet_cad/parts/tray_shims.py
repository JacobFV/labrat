"""Swappable tray shims: frames that sit on the tray floor and centre a specific phone in the pocket
(phone centre == tray centre, as assumed by the default screen homography).  Walls thinner than
0.8 mm are omitted.  World frame (placed in the pocket)."""
from __future__ import annotations

from ..lib.phones import DEVICES
from ..lib.util import box, rbox
from ..params import TRAY_CX, TRAY_CY, TRAY_FLOOR_Z, TRAY_POCKET_L, TRAY_POCKET_W
from ._common import meta
from .tray import CABLE_NOTCH_Y

SHIM_H = 5.0
C = 0.15  # clearance on each contact face
MIN_WALL = 0.8

SHIMS = {
    "tray_shim_iphone_61": ("iphone_15_61", "iPhone 15 / 15 Pro / 16 / 16 Pro (6.1in)"),
    "tray_shim_iphone_67": ("iphone_15_plus_67", "iPhone 15/16 Plus, 15 Pro Max (6.7in); 16 Pro Max needs no shim"),
    "tray_shim_iphone_se": ("iphone_se", "iPhone SE 2nd/3rd gen"),
}
METAS = {k: meta(k, 1, color="#f7dc6f", orient=(0, 0, 0), notes=f"For {v[1]}. Print flat, 100% infill (thin walls).",
                 infill=100) for k, v in SHIMS.items()}
META = METAS["tray_shim_iphone_61"]


def make_shim(key):
    dev = SHIMS[key][0]
    w, l, t, r = DEVICES[dev]
    ox0, ox1 = TRAY_CX - TRAY_POCKET_W / 2 + C, TRAY_CX + TRAY_POCKET_W / 2 - C
    oy0, oy1 = TRAY_CY - TRAY_POCKET_L / 2 + C, TRAY_CY + TRAY_POCKET_L / 2 - C
    ix0, ix1 = TRAY_CX - w / 2 - C, TRAY_CX + w / 2 + C
    iy0, iy1 = TRAY_CY - l / 2 - C, TRAY_CY + l / 2 + C
    z0, z1 = TRAY_FLOOR_Z, TRAY_FLOOR_Z + SHIM_H
    s = rbox(ox0, ox1, oy0, oy1, z0, z1, 2.8)
    s = s - rbox(ix0, ix1, iy0, iy1, z0 - 1, z1 + 1, r)
    side_wall = ix0 - ox0
    end_wall = iy0 - oy0
    if end_wall < MIN_WALL:
        s = s - box(ix0, ix1, oy0 - 1, oy1 + 1, z0 - 1, z1 + 1)
    # cable relief at the rear end (phone port) and to the side notches
    if end_wall >= MIN_WALL:
        s = s - box(TRAY_CX - 8, TRAY_CX + 8, oy0 - 1, iy0 + 1, z0 + 1.2, z1 + 1)
    s = s - box(ox0 - 1, ox1 + 1, CABLE_NOTCH_Y[0] + 1, CABLE_NOTCH_Y[1] - 1, z0 + 1.2, z1 + 1) \
        if side_wall >= MIN_WALL and end_wall > 3 else s
    return s
