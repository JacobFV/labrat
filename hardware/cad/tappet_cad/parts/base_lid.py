"""Base lid (bottom plate): sits inside the base walls, 4x M3 countersunk into the corner bosses.
Carries the electronics: ESP32-S3-DevKitC-1 cradle, PCA9685 standoffs, cradles for the XL4015 (Buck A)
and Mini560 (Buck B), the PB power board (40x17) and the HEAD board (30x20, USB-C J2 on the rear wall),
CH224K PD-trigger cradle; vent slots under the bucks; recesses for 4 thin anti-slip pads."""
from __future__ import annotations

from ..lib.fasteners import m3_countersunk_z_bottom
from ..lib.util import box, cyl_z, rbox
from ..params import BASE_R, BASE_X0, BASE_X1, BASE_Y0, BASE_Y1, LID_T, WALL
from ._common import meta
from .base import (BOARD_Z, BOSS_XY, BUCK_SIZES, BUCKS, DEVKIT, DOVETAIL_X, HEAD_BOARD, PCA, PDTRIG,
                   POWER_BOARD, SOCKET_BLOCK)

META = meta("base_lid", 1, color="#bdc3c7", orient=(0, 0, 0),
            notes="Print flat, standoffs up. 3 walls, 15% infill.", infill=15)

def cradle(x0, y0, x1, y1, z, z_board, rear_open=False, c=0.3):
    """Corner rest pads (top at z_board) + L-shaped corner locators (3 mm above the board) + a zip-tie
    slot pair; independent of the module's hole pattern."""
    out = None
    for xx, yy, sx, sy in ((x0, y0, -1, -1), (x1, y0, 1, -1), (x0, y1, -1, 1), (x1, y1, 1, 1)):
        pad = box(min(xx, xx - sx * 5), max(xx, xx - sx * 5), min(yy, yy - sy * 5), max(yy, yy - sy * 5), z - 0.01, z_board)
        ex, ey = xx + sx * c, yy + sy * c
        loc = box(min(ex, ex + sx * 2), max(ex, ex + sx * 2), min(ey - sy * 6, ey + sy * 2), max(ey - sy * 6, ey + sy * 2),
                  z - 0.01, z_board + 3.0)
        loc = loc + box(min(ex - sx * 6, ex + sx * 2), max(ex - sx * 6, ex + sx * 2), min(ey, ey + sy * 2), max(ey, ey + sy * 2),
                        z - 0.01, z_board + 3.0)
        if rear_open and sy < 0:
            loc = None   # rear edge sits against the wall (connector side)
        part = pad if loc is None else pad + loc
        out = part if out is None else out + part
    return out


FEET = [(-80.0, -36.0), (80.0, -36.0), (-80.0, 50.0), (80.0, 50.0)]


def make():
    c = 0.3
    lid = rbox(BASE_X0 + WALL + c, BASE_X1 - WALL - c, BASE_Y0 + WALL + c, BASE_Y1 - WALL - c, 0, LID_T,
               BASE_R - WALL - c)
    for xc in DOVETAIL_X:
        lid = lid - box(xc - SOCKET_BLOCK["dx"] - c, xc + SOCKET_BLOCK["dx"] + c, SOCKET_BLOCK["y0"] - c, 100, -1, 10)
    for x, y in BOSS_XY:
        lid = lid - m3_countersunk_z_bottom(x, y, 0.0, LID_T + 1)
    for x, y in FEET:
        lid = lid - cyl_z(5.3, -1, 1.0, x, y)
    z = LID_T
    # PCA9685 standoffs (holes 55.9 x 19.1)
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = PCA["cx"] + sx * 9.55, PCA["cy"] + sy * 27.95
            lid = lid + cyl_z(2.8, z - 0.01, z + 5, x, y) - cyl_z(1.05, z, z + 6, x, y)
    # buck modules (XL4015 servo buck, Mini560 logic buck): hole-pattern-agnostic cradles + vents below
    for b, (bw, bl) in zip(BUCKS, BUCK_SIZES):
        x0, x1, y0, y1 = b["cx"] - bw / 2, b["cx"] + bw / 2, b["cy"] - bl / 2, b["cy"] + bl / 2
        lid = lid + cradle(x0, y0, x1, y1, z, z + 5.0)
        n = max(2, int((bw - 8) // 6))
        for i in range(n):
            x = x0 + 5 + i * (bw - 10) / max(n - 1, 1)
            lid = lid - rbox(x - 1.2, x + 1.2, y0 + 4, y1 - 4, -1, LID_T + 1, 1.1)
    # PB power board (40 x 17) and HEAD board (30 x 20, J2 on the rear wall)
    for bd in (POWER_BOARD, HEAD_BOARD):
        lid = lid + cradle(bd["x0"], bd["y0"] + 0.4, bd["x1"], bd["y1"], z, BOARD_Z, rear_open=True)
    # DevKitC-1: sits on its pin headers; centre rest rails + corner locators
    d = DEVKIT
    x0, x1 = d["cx"] - d["w"] / 2, d["cx"] + d["w"] / 2
    y0, y1 = d["y0"], d["y0"] + d["l"]
    zb = d["z_board"]
    for yy in (y0 + 6, y1 - 6):
        lid = lid + box(d["cx"] - 5, d["cx"] + 5, yy - 4, yy + 4, z - 0.01, zb)
    for xx, yy in ((x0, y0 + 10), (x1, y0 + 10), (x0, y1 - 4), (x1, y1 - 4)):
        sgn = -1 if xx == x0 else 1
        lid = lid + box(xx + sgn * 0.3, xx + sgn * 2.8, yy - 4, yy + 4, z - 0.01, zb + 4)
    lid = lid + box(x0 - 2.8, x1 + 2.8, y1 + 0.3, y1 + 2.8, z - 0.01, zb + 4)
    # zip-tie slots under the boards
    for b, (bw, bl) in list(zip(BUCKS, BUCK_SIZES))[:1]:
        for sx in (-1, 1):
            lid = lid - box(b["cx"] + sx * 4 - 0.8, b["cx"] + sx * 4 + 0.8, b["cy"] - bl / 2 - 3.5, b["cy"] - bl / 2 - 1.0, -1, LID_T + 1)
    # CH224K PD trigger cradle
    p = PDTRIG
    px0, px1 = p["cx"] - p["w"] / 2 - 0.3, p["cx"] + p["w"] / 2 + 0.3
    py0, py1 = p["y0"], p["y0"] + p["l"] + 0.3
    pd_cradle = box(px0 - 2, px1 + 2, py0 + 2, py1 + 2, z - 0.01, p["z_board"]) - box(px0, px1, py0, py1, p["z_board"] - 1.5, 30)
    pd_cradle = pd_cradle - box(px0 + 2, px1 - 2, py0 - 1, py1 - 2, z + 1, 30)
    lid = lid + pd_cradle
    # trim anything that would poke past the lid outline (e.g. HEAD-board pads near the rounded corner)
    lid = lid & rbox(BASE_X0 + WALL + c, BASE_X1 - WALL - c, BASE_Y0 + WALL + c, BASE_Y1 - WALL - c, -1, 40,
                     BASE_R - WALL - c)
    return lid
