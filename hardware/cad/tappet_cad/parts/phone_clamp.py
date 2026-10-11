"""Universal spring-loaded phone clamp for the Android head (phones 64-82 mm wide, 7-11 mm thick,
held landscape, screen facing +y).  Back plate with a fixed lower jaw, a vertical dovetail rail and a
top spring stop; the sliding upper jaw (phone_clamp_jaw) is pushed down by a 6 x 32 mm compression
spring.  World frame at tilt = 0."""
from __future__ import annotations

from build123d import Pos, Rot, extrude, RectangleRounded

from ..lib.util import box, cyl_y, cyl_z, polygon_face, rbox
from ..lib.fasteners import hole_z
from ..params import CLR, M3_CLEAR, TILT_Z
from ._common import meta
from ._neck import CLAMP_PLATE_Y, JAW_BOTTOM_Z, PHONE_BACK_Y, PHONE_CZ, PHONE_W
from .neck_tilt_bracket import CLAMP_BOLTS_X

META = meta("phone_clamp", 1, color="#34495e", orient=(90, 0, 0),
            notes="Print back-plate down (rail, ribs, jaws up; no supports). 4 walls, 30% infill.", walls=4)
META_JAW = meta("phone_clamp_jaw", 1, color="#5d6d7e", orient=(180, 0, 0),
                notes="Print upside down (top face on the bed, lip up). 100% infill.", infill=100)

PY0, PY1 = CLAMP_PLATE_Y
PLATE_Z0 = JAW_BOTTOM_Z - 6.0
STOP_Z0 = TILT_Z + 58.0
PLATE_Z1 = STOP_Z0 + 6.0
RAIL_Z0 = TILT_Z + 4.0
LIP_Y = PHONE_BACK_Y + 11.0           # inner face of the jaw lips (max phone thickness 11)
SPRING_Y = PHONE_BACK_Y + 3.0
JAW_H = 9.0
JAW_HALF = 13.0


def rail_profile(clear=0.0):
    # dovetail in x-y, root on the plate front face, widening toward +y
    return [(-6.0 - clear, PY1 - 0.01), (6.0 + clear, PY1 - 0.01), (7.5 + clear, PHONE_BACK_Y + clear),
            (-7.5 - clear, PHONE_BACK_Y + clear)]


def make():
    plate = Pos(0, PY0, (PLATE_Z0 + PLATE_Z1) / 2) * Rot(-90, 0, 0) * extrude(RectangleRounded(72, PLATE_Z1 - PLATE_Z0, 8), PY1 - PY0)
    # extrude along +y from PY0 (Rot(-90) maps +z -> +y)
    rail = Pos(0, 0, RAIL_Z0) * extrude(polygon_face(rail_profile()), STOP_Z0 - RAIL_Z0 + 0.01)
    ribs = box(-27, -22, PY1 - 0.01, PHONE_BACK_Y, JAW_BOTTOM_Z + 4, TILT_Z + 28) + \
        box(22, 27, PY1 - 0.01, PHONE_BACK_Y, JAW_BOTTOM_Z + 4, TILT_Z + 28)
    ledge = box(-18, 18, PY1 - 0.01, LIP_Y + 2.0, JAW_BOTTOM_Z - 4.0, JAW_BOTTOM_Z)
    lip = box(-18, 18, LIP_Y, LIP_Y + 2.0, JAW_BOTTOM_Z - 0.01, JAW_BOTTOM_Z + 3.0)
    stop = box(-JAW_HALF, JAW_HALF, PY1 - 0.01, LIP_Y + 2.0, STOP_Z0, PLATE_Z1)
    c = plate + rail + ribs + ledge + lip + stop
    c = c + cyl_z(2.0, STOP_Z0 - 8.0, STOP_Z0 + 0.01, 0, SPRING_Y + 4.5)
    for x in CLAMP_BOLTS_X:
        c = c - cyl_y(M3_CLEAR / 2, PY0 - 1, PHONE_BACK_Y + 1, x=x, z=TILT_Z)
        c = c - cyl_y(3.1, PY1 - 0.5, PHONE_BACK_Y + 1, x=x, z=TILT_Z)   # head counterbore below phone
    # cable slot in the lower ledge for the head's USB-C lead (port at the phone's short end is
    # left free; this slot is for a 90-degree cable routed down behind the clamp)
    c = c - box(-30, -20, PY0 - 1, PY1 + 1, PLATE_Z0 + 8, PLATE_Z0 + 20)
    return c


def jaw_z0(phone_w=PHONE_W):
    return JAW_BOTTOM_Z + phone_w


def make_jaw():
    z0 = jaw_z0()
    z1 = z0 + JAW_H
    j = box(-JAW_HALF, JAW_HALF, PY1 + 0.3, LIP_Y + 2.0, z0, z1)
    j = j + box(-JAW_HALF, JAW_HALF, PY1 + 0.3, LIP_Y + 2.0, z0, z0 + 3.5)   # finger over the phone edge
    j = j + box(-JAW_HALF, JAW_HALF, LIP_Y, LIP_Y + 2.0, z0 - 3.0, z0 + 0.01)
    groove = Pos(0, 0, z0 - 5) * extrude(polygon_face(rail_profile(CLR)), JAW_H + 10)
    j = j - groove
    j = j - cyl_z(3.4, z1 - 8.0, z1 + 1, 0, SPRING_Y + 4.5)                 # spring pocket
    return j
