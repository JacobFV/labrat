"""Fastener helpers: clearance / pilot holes, counterbores, nut traps, and simple screw mocks."""
from __future__ import annotations

import math

from build123d import Part, Pos, Rot, extrude, RegularPolygon

from ..params import M2_CLEAR, M2_TAP, M3_CLEAR, M3_TAP, M3_HEAD_D, M3_HEAD_H, M3_NUT_AF
from .util import cyl_z


def hole_z(d, z0, z1, x=0.0, y=0.0) -> Part:
    return cyl_z(d / 2, z0, z1, x, y)


def m3_counterbored_z(x, y, z_top, depth, head_depth=M3_HEAD_H) -> Part:
    """Through hole downward from z_top, with a socket-head counterbore at the top."""
    return hole_z(M3_CLEAR, z_top - depth, z_top + 1, x, y) + hole_z(M3_HEAD_D, z_top - head_depth, z_top + 1, x, y)


def m3_countersunk_z_bottom(x, y, z_bot, depth) -> Part:
    """Through hole upward from z_bot with a 90deg countersink at the bottom face (lid screws)."""
    from build123d import Cone, Align
    cs = Pos(x, y, z_bot - 0.01) * Cone(3.1, 1.65, 1.6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return hole_z(M3_CLEAR, z_bot - 1, z_bot + depth, x, y) + cs


def nut_trap_z(x, y, z0, h, af=M3_NUT_AF) -> Part:
    d = af / math.cos(math.pi / 6)
    return Pos(x, y, z0) * extrude(RegularPolygon(d / 2, 6), h)


def screw_mock_z(d, length, head_d, head_h) -> Part:
    """Socket-head screw mock: head on top at z in [0, head_h], shank downward to -length."""
    return cyl_z(head_d / 2, 0, head_h) + cyl_z(d / 2, -length, 0)
