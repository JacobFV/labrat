"""Skeleton chassis (replaces the old closed base + lid).

A flat, open truss printed in one piece on the bed: thin T-section beams (flange on the desk + a low
rib) tie together the two 608 bearing towers (turret yaw axes), the two SG90 yaw-servo post pairs
(gear centre distance unchanged), the column-dowel socket, the two tray lugs and open-mounted
electronics cradles (no enclosure).  World frame; the chassis underside is the desk plane z = 0.

The module-level layout constants are also read (via ``ast``) by hardware/electronics/gen_drawings.py,
so keep their names: BASE_*, WALL, DECK_Z, DEVKIT, PCA, BUCKS, PDTRIG, COLUMN_HOLES, COLUMN_CABLE,
GEAR_CENTER, DOVETAIL_X, SOCKET_BLOCK.
"""
from __future__ import annotations

import math

from build123d import Align, Cone, Pos

from ..lib.util import box, cyl_x, cyl_y, cyl_z, rbox, stadium, union_all
from build123d import extrude
from ..params import ARM_BASE, BEARING_608, DECK_Z, M2_TAP, M3_TAP, yaw_pinion_xy
from ._common import SMALL_SERVO, meta
from .yaw_gear_servo import YAW_SERVO_Z

META = meta("chassis", 1, color="#ececec", orient=(0, 0, 0),
            notes="Print as modelled (flat on the bed, towers up). No supports, skirt only. "
                  "0.28 mm draft, 2 walls, 10% infill (it is almost all walls anyway).", infill=10, walls=2, layer=0.28)

# ---- envelope (used by the electronics drawing; the chassis is open, WALL = drawing margin) -------
BASE_X0, BASE_X1 = -100.0, 100.0
BASE_Y0, BASE_Y1 = -50.0, 70.0
BASE_R = 14.0
WALL = 2.6
GEAR_CENTER = 25.0

# ---- beams ----------------------------------------------------------------------------------------
FLANGE_W, FLANGE_T = 5.0, 1.2      # flat strip on the desk
RIB_W, RIB_H = 1.6, 4.0            # stiffening rib on main beams (below every board / servo)

# ---- mounts ---------------------------------------------------------------------------------------
TOWER_R = 12.8                     # bearing seat ring OD/2 (608 OD 22 + 0.2)
DOWEL_D = 10.0                     # column: 10 mm hardwood dowel / aluminium tube
DOWEL_XY = (0.0, -15.25)           # under the pan-servo body centre (pan axis y - shaft offset)
DOWEL_Z0 = 2.0                     # dowel bottom (socket floor)
SOCKET_H = 15.0
DOVETAIL_X = (-30.0, 30.0)         # tray lugs (name kept for the electronics drawing)
SOCKET_BLOCK = dict(dx=8.0, y0=62.0)
LUG = dict(y0=62.0, y1=BASE_Y1, z1=8.0, pin_dx=-4.0, screw_dx=4.0, y=66.0, pin_d=3.8, pin_h=2.5)
COLUMN_HOLES = [(-9.0, -24.25), (9.0, -24.25), (-9.0, -6.25), (9.0, -6.25)]   # dowel boss footprint
COLUMN_CABLE = DOWEL_XY            # neck leads run down the dowel
FEET = [(-93.0, -44.0), (93.0, -44.0), (-40.0, 62.0), (40.0, 62.0)]

# ---- electronics (open-mounted cradles / standoffs) ----------------------------------------------
DEVKIT = dict(cx=-23.0, y0=-46.6, w=25.5, l=69.0, z_board=13.6)
PCA = dict(cx=29.0, cy=3.0, w=25.4, l=62.2)
# [0] = Buck A XL4015 (~51 x 26, servo rail), [1] = Buck B Mini560 (~22 x 17, logic)
BUCKS = [dict(cx=70.0, cy=-1.0), dict(cx=62.0, cy=-36.0)]
BUCK_SIZES = [(51.0, 26.3), (22.0, 17.0)]
PDTRIG = dict(cx=40.0, y0=-46.6, w=10.0, l=20.0, z_board=5.6)
POWER_BOARD = dict(x0=-7.0, y0=-47.0, x1=33.0, y1=-30.0)      # PB 40 x 17 perfboard
HEAD_BOARD = dict(x0=-95.0, y0=-47.0, x1=-65.0, y1=-27.0)     # 30 x 20, USB-C J2 at x = -80 (rear edge)
BOARD_Z = 5.6
USB_PORTS = [(-80.0, "HEAD (J2 on the HEAD board -> Android phone)"),
             (-16.0, "PC (DevKit UART port)"), (40.0, "POWER IN (CH224K USB-C PD)")]


def beam(p, q, rib=True, w=FLANGE_W):
    f = extrude(stadium(p, q, w), FLANGE_T)
    if rib:
        f = f + extrude(stadium(p, q, RIB_W), RIB_H)
    return f


def pad(x0, y0, x1, y1):
    return box(x0, x1, y0, y1, 0, FLANGE_T)


def cradle(x0, y0, x1, y1, z_board, rear_open=False, c=0.3, z=FLANGE_T):
    """Corner rest pads (top at z_board) + L corner locators 3 mm above the board (pattern-agnostic)."""
    parts = []
    for xx, yy, sx, sy in ((x0, y0, -1, -1), (x1, y0, 1, -1), (x0, y1, -1, 1), (x1, y1, 1, 1)):
        parts.append(box(min(xx, xx - sx * 2.5), max(xx, xx - sx * 2.5), min(yy, yy - sy * 2.5), max(yy, yy - sy * 2.5),
                         z - 0.01, z_board))
        if rear_open and sy < 0:
            continue
        ex, ey = xx + sx * c, yy + sy * c
        parts.append(box(min(ex, ex + sx * 1.2), max(ex, ex + sx * 1.2), min(ey - sy * 3, ey + sy * 1.2),
                         max(ey - sy * 3, ey + sy * 1.2), z - 0.01, z_board + 1.6))
        parts.append(box(min(ex - sx * 3, ex + sx * 1.2), max(ex - sx * 3, ex + sx * 1.2), min(ey, ey + sy * 1.2),
                         max(ey, ey + sy * 1.2), z - 0.01, z_board + 1.6))
        # pad under the corner on the desk plane
        parts.append(pad(min(xx - sx * 3.0, xx + sx * 1.6), min(yy - sy * 3.0, yy + sy * 1.6),
                         max(xx - sx * 3.0, xx + sx * 1.6), max(yy - sy * 3.0, yy + sy * 1.6)))
    return union_all(parts)


def tower(bx, by):
    """608 seat: ring 23..30 with a 45-deg lip below, on three legs."""
    zt = DECK_Z
    zp = DECK_Z - BEARING_608["B"]            # pocket floor (bearing seat) z = 23
    r_in = BEARING_608["D"] / 2 + 0.1         # 11.1
    r_lip = 7.0
    ring = cyl_z(TOWER_R, zp - (r_in - r_lip) - 0.3, zt, bx, by)
    legs = []
    for a in (90.0, 210.0, 330.0):
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        cx, cy = bx + (TOWER_R - 1.5) * ca, by + (TOWER_R - 1.5) * sa
        legs.append(Pos(cx, cy, 0) * extrude(stadium((-2.0 * sa, 2.0 * ca), (2.0 * sa, -2.0 * ca), 3.0), zp - 2.0))
    t = ring + union_all(legs) + (cyl_z(TOWER_R + 1.5, 0, FLANGE_T, bx, by) - cyl_z(TOWER_R - 2.0, -1, 5, bx, by))
    t = t - cyl_z(r_in, zp, zt + 1, bx, by)
    hl = r_in - r_lip
    lip = Pos(bx, by, zp - hl) * Cone(r_in, r_lip, hl, align=(Align.CENTER, Align.CENTER, Align.MIN))
    t = t - lip - cyl_z(r_in, -1, zp - hl + 0.01, bx, by) - cyl_z(r_lip, -1, zt + 1, bx, by)
    # keep the 45-degree lip only as three tabs over the legs (the bearing's outer race sits on them)
    keep = []
    for a in (90.0, 210.0, 330.0):
        keep.append(Pos(bx, by, 0) * box(r_lip - 1.0, TOWER_R + 1.0, -3.0, 3.0, zp - hl - 2, zp + 0.02).rotate(
            __import__("build123d").Axis.Z, a))
    t = t - (cyl_z(r_in, zp - hl - 1, zp, bx, by) - union_all(keep))
    return t


def yaw_posts(arm):
    px, py = yaw_pinion_xy(arm)
    s = SMALL_SERVO
    tab_under = YAW_SERVO_Z + s.tab_z
    out = []
    for hx in s.hole_xs:
        y = py + hx
        p = box(px - 3.5, px + 3.5, y - 2.0, y + 2.0, 0, tab_under) + pad(px - 6, y - 3.5, px + 6, y + 3.5)
        p = p - cyl_z(M2_TAP / 2, tab_under - 8, tab_under + 1, px, y)
        out.append(p)
    return union_all(out)


def make():
    parts = []
    TL, TR = ARM_BASE["L"], ARM_BASE["R"]
    RL, RR = (-93.0, -45.0), (93.0, -45.0)
    LL, LR = (DOVETAIL_X[0], LUG["y"]), (DOVETAIL_X[1], LUG["y"])
    C = DOWEL_XY
    for p, q in ((TL, LL), (LL, LR), (LR, TR)):                                  # ribbed front loop (arms + tray)
        parts.append(beam(p, q))
    for p, q in ((RL, RR), (RL, TL), (RR, TR), (TL, (-40.0, -45.0)), (TR, (40.0, -45.0)), (C, (0.0, -45.0))):
        parts.append(beam(p, q, rib=False))
    # tower -> its servo posts
    for arm, T in (("L", TL), ("R", TR)):
        px, py = yaw_pinion_xy(arm)
        parts.append(beam(T, (px, py + SMALL_SERVO.hole_xs[0]), rib=False))
        parts.append(beam(T, (px, py + SMALL_SERVO.hole_xs[1]), rib=False))
        parts.append(tower(*T))
        parts.append(yaw_posts(arm))
    # flat strips carrying the electronics cradles
    d = DEVKIT
    dx0, dx1, dy0, dy1 = d["cx"] - d["w"] / 2, d["cx"] + d["w"] / 2, d["y0"], d["y0"] + d["l"]
    strips = [((d["cx"], -45.0), (d["cx"], dy1 + 2.0)), ((dx0 - 3, dy0 + 10), (dx1 + 3, dy0 + 10)),
              ((dx0 - 3, dy1 - 4), (dx1 + 3, dy1 - 4)), ((dx1 + 3, dy1 - 4), (C[0], C[1] + 8)),
              ((PCA["cx"], -45.0), (PCA["cx"], PCA["cy"] + 30.95)),
              ((PCA["cx"] - 13, PCA["cy"] - 27.95), (PCA["cx"] + 13, PCA["cy"] - 27.95)),
              ((PCA["cx"] - 13, PCA["cy"] + 27.95), (PCA["cx"] + 13, PCA["cy"] + 27.95)),
              ((PCA["cx"], -14.5), (96.0, -14.5)), ((PCA["cx"], 12.5), (93.0, 12.5)),
              ((PCA["cx"] - 13, PCA["cy"] + 27.95), (C[0] + 1.0, C[1] + 8.0)),
              ((45.0, -28.0), (75.0, -28.0)), ((-10.0, -30.0), (PCA["cx"], -30.0)),
              ((-95.0, -27.0), (-62.0, -27.0)), ((-62.0, -27.0), (-62.0, -45.0))]
    for p, q in strips:
        parts.append(beam(p, q, rib=False, w=4.0))
    # devkit: rest rails (inside the header rows) + corner locators
    zb = d["z_board"]
    for yy in (dy0 + 6, dy1 - 6):
        parts.append(box(d["cx"] - 3, d["cx"] + 3, yy - 1.0, yy + 1.0, 0, zb) + box(d["cx"] - 1.0, d["cx"] + 1.0, yy - 3, yy + 3, 0, zb))
    for xx, yy in ((dx0, dy0 + 10), (dx1, dy0 + 10), (dx0, dy1 - 4), (dx1, dy1 - 4)):
        sg = -1 if xx == dx0 else 1
        parts.append(box(min(xx + sg * 0.3, xx + sg * 1.9), max(xx + sg * 0.3, xx + sg * 1.9), yy - 2.5, yy + 2.5, 0, zb + 3.0))
    # PCA9685 standoffs (holes 55.9 x 19.1)
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = PCA["cx"] + sx * 9.55, PCA["cy"] + sy * 27.95
            parts.append(cyl_z(2.8, 0, FLANGE_T + 5, x, y) - cyl_z(1.05, 1.0, FLANGE_T + 6, x, y))
    # buck modules, PB, HEAD board, PD trigger
    for b, (bw, bl) in zip(BUCKS, BUCK_SIZES):
        parts.append(cradle(b["cx"] - bw / 2, b["cy"] - bl / 2, b["cx"] + bw / 2, b["cy"] + bl / 2, FLANGE_T + 5.0))
    for bd in (POWER_BOARD, HEAD_BOARD):
        parts.append(cradle(bd["x0"], bd["y0"], bd["x1"], bd["y1"], BOARD_Z))
    p = PDTRIG
    parts.append(cradle(p["cx"] - p["w"] / 2, p["y0"], p["cx"] + p["w"] / 2, p["y0"] + p["l"], p["z_board"]))
    # column dowel socket
    cx, cy = C
    boss = cyl_z(DOWEL_D / 2 + 2.2, 0, SOCKET_H, cx, cy) + cyl_z(DOWEL_D / 2 + 7.0, 0, FLANGE_T, cx, cy)
    parts += [box(cx - 0.9, cx + 0.9, cy, cy + 14, 0, 12), box(cx - 0.9, cx + 0.9, cy - 12, cy, 0, 12),
              box(cx, cx + 14, cy - 0.9, cy + 0.9, 0, 12)]
    parts.append(boss)
    # tray lugs: top face carries a locating pin + an M3 pilot (tray tab sits on top)
    for xc in DOVETAIL_X:
        lug = box(xc - SOCKET_BLOCK["dx"], xc + SOCKET_BLOCK["dx"], LUG["y0"], LUG["y1"], 0, LUG["z1"])
        lug = lug - box(xc - SOCKET_BLOCK["dx"] + 1.6, xc + SOCKET_BLOCK["dx"] - 1.6, LUG["y0"] + 1.6, LUG["y1"] - 1.6,
                        FLANGE_T, LUG["z1"] - 1.6) + cyl_z(3.0, 0, LUG["z1"], xc + LUG["screw_dx"], LUG["y"])
        lug = lug + cyl_z(LUG["pin_d"] / 2, LUG["z1"] - 0.01, LUG["z1"] + LUG["pin_h"], xc + LUG["pin_dx"], LUG["y"])
        lug = lug - cyl_z(M3_TAP / 2, LUG["z1"] - 7, LUG["z1"] + 1, xc + LUG["screw_dx"], LUG["y"])
        parts.append(lug)
    # feet discs
    for x, y in FEET:
        parts.append(cyl_z(7.0, 0, FLANGE_T, x, y))
    ch = union_all(parts)
    # cuts after the union
    ch = ch - cyl_z(DOWEL_D / 2 + 0.15, DOWEL_Z0, SOCKET_H + 1, cx, cy)
    ch = ch - cyl_x(M3_TAP / 2, cx - 10, cx, y=cy, z=SOCKET_H - 7)              # set screw from -x
    for x, y in FEET:
        ch = ch - cyl_z(5.3, -1, 0.8, x, y)                                     # 0.8 mm silicone pads, flush
    for arm in ("L", "R"):
        bx, by = ARM_BASE[arm]
        ch = ch - cyl_z(7.0, -1, DECK_Z + 1, bx, by)                            # retainer screw access
    return ch
