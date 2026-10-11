"""Upper arm: shoulder pivot (on the shoulder MG90S horn) -> elbow pin, L1 pivot-to-pivot."""
from __future__ import annotations

from ..lib.servos import horn_pocket, horn_screw_holes
from ..lib.util import circle_at, plate_xz, stadium, place, cyl_y
from ..params import L1, PLANES, PIN_HOLE, M2_CLEAR
from ._common import ARM_SERVO, HORN_IN_SHOULDER_LINK, meta

META = meta("upper_arm", 2, color="#f28c28", orient=(90, 0, 0),
            notes="Print flat (horn pocket face up). 4 walls, 40% infill for stiffness. Same part for L and R (flip over).",
            infill=40, walls=4)


def make():
    v0, v1 = PLANES["upper_arm"]
    sk = stadium((0, 0), (L1, 0), 13) + circle_at((0, 0), 9) + circle_at((L1, 0), 7)
    sk = sk - stadium((26, 0), (L1 - 19, 0), 5)
    sk = sk - circle_at((L1, 0), PIN_HOLE / 2) - circle_at((0, 0), M2_CLEAR / 2 + 0.1)
    p = plate_xz(sk, v0, v1)
    p = p - place(horn_pocket(ARM_SERVO, 0.2), HORN_IN_SHOULDER_LINK)
    for hx in horn_screw_holes(ARM_SERVO):
        p = p - cyl_y(0.8, v0 - 1, v1 + 1, x=hx)
    # counterbore for the horn centre screw head (outer face)
    p = p - cyl_y(2.6, v1 - 1.6, v1 + 1)
    return p
