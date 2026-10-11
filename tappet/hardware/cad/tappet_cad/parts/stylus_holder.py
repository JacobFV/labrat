"""Stylus holder: hangs on the wrist pin, kept level by wrist_link_b.  Vertical sleeve (axis at
tool_dx in front of the wrist pin) guiding the compliant plunger.  Holder frame: origin = wrist pivot,
axes = arm (u, v, w)."""
from __future__ import annotations

from ..lib.util import box, circle_at, cyl_y, cyl_z, plate_xz, stadium
from ..params import (E_VEC, PIN_HOLE, PLANES, SLEEVE_BORE, SLEEVE_OD, SLEEVE_Z0, SLEEVE_Z1, TOOL_DX,
                      RETAIN_SCREW_Z, M2_TAP)
from ._common import meta

META = meta("stylus_holder", 2, color="#8e44ad", orient=(0, 0, 0), handed=True,
            notes="Print upright on the sleeve flare (round bore), tree supports under the pin plate only; ream bore with a 7 mm drill. "
                  "L and R are mirror images (export _L/_R).", infill=60, walls=4)


def make():
    v0, v1 = PLANES["stylus_holder"]
    O = (0.0, 0.0)
    sk = stadium(O, E_VEC, 10) + circle_at(O, 6) + circle_at(E_VEC, 5)
    sk = sk + stadium(O, (TOOL_DX, -8), 10) + stadium(E_VEC, (TOOL_DX + 2, -2), 9)
    sk = sk - circle_at(O, PIN_HOLE / 2) - circle_at(E_VEC, PIN_HOLE / 2)
    plate = plate_xz(sk, v0, v1)
    x = TOOL_DX
    collar = box(x - 6, x + 6, 0, v1, -14, SLEEVE_Z1)
    sleeve = cyl_z(SLEEVE_OD / 2, SLEEVE_Z0, SLEEVE_Z1, x=x)
    # lower guide flare
    flare = cyl_z(SLEEVE_OD / 2 + 0.8, SLEEVE_Z0, SLEEVE_Z0 + 3, x=x)
    p = plate + collar + sleeve + flare
    # keep the plate clear of the wrist pin boss of the forearm (y < v0) – collar only spans y>=0
    p = p - cyl_z(SLEEVE_BORE / 2, SLEEVE_Z0 - 1, SLEEVE_Z1 - 2, x=x)          # bore (closed cap on top)
    p = p - cyl_z(1.2, SLEEVE_Z1 - 3, SLEEVE_Z1 + 1, x=x)                       # ground-lead exit
    p = p - cyl_y(M2_TAP / 2, -SLEEVE_OD, 0, x=x, z=RETAIN_SCREW_Z)             # M2 retaining screw (from -v)
    # ground-lead groove along the top of the collar
    p = p - box(x - 0.9, x + 0.9, -1, v1 + 1, SLEEVE_Z1 - 1.0, SLEEVE_Z1 + 1)
    return p
