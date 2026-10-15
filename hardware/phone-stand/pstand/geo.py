"""Small build123d helpers: prisms from 2D profiles along x/y/z, teardrops, rounded rectangles, involute gears."""
from __future__ import annotations

import math

import numpy as np
from build123d import (Axis, Box, Circle, Cone, Cylinder, Location, Plane, Polygon, Pos, Rectangle, RectangleRounded,
                       Rot, Sketch, Solid, extrude)

# profile planes: (u, v) axes and the extrusion normal
#   'x': (u, v) = (y, z), normal +x      'y': (u, v) = (x, z), normal -y (we extrude from a1 toward a0)
#   'z': (u, v) = (x, y), normal +z


def _plane(axis: str, a: float) -> Plane:
    if axis == "x":
        return Plane(origin=(a, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0))
    if axis == "y":
        return Plane(origin=(0, a, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
    return Plane(origin=(0, 0, a), x_dir=(1, 0, 0), z_dir=(0, 0, 1))


_DIR = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


def prism(sk, axis: str, a0: float, a1: float):
    """extrude a 2D sketch (build123d Sketch/Face in plane-local (u, v)) along `axis` between a0 < a1 (explicit
    direction: independent of the profile's winding)."""
    return extrude(_plane(axis, a0) * sk, amount=a1 - a0, dir=_DIR[axis])


def poly(pts) -> Sketch:
    return Polygon(*[tuple(p) for p in pts], align=None)


def circ(r, u=0.0, v=0.0) -> Sketch:
    return Pos(u, v) * Circle(r)


def rrect(u0, u1, v0, v1, r) -> Sketch:
    r = min(r, (u1 - u0) / 2 - 1e-3, (v1 - v0) / 2 - 1e-3)
    return Pos((u0 + u1) / 2, (v0 + v1) / 2) * RectangleRounded(u1 - u0, v1 - v0, r)


def rect(u0, u1, v0, v1) -> Sketch:
    return Pos((u0 + u1) / 2, (v0 + v1) / 2) * Rectangle(u1 - u0, v1 - v0)


def teardrop(d, u=0.0, v=0.0, up=(0.0, 1.0)) -> Sketch:
    """circle of diameter d at (u, v) with a 45 deg roof toward `up` (the print's +Z in this sketch's (u, v))."""
    r = d / 2
    ux, uy = up
    px, py = -uy, ux
    k = r / math.sqrt(2)
    apex = (u + ux * r * math.sqrt(2), v + uy * r * math.sqrt(2))
    roof = Polygon((u + px * k + ux * k, v + py * k + uy * k), apex, (u - px * k + ux * k, v - py * k + uy * k), (u, v),
                   align=None)
    return Pos(u, v) * Circle(r) + roof


def box(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def cyl_z(r, z0, z1, x=0.0, y=0.0):
    return Pos(x, y, (z0 + z1) / 2) * Cylinder(r, z1 - z0)


def cyl_x(r, x0, x1, y=0.0, z=0.0):
    return Pos((x0 + x1) / 2, y, z) * Rot(0, 90, 0) * Cylinder(r, x1 - x0)


def cyl_y(r, y0, y1, x=0.0, z=0.0):
    return Pos(x, (y0 + y1) / 2, z) * Rot(90, 0, 0) * Cylinder(r, y1 - y0)


def cone_z(r0, r1, z0, z1, x=0.0, y=0.0):
    """cone from radius r0 at z0 to r1 at z1 (z0 < z1)."""
    return _cone(r0, r1, z0, z1, x, y)


def _cone(r0, r1, z0, z1, x, y):
    from build123d import Align
    return Pos(x, y, z0) * Cone(r0, r1, z1 - z0, align=(Align.CENTER, Align.CENTER, Align.MIN))


def drill_z(d, z_top, depth, x=0.0, y=0.0, down=True):
    """blind hole along z with a 90 deg drill point (prints without a flat ceiling when drilled up from the bed)."""
    r = d / 2
    if down:
        return cyl_z(r, z_top - depth, z_top + 0.01, x, y) + _cone(0.0, r, z_top - depth - r, z_top - depth, x, y)
    return cyl_z(r, z_top - 0.01, z_top + depth, x, y) + _cone(r, 0.0, z_top + depth, z_top + depth + r, x, y)


# ---------------------------------------------------------------- involute spur gear + rack (2D points)
def involute_gear_pts(m, z, pa_deg=20.0, n_inv=8, clearance=0.25, backlash=0.05):
    pa = math.radians(pa_deg)
    rp = m * z / 2
    rb = rp * math.cos(pa)
    ra = rp + m
    rf = rp - (1 + clearance) * m
    inv = lambda a: math.tan(a) - a
    # half tooth angle at the pitch circle (minus backlash)
    half = (math.pi / (2 * z)) - backlash / (2 * rp)
    pts = []
    for k in range(z):
        c = 2 * math.pi * k / z
        flank = []
        r_start = max(rb, rf)
        for i in range(n_inv + 1):
            r = r_start + (ra - r_start) * i / n_inv
            a = math.acos(min(1.0, rb / r))
            th = half + inv(pa) - inv(a)
            flank.append((r, th))
        left = [(r, c - th) for r, th in flank]
        right = [(r, c + th) for r, th in reversed(flank)]
        tooth = [(rf, c - half - inv(pa) - 0.02)] + left + right + [(rf, c + half + inv(pa) + 0.02)]
        # root arc to the next tooth
        nxt = 2 * math.pi * (k + 1) / z
        a0, a1 = c + half + inv(pa) + 0.02, nxt - half - inv(pa) - 0.02
        root = [(rf, a0 + (a1 - a0) * j / 4) for j in range(1, 4)]
        pts += tooth + root
    return [(r * math.cos(a), r * math.sin(a)) for r, a in pts]


def mat_to_loc(M) -> Location:
    """4x4 numpy -> build123d Location."""
    from OCP.gp import gp_Trsf
    t = gp_Trsf()
    M = np.asarray(M, float)
    t.SetValues(M[0, 0], M[0, 1], M[0, 2], M[0, 3], M[1, 0], M[1, 1], M[1, 2], M[1, 3], M[2, 0], M[2, 1], M[2, 2],
                M[2, 3])
    return Location(t)


def rot_x(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]], float)


def rot_y(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]], float)


def rot_z(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float)


def trans(x, y, z):
    M = np.eye(4)
    M[:3, 3] = (x, y, z)
    return M


def frame(origin, ex, ey, ez):
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2], M[:3, 3] = ex, ey, ez, origin
    return M
