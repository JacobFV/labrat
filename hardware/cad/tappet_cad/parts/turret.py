"""Arm turret (L; R is the mirror image).  Modelled in the arm frame (x=u radial, y=v lateral,
z up, origin on the desk under the yaw axis).  Bolts onto yaw_gear_turret (3x M3), carries the
shoulder MG90S (-v) and elbow MG90S (+v) coaxially on the shoulder axis (u=r0, z=H) plus the ground
post for the wrist-levelling parallelogram."""
from __future__ import annotations

import math

from build123d import RectangleRounded, Pos

from ..lib.util import box, circle_at, cyl_y, cyl_z, plate_xz, rbox, stadium
from ..params import (D_VEC, H, HORN_FACE_V, M2_TAP, M3_CLEAR, M3_HEAD_D, PIN_HOLE, PLANES, R0,
                      TURRET_PLATFORM_T, TURRET_PLATFORM_Z0, CLR)
from ._common import ARM_SERVO, meta

META = meta("turret", 1, color="#3d8bd9", orient=(0, 0, 0), handed=True,
            notes="Print platform-down, no supports (servo windows bridge 12 mm). 4 walls, 30% gyroid. "
                  "Export turret_L and turret_R (mirror).", walls=4)

S = ARM_SERVO
SERVO_ORIGIN_V = HORN_FACE_V + S.horn_top          # 47: servo body bottom |v|
CHEEK_OUT = SERVO_ORIGIN_V - S.tab_z - S.tab_t     # tab top face |v| (cheek outer face)
CHEEK_T = 3.0
CHEEK_IN = CHEEK_OUT - CHEEK_T
U0, U1 = -17.0, 28.0
Z0 = TURRET_PLATFORM_Z0
Z_TOP = H + 14.0
GEAR_BOLT_R = 8.0
GEAR_BOLT_ANGLES = (90.0, 210.0, 330.0)


def make():
    z1 = Z0 + TURRET_PLATFORM_T
    plat = rbox(U0, U1, -CHEEK_OUT, CHEEK_OUT, Z0, z1, 7)
    # cheeks (yz-profile rounded on top)
    cheek_sk = Pos((U0 + U1) / 2, (Z0 + Z_TOP) / 2) * RectangleRounded(U1 - U0, Z_TOP - Z0, 6)
    cheeks = plate_xz(cheek_sk, CHEEK_IN, CHEEK_OUT) + plate_xz(cheek_sk, -CHEEK_OUT, -CHEEK_IN)
    # servo windows + tab pilot holes (servo long axis vertical, shaft end up)
    win = box(R0 - S.body_w / 2 - 0.25, R0 + S.body_w / 2 + 0.25, -60, 60,
              H + S.body_x0 - 0.25, H + S.body_x1 + 0.25)
    cheeks = cheeks - win
    for hx in S.hole_xs:
        cheeks = cheeks - cyl_y(M2_TAP / 2, -60, 60, x=R0, z=H + hx)
    # rear brace
    brace = box(U0, U0 + 3.0, -CHEEK_IN - 0.5, CHEEK_IN + 0.5, Z0, Z0 + 13.5)
    # ground post for parallelogram #1 (pivot G = shoulder + D)
    gu, gz = R0 + D_VEC[0], H + D_VEC[1]
    pv0, pv1 = PLANES["ground_post"]
    post_sk = stadium((gu, Z0 + 5), (gu, gz), 9) + circle_at((gu, gz), 5.5) + stadium((U0 + 1.5, Z0 + 4), (gu, Z0 + 4), 8)
    post = plate_xz(post_sk, pv0, pv1) - cyl_y(PIN_HOLE / 2, pv0 - 1, pv1 + 1, x=gu, z=gz)
    # small fillet-like gusset from the brace to the post
    t = plat + cheeks + brace + post
    # gear bolts (M3, heads countersunk into the platform top)
    for a in GEAR_BOLT_ANGLES:
        x, y = GEAR_BOLT_R * math.cos(math.radians(a)), GEAR_BOLT_R * math.sin(math.radians(a))
        t = t - cyl_z(M3_CLEAR / 2, Z0 - 1, z1 + 1, x, y) - cyl_z(M3_HEAD_D / 2, z1 - 1.8, z1 + 1, x, y)
    # servo cable pass-throughs
    for s in (-1, 1):
        t = t - rbox(-12, -2, s * 18 - 3.5, s * 18 + 3.5, Z0 - 1, z1 + 1, 3)
    # weight relief in the platform front
    t = t - rbox(14, 24, -10, 10, Z0 - 1, z1 + 1, 4)
    return t
