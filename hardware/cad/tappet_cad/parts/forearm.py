"""Forearm: elbow pin at origin, wrist pin at +L2, rear crank pin at -CRANK_A (parallel-linkage arm)."""
from __future__ import annotations

from ..lib.util import circle_at, plate_xz, stadium
from ..params import CRANK_A, L2, PLANES, PIN_HOLE
from ._common import meta

META = meta("forearm", 2, color="#f2a541", orient=(90, 0, 0),
            notes="Print flat. 4 walls, 40% infill. Same part for L and R.", infill=40, walls=4)


def make():
    v0, v1 = PLANES["forearm"]
    sk = stadium((-CRANK_A, 0), (L2, 0), 11) + circle_at((0, 0), 7.5) + circle_at((-CRANK_A, 0), 6) + circle_at((L2, 0), 6)
    sk = sk - stadium((15, 0), (L2 - 15, 0), 4)
    for x in (-CRANK_A, 0, L2):
        sk = sk - circle_at((x, 0), PIN_HOLE / 2)
    return plate_xz(sk, v0, v1)
