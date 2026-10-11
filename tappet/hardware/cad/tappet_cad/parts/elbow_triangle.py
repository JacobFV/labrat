"""Elbow triangle: free on the elbow pin; carries the ends of wrist_link_a (at D) and wrist_link_b
(at E).  Its orientation is fixed relative to the turret by parallelogram #1."""
from __future__ import annotations

from ..lib.util import circle_at, plate_xz, polygon_face, stadium
from ..params import D_VEC, E_VEC, PLANES, PIN_HOLE
from build123d import Sketch
from ._common import meta

META = meta("elbow_triangle", 2, color="#16a085", orient=(90, 0, 0), notes="Print flat. 100% infill.", infill=100)


def make():
    v0, v1 = PLANES["elbow_triangle"]
    O = (0.0, 0.0)
    sk = Sketch() + polygon_face([O, D_VEC, E_VEC])
    for a, b in ((O, D_VEC), (O, E_VEC), (D_VEC, E_VEC)):
        sk = sk + stadium(a, b, 9)
    sk = sk + circle_at(O, 7) + circle_at(D_VEC, 5) + circle_at(E_VEC, 5)
    cx, cy = (D_VEC[0] + E_VEC[0]) / 3, (D_VEC[1] + E_VEC[1]) / 3 + 1.0
    sk = sk - circle_at((cx, cy), 2.0)
    for p in (O, D_VEC, E_VEC):
        sk = sk - circle_at(p, PIN_HOLE / 2)
    return plate_xz(sk, v0, v1)
