"""Make parts. Each builder returns one solid in the PART frame stated in its docstring.

Processes: FDM (PETG, 0.2 mm layers, 0.4 mm nozzle), turned POM / brass. Every printed part prints WITHOUT supports in
the orientation given by ``build.PRINT_ORIENT``: in that orientation it is 2.5D (each layer sits on the one below), and
holes that run parallel to the bed are teardrops (45 deg roof).
FDM rules used here: holes for heat-set inserts = insert OD, bearing bores = bearing OD (press), dowel holes = pin
diameter (press) / + 0.05 (slip), clearance holes M2 2.4 / M3 3.4, outer edges broken (chamfered primitives, chamfered
hole entries), walls >= 1.2 mm.
"""
from __future__ import annotations

import math

from bd_warehouse.gear import SpurGear
from build123d import (Align, Axis, Box, BuildPart, BuildSketch, Circle, Cone, Cylinder, Kind, chamfer, Location, Locations, Mode,
                       Plane, Polygon, Rectangle, RectangleRounded, SlotCenterToCenter, SlotOverall, Solid, add,
                       extrude, make_hull, offset, revolve)

from .spec import LAY, S

CB = (Align.CENTER, Align.CENTER, Align.MIN)
CT = (Align.CENTER, Align.CENTER, Align.MAX)

M3_CLR, M2_CLR = 3.4, 2.4
M3_INSERT = (4.6, 5.7)     # ruthex M3: OD over the knurl, length
M2_INSERT = (3.6, 4.0)
PIN_SLIP = S.pin_d + 0.05

PRINT_ORIENT = {   # part frame -> print bed frame (axis, degrees); the body and the gears already print in their z
    "SYR-101": ((0, 1, 0), 90.0),    # gear plate 2 on its outer face, standoffs up
    "SYR-102": ((1, 0, 0), 180.0),   # flange clamp upside down: relief pocket up
    "SYR-103": ((1, 0, 0), -90.0),   # carriage on its back face (+Y): the rack teeth stand up
    "SYR-105": ((1, 0, 0), 180.0),   # thumb-disc cap upside down: pocket up
    "TM-110": ((0, 1, 0), -90.0),    # tool modules lie on their side (module frame +X up)
    "TM-120": ((0, 1, 0), -90.0),
}
CLAMP_SCREWS = [(0.0, 20.0), (0.0, -20.0)]     # flange clamp: 2x M2 beyond the flange's long ends
PLATE_T = 2.5                 # carriage wall beyond the slide rail's outer face
TIE = (-10.0, 70.0)           # cable tie on gear plate 2 (machine y, z)
TIE_SLOT_DZ = 4.5


# ---------------------------------------------------------------------------------------------------------------------
# primitives with broken edges
# ---------------------------------------------------------------------------------------------------------------------
# Edge breaks on printed parts: every primitive is chamfered on ALL its edges, and primitives are joined by embedding
# (>= 0.5 mm overlap, never a coplanar contact). Chamfers that end up inside a neighbour vanish in the union; the
# exposed ones stay. Cutters carry their own 45 deg rim taper.
EB = 0.5


def rbox(x0, x1, y0, y1, z0, z1, c=EB):
    b = Solid.make_box(x1 - x0, y1 - y0, z1 - z0, Plane((x0, y0, z0)))
    c = min(c, (x1 - x0) / 3, (y1 - y0) / 3, (z1 - z0) / 3)
    return b.chamfer(c, None, b.edges()) if c > 0 else b


def rcyl(r, h, loc: Location, c=EB):
    """cylinder of radius r along local +z from loc, both end edges chamfered."""
    s = Solid.make_cylinder(r, h)
    c = min(c, h / 3, r / 3)
    if c > 0:
        s = s.chamfer(c, None, s.edges().filter_by(lambda e: e.geom_type.name == "CIRCLE"))
    return s.moved(loc)


def chamfer_all(shape, c=EB):
    try:
        return shape.chamfer(c, None, shape.edges())
    except Exception:
        return shape


def pocket(sketch_fn, plane: Plane, depth, rim=0.4, far_rim=False):
    """cutter for a pocket of the sketch's profile, ``depth`` below ``plane`` (along -normal), with a 45 deg rim
    (and one at the far end too when the pocket breaks through into another cavity)."""
    with BuildPart() as bp:
        if far_rim and rim > 0:
            with BuildSketch(plane.offset(-depth)):
                sketch_fn()
                offset(amount=rim, kind=Kind.INTERSECTION)
            extrude(amount=rim, taper=45)
        with BuildSketch(plane):
            sketch_fn()
        extrude(amount=-depth)
        with BuildSketch(plane):
            sketch_fn()
            if rim > 0:
                offset(amount=rim, kind=Kind.INTERSECTION)
        if rim > 0:
            extrude(amount=-rim, taper=45)
            with BuildSketch(plane):
                sketch_fn()
                offset(amount=rim, kind=Kind.INTERSECTION)
        extrude(amount=1.0)
    return bp.part


def drill(d, depth, loc: Location, entry=0.4):
    """hole cutter from loc's origin along local -z, entry chamfer (45 deg) at the origin."""
    with BuildPart() as bp:
        Cylinder(d / 2, depth, align=CT)
        if entry:
            Cone(d / 2, d / 2 + entry, entry, align=CT)
    return bp.part.moved(loc)


def teardrop(d, up=(0.0, 1.0)):
    """sketch fn: circle of diameter d with a 45 deg roof toward the sketch direction ``up`` (the print's +Z), so a
    hole that runs parallel to the bed prints without support."""
    r = d / 2
    ux, uy = up
    px, py = -uy, ux                                    # perpendicular
    k = r / math.sqrt(2)

    def fn():
        Circle(r)
        apex = (ux * r * math.sqrt(2), uy * r * math.sqrt(2))
        Polygon((px * k + ux * k, py * k + uy * k), apex, (-px * k + ux * k, -py * k + uy * k), (0.0, 0.0), align=None)
    return fn


def yz_plane(x, normal=+1):
    """sketch plane at machine x, local (u, v) = machine (Y, Z), normal +X (or -X: then u = -Y)."""
    if normal > 0:
        return Plane(origin=(x, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0))
    return Plane(origin=(x, 0, 0), x_dir=(0, -1, 0), z_dir=(-1, 0, 0))


def axis_x(x, y, z, toward=+1):
    """Location at (x, y, z) whose local +z points along +X (toward=+1) or -X (toward=-1)."""
    return Location((x, y, z), (0, 1, 0), 90 if toward > 0 else -90)


def x_hole(d, x, y, z, depth, into=-1, rim=0.4, far_rim=False, up=(0.0, 1.0)):
    """teardrop hole along X from the face at ``x`` going ``into`` (-1: toward -X); ``up`` = the print's +Z as machine
    (Y, Z) components (default machine +Z)."""
    pl = yz_plane(x, -into)
    loc_up = up if into < 0 else (-up[0], up[1])          # a -X normal plane has local u = -Y
    return pocket(teardrop(d, loc_up), pl, depth, rim, far_rim)


# =====================================================================================================================
# SYR-100  head body (FDM PETG), frame = head frame. Prints upright (plug on the bed), no supports:
# plug -> seat ring -> 45 deg flare -> rectangular mid body (barrel bore, flange table) -> rail spine with the rail
# guide bar, tool rail, back wall, gear plate 1 (window for the carriage, dowel seats, standoff inserts).
# =====================================================================================================================
def bar_section(grow=0.0):
    """sketch fn (Plane.XY): the guide bar's diamond section, grown by ``grow`` per face (the sleeve's hole)."""
    cx, cy = LAY.bar_c
    d = LAY.bar_half_diag + grow * math.sqrt(2)

    def fn():
        Polygon((cx - d, cy), (cx, cy - d), (cx + d, cy), (cx, cy + d), align=None)
    return fn


def guide_bar():
    """the guide bar: a plain 14 mm square bar, diamond-wise, standing on the table up to the spine top."""
    L = LAY
    with BuildPart() as bp:
        with BuildSketch(Plane.XY.offset(L.table_top_z - 1.0)):
            bar_section()()
        extrude(amount=L.spine_top_z - L.table_top_z + 1.0)
    return chamfer_all(bp.part, 0.5)                                        # long edges + a lead-in at the top


def plate1_sketch():
    """gear plate 1 outline (machine Y, Z): hull of the dowel / standoff bosses, the window frame and the table."""
    L = LAY
    tb1 = L.table_top_z
    (wy0, wy1), (wz0, wz1) = L.window_y, L.window_z
    peak = ((wy0 + wy1) / 2, wz1 + (wy1 - wy0) / 2)
    by0, by1 = L.body_y
    circles = [(L.C, 6.0), (L.B, 6.0), ((wy0, wz1), 4.0), (peak, 4.5)]
    for (y, z) in L.standoffs:
        circles.append(((y, z), by1 - y if y > 0 else 6.0))
    with BuildSketch(yz_plane(L.plate1_x[0])) as sk:
        with Locations(((by0 + by1) / 2, tb1 - 0.5)):
            Rectangle(by1 - by0, 1.0)
        for (c, r) in circles:
            with Locations(c):
                Circle(r)
        make_hull()
    return sk.sketch


def window_profile():
    L = LAY
    (wy0, wy1), (wz0, wz1) = L.window_y, L.window_z

    def fn():
        Polygon((wy0, wz0), (wy1, wz0), (wy1, wz1), ((wy0 + wy1) / 2, wz1 + (wy1 - wy0) / 2), (wy0, wz1), align=None)
    return fn


def plug():
    """the Ø41.9 x 34 plug, 1.0 lead-in chamfer at the bottom; its top runs 0.5 into the 45 deg flare (no ledge)."""
    s = Solid.make_cylinder(S.plug_od / 2, S.clamp_h + 0.5, Plane((0, 0, LAY.plug_bottom_z)))
    return s.chamfer(1.0, None, s.edges().filter_by(lambda e: e.geom_type.name == "CIRCLE").sort_by(Axis.Z)[:1])


def body():
    from . import tools as T
    L = LAY
    tb1 = L.table_top_z
    bx0, bx1 = L.body_x
    by0, by1 = L.body_y
    st = L.spine_top_z
    mid = rbox(bx0, bx1, by0, by1, 0.0, tb1)
    flare = Solid.make_cone(S.seat_od / 2, S.seat_od / 2 + tb1 + 1.0, tb1 + 1.0)   # 45 deg: no overhang
    mid = mid & flare
    p1 = extrude(plate1_sketch(), amount=L.plate1_x[1] - L.plate1_x[0])
    solids = [
        plug(),
        mid,
        guide_bar(),
        rbox(bx0, bx1 - 0.5, L.back_wall_y[0], L.back_wall_y[1], tb1 - 1.0, st),                 # back wall
        chamfer_all(p1),                                                                           # gear plate 1
        T.tool_rail(),                                                                             # tool rail
    ]
    with BuildPart() as bp:
        for sh in solids:
            add(sh)
        # ---------------- cuts ----------------
        with Locations((0, 0, L.plug_bottom_z - 1)):
            Cylinder(S.barrel_od / 2 + 0.2, tb1 - L.plug_bottom_z + 2, align=CB, mode=Mode.SUBTRACT)
        add(drill(S.barrel_od + 0.4, 0.01, Location((0, 0, L.plug_bottom_z)) * Location((0, 0, 0), (1, 0, 0), 180), 0.6),
            mode=Mode.SUBTRACT)                                              # bore entry chamfer at the plug bottom

        def flange_profile():
            Circle(S.flange_l / 2 + 0.2)
            Rectangle(S.flange_w + 0.4, S.flange_l + 3, mode=Mode.INTERSECT)
        add(pocket(flange_profile, Plane.XY.offset(tb1), 1.0), mode=Mode.SUBTRACT)   # flange locating pocket
        add(drill(S.barrel_od + 0.4, 0.01, Location((0, 0, tb1 - 1.0)), 0.5), mode=Mode.SUBTRACT)  # bore rim
        for (x, y) in CLAMP_SCREWS:
            add(drill(M2_INSERT[0], M2_INSERT[1] + 0.6, Location((x, y, tb1))), mode=Mode.SUBTRACT)
        # gear plate 1: carriage window, dowel seats (slip), standoff inserts
        x1 = L.plate1_x[1]
        t1 = x1 - L.plate1_x[0]
        add(pocket(window_profile(), yz_plane(x1), t1, far_rim=True), mode=Mode.SUBTRACT)
        for (y, z) in (L.B, L.C):
            add(x_hole(PIN_SLIP, x1, 0, 0, t1, rim=0.3, far_rim=True).moved(Location((0, y, z))), mode=Mode.SUBTRACT)
        for (y, z) in L.standoffs:
            add(x_hole(M3_INSERT[0], x1, 0, 0, t1, rim=0.3, far_rim=True).moved(Location((0, y, z))), mode=Mode.SUBTRACT)
        for c in T.tool_rail_cuts():
            add(c, mode=Mode.SUBTRACT)
    return bp.part


def body_holes():
    """(purpose, local point on the hole axis, note) for holes that hold no screw of their own."""
    from . import tools as T
    L = LAY
    x1 = L.plate1_x[1]
    out = [("shaft", (0.0, 0.0, -10.0), "syringe barrel bore (slip fit, 0.2 radial)")]
    out += [("pin", (x1 - 2.0, y, z), "dowel seat (slip, the dowel is pressed into gear plate 2)") for (y, z) in (L.B, L.C)]
    out += [("pin", (x, y, L.table_top_z - 2.0), "M2 heat-set insert seat") for (x, y) in CLAMP_SCREWS]
    out += [("pin", (x1 - 3.0, y, z), "M3 heat-set insert seat") for (y, z) in L.standoffs]
    out += [("access", (x1 - 3.0, (L.window_y[0] + L.window_y[1]) / 2, (L.window_z[0] + L.window_z[1]) / 2),
             "window: the carriage's bottom arm and top bridge run through it")]
    out += T.tool_rail_holes()
    return out


# =====================================================================================================================
# SYR-101  gear plate 2 (FDM PETG), head frame: plate x = plate2_x, three integral standoffs reaching gear plate 1.
# Prints on its outer face (standoffs up). Carries the servo (tabs on the outer face, 2x M2 + nuts), the dowels
# (press) and the cable tie.
# =====================================================================================================================
def plate2_sketch_fn():
    L = LAY
    sv = L.servo
    sc = L.servo_case_c

    def fn():
        for c, r in ((L.B, 5.5), (L.C, 5.5)):
            with Locations(c):
                Circle(r)
        for (y, z) in L.standoffs:
            with Locations((y, z)):
                Circle(min(5.5, L.body_y[1] - y))
        with Locations(sc):
            Rectangle(sv["W"] + 5.0, sv["tab_len"] + 3.0)
        with Locations(TIE):
            Rectangle(6.0, 2 * TIE_SLOT_DZ + 7.0)
        make_hull()
    return fn


def plate2():
    L = LAY
    sv = L.servo
    sc = L.servo_case_c
    xi, xo = L.plate2_x
    with BuildPart() as pl:
        with BuildSketch(yz_plane(xi)):
            plate2_sketch_fn()()
        extrude(amount=xo - xi)
    solids = [chamfer_all(pl.part)]
    for (y, z) in L.standoffs:
        solids.append(rcyl(L.standoff_d / 2, xi + 0.5 - L.plate1_x[1], axis_x(xi + 0.5, y, z, -1)))
    with BuildPart() as bp:
        for sh in solids:
            add(sh)
        for (y, z) in (L.B, L.C):                                    # dowels: press, through
            add(drill(S.pin_d, xo - xi + 1, axis_x(xo, y, z, +1), 0.3), mode=Mode.SUBTRACT)
            add(drill(S.pin_d, 0.01, axis_x(xi, y, z, -1), 0.3), mode=Mode.SUBTRACT)
        for (y, z) in L.standoffs:                                   # M3 through plate + standoff
            add(drill(M3_CLR, xo - L.plate1_x[1] + 1, axis_x(xo, y, z, +1), 0.4), mode=Mode.SUBTRACT)
            add(drill(M3_CLR, 0.01, axis_x(L.plate1_x[1], y, z, -1), 0.4), mode=Mode.SUBTRACT)

        def servo_cut():
            with Locations(sc):
                RectangleRounded(sv["W"] + 0.4, sv["L"] + 0.4, 0.5)
                SlotOverall(sv["tab_pitch"] + M2_CLR, M2_CLR, rotation=90)
        add(pocket(servo_cut, yz_plane(xo), xo - xi, far_rim=True), mode=Mode.SUBTRACT)

        def tie_slots():
            for dz in (-TIE_SLOT_DZ, TIE_SLOT_DZ):
                with Locations((TIE[0], TIE[1] + dz)):
                    Rectangle(3.2, 3.0)
        add(pocket(tie_slots, yz_plane(xo), xo - xi, rim=0.3, far_rim=True), mode=Mode.SUBTRACT)
    return bp.part


def plate2_holes():
    L = LAY
    xi, xo = L.plate2_x
    out = [("pin", (xi + 2.0, y, z), "dowel seat (press)") for (y, z) in (L.B, L.C)]
    out += [("access", (xi + 2.0, L.servo_case_c[0], L.servo_case_c[1]), "servo case passes through")]
    out += [("cable", (xi + 2.0, TIE[0], TIE[1] + dz), "cable-tie slot") for dz in (-TIE_SLOT_DZ, TIE_SLOT_DZ)]
    return out


# =====================================================================================================================
# SYR-102  flange clamp (FDM PETG), head frame. Clamps the finger flange into the table pocket (2x M2 into inserts).
# Prints upside down (relief pocket up).
# =====================================================================================================================
def flange_clamp():
    L = LAY
    z0, t = L.clampplate_bottom_z, L.clampplate_t
    x0 = L.bar_c[0] + L.bar_half_diag + 0.7
    with BuildPart() as bp:
        add(rbox(x0, L.plate1_x[0] - 0.5, L.body_y[0] + 2.5, L.back_wall_y[0] - 0.5, z0, z0 + t))
        add(drill(S.disc_d + 1.0, t + 1, Location((0, 0, z0 + t)), 0.5), mode=Mode.SUBTRACT)   # plunger + disc pass
        add(drill(S.disc_d + 1.0, 0.01, Location((0, 0, z0), (1, 0, 0), 180), 0.5), mode=Mode.SUBTRACT)

        def relief():
            Circle(S.flange_l / 2 + 0.25)
            Rectangle(S.flange_w + 0.5, S.flange_l + 3, mode=Mode.INTERSECT)
        add(pocket(relief, Plane.XY.offset(z0).rotated((180, 0, 0)), L.flange_top_z - z0), mode=Mode.SUBTRACT)
        add(drill(S.disc_d + 1.0, 0.01, Location((0, 0, L.flange_top_z), (1, 0, 0), 180), 0.5), mode=Mode.SUBTRACT)
        for (x, y) in CLAMP_SCREWS:
            add(drill(M2_CLR, t + 1, Location((x, y, z0 + t)), 0.3), mode=Mode.SUBTRACT)
            add(drill(M2_CLR, 0.01, Location((x, y, z0), (1, 0, 0), 180), 0.3), mode=Mode.SUBTRACT)
    return bp.part


# =====================================================================================================================
# SYR-103  plunger carriage with its m1 rack (FDM PETG), head frame at pos 0. One closed ring in the XZ plane:
# jaw (thumb disc) - plate on the MGN7C - top bridge - rack bar - bottom arm. Every member reaches the back plane
# y = y_back, and the part prints on it: the rack teeth stand up from the bar (flanks 25 deg off vertical), the slots
# open upward, horizontal holes are teardrops.
# =====================================================================================================================
def rack_tooth_centres():
    p = math.pi * S.m3
    z0, z1 = LAY.rack0_z0, LAY.rack0_z1
    k0 = math.ceil((z0 + p / 2 - LAY.zc) / p - 0.5)
    out = []
    k = k0
    while True:
        zc = LAY.zc + (k + 0.5) * p
        if zc + p / 2 > z1 - 0.3:
            break
        if zc - p / 2 >= z0 + 0.3:
            out.append(zc)
        k += 1
    return out


def slot_profile(r, length=40.0, flipped=False):
    """U-slot of radius r centred on the syringe axis, open toward machine -Y (one analytic profile: a circle + tangent
    box union would leave sliver faces). ``flipped``: the sketch plane is turned 180 deg about X (local y = -Y)."""
    def fn():
        with Locations((0, (length / 2) if flipped else (-length / 2))):
            SlotCenterToCenter(length, 2 * r, rotation=90)
    return fn


def edge_chamfer(a, b, c, axis, span):
    """45 deg chamfer cutter for a straight convex edge along ``axis`` ('x' | 'z'); ``a`` = the edge point in the
    other two coordinates, ``b`` = the two directions (+1/-1) pointing INTO the material."""
    (u0, v0), (du, dv) = a, b
    tri = [(u0, v0), (u0 + du * c, v0), (u0, v0 + dv * c)]
    with BuildPart() as bp:
        if axis == "x":            # (u, v) = (y, z)
            with BuildSketch(Plane.YZ.offset(span[0])):
                Polygon(*tri, align=None)
            extrude(amount=span[1] - span[0])
        else:                      # axis z: (u, v) = (x, y)
            with BuildSketch(Plane.XY.offset(span[0])):
                Polygon(*tri, align=None)
            extrude(amount=span[1] - span[0])
    return bp.part


UP_CARRIAGE = (0.0, -1.0)      # the carriage prints on its back face: its print +Z is machine -Y


def carriage():
    L = LAY
    jx0, jx1 = L.jaw_x
    jy0, yb = L.jaw_y
    zj0 = L.jaw0_bottom_z
    zd = L.disc0_z
    zj1 = zd + L.jaw_top_dz
    zr1 = L.rack0_z1
    gz0, gz1 = L.guide_z
    ay0 = L.arm_y[0]
    rx0, rx1 = L.x_rack
    px0, px1 = jx0, jx0 + PLATE_T                 # MGN7C mounting plate: its -X face is the block's mounting face
    with BuildPart() as bp:
        add(rbox(px0 + 0.5, jx1, jy0, yb, zj0, zj1))                                   # jaw (disc seat + rear block)
        cx, sh = L.bar_c[0], L.sleeve_half
        add(rbox(cx - sh, px1, -sh, yb, gz0, gz1))                                     # guide block (sleeve)
        add(rbox(px0 + 0.3, px1 - 0.3, ay0, yb, gz1 - 1.0, zr1 - 0.5))                 # plate up to the top bridge
        add(rbox(px0 + 0.6, rx1, ay0, yb, zr1 - 4.5, zr1))                             # top bridge
        add(rbox(jx1 - 0.5, rx0 + 0.5, ay0, yb, zj0, zj1))                             # bottom arm
        add(rbox(rx0, rx1, L.rack_root_y, yb, zj0, zr1))                               # rack bar
        # ---- cuts ----
        add(pocket(slot_profile(S.disc_d / 2 + 0.2), Plane.XY.offset(zj1), zj1 - zd), mode=Mode.SUBTRACT)   # disc seat
        add(pocket(slot_profile(S.rod_span / 2 + 0.5, flipped=True), Plane.XY.offset(zj0).rotated((180, 0, 0)),
                   zd - zj0, far_rim=True), mode=Mode.SUBTRACT)                        # rod slot (fork)
        rd, rr = S.disc_d / 2 + 0.2, S.rod_span / 2 + 0.5
        add(edge_chamfer((jy0, zd), (+1, -1), 0.3, "x", (-rd, rd)), mode=Mode.SUBTRACT)  # seat floor / front
        for sx in (-1, 1):                                                             # 1 mm lead-ins at the slot mouths
            add(edge_chamfer((sx * rd, jy0), (sx, +1), 1.0, "z", (zd - 0.5, zj1 + 0.5)), mode=Mode.SUBTRACT)
            add(edge_chamfer((sx * rr, jy0), (sx, +1), 1.0, "z", (zj0 - 0.5, zd + 0.5)), mode=Mode.SUBTRACT)
        for (x, y) in L.cap_screws:                                                    # cap inserts (M2), through
            add(pocket(teardrop(M2_INSERT[0], UP_CARRIAGE), Plane(origin=(x, y, zj1)), zj1 - zj0, rim=0.3,
                       far_rim=True), mode=Mode.SUBTRACT)
            # tool + screw access through the top bridge, straight above the cap screw
            add(pocket(teardrop(4.4, UP_CARRIAGE), Plane(origin=(x, y, zr1)), 4.5, rim=0.3, far_rim=True),
                mode=Mode.SUBTRACT)
        # the sleeve's hole: the bar's section + slide_clr per face, through
        with BuildSketch(Plane.XY.offset(gz0 - 1.0)):
            bar_section(S.slide_clr)()
        extrude(amount=gz1 - gz0 + 2.0, mode=Mode.SUBTRACT)
        # m1 PA 25 rack teeth on the -Y face of the bar (embedded 0.5 mm: no slivers; edges left sharp, see checks.yaml)
        p, m, ta = math.pi * S.m3, S.m3, math.tan(math.radians(S.pa))
        ry, ty = L.rack_root_y, L.rack_tip_y
        for zc in rack_tooth_centres():
            bl = 0.04                                                                   # backlash per flank
            hr, ht = p / 4 - bl + 1.25 * m * ta, p / 4 - bl - 1.0 * m * ta
            with BuildSketch(yz_plane(rx0)):
                Polygon((ry + 0.5, zc - hr - 0.5 * ta), (ry, zc - hr), (ty, zc - ht), (ty, zc + ht), (ry, zc + hr),
                        (ry + 0.5, zc + hr + 0.5 * ta), align=None)
            extrude(amount=rx1 - rx0)
    return bp.part


def disc_cap():
    """SYR-105 thumb-disc cap (FDM PETG), head frame at pos 0: pocket 0.9 deep lands on the disc top; 2x M2x8.
    Prints upside down (pocket up)."""
    L = LAY
    jx0, jx1 = L.jaw_x
    jy0, jy1 = L.jaw_y
    z0 = L.disc0_z + L.jaw_top_dz + L.cap_gap
    with BuildPart() as bp:
        add(rbox(jx0 + PLATE_T + 0.1, jx1, jy0, jy1, z0, z0 + L.cap_t))
        add(pocket(slot_profile(S.disc_d / 2 + 0.2, flipped=True), Plane.XY.offset(z0).rotated((180, 0, 0)),
                   L.disc0_z + S.disc_t - z0), mode=Mode.SUBTRACT)
        rd = S.disc_d / 2 + 0.2
        for sx in (-1, 1):
            add(edge_chamfer((sx * rd, jy0), (sx, +1), 1.0, "z", (z0 - 0.5, L.disc0_z + S.disc_t + 0.5)), mode=Mode.SUBTRACT)
        add(edge_chamfer((jy0, L.disc0_z + S.disc_t), (+1, +1), 0.3, "x", (-rd, rd)), mode=Mode.SUBTRACT)
        for (x, y) in L.cap_screws:
            add(drill(M2_CLR, L.cap_t + 1, Location((x, y, z0 + L.cap_t)), 0.3), mode=Mode.SUBTRACT)
            add(drill(M2_CLR, 0.01, Location((x, y, z0), (1, 0, 0), 180), 0.3), mode=Mode.SUBTRACT)
    return bp.part


def carriage_holes():
    L = LAY
    zr1 = L.rack0_z1
    out = [("pin", (x, y, L.disc0_z + L.jaw_top_dz - 2.0), "M2 heat-set insert seat (disc cap)") for (x, y) in L.cap_screws]
    out += [("access", (x, y, zr1 - 2.0), "hex key + screw access to the disc-cap screw below") for (x, y) in L.cap_screws]
    return out


# =====================================================================================================================
# gears (FDM PETG). PART frame: z = gear axis, z = 0 on the face that prints on the bed (the big gear's outer face);
# every gear is a pure 2.5D stack - the small gear stands straight on the big gear's face (no neck), the bore is one
# constant bearing-OD diameter (an MR63ZZ pressed flush into each end) and lightening holes go straight through.
# =====================================================================================================================
def _gear(m, z, w, phase_deg=0.0, z0=0.0):
    g = SpurGear(m, z, S.pa, w, align=CB, mode=Mode.PRIVATE)   # sharp root: FDM cannot hold a 0.1-0.2 mm fillet
    if phase_deg:
        g = g.rotate(Axis.Z, phase_deg)
    return g.moved(Location((0, 0, z0)))


def gear_a(phase_deg=0.0):
    """SYR-110 G1 17T m0.8 on the servo horn. Part z = machine +X; z = 0 is the face toward the gears (the M2 horn
    screw's head bears on it), the cross-horn pocket opens on the servo face (prints up)."""
    sv = LAY.servo
    w = LAY.g1_w
    depth = LAY.horn_pocket
    with BuildPart() as bp:
        add(_gear(S.m1, S.z1, w, phase_deg))
        with Locations((0, 0, w)):
            for a in (0, 90):
                Box(2 * sv["horn_trim_r"] + 0.4, sv["horn_arm_w"] + 0.3, depth, align=CT, rotation=(0, 0, a),
                    mode=Mode.SUBTRACT)
            Cylinder(sv["horn_hub_d"] / 2 + 0.2, depth, align=CT, mode=Mode.SUBTRACT)
        Cylinder(M2_CLR / 2, 3 * w, mode=Mode.SUBTRACT)
    return bp.part


def gear_b(phase2=0.0, phase3=0.0):
    """SYR-111 compound: G2 50T m0.8 (on the bed, z 0..4) + G3 12T m1.0 straight on its face; 2x MR63ZZ."""
    L = LAY
    l3 = L.x_g3[1] - L.x_g3[0]
    with BuildPart() as bp:
        add(_gear(S.m1, S.z2, S.w12, phase2))
        add(_gear(S.m2, S.z3, l3 + 0.5, phase3, S.w12 - 0.5))          # embedded 0.5 in G2
        for (x, y) in lightening(12.0):
            with Locations((x, y, 0)):
                Cylinder(3.0, 2 * S.w12 + 1, mode=Mode.SUBTRACT)
        Cylinder(S.brg_D / 2, 100, mode=Mode.SUBTRACT)                 # one bearing-OD bore, bearings flush each end
    return bp.part


def gear_c(phase4=0.0, phase5=0.0):
    """SYR-112 compound: G4 49T m1.0 (on the bed, z 0..5) + G5 12T m1.0 output pinion straight on its face;
    2x MR63ZZ."""
    L = LAY
    l5 = L.x_g5[1] - L.x_g5[0]
    with BuildPart() as bp:
        add(_gear(S.m2, S.z4, S.w34, phase4))
        add(_gear(S.m3, S.z5, l5 + 0.5, phase5, S.w34 - 0.5))
        for (x, y) in lightening(15.0, a0=math.pi / 6):
            with Locations((x, y, 0)):
                Cylinder(4.5, 2 * S.w34 + 1, mode=Mode.SUBTRACT)
        Cylinder(S.brg_D / 2, 100, mode=Mode.SUBTRACT)
    return bp.part


def gear_holes(total, lightening=()):
    out = [("shaft", (0.0, 0.0, S.brg_B / 2), "MR63ZZ seat (press, flush)"),
           ("shaft", (0.0, 0.0, total - S.brg_B / 2), "MR63ZZ seat (press, flush)"),
           ("shaft", (0.0, 0.0, total / 2), "bore between the bearings (dowel clearance)")]
    out += [("weight", (x, y, 1.0), "lightening hole") for (x, y) in lightening]
    return out


def lightening(r, n=6, a0=0.0):
    return [(r * math.cos(a0 + 2 * math.pi * k / n), r * math.sin(a0 + 2 * math.pi * k / n)) for k in range(n)]


# =====================================================================================================================
# SYR-104  luer-to-tip adapter, turned POM (white). frame: head frame (axis = Z).
# =====================================================================================================================
def adapter():
    L = LAY
    zt, zc = L.adapter_top_z, L.cone_top_z
    r0, r1 = S.adapter_cone_d0 / 2, S.adapter_cone_d1 / 2
    pts = [(0.6, zc - S.adapter_cone_l), (r1 - 0.25, zc - S.adapter_cone_l), (r1, zc - S.adapter_cone_l + 0.25),
           (r0, zc), (S.adapter_body_d / 2 - 0.4, zc), (S.adapter_body_d / 2, zc + 0.4),
           (S.adapter_body_d / 2, zc + 3.6), (S.adapter_body_d / 2 - 0.5, zc + 4.1), (S.adapter_body_d / 2 - 0.5, zc + 4.9),
           (S.adapter_body_d / 2, zc + 5.4), (S.adapter_body_d / 2, zt - 0.4), (S.adapter_body_d / 2 - 0.4, zt)]
    # female luer socket (6 %): mouth = male OD where the nozzle enters, then the nozzle profile
    d_mouth = S.luer_tip_d + S.luer_taper * S.luer_l + 0.02
    pts += [(d_mouth / 2 + 0.3, zt), (d_mouth / 2, zt - 0.3),
            ((S.luer_tip_d - S.luer_taper * (S.adapter_socket_depth - S.adapter_luer_seat)) / 2 + 0.01, zt - S.adapter_socket_depth),
            (0.6, zt - S.adapter_socket_depth)]
    with BuildPart() as bp:
        with BuildSketch(Plane.XZ):
            Polygon(*pts, align=None)
        revolve(axis=Axis.Z)
    return bp.part


# =====================================================================================================================
# SYR-13x  bearing spacers, brass tube 4 x 0.45: OD 4.0 (bears on the MR63 inner ring only), ID 3.1. frame: z axis.
# =====================================================================================================================
def spacer(length):
    with BuildPart() as bp:
        Cylinder(2.0, length, align=CB)
        Cylinder(1.55, length + 1, align=CB, mode=Mode.SUBTRACT)
    shp = bp.part
    try:
        shp = shp.chamfer(0.15, None, shp.edges().filter_by(lambda e: e.geom_type.name == "CIRCLE"))
    except Exception:
        pass
    return shp
