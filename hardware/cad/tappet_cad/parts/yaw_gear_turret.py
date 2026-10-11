"""30T module-1 involute gear + 8 mm axle (rides in the 608 bearing).  Bolted to the turret.
Arm frame (z up, origin on desk under the yaw axis)."""
from __future__ import annotations

import math

from ..lib.gears import spur_gear
from ..lib.util import cyl_z
from ..params import (BEARING_608, DECK_Z, GEAR_BACKLASH, GEAR_MODULE, GEAR_Z0, M3_TAP, TURRET_GEAR_T,
                      TURRET_PLATFORM_Z0, Z_TURRET_GEAR)
from ._common import meta
from .turret import GEAR_BOLT_ANGLES, GEAR_BOLT_R

META = meta("yaw_gear_turret", 2, color="#1f4e79", orient=(180, 0, 0),
            notes="Print upside down (hub face on bed, axle up). 0.12 mm layers, 100% infill for crisp teeth.",
            infill=100, layer=0.12)

AXLE_D = BEARING_608["d"] - 0.1
BEARING_Z0 = DECK_Z - BEARING_608["B"]


def make():
    g = spur_gear(Z_TURRET_GEAR, GEAR_MODULE, TURRET_GEAR_T, GEAR_BACKLASH / 2)
    from build123d import Pos
    g = Pos(0, 0, GEAR_Z0) * g
    neck = cyl_z(12.5, GEAR_Z0 + TURRET_GEAR_T - 0.01, TURRET_PLATFORM_Z0)
    ring = cyl_z(BEARING_608["inner_land_d"] / 2 - 0.2, DECK_Z + 0.1, GEAR_Z0 + 0.01)   # rides on inner race
    axle = cyl_z(AXLE_D / 2, BEARING_Z0 + 0.4, DECK_Z + 0.2)
    p = g + neck + ring + axle
    p = p - cyl_z(M3_TAP / 2, BEARING_Z0, BEARING_Z0 + 9)          # retaining screw (from below)
    for a in GEAR_BOLT_ANGLES:
        x, y = GEAR_BOLT_R * math.cos(math.radians(a)), GEAR_BOLT_R * math.sin(math.radians(a))
        p = p - cyl_z(M3_TAP / 2, GEAR_Z0 + 0.8, TURRET_PLATFORM_Z0 + 1, x, y)
    return p
