"""Neck tilt bracket: U-yoke on the tilt axis (right cheek keyed to the tilt SG90 horn, left cheek on
an M3 idler pin) with a front bridge that the phone clamp bolts to.  World frame at tilt = 0."""
from __future__ import annotations

from ..lib.servos import horn_pocket
from ..lib.util import box, cyl_x, cyl_y, place, rbox
from ..lib.fasteners import nut_trap_z
from ..params import M2_CLEAR, M3_CLEAR, M3_NUT_AF, PAN_Y, TILT_Z
from build123d import Pos, Rot, RegularPolygon, extrude
import math
from ._common import SMALL_SERVO as S, meta
from ._neck import BRACKET_FACE_X, BRIDGE_Y, CHEEK_T, TILT_HORN_FRAME

META = meta("neck_tilt_bracket", 1, color="#c0392b", orient=(-90, 0, 0),
            notes="Print on the bridge front face (cheeks standing up, no supports). 4 walls, 40% infill.", infill=40, walls=4)

CLAMP_BOLTS_X = (-10.0, 10.0)


def make():
    parts = []
    for sgn in (1, -1):
        x0, x1 = sorted((sgn * (BRACKET_FACE_X - CHEEK_T), sgn * BRACKET_FACE_X))
        ch = cyl_x(9.5, x0, x1, y=PAN_Y, z=TILT_Z) + box(x0, x1, PAN_Y, BRIDGE_Y[0] + 0.01, TILT_Z - 9.5, TILT_Z + 9.5)
        parts.append(ch)
    bridge = box(-16, 16, BRIDGE_Y[0], BRIDGE_Y[1], TILT_Z - 14, TILT_Z + 14)
    # vertical tab on the horn cheek that carries the (upward) horn arm
    tab = box(BRACKET_FACE_X - CHEEK_T, BRACKET_FACE_X, PAN_Y - 5.0, PAN_Y + 5.0, TILT_Z, TILT_Z + 20.0)
    b = parts[0] + parts[1] + bridge + tab
    b = b - place(horn_pocket(S, 0.2), TILT_HORN_FRAME)
    b = b - cyl_x(M2_CLEAR / 2 + 0.1, BRACKET_FACE_X - CHEEK_T - 1, BRACKET_FACE_X + 1, y=PAN_Y, z=TILT_Z)
    b = b - cyl_x(2.4, BRACKET_FACE_X - CHEEK_T - 1, BRACKET_FACE_X - CHEEK_T + 1.6, y=PAN_Y, z=TILT_Z)
    b = b - cyl_x(M3_CLEAR / 2, -BRACKET_FACE_X - 1, -BRACKET_FACE_X + CHEEK_T + 1, y=PAN_Y, z=TILT_Z)
    for hx in (6.0, 9.5, 13.0):
        b = b - cyl_x(0.8, BRACKET_FACE_X - CHEEK_T - 1, BRACKET_FACE_X + 1, y=PAN_Y, z=TILT_Z + hx)
    for x in CLAMP_BOLTS_X:
        b = b - cyl_y(M3_CLEAR / 2, BRIDGE_Y[0] - 1, BRIDGE_Y[1] + 1, x=x, z=TILT_Z)
        d = M3_NUT_AF / math.cos(math.pi / 6)
        trap = Pos(x, BRIDGE_Y[0] - 0.01, TILT_Z) * Rot(-90, 0, 0) * extrude(RegularPolygon(d / 2, 6), 2.6)
        b = b - trap
    return b
