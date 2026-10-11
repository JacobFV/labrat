"""Elbow drive crank: keyed to the elbow MG90S horn (coaxial with the shoulder), radius CRANK_A."""
from __future__ import annotations

from ..lib.servos import horn_pocket, horn_screw_holes
from ..lib.util import circle_at, plate_xz, stadium, place, cyl_y
from ..params import CRANK_A, PLANES, PIN_HOLE, M2_CLEAR
from ._common import ARM_SERVO, HORN_IN_ELBOW_LINK, meta

META = meta("elbow_drive_link", 2, color="#d94f30", orient=(90, 0, 0),
            notes="Print flat, horn pocket up. 100% infill (small, torque-carrying).", infill=100)


def make():
    v0, v1 = PLANES["elbow_drive_link"]
    sk = stadium((0, 0), (CRANK_A, 0), 11) + circle_at((0, 0), 9) + circle_at((CRANK_A, 0), 5.5)
    sk = sk - circle_at((CRANK_A, 0), PIN_HOLE / 2) - circle_at((0, 0), M2_CLEAR / 2 + 0.1)
    p = plate_xz(sk, v0, v1)
    p = p - place(horn_pocket(ARM_SERVO, 0.2), HORN_IN_ELBOW_LINK)
    for hx in horn_screw_holes(ARM_SERVO):
        if hx < CRANK_A - 6:
            p = p - cyl_y(0.8, v0 - 1, v1 + 1, x=hx)
    p = p - cyl_y(2.6, v0 - 1, v0 + 1.6)
    return p
