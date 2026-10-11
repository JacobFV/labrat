"""Target-phone tray: pocket pocket_w x pocket_l centred at (center_x, center_y), floor at floor_z.
Two dovetail tongues on its rear face drop into sockets in the base front and are locked by M3
screws from the deck, so the tray geometry is fixed in the world frame.  Finger scoops, cable
notches on both sides at the rear (phone port end), lightening grid underneath."""
from __future__ import annotations

from build123d import Pos, extrude

from ..lib.util import box, cyl_x, cyl_z, polygon_face, rbox
from ..params import BASE_Y1, M3_TAP, TRAY_CX, TRAY_CY, TRAY_FLOOR_Z, TRAY_POCKET_L, TRAY_POCKET_W
from ._common import meta
from .base import DOVETAIL_H, DOVETAIL_ROOT_Y, DOVETAIL_TIP_Y, DOVETAIL_X, dovetail_pts

META = meta("tray", 1, color="#7fb3d5", orient=(0, 0, 0),
            notes="Print flat (bottom on the bed). 3 walls, 20% infill. 100 x 180 mm footprint.", infill=20)

WALL_TOP = TRAY_FLOOR_Z + 6.0
Y0 = DOVETAIL_ROOT_Y
Y1 = 2 * TRAY_CY - Y0
X0, X1 = TRAY_CX - 50.0, TRAY_CX + 50.0
PX0, PX1 = TRAY_CX - TRAY_POCKET_W / 2, TRAY_CX + TRAY_POCKET_W / 2
PY0, PY1 = TRAY_CY - TRAY_POCKET_L / 2, TRAY_CY + TRAY_POCKET_L / 2
CABLE_NOTCH_Y = (PY0 + 3.0, PY0 + 17.0)


def make():
    t = rbox(X0, X1, Y0, Y1, 0, WALL_TOP, 6)
    t = t - rbox(PX0, PX1, PY0, PY1, TRAY_FLOOR_Z, WALL_TOP + 1, 3)
    # finger scoops on both long sides
    for x in (X0, X1):
        t = t - cyl_x(14, x - 12, x + 12, y=TRAY_CY, z=WALL_TOP + 7)
    # cable notches (rear end, both sides) down to just above the floor
    for sx in (-1, 1):
        xa, xb = (PX1 - 1, X1 + 1) if sx > 0 else (X0 - 1, PX0 + 1)
        t = t - box(xa, xb, CABLE_NOTCH_Y[0], CABLE_NOTCH_Y[1], TRAY_FLOOR_Z + 1.0, WALL_TOP + 1)
    # lightening grid under the floor (keeps a 2.4 mm skin)
    nx, ny = 3, 6
    cw, cl = (PX1 - PX0 - 8) / nx, (PY1 - PY0 - 8) / ny
    for i in range(nx):
        for j in range(ny):
            cx = PX0 + 4 + cw * (i + 0.5)
            cy = PY0 + 4 + cl * (j + 0.5)
            t = t - rbox(cx - cw / 2 + 1.5, cx + cw / 2 - 1.5, cy - cl / 2 + 1.5, cy + cl / 2 - 1.5, -1,
                         TRAY_FLOOR_Z - 2.4, 3)
    # anti-slip pad recesses
    for x in (X0 + 10, X1 - 10):
        for y in (Y0 + 12, Y1 - 12):
            t = t - cyl_z(5.3, -1, 1.0, x, y)
    # dovetail tongues
    for xc in DOVETAIL_X:
        tongue = Pos(xc, 0, 0) * extrude(polygon_face(dovetail_pts(0.0, root_ext=0.5)), DOVETAIL_H)
        tongue = tongue - cyl_z(M3_TAP / 2, DOVETAIL_H - 9, DOVETAIL_H + 1, xc, (DOVETAIL_ROOT_Y + DOVETAIL_TIP_Y) / 2)
        t = t + tongue
    return t
