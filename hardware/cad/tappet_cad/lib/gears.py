"""Involute spur-gear generator (2D profile -> extruded Part).

Standard full-depth involute: addendum = m, dedendum = 1.25 m, pressure angle 20 deg.  Backlash is
removed from the tooth thickness (half per gear).  The tooth with index 0 is centred on +x.
"""
from __future__ import annotations

import math

from build123d import Part, Pos, extrude

from .util import polygon_face


def _inv(a):
    return math.tan(a) - a


def involute_points(z: int, m: float, pa_deg: float = 20.0, backlash: float = 0.1, n_flank: int = 9, n_tip: int = 3):
    pa = math.radians(pa_deg)
    rp = m * z / 2
    rb = rp * math.cos(pa)
    ra = rp + m
    rf = rp - 1.25 * m
    # half tooth angle at pitch circle (circular thickness = pi m / 2 - backlash/2)
    s_p = math.pi * m / 2 - backlash / 2
    half_p = s_p / (2 * rp)
    half_b = half_p + _inv(pa)               # half tooth angle at base circle

    def flank_angle(r):
        if r <= rb:
            return half_b
        a = math.acos(rb / r)
        return half_b - _inv(a)

    r_start = max(rb, rf)
    rs = [r_start + (ra - r_start) * (i / (n_flank - 1)) ** 1.4 for i in range(n_flank)]
    pts = []
    for i in range(z):
        c = 2 * math.pi * i / z
        # root corner (left)
        if rf < rb:
            pts.append((rf, c - half_b))
        for r in rs:
            pts.append((r, c - flank_angle(r)))
        ta = flank_angle(ra)
        for k in range(1, n_tip):
            pts.append((ra, c - ta + 2 * ta * k / n_tip))
        for r in reversed(rs):
            pts.append((r, c + flank_angle(r)))
        if rf < rb:
            pts.append((rf, c + half_b))
    return [(r * math.cos(a), r * math.sin(a)) for r, a in pts], dict(rp=rp, rb=rb, ra=ra, rf=rf)


def spur_gear(z: int, m: float, thickness: float, backlash: float = 0.1) -> Part:
    pts, _ = involute_points(z, m, backlash=backlash)
    return extrude(polygon_face(pts), thickness)


def gear_radii(z: int, m: float):
    rp = m * z / 2
    return dict(rp=rp, ra=rp + m, rf=rp - 1.25 * m)
