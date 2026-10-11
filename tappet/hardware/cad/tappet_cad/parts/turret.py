"""Arm turret, skeleton version (L; R is the mirror image).  Arm frame (x = u radial, y = v lateral,
z up, origin on the desk under the yaw axis).  Bolts onto yaw_gear_turret (3x M3 on r = 8), carries the
shoulder MG90S (-v) and elbow MG90S (+v) coaxially on the shoulder axis (u = r0, z = H) in two
2.4 mm window-frame cheeks (tab faces unchanged), plus the ground post for the wrist parallelogram."""
from __future__ import annotations

import math

from build123d import Pos, RectangleRounded

from ..lib.util import box, circle_at, cyl_y, cyl_z, plate_xz, rbox, stadium, union_all
from ..params import (D_VEC, H, HORN_FACE_V, M2_TAP, M3_CLEAR, M3_HEAD_D, PIN_HOLE, PLANES, R0,
                      TURRET_PLATFORM_Z0)
from ._common import ARM_SERVO, meta

META = meta("turret", 1, color="#3d8bd9", orient=(0, 0, 0), handed=True,
            notes="Print platform-down, no supports (servo windows bridge 13 mm). 0.24 mm, 3 walls, 20% infill. "
                  "Export turret_L and turret_R (mirror).", walls=3, infill=20, layer=0.24)

S = ARM_SERVO
SERVO_ORIGIN_V = HORN_FACE_V + S.horn_top          # 47: servo body bottom |v|
CHEEK_OUT = SERVO_ORIGIN_V - S.tab_z - S.tab_t     # tab top face |v| (cheek outer face) = 28.6
CHEEK_T = 2.2
CHEEK_IN = CHEEK_OUT - CHEEK_T
PLAT_T = 3.0
U0, U1 = -14.0, 25.0
Z0 = TURRET_PLATFORM_Z0
GEAR_BOLT_R = 8.0
GEAR_BOLT_ANGLES = (90.0, 210.0, 330.0)


def make():
    z1 = Z0 + PLAT_T
    # platform: gear hub disc + side bars under the cheeks + a cross strut + a foot for the post
    wu0, wu1 = R0 - S.body_w / 2 - 0.25, R0 + S.body_w / 2 + 0.25
    cu0, cu1 = wu0 - 3.6, wu1 + 3.6
    plat = cyl_z(12.0, Z0, z1) - cyl_z(4.5, Z0 - 1, z1 + 1)
    for s in (-1, 1):
        plat = plat + rbox(cu0, cu1, min(s * (CHEEK_IN - 5), s * CHEEK_OUT), max(s * (CHEEK_IN - 5), s * CHEEK_OUT), Z0, z1, 2)
    plat = plat + box(R0 - 3.0, R0 + 3.0, -CHEEK_IN, CHEEK_IN, Z0, z1)
    plat = plat + rbox(U0, -4.0, -13.0, -3.0, Z0, z1, 2)
    # cheeks: window frames around the servo body, tab holes, joined to the platform
    wz0, wz1 = H + S.body_x0 - 0.25, H + S.body_x1 + 0.25
    hz0, hz1 = H + S.hole_xs[0], H + S.hole_xs[1]
    sk = Pos((cu0 + cu1) / 2, (Z0 + hz1 + 4.5) / 2) * RectangleRounded(cu1 - cu0, hz1 + 4.5 - Z0, 4)
    cheek = plate_xz(sk, CHEEK_IN, CHEEK_OUT) + plate_xz(sk, -CHEEK_OUT, -CHEEK_IN)
    cheek = cheek - box(wu0, wu1, -60, 60, wz0, wz1)
    cheek = cheek - box(wu0 + 0.5, wu1 - 0.5, -60, 60, z1 + 2.0, hz0 - 2.6)           # lower relief window
    cheek = cheek - box(cu0 + 1.8, wu0 - 1.4, -60, 60, z1 + 9.0, hz1 - 2.0)            # side slots
    cheek = cheek - box(wu1 + 1.4, cu1 - 1.8, -60, 60, z1 + 9.0, hz1 - 2.0)
    for hz in (hz0, hz1):
        cheek = cheek - cyl_y(M2_TAP / 2, -60, 60, x=R0, z=hz)
    # 45-degree gussets inside each cheek at both edges
    from ..lib.util import frame, place, polygon_face
    from build123d import extrude
    gus = []
    for s in (-1, 1):
        tri = polygon_face([(s * CHEEK_IN, z1 - 0.01), (s * (CHEEK_IN - 8.0), z1 - 0.01), (s * CHEEK_IN, z1 + 8.0)])
        for u in (cu0, cu1 - 2.0):
            gus.append(place(extrude(tri, 2.0), frame((u, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 0))))
    # ground post for parallelogram #1 (pivot G = shoulder + D)
    gu, gz = R0 + D_VEC[0], H + D_VEC[1]
    pv0, pv1 = PLANES["ground_post"]
    post_sk = stadium((gu, Z0 + 5), (gu, gz), 8) + circle_at((gu, gz), 5.0) + stadium((U0 + 3.0, Z0 + 3), (gu + 3, Z0 + 3), 6)
    post = plate_xz(post_sk, pv0, pv1) - cyl_y(PIN_HOLE / 2, pv0 - 1, pv1 + 1, x=gu, z=gz)
    post = post - cyl_y(2.2, pv0 - 1, pv1 + 1, x=gu, z=(Z0 + gz) / 2 + 2) - cyl_y(2.2, pv0 - 1, pv1 + 1, x=gu, z=(Z0 + gz) / 2 + 10)
    t = union_all([plat, cheek, post] + gus)
    for a in GEAR_BOLT_ANGLES:
        x, y = GEAR_BOLT_R * math.cos(math.radians(a)), GEAR_BOLT_R * math.sin(math.radians(a))
        t = t - cyl_z(M3_CLEAR / 2, Z0 - 1, z1 + 1, x, y) - cyl_z(M3_HEAD_D / 2, z1 - 1.2, z1 + 1, x, y)
    return t
