"""Shared bits for part modules."""
from __future__ import annotations

import numpy as np

from ..lib.servos import MG90S, SG90
from ..lib.util import frame
from ..params import HORN_FACE_V

ARM_SERVO = MG90S          # shoulder + elbow
SMALL_SERVO = SG90         # yaw + neck

# horn frame expressed in the frame of the link it drives (link x == horn arm direction)
# shoulder servo sits on -v, shaft points +v
HORN_IN_SHOULDER_LINK = frame((0, -(HORN_FACE_V + ARM_SERVO.horn_top), 0), (1, 0, 0), (0, 0, -1), (0, 1, 0))
# elbow servo sits on +v, shaft points -v
HORN_IN_ELBOW_LINK = frame((0, +(HORN_FACE_V + ARM_SERVO.horn_top), 0), (1, 0, 0), (0, 0, 1), (0, -1, 0))


def meta(name, qty, material="PLA", color="#e8833a", orient=(0, 0, 0), notes="", handed=False, printed=True,
         infill=30, supports="none", layer=0.2, walls=3):
    return dict(name=name, qty=qty, material=material, color=color, orient=orient, notes=notes,
                handed=handed, printed=printed, infill=infill, supports=supports, layer=layer, walls=walls)
