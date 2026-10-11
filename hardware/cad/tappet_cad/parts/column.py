"""Column = a 10 mm hardwood dowel (or 10 mm aluminium/CF tube) standing in the chassis socket, plus a
small printed head fitting that holds the NECK_PAN SG90 (shaft up on the pan axis).  The dowel sits
directly under the servo body; an M3 set screw (plus a drop of CA glue) stops it turning.
World frame."""
from __future__ import annotations

from ..lib.util import box, cyl_x, cyl_z, rbox, union_all
from ..params import COLUMN_TOP_Z, M2_TAP, M3_TAP, PAN_X, PAN_Y
from ._common import SMALL_SERVO, meta
from .base import DOWEL_D, DOWEL_XY, DOWEL_Z0

META = meta("column_head", 1, color="#f4f1ea", orient=(0, 0, 0),
            notes="Print upright (socket down); the 45-degree funnel carries the servo floor, no supports.",
            walls=3, infill=20, layer=0.24)

S = SMALL_SERVO
PAN_SERVO_Z = COLUMN_TOP_Z + 2.5 - S.horn_top + S.horn_arm_t   # horn arm bottom 1 mm above the fitting top
SOCKET_DEPTH = 18.0
FLOOR_T = 2.0
DOWEL_TOP = PAN_SERVO_Z - FLOOR_T - 0.5
DOWEL_LEN = round(DOWEL_TOP - DOWEL_Z0)


def make():
    dx, dy = DOWEL_XY
    zf = PAN_SERVO_Z - FLOOR_T - 0.3                      # servo floor bottom
    sock = cyl_z(DOWEL_D / 2 + 2.0, zf - SOCKET_DEPTH, zf + 0.01, dx, dy)
    bw = S.body_w / 2 + 0.3
    y0, y1 = PAN_Y + S.body_x0 - 0.3, PAN_Y + S.body_x1 + 0.3
    yA, yB = PAN_Y + S.hole_xs[0] - 2.6, PAN_Y + S.hole_xs[1] + 2.6     # floor extent (covers the tab posts)
    floor = rbox(-bw - 1.6, bw + 1.6, yA, yB, zf, PAN_SERVO_Z - 0.3, 3)
    # two tab posts (servo screwed down onto them) + a low side rail on each side of the body
    tab_under = PAN_SERVO_Z + S.tab_z
    posts = []
    for hx in S.hole_xs:
        y = PAN_Y + hx
        posts.append(box(-3.0, 3.0, y - 2.2, y + 2.2, zf, tab_under))
    rails = box(-bw - 1.6, -bw, y0, y1, zf, zf + 5) + box(bw, bw + 1.6, y0, y1, zf, zf + 5)
    # 45-degree funnel shell from the socket to the floor (prints without supports)
    from build123d import Align, Cone, Pos
    r1, r2 = DOWEL_D / 2 + 2.0, 18.6
    hh = r2 - r1
    cone = Pos(dx, dy, zf - hh) * Cone(r1, r2, hh, align=(Align.CENTER, Align.CENTER, Align.MIN))
    cone_in = Pos(dx, dy, zf - hh - 0.01) * Cone(r1 - 1.6, r2 - 1.6, hh + 0.02, align=(Align.CENTER, Align.CENTER, Align.MIN))
    funnel = (cone - cone_in) & box(-bw - 1.6, bw + 1.6, yA, yB, zf - hh - 1, zf + 0.01)
    h = sock + funnel + floor + union_all(posts) + rails
    for hx in S.hole_xs:
        h = h - cyl_z(M2_TAP / 2, tab_under - 7, tab_under + 1, PAN_X, PAN_Y + hx)
    h = h - cyl_z(DOWEL_D / 2 + 0.15, zf - SOCKET_DEPTH - 1, zf - 0.01, dx, dy)
    h = h - cyl_x(M3_TAP / 2, dx - 10, dx, y=dy, z=zf - 10)          # set screw
    # cable exit for the pan servo lead (servo lead leaves at the body's far end)
    h = h - box(-3.5, 3.5, y0 - 3, y0 + 4, zf - 1, zf + 4)
    return h


def dowel_mock():
    dx, dy = DOWEL_XY
    return cyl_z(DOWEL_D / 2, DOWEL_Z0, DOWEL_Z0 + DOWEL_LEN, dx, dy)
