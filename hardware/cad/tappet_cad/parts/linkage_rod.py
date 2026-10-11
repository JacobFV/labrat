"""Parallel-linkage rod: crank pin -> forearm rear pin.  Length == L1 so crank/rod/forearm/upper arm
form a parallelogram (forearm angle == crank angle + 180, i.e. theta2 is absolute)."""
from __future__ import annotations

from ..lib.util import circle_at, plate_xz, stadium
from ..params import L1, PLANES, PIN_HOLE
from ._common import meta

META = meta("linkage_rod", 2, color="#c0392b", orient=(90, 0, 0), notes="Print flat. 4 walls.", walls=4)


def make():
    v0, v1 = PLANES["linkage_rod"]
    sk = stadium((0, 0), (L1, 0), 8) + circle_at((0, 0), 5) + circle_at((L1, 0), 5)
    sk = sk - stadium((14, 0), (L1 - 14, 0), 2.6)
    sk = sk - circle_at((0, 0), PIN_HOLE / 2) - circle_at((L1, 0), PIN_HOLE / 2)
    return plate_xz(sk, v0, v1)
