"""Target-phone tray, open frame: perimeter rail (with a 2 mm ledge at the pocket floor), three cross
ribs and a centre spine whose tops are the phone floor (z = floor_z), raised L corner stops that locate
the phone / shim in the pocket_w x pocket_l pocket centred at (center_x, center_y), and a cable notch
at the port end.  Two rear tabs sit on the chassis lugs: an integral pin on each lug + 2 M3 screws fix
the tray's position in the world frame.  World frame."""
from __future__ import annotations

from build123d import extrude

from ..lib.util import box, cyl_z, rbox, stadium, union_all
from ..params import M3_CLEAR, TRAY_CX, TRAY_CY, TRAY_FLOOR_Z, TRAY_POCKET_L, TRAY_POCKET_W
from ._common import meta
from .base import BASE_Y1, DOVETAIL_X, LUG

META = meta("tray", 1, color="#7fb3d5", orient=(0, 0, 0),
            notes="Print flat. 0.28 mm draft, 2 walls, 10% infill, no supports.", infill=10, walls=2, layer=0.28)

RAIL_W = 2.0
LEDGE = 1.6
STOP_TOP = TRAY_FLOOR_Z + 3.5
PX0, PX1 = TRAY_CX - TRAY_POCKET_W / 2, TRAY_CX + TRAY_POCKET_W / 2
PY0, PY1 = TRAY_CY - TRAY_POCKET_L / 2, TRAY_CY + TRAY_POCKET_L / 2
X0, X1 = PX0 - RAIL_W, PX1 + RAIL_W
Y0, Y1 = BASE_Y1 + 0.2, PY1 + RAIL_W
CABLE_NOTCH_Y = (PY0 + 3.0, PY0 + 17.0)     # kept for the shims
TAB_Z0 = LUG["z1"]
TAB_Z1 = TAB_Z0 + 3.0


def make():
    zf = TRAY_FLOOR_Z
    ring = rbox(X0, X1, Y0, Y1, 0, zf, 4) - rbox(PX0, PX1, PY0, PY1, -1, zf + 1, 2)
    ring = ring - rbox(PX0 + 2.0, PX1 - 2.0, Y0 + 2.0, PY0 - 0.01, -1, zf + 1, 2)        # hollow rear end
    ledge = rbox(PX0 + 0.01, PX1 - 0.01, PY0 + 0.01, PY1 - 0.01, zf - 1.6, zf, 2) - \
        rbox(PX0 + LEDGE, PX1 - LEDGE, PY0 + LEDGE, PY1 - LEDGE, zf - 2, zf + 1, 1)
    ring = ring + ledge
    # through-windows along the side rails (bridged 18 mm)
    k = 7
    for i in range(k):
        y = PY0 + 11 + i * (TRAY_POCKET_L - 22) / (k - 1)
        ring = ring - box(X0 - 1, X1 + 1, y - 9.5, y + 9.5, 1.0, zf - 1.8)
    ribs = []
    n = 3
    for i in range(n):
        y = PY0 + (i + 1) * (PY1 - PY0) / (n + 1)
        ribs.append(box(PX0 - 1, PX1 + 1, y - 0.6, y + 0.6, 0, zf))
    # raised L corner stops on the rail (locate phone / shim)
    stops = []
    L = 12.0
    for sx, xx in ((-1, PX0), (1, PX1)):
        for sy, yy in ((-1, PY0), (1, PY1)):
            xo = xx if sx > 0 else xx - RAIL_W
            yo = yy if sy > 0 else yy - RAIL_W
            stops.append(box(xo, xo + RAIL_W, min(yy, yy - sy * L), max(yy, yy - sy * L), zf - 0.01, STOP_TOP))
            stops.append(box(min(xx, xx - sx * L), max(xx, xx - sx * L), yo, yo + RAIL_W, zf - 0.01, STOP_TOP))
    # rear tabs onto the chassis lugs
    tabs = []
    for xc in DOVETAIL_X:
        t = box(xc - 7.5, xc + 7.5, LUG["y0"] + 0.3, Y0 + 0.01, TAB_Z0, TAB_Z1)
        t = t + box(xc - 7.5, xc + 7.5, Y0, Y0 + RAIL_W + 0.5, 0, TAB_Z1)        # riser on the rear rail
        t = t - cyl_z(LUG["pin_d"] / 2 + 0.15, TAB_Z0 - 1, TAB_Z1 + 1, xc + LUG["pin_dx"], LUG["y"])
        t = t - cyl_z(M3_CLEAR / 2, TAB_Z0 - 1, TAB_Z1 + 1, xc + LUG["screw_dx"], LUG["y"])
        tabs.append(t)
    t = union_all([ring] + ribs + stops + tabs)
    # cable notch (port end, centre): open the rear ledge + rail down to 1 mm
    t = t - box(TRAY_CX - 9, TRAY_CX + 9, Y0 - 1, PY0 + LEDGE + 0.5, 1.5, zf + 5)
    # pad recesses (0.8 mm silicone) at the four outer corners
    for x in (X0 + 5, X1 - 5):
        for y in (Y0 + 6, Y1 - 5):
            t = t + cyl_z(5.0, 0, 2.0, x, y)
            t = t - cyl_z(4.0, -1, 0.8, x, y)
    return t
