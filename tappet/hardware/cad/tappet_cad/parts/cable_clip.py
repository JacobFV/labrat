"""Snap-on cable clip for the 11-13 mm wide arm links: a C that grips the link edges plus a loop for
the servo leads / stylus ground wire."""
from __future__ import annotations

from ..lib.util import box, cyl_y
from ._common import meta

META = meta("cable_clip", 12, material="PETG", color="#27ae60", orient=(90, 0, 0),
            notes="Print on its side (C profile flat on the bed) in PETG for springiness, 100% infill.", infill=100)

INNER_W = 13.2   # fits 11-13 mm wide links (lips flex)
INNER_T = 5.4    # fits 4-5 mm thick links
T = 1.6
DEPTH = 6.0


def make():
    w, t = INNER_W, INNER_T
    outer = box(-w / 2 - T, w / 2 + T, 0, DEPTH, -T, t + T)
    c = outer - box(-w / 2, w / 2, -1, DEPTH + 1, 0, t)
    c = c - box(-w / 2 + 1.6, w / 2 - 1.6, -1, DEPTH + 1, t - 0.1, t + T + 1)  # snap opening with lips
    loop = cyl_y(4.2, 0, DEPTH, x=0, z=-T - 3.4) - cyl_y(2.6, -1, DEPTH + 1, x=0, z=-T - 3.4)
    loop = loop - box(-0.6, 0.6, -1, DEPTH + 1, -T - 8, -T - 5.8)       # slit to push the wires in
    return c + loop
