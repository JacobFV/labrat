"""20T module-1 involute pinion pressed onto the SG90 yaw-servo spline (M2 screw on top).
Frame: pinion axis = z through origin, world heights."""
from __future__ import annotations

from build123d import Pos

from ..lib.gears import spur_gear
from ..lib.util import cyl_z
from ..params import GEAR_BACKLASH, GEAR_MODULE, GEAR_Z0, PINION_T, Z_SERVO_GEAR
from ._common import meta, SMALL_SERVO

META = meta("yaw_gear_servo", 2, color="#5dade2", orient=(0, 0, 0),
            notes="Print spline-bore down, 0.12 mm layers, 100% infill. Bore is a light press on the SG90 spline.",
            infill=100, layer=0.12)

# yaw SG90 body bottom height so that its spline top is 3 mm into the pinion
YAW_SERVO_Z = GEAR_Z0 + 3.0 - SMALL_SERVO.shaft_top


def make():
    g = Pos(0, 0, GEAR_Z0) * spur_gear(Z_SERVO_GEAR, GEAR_MODULE, PINION_T, GEAR_BACKLASH / 2)
    g = g - cyl_z(SMALL_SERVO.shaft_d / 2 + 0.05, GEAR_Z0 - 1, GEAR_Z0 + 3.0)      # spline bore
    g = g - cyl_z(1.15, GEAR_Z0, GEAR_Z0 + PINION_T + 1)                              # M2 screw
    g = g - cyl_z(2.2, GEAR_Z0 + PINION_T - 1.6, GEAR_Z0 + PINION_T + 1)              # head counterbore
    return g
