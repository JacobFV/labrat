"""Neck pan mount: keyed onto the pan SG90 horn (rotates about the pan axis); holds the tilt SG90
(shaft along -x on the tilt axis) in its right cheek and the idler pivot in its left cheek."""
from __future__ import annotations

from build123d import Pos, RectangleRounded

from ..lib.servos import horn_pocket
from ..lib.util import box, circle_at, cyl_x, cyl_z, place, plate_xz, rbox
from ..params import M2_CLEAR, M2_TAP, M3_CLEAR, PAN_X, PAN_Y, TILT_Z
from ._common import SMALL_SERVO as S, meta
from ._neck import (BRACKET_FACE_X, CHEEK_T, PAN_HORN_FRAME, PLATE_Z0, PLATE_Z1, TAB_TOP_X)

META = meta("neck_pan_mount", 1, color="#e74c3c", orient=(0, 0, 0),
            notes="Print plate-down. Servo window bridges ~13 mm.", infill=20, walls=3, layer=0.24)

Y0, Y1 = PAN_Y - 24.0, PAN_Y + 12.0
LEFT_X = (-(BRACKET_FACE_X + CHEEK_T + 1.0 + 3.0), -(BRACKET_FACE_X + CHEEK_T + 1.0))


def make():
    rx0, rx1 = TAB_TOP_X - 2.4, TAB_TOP_X
    plate = rbox(LEFT_X[0], rx1, Y0, Y1, PLATE_Z0, PLATE_Z1, 4)
    right = rbox(rx0, rx1, Y0, Y1, PLATE_Z0, TILT_Z + 13, 3)
    win = box(rx0 - 1, rx1 + 1, PAN_Y + S.body_x0 - 0.25, PAN_Y + S.body_x1 + 0.25,
              TILT_Z - S.body_w / 2 - 0.25, TILT_Z + S.body_w / 2 + 0.25)
    right = right - win
    right = right - box(rx0 - 1, rx1 + 1, PAN_Y - 12.0, PAN_Y + 3.0, PLATE_Z1 + 2.0, TILT_Z - S.body_w / 2 - 3.0)
    for hx in S.hole_xs:
        right = right - cyl_x(M2_TAP / 2, rx0 - 1, rx1 + 1, y=PAN_Y + hx, z=TILT_Z)
    left = cyl_x(9.0, LEFT_X[0], LEFT_X[1], y=PAN_Y, z=TILT_Z) + box(LEFT_X[0], LEFT_X[1], PAN_Y - 9, PAN_Y + 9, PLATE_Z0, TILT_Z)
    left = left - cyl_x(M3_CLEAR / 2, LEFT_X[0] - 1, LEFT_X[1] + 1, y=PAN_Y, z=TILT_Z)
    # gussets
    g = box(rx0 - 6, rx0, Y0, Y0 + 2.4, PLATE_Z1 - 0.01, PLATE_Z1 + 6) + box(rx0 - 6, rx0, Y1 - 2.4, Y1, PLATE_Z1 - 0.01, PLATE_Z1 + 6) + \
        box(LEFT_X[1], LEFT_X[1] + 4, PAN_Y - 9, PAN_Y + 9, PLATE_Z1 - 0.01, PLATE_Z1 + 3)
    m = plate + right + left + g
    m = m - rbox(4.5, rx0 - 3.0, Y0 + 3.0, Y1 - 3.0, PLATE_Z0 - 1, PLATE_Z1 + 1, 2.5)
    m = m - rbox(LEFT_X[1] + 5.0, -4.5, Y0 + 3.0, PAN_Y - 9.0, PLATE_Z0 - 1, PLATE_Z1 + 1, 2.0)
    m = m - place(horn_pocket(S, 0.2), PAN_HORN_FRAME)
    m = m - cyl_z(M2_CLEAR / 2, PLATE_Z0 - 1, PLATE_Z1 + 1, PAN_X, PAN_Y)
    m = m - cyl_z(2.4, PLATE_Z1 - 1.8, PLATE_Z1 + 1, PAN_X, PAN_Y)
    for hx in (6.0, 9.5, 13.0):
        m = m - cyl_z(0.8, PLATE_Z0 - 1, PLATE_Z1 + 1, PAN_X, PAN_Y - hx)
    return m
