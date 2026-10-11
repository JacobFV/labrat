"""Wrist-levelling links (two parallelograms keep the stylus holder's orientation fixed in the turret):
 * wrist_link_a: turret ground post -> elbow triangle, length L1 (parallel to the upper arm)
 * wrist_link_b: elbow triangle -> stylus holder, length L2 (parallel to the forearm)"""
from __future__ import annotations

from ..lib.util import circle_at, plate_xz, stadium
from ..params import L1, L2, PLANES, PIN_HOLE
from ._common import meta

META_A = meta("wrist_link_a", 2, color="#7f8c8d", orient=(90, 0, 0), notes="Print flat. 4 walls.", walls=4)
META_B = meta("wrist_link_b", 2, color="#95a5a6", orient=(90, 0, 0), notes="Print flat. 4 walls.", walls=4)
META = META_A


def _rod(length, plane):
    v0, v1 = PLANES[plane]
    sk = stadium((0, 0), (length, 0), 7) + circle_at((0, 0), 4.6) + circle_at((length, 0), 4.6)
    sk = sk - circle_at((0, 0), PIN_HOLE / 2) - circle_at((length, 0), PIN_HOLE / 2)
    return plate_xz(sk, v0, v1)


def make_a():
    return _rod(L1, "wrist_link_a")


def make_b():
    return _rod(L2, "wrist_link_b")


make = make_a
