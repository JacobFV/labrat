"""Compliant stylus plunger (slides in the holder sleeve, 6 mm travel, M2 screw in a slot limits
travel, small compression spring above).  Carries a 6 mm conductive stylus tip in its bottom socket;
a 1.6 mm axial channel passes the grounding lead.  Frame = holder frame at rest (fully extended)."""
from __future__ import annotations

from build123d import Sphere, Pos

from ..lib.util import box, cyl_z
from ..params import (PLUNGER_D, PLUNGER_Z0, PLUNGER_Z1, RETAIN_SCREW_Z, STYLUS_TRAVEL, TIP_D, TOOL_DX,
                      TOOL_DZ)
from ._common import meta

META = meta("stylus_plunger", 2, color="#2c3e50", orient=(0, 0, 0),
            notes="Print upright, 0.12 mm layers, 100% infill; sand lightly to slide freely.", infill=100, layer=0.12)

SLOT_W = 2.6


def make():
    x = TOOL_DX
    p = cyl_z(PLUNGER_D / 2, PLUNGER_Z0, PLUNGER_Z1, x=x) + cyl_z(1.6, PLUNGER_Z1, PLUNGER_Z1 + 2.5, x=x)
    p = p - cyl_z(0.8, PLUNGER_Z0 - 1, PLUNGER_Z1 + 3, x=x)                 # wire channel
    p = p - cyl_z(1.35, PLUNGER_Z0 - 1, PLUNGER_Z0 + 6, x=x)                # tip stem socket
    # travel slot on the -v side: screw sits at the slot top when extended
    z_top = RETAIN_SCREW_Z + SLOT_W / 2
    z_bot = RETAIN_SCREW_Z - SLOT_W / 2 - STYLUS_TRAVEL
    p = p - box(x - SLOT_W / 2, x + SLOT_W / 2, -PLUNGER_D, -1.5, z_bot, z_top)
    return p


def tip_mock():
    """Commercial 6 mm conductive rubber/mesh stylus tip on a 2.5 mm stem.  Lowest point = tool point."""
    x = TOOL_DX
    r = TIP_D / 2
    z_bottom = -TOOL_DZ
    zc = z_bottom + r
    dome = Pos(x, 0, zc) * Sphere(r)
    dome = dome - cyl_z(r + 1, zc, zc + r + 1, x=x)
    body = cyl_z(r, zc, PLUNGER_Z0, x=x)
    stem = cyl_z(1.25, PLUNGER_Z0, PLUNGER_Z0 + 5.5, x=x)
    return dome + body + stem
