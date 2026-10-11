"""Shared neck geometry (world frame at pan = tilt = 0)."""
from __future__ import annotations

from ..lib.util import frame
from ..params import PAN_X, PAN_Y, TILT_Z
from ._common import SMALL_SERVO as S
from .column import PAN_SERVO_Z

# pan SG90 in the column: shaft +z, body toward -y... (servo x -> +y, so body spans PAN_Y + [x0, x1])
PAN_SERVO_FRAME = frame((PAN_X, PAN_Y, PAN_SERVO_Z), (0, 1, 0), (-1, 0, 0), (0, 0, 1))
PAN_HORN_TOP = PAN_SERVO_Z + S.horn_top
PLATE_Z0 = PAN_HORN_TOP - S.horn_arm_t           # pan-mount plate bottom (horn arm sits in a pocket)
PLATE_Z1 = PLATE_Z0 + 4.5
# horn on the pan servo, arm pointing -y (under the plate)
PAN_HORN_FRAME = frame((PAN_X, PAN_Y, PAN_SERVO_Z), (0, -1, 0), (1, 0, 0), (0, 0, 1))

# tilt SG90: shaft along -x, horn top face at x = TILT_HORN_X
TILT_HORN_X = 8.0
TILT_SERVO_X = TILT_HORN_X + S.horn_top            # body bottom
TILT_SERVO_FRAME = frame((TILT_SERVO_X, PAN_Y, TILT_Z), (0, 1, 0), (0, 0, -1), (-1, 0, 0))
from ..lib.util import Rz as _Rz
# tilt horn arm points up (+z) so it clears the clamp plate in front
TILT_HORN_FRAME = TILT_SERVO_FRAME @ _Rz(-90)
TAB_TOP_X = TILT_SERVO_X - S.tab_z - S.tab_t
BRACKET_FACE_X = TILT_HORN_X + S.horn_arm_t       # tilt bracket cheek outer face (servo side)
CHEEK_T = 4.0
BRIDGE_Y = (3.0, 7.0)
CLAMP_PLATE_Y = (7.0, 11.0)
PHONE_BACK_Y = 14.0
PHONE_W = 75.0                                     # head phone mock (landscape: width vertical)
JAW_BOTTOM_Z = TILT_Z - 36.5                       # top face of the fixed lower jaw
PHONE_CZ = JAW_BOTTOM_Z + PHONE_W / 2
