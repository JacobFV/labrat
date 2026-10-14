"""Make parts. Each builder returns one solid in the PART frame stated in its docstring.

Processes: FDM (PETG, 0.2 mm layers, 0.4 mm nozzle), laser-cut 3 mm cast acrylic, turned POM / brass.
FDM rules used here: holes for heat-set inserts = insert OD, bearing pockets = bearing OD (press), dowel holes = pin
diameter (press), clearance holes M2 2.4 / M3 3.4, outer edges broken (filleted primitives, chamfered hole
entries), walls >= 1.2 mm.
"""
from __future__ import annotations

import math

from bd_warehouse.gear import SpurGear
from build123d import (Align, Axis, Box, BuildPart, BuildSketch, Circle, Cone, Cylinder, Kind, Location, Locations, Mode,
                       Plane, Polygon, Rectangle, RectangleRounded, SlotCenterToCenter, SlotOverall, Solid, Vector, add, chamfer, extrude,
                       fillet, make_hull, offset, revolve)

from .spec import LAY, S

CB = (Align.CENTER, Align.CENTER, Align.MIN)
CT = (Align.CENTER, Align.CENTER, Align.MAX)

M3_CLR, M2_CLR = 3.4, 2.4
M3_INSERT = (4.6, 5.7)     # microfab heatset_insert('M3-0.5'): OD over the knurl, length
M2_INSERT = (3.6, 4.0)
M3_TAP = 2.5

# cover standoff positions (machine x, z); clear of every gear / the rack (checked)
STANDOFFS = [(25.5, 52.0), (62.0, 84.0), (62.0, 130.0), (6.0, 136.0)]
STANDOFF_D = 8.0
CLAMP_SCREWS = [(14.0, 20.5), (-14.0, -20.5)]   # outside the carriage jaw's path (>= 1 mm running clearance)
TABLE_Y = 26.0
BODY_TOP_Z = 142.0
PLATE_T, CBORE = 2.5, 1.0     # carriage MGN7C plate: 1.5 mm under the M2x4 heads, 2.5 mm thread in the block
TOPBAR_Z = 121.5


# ---------------------------------------------------------------------------------------------------------------------
# primitives with broken edges
# ---------------------------------------------------------------------------------------------------------------------
# Edge breaks on printed parts: every primitive is chamfered on ALL its edges, and primitives are joined by embedding
# (>= 0.5 mm overlap, never a coplanar contact). Chamfers that end up inside a neighbour vanish in the union; the
# exposed ones stay. Cutters carry their own 45 deg rim taper. (A bulk chamfer after the booleans fails on most edges
# of a part like the body, and fillet-then-chamfer leaves sliver faces.)
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
        if far_rim:
            with BuildSketch(plane.offset(-depth)):
                sketch_fn()
                offset(amount=rim, kind=Kind.INTERSECTION)
            extrude(amount=rim, taper=45)
        with BuildSketch(plane):
            sketch_fn()
        extrude(amount=-depth)
        with BuildSketch(plane):
            sketch_fn()
            offset(amount=rim, kind=Kind.INTERSECTION)
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


def axis_y(x, y, z, toward=+1):
    """Location at (x, y, z) whose local +z points along +Y (toward=+1) or -Y (toward=-1)."""
    return Location((x, y, z), (1, 0, 0), -90 if toward > 0 else 90)


def axis_x(x, y, z, toward=+1):
    return Location((x, y, z), (0, 1, 0), 90 if toward > 0 else -90)


# =====================================================================================================================
# SYR-100  head body (FDM PETG), frame = head frame. Printed upright (plug down); tree supports under the lip and the
# table. Plug + lip + column/ribs + flange table + rail spine + back plate (gear pins, cover standoffs) + top bar.
# =====================================================================================================================
def body():
    L = LAY
    tb0, tb1 = L.table_top_z - L.table_t, L.table_top_z
    by0, by1 = L.back_plate_y
    rf = L.rail_face_x
    sp0 = rf - L.spine_t
    solids = [
        rcyl(S.plug_od / 2, -L.plug_bottom_z + 0.5, Location((0, 0, L.plug_bottom_z)), 1.0),   # into the lip
        rcyl(S.lip_od / 2, S.lip_t, Location((0, 0, 0))),
        rcyl(15.0, tb0 - S.lip_t + 2.0, Location((0, 0, S.lip_t - 1.0))),                      # lip -> table
        rbox(sp0 - 1.0, 30.0, -TABLE_Y, TABLE_Y, tb0, tb1),                                    # flange table
        rbox(sp0, rf, -9.0, 9.0, tb1 - 1.0, BODY_TOP_Z - 1.0),                                  # rail spine
        rbox(20.0, 66.0, by0, by1, tb0 + 0.5, BODY_TOP_Z),                                      # back plate
        rbox(sp0 - 0.5, 25.0, by0 + 0.5, by1 - 0.5, TOPBAR_Z, BODY_TOP_Z - 1.0),               # top bar
    ]
    for (x, z) in STANDOFFS:
        solids.append(rcyl(STANDOFF_D / 2, by0 + 1.0 - L.cover_back_y, axis_y(x, L.cover_back_y, z, +1)))
    for (x, y) in CLAMP_SCREWS:                                               # insert bosses under the table
        solids.append(rcyl(4.2, 3.5 + 1.0, Location((x, y, tb0 - 3.5))))
    for poly, pl in ((((-24.0, S.lip_t - 1.0), (24.0, S.lip_t - 1.0), (29.0, tb0 - 4.0), (29.0, tb0 + 1.0),
                       (-26.0, tb0 + 1.0), (-26.0, tb0 - 2.0)), Plane.XZ.offset(-2.0)),
                     ((((-24.0, S.lip_t - 1.0), (24.0, S.lip_t - 1.0), (TABLE_Y - 2.0, tb0 + 1.0),
                        (-TABLE_Y + 2.0, tb0 + 1.0))), Plane.YZ.offset(-2.0))):           # ribs: lip -> table
        with BuildPart() as rb:
            with BuildSketch(pl):
                Polygon(*poly, align=None)
            extrude(amount=4.0)
        solids.append(chamfer_all(rb.part))
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
            Rectangle(S.flange_l + 3, S.flange_w + 0.4, mode=Mode.INTERSECT)
        add(pocket(flange_profile, Plane.XY.offset(tb1), 1.0), mode=Mode.SUBTRACT)   # flange locating pocket
        add(drill(S.barrel_od + 0.4, 0.01, Location((0, 0, tb1 - 1.0)), 0.5), mode=Mode.SUBTRACT)  # bore rim
        for (x, y) in CLAMP_SCREWS:
            add(drill(M3_INSERT[0], 6.2, Location((x, y, tb1))), mode=Mode.SUBTRACT)            # 1.3 mm floor
        from .vendor import rail_hole_z
        for zr in rail_hole_z():
            add(drill(M2_INSERT[0], M2_INSERT[1] + 0.6, axis_x(rf, 0, L.rail_z0 + zr, +1), 0.3), mode=Mode.SUBTRACT)
        for (x, z) in (L.B, L.C):                                            # dowel pins: through the back plate
            add(drill(S.pin_d, by1 - by0 + 1, Location((x, by0, z), (1, 0, 0), 90), 0.3), mode=Mode.SUBTRACT)
            add(drill(S.pin_d, 0.01, Location((x, by1, z), (1, 0, 0), -90), 0.3), mode=Mode.SUBTRACT)
        for (x, z) in STANDOFFS:                                             # cover screw inserts
            add(drill(M3_INSERT[0], 6.5, Location((x, L.cover_back_y, z), (1, 0, 0), 90), 0.3), mode=Mode.SUBTRACT)
    return bp.part


def body_holes():
    """(purpose, local point on the hole axis, note) for holes that hold no screw of their own."""
    L = LAY
    from .vendor import rail_hole_z
    out = [("shaft", (0.0, 0.0, -10.0), "syringe barrel bore (slip fit, 0.2 radial)")]
    out += [("pin", (x, 8.0, z), "ISO 2338 dowel seat (press)") for (x, z) in (L.B, L.C)]
    out += [("pin", (x, y, L.table_top_z - 3.0), "M3 heat-set insert seat") for (x, y) in CLAMP_SCREWS]
    out += [("pin", (L.rail_face_x - 2.0, 0.0, L.rail_z0 + zr), "M2 heat-set insert seat") for zr in rail_hole_z()]
    out += [("pin", (x, L.cover_back_y + 3.0, z), "M3 heat-set insert seat") for (x, z) in STANDOFFS]
    return out


# =====================================================================================================================
# SYR-101  gearbox cover, laser-cut 3 mm cast acrylic. PART frame: plate in XY, z = 0 .. 3 (z = 0 is the BACK face,
# toward the gears); local (x, y) = machine (x, z). Placed with local +z = machine -Y at y = cover_back_y.
# =====================================================================================================================
def cover_points():
    """outline-driving points in machine (x, z) with their keep-out radii."""
    L = LAY
    sv = L.servo
    scx = L.A[0] - (sv["L"] / 2 - sv["shaft_from_end"])
    pts = [((x, z), STANDOFF_D / 2 + 3.0) for (x, z) in STANDOFFS]
    pts += [(L.B, S.z2 * S.m1 / 2 + S.m1 + 2.5), (L.C, S.z4 * S.m2 / 2 + S.m2 + 2.5)]
    for dx in (-sv["tab_len"] / 2, sv["tab_len"] / 2):
        pts.append(((scx + dx, L.A[1]), sv["W"] / 2 + 2.0))
    pts.append(((-3.0, TIE_Z), 10.5))                                 # cable-tie slots keep >= 3 mm webs
    return pts, scx


TIE_Z = 128.0


def cover():
    L = LAY
    sv = L.servo
    pts, scx = cover_points()
    with BuildPart() as bp:
        with BuildSketch():
            for (x, z), r in pts:
                with Locations((x, z)):
                    Circle(r)
            make_hull()
        extrude(amount=L.cover_t)
        # pins (slip H7 in acrylic: pressed only into the body)
        for (x, z) in (L.B, L.C):
            with Locations((x, z, 0)):
                Cylinder(S.pin_d / 2 + 0.01, 10, mode=Mode.SUBTRACT)
        for (x, z) in STANDOFFS:
            with Locations((x, z, 0)):
                Cylinder(M3_CLR / 2, 10, mode=Mode.SUBTRACT)
        # servo cut-out (case + 0.2 all round, laser corner radius 0.5); the tab screws pass through slots that run
        # into the cut-out (a hole 1.1 mm from the cut-out would leave a web far thinner than the 3 mm sheet)
        with BuildSketch():
            with Locations((scx, L.A[1])):
                RectangleRounded(sv["L"] + 0.4, sv["W"] + 0.4, 0.5)
                SlotOverall(sv["tab_pitch"] + M2_CLR, M2_CLR)
        extrude(amount=L.cover_t, mode=Mode.SUBTRACT)
        # cable-tie slot pair (servo lead)
        for dx in (-4.5, 4.5):
            with Locations((-3.0 + dx, TIE_Z, 0)):
                Box(3.0, 3.2, 10, mode=Mode.SUBTRACT)
    return bp.part


def cover_loc():
    return Location((0, LAY.cover_back_y, 0), (1, 0, 0), 90)


# =====================================================================================================================
# SYR-102  flange clamp (FDM PETG), head frame. Clamps the finger flange into the table pocket (2x M3 into inserts).
# =====================================================================================================================
def flange_clamp():
    L = LAY
    z0, t = L.clampplate_bottom_z, L.clampplate_t
    with BuildPart() as bp:
        add(rbox(-20.5, 19.5, -TABLE_Y, TABLE_Y, z0, z0 + t))          # x < 20: clear of the back plate
        add(drill(S.disc_d + 1.0, t + 1, Location((0, 0, z0 + t)), 0.5), mode=Mode.SUBTRACT)   # plunger + disc pass
        add(drill(S.disc_d + 1.0, 0.01, Location((0, 0, z0), (1, 0, 0), 180), 0.5), mode=Mode.SUBTRACT)

        def relief():
            Circle(S.flange_l / 2 + 0.25)
            Rectangle(S.flange_l + 3, S.flange_w + 0.5, mode=Mode.INTERSECT)
        add(pocket(relief, Plane.XY.offset(z0).rotated((180, 0, 0)), L.flange_top_z - z0), mode=Mode.SUBTRACT)
        add(drill(S.disc_d + 1.0, 0.01, Location((0, 0, L.flange_top_z), (1, 0, 0), 180), 0.5), mode=Mode.SUBTRACT)
        for (x, y) in CLAMP_SCREWS:
            add(drill(M3_CLR, t + 1, Location((x, y, z0 + t)), 0.4), mode=Mode.SUBTRACT)
            add(drill(M3_CLR, 0.01, Location((x, y, z0), (1, 0, 0), 180), 0.4), mode=Mode.SUBTRACT)
    return bp.part


# =====================================================================================================================
# SYR-103  plunger carriage + rack (FDM PETG, printed on its back face so the rack teeth are in the XY plane).
# Head frame at pos 0. Ring frame: jaw (holds the thumb disc) - left plate (on the MGN7C) - top bridge - rack.
# =====================================================================================================================
def rack_tooth_centres():
    p = math.pi * S.m3
    z0, z1 = LAY.rack0_z0, LAY.rack0_z0 + LAY.rack_len
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


def carriage():
    L = LAY
    jx0, jx1 = L.jaw_x
    jy0, jy1 = L.jaw_y
    zj0 = L.jaw0_bottom_z
    zd = L.disc0_z
    zj1 = zd + L.jaw_top_dz
    zr1 = L.rack0_z0 + L.rack_len
    mz = L.mgn0_z
    px0, px1 = jx0, jx0 + PLATE_T                 # MGN7C mounting plate: its -X face is the block's mounting face
    with BuildPart() as bp:
        add(rbox(px0 + 0.5, jx1, jy0, jy1, zj0, zj1))                                  # jaw (disc seat + rear block)
        add(rbox(px0, px1, -9.5, 9.5, zj0 + 0.5, mz + L.mgn_L / 2))                    # plate (>= 1.4 mm around the
        add(rbox(px0 + 0.3, px1 - 0.3, -4.0, 4.0, mz + L.mgn_L / 2 - 1.0, zr1 - 0.5))  # M2 counterbores), narrow above
        add(rbox(px0 + 0.6, L.rack_root_x - 0.6, -2.5, 2.5, zr1 - 4.5, zr1 - 0.5))   # top bridge
        add(rbox(L.rack_x0 - 0.5, L.rack_root_x, -S.w5 / 2, S.w5 / 2, zj0 + 0.5, zr1))  # rack bar
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
            add(drill(M2_INSERT[0], zj1 - zj0 + 1, Location((x, y, zj1)), 0.3), mode=Mode.SUBTRACT)
            add(drill(M2_INSERT[0], 0.01, Location((x, y, zj0), (1, 0, 0), 180), 0.3), mode=Mode.SUBTRACT)
        for sy in (-1, 1):                                                             # 4x M2 to the MGN7C
            for sz in (-1, 1):
                loc = axis_x(px1, sy * L.mgn_B / 2, mz + sz * L.mgn_C / 2, +1)
                add(drill(M2_CLR, 4.0, loc, 0.0), mode=Mode.SUBTRACT)
                add(drill(4.2, CBORE, loc, 0.3), mode=Mode.SUBTRACT)                  # counterbore
                add(drill(M2_CLR, 0.01, axis_x(px0, sy * L.mgn_B / 2, mz + sz * L.mgn_C / 2, -1), 0.2), mode=Mode.SUBTRACT)
        # m1 PA 25 rack teeth on the +X face of the bar (embedded 0.5 mm: no slivers; edges left sharp, see checks.yaml)
        p, m, ta = math.pi * S.m3, S.m3, math.tan(math.radians(S.pa))
        for zc in rack_tooth_centres():
            bl = 0.04                                                                   # backlash per flank
            hr, ht = p / 4 - bl + 1.25 * m * ta, p / 4 - bl - 1.0 * m * ta
            with BuildSketch(Plane.XZ.offset(-S.w5 / 2)):
                Polygon((L.rack_root_x - 0.5, zc - hr - 0.5 * ta), (L.rack_root_x, zc - hr), (L.rack_tip_x, zc - ht),
                        (L.rack_tip_x, zc + ht), (L.rack_root_x, zc + hr), (L.rack_root_x - 0.5, zc + hr + 0.5 * ta),
                        align=None)
            extrude(amount=S.w5)
    return bp.part


def disc_cap():
    """SYR-105 thumb-disc cap (FDM PETG), head frame at pos 0: pocket 0.9 deep lands on the disc top; 2x M2x8."""
    L = LAY
    jx0, jx1 = L.jaw_x
    jy0, jy1 = L.jaw_y
    z0 = L.disc0_z + L.jaw_top_dz + L.cap_gap
    with BuildPart() as bp:
        add(rbox(jx0 + PLATE_T + 0.1, L.rack_x0 - 0.6, jy0, jy1, z0, z0 + L.cap_t))  # between the plate and the rack
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
    return [("pin", (x, y, L.disc0_z + L.jaw_top_dz - 2.0), "M2 heat-set insert seat (disc cap)") for (x, y) in L.cap_screws]


# =====================================================================================================================
# gears (FDM PETG, printed flat). PART frame: z = gear axis (= machine +Y), z = 0 on the front face (toward the cover).
# =====================================================================================================================
def _gear(m, z, w, phase_deg=0.0, z0=0.0):
    g = SpurGear(m, z, S.pa, w, align=CB, mode=Mode.PRIVATE)   # sharp root: FDM cannot hold a 0.1-0.2 mm fillet
    if phase_deg:
        g = g.rotate(Axis.Z, phase_deg)
    return g.moved(Location((0, 0, z0)))


def _bearing_pockets(bp, length):
    for z, al in ((0.0, CB), (length, CT)):
        with Locations((0, 0, z)):
            Cylinder(S.brg_D / 2, S.brg_B, align=al, mode=Mode.SUBTRACT)
    Cylinder(S.brg_d / 2 + 1.2, 100, mode=Mode.SUBTRACT)                 # pin clearance between the bearings


def gear_a(phase_deg=0.0):
    """SYR-110 G1 17T m0.8 on the servo horn: cross-horn pocket on the front face, M2 horn screw through the centre."""
    sv = LAY.servo
    w = LAY.g1_w
    with BuildPart() as bp:
        add(_gear(S.m1, S.z1, w, phase_deg))
        depth = 1.8
        for a in (0, 90):
            Box(2 * sv["horn_trim_r"] + 0.4, sv["horn_arm_w"] + 0.3, depth, align=CB, rotation=(0, 0, a), mode=Mode.SUBTRACT)
        Cylinder(sv["horn_hub_d"] / 2 + 0.2, depth, align=CB, mode=Mode.SUBTRACT)
        Cylinder(LAY.servo["tower_d"] + 10.0, 0.3, align=CB, mode=Mode.SUBTRACT)  # back face 0.3 relieved: 1.1 mm to the tower
        Cylinder(1.2, 10, mode=Mode.SUBTRACT)
        with Locations((0, 0, w)):
            Cylinder(2.1, w - depth - 1.2, align=CT, mode=Mode.SUBTRACT)       # screw-head counterbore
    return bp.part


def gear_b(phase2=0.0, phase3=0.0):
    """SYR-111 compound: G2 50T m0.8 (front) + G3 12T m1.0 (rear), 2x MR63ZZ."""
    gap = LAY.y_g4[0] - LAY.y_g2[1]
    total = S.w12 + gap + S.w34
    with BuildPart() as bp:
        add(_gear(S.m1, S.z2, S.w12, phase2))
        with Locations((0, 0, S.w12 - 0.5)):
            Cylinder(4.6, gap + 1.0, align=CB)            # hub < G3 root and < G4 tip reach; embedded 0.5 in both gears
        add(_gear(S.m2, S.z3, S.w34, phase3, S.w12 + gap))
        for (x, y) in lightening(12.0):                                            # lightening holes in G2
            with Locations((x, y, 0)):
                Cylinder(3.0, 2 * S.w12 + 1, mode=Mode.SUBTRACT)
        _bearing_pockets(bp, total)
    return bp.part


def gear_c(phase4=0.0, phase5=0.0):
    """SYR-112 compound: G4 49T m1.0 (front) + hub + G5 12T m1.0 output pinion (rear, on the rack), 2x MR63ZZ."""
    hub = LAY.y_rack[0] - LAY.y_g4[1]
    total = S.w34 + hub + S.w5
    with BuildPart() as bp:
        add(_gear(S.m2, S.z4, S.w34, phase4))
        with Locations((0, 0, S.w34 - 0.5)):
            Cylinder(5.5, hub + 0.5, align=CB)            # embedded 0.5 in G4; ends on the G5 face (hub r > G5 root)
        add(_gear(S.m3, S.z5, S.w5, phase5, S.w34 + hub))
        for (x, y) in lightening(14.0, a0=math.pi / 6):
            with Locations((x, y, 0)):
                Cylinder(4.5, 2 * S.w34 + 1, mode=Mode.SUBTRACT)
        _bearing_pockets(bp, total)
    return bp.part


def gear_holes(total, lightening=()):
    out = [("shaft", (0.0, 0.0, S.brg_B / 2), "MR63ZZ pocket (press)"),
           ("shaft", (0.0, 0.0, total - S.brg_B / 2), "MR63ZZ pocket (press)"),
           ("shaft", (0.0, 0.0, total / 2), "dowel clearance between the bearings")]
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
# SYR-13x  bearing spacers, turned brass C360: OD 4.0 (bears on the MR63 inner ring only), ID 3.05. frame: z axis.
# =====================================================================================================================
def spacer(length):
    with BuildPart() as bp:
        Cylinder(2.0, length, align=CB)
        Cylinder(1.525, length + 1, align=CB, mode=Mode.SUBTRACT)
    shp = bp.part
    try:
        shp = shp.chamfer(0.15, None, shp.edges().filter_by(lambda e: e.geom_type.name == "CIRCLE"))
    except Exception:
        pass
    return shp
