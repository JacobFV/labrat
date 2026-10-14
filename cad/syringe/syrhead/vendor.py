"""Purchased parts, modelled as the product (catalogue dimensions, recognisable features), each in its own frame.

Every builder returns one solid/compound of ONE material; multi-material products are split into sub-parts
(e.g. servo case + label + horn) that the BOM rolls up under the product's line.
"""
from __future__ import annotations

import math

from build123d import (Align, Axis, Box, BuildLine, BuildPart, BuildSketch, Circle, Compound, Cylinder, Location,
                       Locations, Mode, Plane, Polyline, Pos, Rectangle, RegularPolygon, Rot, Solid, Spline, Text,
                       Vector, add, chamfer, extrude, fillet, loft, make_face, revolve, sweep)

from .spec import LAY, S, tip_bore_d

C = (Align.CENTER, Align.CENTER, Align.CENTER)
CB = (Align.CENTER, Align.CENTER, Align.MIN)   # base on z=0
CT = (Align.CENTER, Align.CENTER, Align.MAX)


def _rev(pts, axis=Axis.Z):
    """revolve a closed (r, z) polyline about Z."""
    with BuildPart() as bp:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Polyline(*pts, close=True)
            make_face()
        revolve(axis=axis)
    return bp.part


# =====================================================================================================================
# Feetech FS90R continuous-rotation micro servo (SG90 family case), frame: shaft axis, z = tab top plane, +z = output,
# +x = along the case from the shaft end toward the far end
# =====================================================================================================================
SV = LAY.servo


def servo_case():
    L, W = SV["L"], SV["W"]
    zb = -(SV["tab_from_bottom"] + SV["tab_t"])          # case bottom
    zt = SV["above_tab"]                                  # gearbox top
    cx = L / 2 - SV["shaft_from_end"]
    with BuildPart() as bp:
        # lower case + bottom cover (seam groove between them), upper case, gearbox cover
        with Locations((cx, 0, zb)):
            Box(L, W, 3.0, align=CB)
        with Locations((cx, 0, zb + 3.0)):
            Box(L - 0.3, W - 0.3, 0.3, align=CB)        # seam groove
        with Locations((cx, 0, zb + 3.3)):
            Box(L, W, -zb - 3.3 - SV["tab_t"], align=CB)
        # mounting tabs with holes and slots to the edge
        with Locations((cx, 0, -SV["tab_t"])):
            Box(SV["tab_len"], W, SV["tab_t"], align=CB)
        for sx in (-1, 1):
            hx = cx + sx * SV["tab_pitch"] / 2
            with Locations((hx, 0, -SV["tab_t"] / 2)):
                Cylinder(SV["tab_hole"] / 2, 4, mode=Mode.SUBTRACT)
            with Locations((hx + sx * 1.6, 0, -SV["tab_t"] / 2)):
                Box(3.2, 1.0, 4, mode=Mode.SUBTRACT)
        with Locations((cx, 0, 0)):
            Box(L, W, zt, align=CB)
        with Locations((cx, 0, zt - 0.3)):
            Box(L + 0.1, W + 0.1, 0.25, align=CB, mode=Mode.SUBTRACT)   # cover seam
        # output tower + second gear bump
        with Locations((0, 0, zt)):
            Cylinder(SV["tower_d"] / 2, SV["tower_h"], align=CB)
        with Locations((0, 0, zt + SV["tower_h"])):
            Cylinder(2.45, 3.0, align=CT, mode=Mode.SUBTRACT)    # bearing bore of the output shaft
        with Locations((6.2, 0, zt)):
            Cylinder(2.8, 1.0, align=CB)
        # case screws (two long screws from the bottom): recessed heads
        for x in (cx - L / 2 + 2.2, cx + L / 2 - 2.2):
            with Locations((x, 0, zb)):
                Cylinder(1.1, 0.6, align=CB, mode=Mode.SUBTRACT)
        # cable exit boss on the far end, low
        with Locations((cx + L / 2, 0, zb + 2.2)):
            Box(1.6, 4.2, 2.2, align=(Align.MIN, Align.CENTER, Align.CENTER))
    shp = bp.part
    try:
        shp = fillet(shp.edges().filter_by(Axis.Z), 0.6)
    except Exception:
        pass
    return shp


def servo_spline():
    """output shaft (brass-coloured POM spline in the FS90R; modelled with 21 splines)."""
    zt = SV["above_tab"] + SV["tower_h"]
    with BuildPart() as bp:
        with Locations((0, 0, zt - 3.0)):
            Cylinder(2.4, 3.0, align=CB)                          # output shaft journal inside the tower
        with Locations((0, 0, zt)):
            Cylinder(SV["spline_d"] / 2 - 0.25, SV["spline_h"], align=CB)
            for k in range(21):
                a = 2 * math.pi * k / 21
                with Locations((math.cos(a) * (SV["spline_d"] / 2 - 0.25), math.sin(a) * (SV["spline_d"] / 2 - 0.25), 0)):
                    Cylinder(0.28, SV["spline_h"], align=CB)
        with Locations((0, 0, zt + SV["spline_h"])):
            Cylinder(0.8, 4.5, align=CT, mode=Mode.SUBTRACT)   # M2 tap drill, 4.5 deep
    return bp.part


def servo_label():
    """printed vinyl label on the case side (+y face)."""
    L = SV["L"]
    cx = L / 2 - SV["shaft_from_end"]
    with BuildPart() as bp:
        with Locations((cx, SV["W"] / 2, -9.5)):
            Box(L - 3, 0.08, 9.0, align=(Align.CENTER, Align.MIN, Align.CENTER))
    return bp.part


def servo_label_text():
    cx = SV["L"] / 2 - SV["shaft_from_end"]
    with BuildPart() as bp:
        with BuildSketch(Plane(origin=(cx, SV["W"] / 2 + 0.08, -7.6), x_dir=(1, 0, 0), z_dir=(0, 1, 0))):
            Text("FEETECH", 2.4)
        extrude(amount=0.04)
        with BuildSketch(Plane(origin=(cx, SV["W"] / 2 + 0.08, -11.2), x_dir=(1, 0, 0), z_dir=(0, 1, 0))):
            Text("FS90R  360°", 2.0)
        extrude(amount=0.04)
    return bp.part


def servo_horn():
    """stock 4-arm (cross) horn, arms trimmed to r = horn_trim_r so it fits the G1 pocket."""
    zs = SV["spline_top_above_tab"]
    zt = zs + SV["horn_over_spline"]
    with BuildPart() as bp:
        with Locations((0, 0, zs - 1.7)):                     # hub skirt stops 0.3 above the output tower
            Cylinder(SV["horn_hub_d"] / 2, zt - (zs - 1.7), align=CB)
        with Locations((0, 0, zt - SV["horn_arm_t"])):
            for a in (0, 90):
                Box(2 * SV["horn_trim_r"], SV["horn_arm_w"], SV["horn_arm_t"], align=CB, rotation=(0, 0, a))
        with Locations((0, 0, zs)):
            Cylinder(SV["spline_d"] / 2 + 0.05, 3.0, align=CT, mode=Mode.SUBTRACT)  # spline socket
        with Locations((0, 0, zt)):
            Cylinder(1.1, 2.0, align=CT, mode=Mode.SUBTRACT)                       # screw hole
    return bp.part


def servo_cable_exit():
    """local point (servo frame) where the 3-wire lead leaves the case, and its direction."""
    zb = -(SV["tab_from_bottom"] + SV["tab_t"])
    return (SV["L"] - SV["shaft_from_end"] + 1.6, 0.0, zb + 2.2), (1, 0, 0)


# =====================================================================================================================
# 12 mL luer-slip syringe. frame: barrel axis, z = 0 on the flange underside, barrel downward. pos = plunger travel
# =====================================================================================================================
def syringe_barrel():
    r_o, r_i = S.barrel_od / 2, S.barrel_id / 2
    zb = -S.barrel_len
    wall_b = 1.2
    pts = [(r_i, 0.6), (r_i, zb + wall_b), (1.6, zb + wall_b - 0.6), (0.8, zb + wall_b - 0.6),
           (0.8, zb - S.hub_l - S.luer_l), (S.luer_tip_d / 2, zb - S.hub_l - S.luer_l),
           (S.luer_tip_d / 2 + S.luer_taper * S.luer_l / 2, zb - S.hub_l), (S.hub_d / 2, zb - S.hub_l),
           (S.hub_d / 2, zb - 0.4), (r_o - 1.2, zb), (r_o, zb + 0.8), (r_o, 0.0), (r_i + 0.4, 0.0), (r_i + 0.4, 0.6)]
    body = _rev(pts)
    with BuildPart() as bp:
        add(body)
        # finger flange: oval with flats, 2.2 thick, on z = 0 .. flange_t
        with BuildSketch(Plane.XY):
            Circle(S.flange_l / 2)
            Rectangle(S.flange_l + 2, S.flange_w, mode=Mode.INTERSECT)
            Circle(r_i, mode=Mode.SUBTRACT)
        extrude(amount=S.flange_t)
        with Locations((0, 0, S.flange_t)):
            Cylinder(r_i + 0.9, 0.6, align=CB)
            Cylinder(r_i, 0.6, align=CB, mode=Mode.SUBTRACT)
    shp = bp.part
    try:
        shp = fillet(shp.edges().filter_by_position(Axis.Z, -0.01, S.flange_t + 0.01).filter_by(lambda e: e.length > 20), 0.5)
    except Exception:
        pass
    return shp


def syringe_ink():
    """printed graduations (0.2 mL minor, 1 mL major) and numbers every 2 mL, on the front (-Y) of the barrel."""
    r = S.barrel_od / 2
    z0 = -S.barrel_len + 1.2                    # stopper face at 0 mL
    mm_per_ml = 1000.0 / LAY.ul_per_mm
    parts = []
    n = int(12 / 0.2) + 1
    for k in range(n):
        ml = k * 0.2
        z = z0 + ml * mm_per_ml
        major = abs(ml - round(ml)) < 1e-6
        half = 34 if major else (22 if abs(ml * 2 - round(ml * 2)) < 1e-6 else 14)
        t = 0.30 if major else 0.20
        ring = Solid.make_cylinder(r + 0.03, t, Plane((0, 0, z - t / 2))) - Solid.make_cylinder(r - 0.01, t, Plane((0, 0, z - t / 2)))
        # keep an arc centred on -Y
        wedge = Solid.make_box(2 * r + 2, 2 * r + 2, t + 0.2, Plane((-(r + 1), -(r + 1), z - t / 2 - 0.1)))
        cutter = Solid.make_cylinder(r + 2, t + 0.2, Plane((0, 0, z - t / 2 - 0.1)), angle=360 - 2 * half)
        cutter = cutter.rotate(Axis.Z, -90 + half)
        parts.append((ring & wedge) - cutter)
        if major and int(round(ml)) % 2 == 0 and ml > 0:
            with BuildPart() as tp:
                with BuildSketch(Plane(origin=(-r * math.sin(math.radians(40)), -r * math.cos(math.radians(40)), z),
                                       x_dir=(math.cos(math.radians(40)), -math.sin(math.radians(40)), 0),
                                       z_dir=(-math.sin(math.radians(40)), -math.cos(math.radians(40)), 0))):
                    Text(str(int(round(ml))), 2.0)
                extrude(amount=0.04)
            parts.append(tp.part)
    return Compound(children=parts)


def plunger_rod():
    """'+' section PP rod with thumb disc; frame = syringe frame at pos 0 (stopper bottomed)."""
    z_stop_top = -S.barrel_len + 1.2 + S.stopper_l
    z_disc = S.flange_t + S.disc_gap_pressed
    h = z_disc - (z_stop_top - 3.0)
    with BuildPart() as bp:
        with Locations((0, 0, z_stop_top)):
            Box(S.rod_span, S.rod_fin_t, h - 3.0, align=CB)
            Box(S.rod_fin_t, S.rod_span, h - 3.0, align=CB)
        with Locations((0, 0, z_stop_top - 3.0)):
            Cylinder(4.0, 3.0, align=CB)                          # spigot pressed into the stopper
        for z in (z_stop_top + 1.5, z_stop_top + 30.0):           # stiffening discs
            with Locations((0, 0, z)):
                Cylinder(S.rod_span / 2, 1.2, align=CB)
        with Locations((0, 0, z_disc)):
            Cylinder(S.disc_d / 2, S.disc_t, align=CB)
    shp = bp.part
    try:
        shp = fillet(shp.edges().filter_by(lambda e: e.length > 60), 0.3)
    except Exception:
        pass
    return shp


def stopper():
    r = S.barrel_id / 2 - 0.025
    z0 = -S.barrel_len + 1.2
    pts = [(0, z0 + 0.0), (r - 2.0, z0 + 0.0), (r, z0 + 1.2), (r, z0 + 2.6), (r - 0.5, z0 + 3.2), (r - 0.5, z0 + 5.6),
           (r, z0 + 6.2), (r, z0 + 7.6), (r - 0.6, z0 + S.stopper_l), (0, z0 + S.stopper_l)]
    shp = _rev(pts)
    with BuildPart() as bp:
        add(shp)
        with Locations((0, 0, z0 - 0.0)):
            Cylinder(2.2, 0.8, align=CB, mode=Mode.ADD)          # nose cone that fills the nozzle entry
        with Locations((0, 0, z0 + S.stopper_l)):
            Cylinder(4.05, 3.0, align=CT, mode=Mode.SUBTRACT)   # socket for the rod spigot
    return bp.part


# =====================================================================================================================
# universal 200 uL pipette tip (yellow PP). frame: z = 0 at the rim, tip end at z = -tip_len
# =====================================================================================================================
def pipette_tip():
    Ln = S.tip_len
    outer = [(S.tip_top_od / 2, 0.0), (S.tip_top_od / 2, -1.6), (S.tip_top_id / 2 + 0.55, -2.4),
             (S.tip_top_id / 2 + 0.45, -12.0), (1.45, -Ln + 9.0), (S.tip_end_od / 2, -Ln)]
    inner = [(0.25, -Ln), (tip_bore_d(S, 12.0) / 2, -12.0), (S.tip_top_id / 2, 0.0)]
    pts = outer + inner
    shp = _rev(pts)
    with BuildPart() as bp:
        add(shp)
        for k in range(4):                                   # stacking ribs on the collar
            a = k * 90
            with Locations(Location((0, 0, -6.0), (0, 0, 1), a)):
                with Locations((S.tip_top_id / 2 + 0.75, 0, 0)):
                    Box(0.6, 0.5, 7.0)
    return bp.part


# =====================================================================================================================
# HIWIN MGN7 miniature rail + MGN7C carriage (representative; verify at procurement)
# rail frame: z along the rail from 0, x = height (bottom face at x = 0), y = width
# =====================================================================================================================
def mgn7_rail(length: float = S.rail_len):
    W, H = LAY.rail_W, LAY.rail_H
    with BuildPart() as bp:
        with BuildSketch(Plane.XY):
            with Locations((H / 2, 0)):
                Rectangle(H, W)
            for sy in (-1, 1):                                # ball raceways (gothic arch) on both flanks
                with Locations((H - 1.6, sy * W / 2)):
                    Circle(0.65, mode=Mode.SUBTRACT)
        extrude(amount=length)
        n = int(round((length - 10) / 15)) + 1
        for k in range(n):
            z = 5.0 + 15.0 * k
            with Locations(Location((H, 0, z), (0, 1, 0), 90)):
                Cylinder(2.1, 2.3, align=CT, mode=Mode.SUBTRACT)   # counterbore from the top (x = H)
                Cylinder(1.2, H + 1, align=CT, mode=Mode.SUBTRACT)
    shp = bp.part
    try:
        shp = chamfer(shp.edges().filter_by(Axis.Z), 0.2)
    except Exception:
        pass
    return shp


def rail_hole_z(length: float = S.rail_len):
    n = int(round((length - 10) / 15)) + 1
    return [5.0 + 15.0 * k for k in range(n)]


def mgn7c_block():
    """steel carriage body (without end caps), frame = rail frame, centred at z = 0."""
    W, H, H1, L1 = LAY.mgn_W, LAY.mgn_H, LAY.mgn_H1, 13.5
    with BuildPart() as bp:
        with Locations((H1 + (H - H1) / 2, 0, 0)):
            Box(H - H1, W, L1)
        with Locations((LAY.rail_H / 2 - 0.01, 0, 0)):
            Box(LAY.rail_H + 0.4, LAY.rail_W + 0.3, L1 + 1, mode=Mode.SUBTRACT)
        for sy in (-1, 1):
            for sz in (-1, 1):
                with Locations(Location((H, sy * LAY.mgn_B / 2, sz * LAY.mgn_C / 2), (0, 1, 0), 90)):
                    Cylinder(0.8, 2.6, align=CT, mode=Mode.SUBTRACT)     # M2 tapped, tap-drill bore
    shp = bp.part
    try:
        shp = chamfer(shp.edges().filter_by(Axis.Z).group_by(Axis.X)[-1], 0.3)
    except Exception:
        pass
    return shp


def mgn7c_endcaps():
    W, H, H1, L1 = LAY.mgn_W, LAY.mgn_H, LAY.mgn_H1, 13.5
    parts = []
    for sz in (-1, 1):
        with BuildPart() as bp:
            with Locations((H1 + (H - H1 - 0.6) / 2, 0, sz * (L1 / 2 + 2.0))):
                Box(H - H1 - 0.6, W - 0.6, 4.0)
            with Locations((LAY.rail_H / 2 - 0.01, 0, sz * (L1 / 2 + 2.0))):
                Box(LAY.rail_H + 0.4, LAY.rail_W + 0.2, 5, mode=Mode.SUBTRACT)
            with Locations(Location((H - 2.2, 0, sz * (L1 / 2 + 4.0)), (0, 0, 1), 0)):
                Cylinder(0.6, 1.0, align=CT if sz > 0 else CB, mode=Mode.SUBTRACT)  # lube port
        parts.append(bp.part)
    return Compound(children=parts)


def mgn7c_seals():
    W, H, H1, L1 = LAY.mgn_W, LAY.mgn_H, LAY.mgn_H1, 13.5
    parts = []
    for sz in (-1, 1):
        with BuildPart() as bp:
            with Locations((H1 + (H - H1 - 0.8) / 2, 0, sz * (L1 / 2 + 4.5))):
                Box(H - H1 - 0.8, W - 1.0, 1.0)
            with Locations((LAY.rail_H / 2 - 0.01, 0, sz * (L1 / 2 + 4.5))):
                Box(LAY.rail_H + 0.2, LAY.rail_W + 0.05, 2, mode=Mode.SUBTRACT)
        parts.append(bp.part)
    return Compound(children=parts)


# =====================================================================================================================
# small hardware
# =====================================================================================================================
def mr63zz():
    """MR63ZZ miniature bearing 3x6x2.5, z = 0 .. B, shields recessed."""
    d, D, B = S.brg_d, S.brg_D, S.brg_B
    pts = [(d / 2, 0.0), (d / 2 + 0.45, 0.0), (d / 2 + 0.45, 0.15), (D / 2 - 0.45, 0.15), (D / 2 - 0.45, 0.0),
           (D / 2, 0.0), (D / 2, B), (D / 2 - 0.45, B), (D / 2 - 0.45, B - 0.15), (d / 2 + 0.45, B - 0.15),
           (d / 2 + 0.45, B), (d / 2, B)]
    shp = _rev(pts)
    with BuildPart() as bp:
        add(shp)
        for z in (0.15, B - 0.15):                         # shield / ring split lines
            for r in (d / 2 + 0.5, D / 2 - 0.5):
                with Locations((0, 0, z)):
                    Cylinder(r + 0.04, 0.06, align=CB if z < 1 else CT, mode=Mode.SUBTRACT)
                    Cylinder(r - 0.04, 0.06, align=CB if z < 1 else CT, mode=Mode.ADD)
    return bp.part


def dowel_pin(d: float = S.pin_d, length: float = S.pin_len):
    """ISO 2338 m6 dowel: chamfer one end, radius-crowned other end. z = 0 .. length."""
    pts = [(0, 0), (d / 2 - 0.3, 0), (d / 2, 0.3), (d / 2, length - 0.5), (d / 2 - 0.2, length - 0.15),
           (d / 2 - 0.45, length), (0, length)]
    return _rev(pts)


INSERTS = {"M2-0.4": (3.6, 4.0, 1.6), "M3-0.5": (4.6, 5.7, 2.5)}   # OD over the knurl, length, tap bore


def heatset(thread: str = "M2-0.4"):
    """ruthex-style brass heat-set insert (frame: z = 0 at the top face, into -z). Knurl bands are added BEFORE the
    bore is drilled, so the thread is continuous over the full length."""
    od, Ln, tap = INSERTS[thread]
    with BuildPart() as bp:
        Cylinder(od / 2 - 0.2, Ln, align=CT)
        for z in (-0.22 * Ln, -0.55 * Ln, -0.85 * Ln):
            with Locations((0, 0, z)):
                Cylinder(od / 2, 0.7, align=C)          # knurl bands
        Cylinder(tap / 2, Ln + 1, align=CT, mode=Mode.SUBTRACT)
    return bp.part


def dupont_3pin():
    """2.54 mm 3-way female crimp housing (servo lead end), frame: z = 0 at the cable entry, mating face at +z."""
    with BuildPart() as bp:
        with Locations((0, 0, 0)):
            Box(7.62, 2.54, 14.0, align=CB)
        for k in (-1, 0, 1):
            with Locations((k * 2.54, 0, 14.0)):
                Box(1.0, 1.0, 3.0, align=CT, mode=Mode.SUBTRACT)
            with Locations((k * 2.54, 1.27, 9.0)):
                Box(1.2, 0.6, 3.0, mode=Mode.SUBTRACT)      # latch windows
        with Locations((-3.81 + 0.4, 0, 14.0)):
            Box(0.8, 0.8, 0.6, align=CT, mode=Mode.SUBTRACT)  # pin-1 marker
    return bp.part


def ribbon_offsets(path_pts, n0=(0, 0, 1)):
    """per-point unit normals along a polyline by parallel transport (rotation-minimising frame), starting at n0."""
    P = [Vector(*p) for p in path_pts]
    T = [(P[min(i + 1, len(P) - 1)] - P[max(i - 1, 0)]).normalized() for i in range(len(P))]
    n = Vector(*n0)
    n = (n - T[0] * n.dot(T[0])).normalized()
    out = [n]
    for i in range(1, len(P)):
        n = (n - T[i] * n.dot(T[i])).normalized()
        out.append(n)
    return out


def wire(path_pts, d: float = 1.0, k: float = 0.0, n0=(0, 0, 1)):
    """conductor k (-1, 0, +1) of a flat 3-way lead (pitch d + 0.02) along a spline through ``path_pts``."""
    N = ribbon_offsets(path_pts, n0)
    pts = [Vector(*p) + N[i] * (k * (d + 0.02)) for i, p in enumerate(path_pts)]
    with BuildPart() as bp:
        with BuildLine() as ln:
            Spline(*pts)
        path = ln.wire()
        start_dir = path.tangent_at(0)
        with BuildSketch(Plane(origin=pts[0], z_dir=start_dir)):
            Circle(d / 2)
        sweep(path=path)
    return bp.part


def cable_tie(center, axis_dir=(0, 1, 0), inner=(3.6, 1.6), w=2.5, t=1.0):
    """nylon cable tie loop around a bundle, loop plane normal = axis_dir (bundle direction is the tie's axis)."""
    a, b = inner
    with BuildPart() as bp:
        with BuildSketch(Plane(origin=center, z_dir=axis_dir)):
            Rectangle(a + 2 * t, b + 2 * t)
            Rectangle(a, b, mode=Mode.SUBTRACT)
        extrude(amount=w / 2, both=True)
        with BuildSketch(Plane(origin=center, z_dir=axis_dir)):           # locking head on the front run
            with Locations((0, -(b / 2 + t + 1.4))):
                Rectangle(4.0, 2.8)
        extrude(amount=2.0, both=True)
    return bp.part
