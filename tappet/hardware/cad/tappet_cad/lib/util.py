"""Small geometry helpers on top of build123d (algebra API) + 4x4 transform utilities."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
from build123d import (
    Align, Box, Circle, Cylinder, Face, Location, Part, Pos, Rectangle, Rot, Shape, Sketch,
    SlotCenterToCenter, Vector, Wire, extrude, Plane,
)
from OCP.gp import gp_Trsf

# ------------------------------------------------------------------------------- solids
def box(x0, x1, y0, y1, z0, z1) -> Part:
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(abs(x1 - x0), abs(y1 - y0), abs(z1 - z0))


def cyl_z(r, z0, z1, x=0.0, y=0.0) -> Part:
    return Pos(x, y, z0) * Cylinder(r, z1 - z0, align=(Align.CENTER, Align.CENTER, Align.MIN))


def cyl_y(r, y0, y1, x=0.0, z=0.0) -> Part:
    # cylinder along +y from y0 to y1
    return Pos(x, y0, z) * Rot(-90, 0, 0) * Cylinder(r, y1 - y0, align=(Align.CENTER, Align.CENTER, Align.MIN))


def cyl_x(r, x0, x1, y=0.0, z=0.0) -> Part:
    return Pos(x0, y, z) * Rot(0, 90, 0) * Cylinder(r, x1 - x0, align=(Align.CENTER, Align.CENTER, Align.MIN))


def rbox(x0, x1, y0, y1, z0, z1, r) -> Part:
    """Box with vertical edges rounded (r)."""
    w, l = abs(x1 - x0), abs(y1 - y0)
    r = min(r, w / 2 - 1e-3, l / 2 - 1e-3)
    sk = Rectangle(w, l)
    if r > 0.05:
        from build123d import RectangleRounded
        sk = RectangleRounded(w, l, r)
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, z0) * extrude(sk, z1 - z0)


def polygon_face(pts: Sequence[tuple[float, float]]) -> Face:
    """Planar polygon face in XY, always oriented with +z normal (CCW) so extrusions go +z."""
    pts = list(pts)
    area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    if area < 0:
        pts = pts[::-1]
    return Face(Wire.make_polygon([Vector(p[0], p[1], 0) for p in pts], close=True))


def stadium(p, q, w) -> Sketch:
    """2D stadium (slot) between centre points p and q, width w (XY plane)."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    d = math.hypot(dx, dy)
    ang = math.degrees(math.atan2(dy, dx))
    mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    if d < 1e-6:
        return Pos(*mid) * Circle(w / 2)
    return Pos(mid[0], mid[1]) * Rot(0, 0, ang) * SlotCenterToCenter(d, w)


def circle_at(p, r) -> Sketch:
    return Pos(p[0], p[1]) * Circle(r)


def plate_xz(sk, v0: float, v1: float) -> Part:
    """Extrude a sketch drawn in XY (x = link axis, y = in-plane normal) into a plate spanning
    world/local y in [v0, v1], with the sketch's y mapped onto local z.  (Rx(+90): y->z, z->-y)."""
    solid = Pos(0, 0, -v1) * extrude(sk, v1 - v0)
    return Rot(90, 0, 0) * solid


def hole_y(r, x, z, v0=-100.0, v1=100.0) -> Part:
    return cyl_y(r, v0, v1, x=x, z=z)


# ------------------------------------------------------------------------------- transforms
def T(x=0.0, y=0.0, z=0.0) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


def Rx(deg) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = np.eye(4)
    m[1:3, 1:3] = [[c, -s], [s, c]]
    return m


def Ry(deg) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = np.eye(4)
    m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, s, -s, c
    return m


def Rz(deg) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = np.eye(4)
    m[:2, :2] = [[c, -s], [s, c]]
    return m


def frame(origin, xdir, ydir, zdir) -> np.ndarray:
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = xdir, ydir, zdir, origin
    return m


def to_location(M: np.ndarray) -> Location:
    t = gp_Trsf()
    t.SetValues(*[float(v) for v in M[0, :4]], *[float(v) for v in M[1, :4]], *[float(v) for v in M[2, :4]])
    return Location(t)


def place(shape: Shape, M: np.ndarray) -> Shape:
    return shape.moved(to_location(M))


def apply(M: np.ndarray, p) -> np.ndarray:
    return (M @ np.array([p[0], p[1], p[2], 1.0]))[:3]


def bbox_dims(shape: Shape):
    bb = shape.bounding_box()
    return (bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z)


def solid_count(shape: Shape) -> int:
    return len(shape.solids())


def vol(shape_or_list) -> float:
    if shape_or_list is None:
        return 0.0
    if isinstance(shape_or_list, (list, tuple)) or hasattr(shape_or_list, "__iter__") and not hasattr(shape_or_list, "volume"):
        return float(sum(s.volume for s in shape_or_list))
    return float(shape_or_list.volume)


def union_all(parts: Iterable[Part]) -> Part:
    parts = list(parts)
    out = parts[0]
    for p in parts[1:]:
        out = out + p
    return out
