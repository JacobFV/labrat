"""Lamp-arm column: foot flange bolted to the deck, a Luxo-style 'Z' (lower strut leaning back,
knuckle, upper strut leaning forward) and a head housing that holds the NECK_PAN SG90 (shaft up on
the pan axis).  Hollow throughout for the two neck servo cables.  World frame."""
from __future__ import annotations

from build123d import Pos, Rectangle, RectangleRounded, Sphere, loft

from ..lib.util import box, cyl_x, cyl_z, rbox
from ..lib.fasteners import m3_counterbored_z
from ..params import COLUMN_TOP_Z, DECK_Z, M2_TAP, PAN_X, PAN_Y
from ._common import SMALL_SERVO, meta
from .base import COLUMN_HOLES, COLUMN_CABLE

META = meta("column", 1, color="#f4f1ea", orient=(0, 0, 0),
            notes="Print upright on the foot. 3 walls, 20% infill; support only under the knuckle caps "
                  "(or none - 45 deg chamfered). Brim recommended.", infill=20)

S = SMALL_SERVO
PAN_SERVO_Z = COLUMN_TOP_Z + 2.5 - S.horn_top + S.horn_arm_t   # horn arm bottom 1 mm above column top
KNUCKLE = (-38.0, 120.0)        # (y, z)
FOOT_T = 4.0
HOUSING = dict(x=9.5, y0=PAN_Y + S.body_x0 - 7.5, y1=PAN_Y + S.body_x1 + 7.5, z0=PAN_SERVO_Z - 4.0)


def _rr(w, l, r):
    return RectangleRounded(w, l, r)


def _seg(p0, p1, s0, s1, r):
    """loft between two horizontal rounded rectangles; p=(y,z) centre, s=(w,l)."""
    f0 = Pos(PAN_X, p0[0], p0[1]) * _rr(s0[0], s0[1], r)
    f1 = Pos(PAN_X, p1[0], p1[1]) * _rr(s1[0], s1[1], r)
    return loft([f0.face(), f1.face()])


def make():
    zf = DECK_Z
    foot = rbox(-23, 23, -38, -2, zf, zf + FOOT_T, 8)
    ky, kz = KNUCKLE
    h = HOUSING
    hy = (h["y0"] + h["y1"]) / 2
    lower = _seg((COLUMN_CABLE[1], zf + 0.5), (ky, kz - 6), (24, 20), (18, 15), 5)
    upper = _seg((ky, kz + 6), (hy, h["z0"] + 2), (18, 15), (19, h["y1"] - h["y0"] - 2), 5)
    knuckle = cyl_x(13, -11.5, 11.5, y=ky, z=kz) + cyl_x(8, -14, 14, y=ky, z=kz)
    housing = rbox(-h["x"], h["x"], h["y0"], h["y1"], h["z0"], COLUMN_TOP_Z, 4)
    c = foot + lower + upper + knuckle + housing
    # hollow wire channel
    lower_in = _seg((COLUMN_CABLE[1], zf - 1), (ky, kz - 4), (12, 10), (10, 7), 2.5)
    upper_in = _seg((ky, kz + 4), (hy - 4, h["z0"] + 6), (10, 7), (10, 8), 2.5)
    c = c - lower_in - upper_in - (Pos(0, ky, kz) * Sphere(6.5))
    # servo pocket: body + tab ledge
    bw = S.body_w / 2 + 0.3
    c = c - box(-bw, bw, PAN_Y + S.body_x0 - 0.3, PAN_Y + S.body_x1 + 0.3, PAN_SERVO_Z - 0.5, COLUMN_TOP_Z + 1)
    c = c - box(-bw, bw, PAN_Y + S.tab_x0 - 0.3, PAN_Y + S.tab_x1 + 0.3, PAN_SERVO_Z + S.tab_z, COLUMN_TOP_Z + 1)
    for hx in S.hole_xs:
        c = c - cyl_z(M2_TAP / 2, PAN_SERVO_Z + S.tab_z - 8, PAN_SERVO_Z + S.tab_z + 1, PAN_X, PAN_Y + hx)
    # cable slot from the pocket into the channel
    c = c - box(-4, 4, PAN_Y + S.body_x0 - 0.3, PAN_Y + S.body_x0 + 8, h["z0"] - 6, PAN_SERVO_Z + 1)
    # foot bolts
    for x, y in COLUMN_HOLES:
        c = c - m3_counterbored_z(x, y, zf + FOOT_T, FOOT_T + 1, head_depth=1.6)
    return c
