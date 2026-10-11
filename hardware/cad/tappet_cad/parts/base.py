"""Base housing: rounded tub (walls + top deck), open bottom closed by base_lid.  Houses the
electronics, the two 608 yaw bearings, the two SG90 yaw servos (hanging from the deck), the column
mount, the tray dovetail sockets, USB-C cut-outs (rear) and vent slots (sides).  World frame."""
from __future__ import annotations

from build123d import Pos

from ..lib.util import box, cyl_z, rbox, polygon_face
from ..lib.fasteners import m3_counterbored_z
from build123d import extrude
from ..params import (ARM_BASE, BASE_R, BASE_X0, BASE_X1, BASE_Y0, BASE_Y1, BEARING_608, DECK_T, DECK_Z,
                      M2_TAP, M3_CLEAR, M3_TAP, WALL, yaw_pinion_xy, CLR)
from ._common import SMALL_SERVO, meta
from .yaw_gear_servo import YAW_SERVO_Z

META = meta("base", 1, color="#ececec", orient=(180, 0, 0),
            notes="Print upside down (deck on the bed). Supports only under the 3 rear USB-C cut-outs "
                  "(or rely on 7 mm bridges). 3 walls, 15% infill.", infill=15)

BOSS_XY = [(-93.0, -18.0), (93.0, -40.0), (-93.0, 58.0), (93.0, 58.0)]   # rear-left moved clear of the HEAD board
DOVETAIL_X = (-30.0, 30.0)
DOVETAIL_ROOT_Y = BASE_Y1 + 0.2      # tray rear face
DOVETAIL_TIP_Y = 62.0
DOVETAIL_H = 12.0                    # tongue height (tray)
SOCKET_BLOCK = dict(dx=10.0, y0=54.0)
COLUMN_HOLES = [(-17.0, -32.0), (17.0, -32.0), (-17.0, -8.0), (17.0, -8.0)]
COLUMN_CABLE = (0.0, -20.0)
# electronics layout (also used by base_lid)
DEVKIT = dict(cx=-23.0, y0=-46.6, w=25.5, l=69.0, z_board=13.6)
PCA = dict(cx=30.0, cy=3.0, w=25.4, l=62.2)
# buck stations: [0] = Buck A XL4015 5 A CC/CV (~51 x 26, servo rail), [1] = Buck B Mini560 (~22 x 17, logic)
# (hardware/electronics/gen_drawings.py reads these; U2 is drawn 51 x 26 landscape at cx + 1)
BUCKS = [dict(cx=72.0, cy=-1.0), dict(cx=62.0, cy=-36.0)]
BUCK_SIZES = [(51.0 + 2.0, 26.3), (22.0, 17.0)]   # (x, y) cradle sizes; XL4015 measured with its terminals
POWER_BOARD = dict(x0=-7.0, y0=-47.0, x1=33.0, y1=-30.0)      # PB 40 x 17 perfboard (electronics README)
HEAD_BOARD = dict(x0=-95.0, y0=-47.0, x1=-65.0, y1=-27.0)     # 30 x 20, USB-C J2 on the rear panel at x = -80
PDTRIG = dict(cx=40.0, y0=-46.6, w=10.0, l=20.0, z_board=5.6)
BOARD_Z = 5.6        # underside of the small boards on their cradles (PB, HEAD board, CH224K)
USB_CUTS = [  # (x, z_centre, label)
    (-80.0, BOARD_Z + 1.6 + 1.6, "HEAD (J2 on the HEAD board -> Android phone; AOA host via ESP32-S3 GPIO19/20)"),
    (-16.0, DEVKIT["z_board"] + 3.2, "PC (DevKit UART port, flashing / host tools)"),
    (40.0, PDTRIG["z_board"] + 3.2, "POWER IN (USB-C PD, CH224K 9 V)"),
]
# NB: the DevKit's native 'USB' port (GPIO19/20) is deliberately NOT exposed: those lines go to J2.


def dovetail_pts(clear=0.0, root_ext=0.0):
    r, t = DOVETAIL_ROOT_Y + root_ext, DOVETAIL_TIP_Y - clear
    return [(-5.0 - clear, r), (5.0 + clear, r), (7.0 + clear, t), (-7.0 - clear, t)]


def outline(x0, x1, y0, y1, r, z0, z1):
    return rbox(x0, x1, y0, y1, z0, z1, r)


def make():
    outer = outline(BASE_X0, BASE_X1, BASE_Y0, BASE_Y1, BASE_R, 0, DECK_Z)
    inner = outline(BASE_X0 + WALL, BASE_X1 - WALL, BASE_Y0 + WALL, BASE_Y1 - WALL, BASE_R - WALL, -1, DECK_Z - DECK_T)
    b = outer - inner
    zd = DECK_Z - DECK_T
    # lid screw bosses
    for x, y in BOSS_XY:
        b = b + cyl_z(4.5, 0.0 + 2.6, zd + 0.01, x, y)
        b = b - cyl_z(M3_TAP / 2, 2.0, 2.6 + 12, x, y)
    # tray dovetail socket blocks
    for xc in DOVETAIL_X:
        b = b + box(xc - SOCKET_BLOCK["dx"], xc + SOCKET_BLOCK["dx"], SOCKET_BLOCK["y0"], BASE_Y1 - 0.5, 0, zd + 0.01)
        sock = Pos(xc, 0, -1) * extrude(polygon_face(dovetail_pts(CLR, root_ext=2.0)), DOVETAIL_H + 0.4 + 1)
        b = b - sock
        b = b - m3_counterbored_z(xc, (DOVETAIL_ROOT_Y + DOVETAIL_TIP_Y) / 2, DECK_Z, DECK_Z - DOVETAIL_H + 1)
    # yaw bearings
    for arm in ("L", "R"):
        bx, by = ARM_BASE[arm]
        b = b + cyl_z(14.0, DECK_Z - BEARING_608["B"] - 4.0, zd + 0.01, bx, by)
        b = b - cyl_z(BEARING_608["D"] / 2 + 0.1, DECK_Z - BEARING_608["B"], DECK_Z + 1, bx, by)
        b = b - cyl_z(7.0, -1, DECK_Z, bx, by)
        # yaw SG90 hangs from two posts under the deck
        px, py = yaw_pinion_xy(arm)
        s = SMALL_SERVO
        b = b - cyl_z(s.boss_r + 0.4, zd - 1, DECK_Z + 1, px, py)
        b = b - cyl_z(3.2, zd - 1, DECK_Z + 1, px, py - 7.6)  # secondary gear boss (servo x -> +y)
        tab_top = YAW_SERVO_Z + s.tab_z + s.tab_t
        for hx in s.hole_xs:
            y = py + hx
            hw = 2.3
            post = box(px - s.body_w / 2, px + s.body_w / 2, y - hw, y + hw, tab_top, zd + 0.01)
            b = b + post - cyl_z(M2_TAP / 2, tab_top - 1, tab_top + 6, px, y)
        # cable pass slot behind the turret
        b = b - rbox(bx - 7, bx + 7, by - 28 - 3.5, by - 28 + 3.5, zd - 1, DECK_Z + 1, 3.4)
    # column mount
    for x, y in COLUMN_HOLES:
        b = b - cyl_z(M3_CLEAR / 2, zd - 1, DECK_Z + 1, x, y)
    b = b - rbox(-6, 6, COLUMN_CABLE[1] - 7, COLUMN_CABLE[1] + 7, zd - 1, DECK_Z + 1, 2)
    # rear USB-C cut-outs
    for x, zc, _ in USB_CUTS:
        cut = Pos(x, BASE_Y0 - 1, zc) * __import__("build123d").Rot(-90, 0, 0) * \
              extrude(__import__("build123d").RectangleRounded(11.5, 7.0, 3.0), WALL + 2)
        b = b - cut
    # side vents
    for sx in (BASE_X0, BASE_X1):
        for i in range(7):
            y = -38 + i * 8.0
            b = b - rbox(sx - 4, sx + 4, y - 1.6, y + 1.6, 8, 22, 1.5)
    # rear vents (above the bucks)
    for i in range(6):
        x = 56 + i * 7
        b = b - rbox(x - 1.5, x + 1.5, BASE_Y0 - 1, BASE_Y0 + WALL + 1, 8, 22, 1.4)
    return b
