"""608 ball bearing (8 x 22 x 7) mock.  Frame: axis = z, bottom face at z=0."""
from __future__ import annotations

from ..params import BEARING_608
from .util import cyl_z


def bearing_608():
    d, D, B = BEARING_608["d"], BEARING_608["D"], BEARING_608["B"]
    outer = cyl_z(D / 2, 0, B) - cyl_z(D / 2 - 2.2, -1, B + 1)
    inner = cyl_z(BEARING_608["inner_land_d"] / 2, 0, B) - cyl_z(d / 2, -1, B + 1)
    shield = cyl_z(D / 2 - 2.2, 0.4, B - 0.4) - cyl_z(BEARING_608["inner_land_d"] / 2, -1, B + 1)
    return outer + inner + shield
