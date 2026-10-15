"""Tool rail + interchangeable tool modules: the head is a small tool changer.

The head body carries a **tool rail** along X on the front face of its mid body: a plain trapezoid with 45 deg flanks
(``RAIL_PROFILE``) and M3 heat-set inserts at five index positions (``RAIL_INDEX``). A tool module is any part whose
**saddle** (the same trapezoid groove) slides onto the rail from its end and is fixed at one index position by one
M3x10 through the saddle into the insert: the screw pulls the groove onto the two flanks (a V seat: Y and Z), and the
screw itself sets X. The syringe in the holder is tool 0; modules hang in front of the spindle holder (y < -35 below its top face).

Module frame (``station_loc(x)``): origin on the rail axis at the body's front face, local X = machine +X (along the
rail), local Y = machine +Z (up), local Z = machine -Y (outward). Modules print ON THEIR SIDE (local X down): their whole
side profile (local YZ) lies in the bed plane, and the few features that vary along X are 45 deg flanks or teardrops.

Modules here:
  TM-110  USB microscope (generic "1000x 8-LED" USB microscope, SYR-300): two 90 deg V-cradles, two cable ties
  TM-120  capacitive probe, rev B (probe.py): SG90-swung arm with the TTP223 + Ø2 electrode -> GRBL probe input
New modules (a second syringe, a camera, a light, a stirrer...) reuse ``module_plate`` and add a tool point to TOOLS.
"""
from __future__ import annotations

import math

from build123d import (Align, Axis, Box, BuildPart, BuildSketch, Circle, Cylinder, Location, Locations, Mode, Plane,
                       Polygon, Rectangle, Solid, Text, add, extrude)

from .spec import LAY, S

CB = (Align.CENTER, Align.CENTER, Align.MIN)
CT = (Align.CENTER, Align.CENTER, Align.MAX)

# ---------------------------------------------------------------------------------------------------------------------
# tool rail on the head body
# ---------------------------------------------------------------------------------------------------------------------
RAIL_Z = 27.0                                      # rail axis height above the holder's top face
RAIL_X = (-29.5, LAY.body_x[1] - 1.5)               # starts where the mid body's front face is full height
FACE_Y = LAY.body_y[0]                             # mid body front face
RAIL_INDEX = [-24.0, -16.0, -7.5, 3.0, 11.0]  # M3 insert positions (machine x)
SADDLE_CLR = 0.15
# (u = outward from the face, v = up): a plain trapezoid, 45 deg flanks, front face +-4 for an M3 insert. A module's
# saddle has the same trapezoid groove; its index screw pulls it onto the two flanks (a V seat: no lips, no capture)
RAIL_PROFILE = [(-1.0, -9.0), (0.0, -9.0), (5.0, -4.0), (5.0, 4.0), (0.0, 9.0), (-1.0, 9.0)]
RAIL_DEPTH = 5.0
M3_INSERT = (4.6, 5.7)
M2_INSERT = (3.6, 4.0)


def _rail_plane(x):
    """sketch plane at machine x with local (u, v) = (outward, up): u = -Y, v = +Z; normal +X."""
    return Plane(origin=(x, FACE_Y, RAIL_Z), x_dir=(0, -1, 0), z_dir=(1, 0, 0))


def tool_rail():
    """the rail, moulded on the mid body's front face (prismatic along X; prints upright with the body)."""
    from .parts import chamfer_all
    with BuildPart() as bp:
        with BuildSketch(_rail_plane(RAIL_X[0])):
            Polygon(*RAIL_PROFILE, align=None)
        extrude(amount=RAIL_X[1] - RAIL_X[0])
    shp = bp.part
    # break every edge except the lower 45 deg apex (a chamfer there would be a flat, unsupported facet)
    low = min(e.center().Z for e in shp.edges().filter_by(Axis.X))
    edges = [e for e in shp.edges() if not (e.geom_type.name == "LINE" and abs(e.center().Z - low) < 1e-3
                                              and abs(e.tangent_at(0.5).X) > 0.99)
             and not (FACE_Y - 0.01 <= e.center().Y <= FACE_Y + 1.01 and abs(e.tangent_at(0.5).X) > 0.99)]   # root
    try:
        return shp.chamfer(0.4, None, edges)
    except Exception:
        return shp


def tool_rail_cuts():
    """insert seats (teardrops, roof up) at the index positions, from the rail's front face toward +Y."""
    from .parts import pocket, teardrop
    out = []
    for x in RAIL_INDEX:
        pl = Plane(origin=(x, FACE_Y - RAIL_DEPTH, RAIL_Z), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
        out.append(pocket(teardrop(M3_INSERT[0], (0.0, 1.0)), pl, M3_INSERT[1] + 0.6, rim=0.3))
    return out


def tool_rail_holes():
    return [("pin", (x, FACE_Y - RAIL_DEPTH + 3.0, RAIL_Z), "M3 heat-set insert seat (tool rail index)")
            for x in RAIL_INDEX]


def station_loc(x) -> Location:
    """module frame at rail index x (see the module docstring)."""
    return Location(Plane(origin=(x, FACE_Y, RAIL_Z), x_dir=(1, 0, 0), z_dir=(0, -1, 0)))


# ---------------------------------------------------------------------------------------------------------------------
# generic module plate: saddle + arm, local frame
# ---------------------------------------------------------------------------------------------------------------------
SADDLE_Z = (0.6, RAIL_DEPTH + SADDLE_CLR + 5.0)      # 5 mm front wall for the M3 screw
SADDLE_Y = 9.0 + SADDLE_CLR + 2.5
ARM_Z = (10.0, 15.5)                               # >= 10 outward: clears the holder's front face (machine y -34)
PRINT_ORIENT_MODULE = ((0, 1, 0), -90.0)           # local X -> print -Z: the module lies on its side


def _saddle_cuts(x0, x1, up=(1.0, 0.0)):
    """cutters (module frame) for a saddle spanning local x0 .. x1: the rail groove (through along X, 45 deg rims at
    both ends and a bevel where the flanks meet the saddle's back face) and the M3 index screw hole at local x = 0
    (along local Z: a teardrop whose roof points to ``up`` = the print's +Z as local (X, Y) components)."""
    from .parts import pocket, teardrop
    c = SADDLE_CLR * math.sqrt(2)
    z0 = SADDLE_Z[0]

    def groove():
        v_at = lambda u: 9.0 + c - u                              # flank (+ clearance) half-height at depth u
        Polygon((-2.0, -11.0), (z0, -(v_at(z0) + 0.5)), (z0 + 0.5, -v_at(z0 + 0.5)), (RAIL_DEPTH + SADDLE_CLR,
                -v_at(RAIL_DEPTH + SADDLE_CLR)), (RAIL_DEPTH + SADDLE_CLR, v_at(RAIL_DEPTH + SADDLE_CLR)),
                (z0 + 0.5, v_at(z0 + 0.5)), (z0, v_at(z0) + 0.5), (-2.0, 11.0), align=None)
    g = pocket(groove, Plane(origin=(x1, 0, 0), x_dir=(0, 0, 1), z_dir=(1, 0, 0)), x1 - x0, rim=0.4, far_rim=True)
    h = pocket(teardrop(3.4, up), Plane(origin=(0, 0, SADDLE_Z[1]), x_dir=(1, 0, 0), z_dir=(0, 0, 1)),
               SADDLE_Z[1] - RAIL_DEPTH, rim=0.4, far_rim=True)
    return [g, h]


def saddle(x0, x1, up=(1.0, 0.0)):
    """the bare saddle block over local x0 .. x1 (module frame), groove + index screw hole cut."""
    from .parts import chamfer_all
    b = chamfer_all(Solid.make_box(x1 - x0, 2 * SADDLE_Y, SADDLE_Z[1] - SADDLE_Z[0], Plane((x0, -SADDLE_Y, SADDLE_Z[0]))), 0.5)
    with BuildPart() as bp:
        add(b)
        for c in _saddle_cuts(x0, x1, up):
            add(c, mode=Mode.SUBTRACT)
    return bp.part


def module_plate(width, y_bottom, profile_extra=()):
    """saddle + arm in the module frame, as one side profile (local YZ) extruded over local X +-width/2;
    ``profile_extra`` = more (y0, y1, z0, z1) rectangles of the side profile (tool blocks)."""
    from .parts import chamfer_all
    w = width / 2
    rects = [(-SADDLE_Y, SADDLE_Y, SADDLE_Z[0], SADDLE_Z[1]), (y_bottom, -SADDLE_Y + 0.5, ARM_Z[0], ARM_Z[1])]
    rects += list(profile_extra)
    solids = []
    for (y0, y1, z0, z1) in rects:
        b = Solid.make_box(2 * w, y1 - y0, z1 - z0, Plane((-w, y0, z0)))
        solids.append(chamfer_all(b, min(0.5, (z1 - z0) / 3, (y1 - y0) / 3)))
    with BuildPart() as bp:
        for s in solids:
            add(s)
        for c in _saddle_cuts(-w, w):
            add(c, mode=Mode.SUBTRACT)
    return bp.part


def module_holes():
    return [("access", (0.0, 0.0, 4.0), "saddle groove: slides on the tool rail")]


# =====================================================================================================================
# TM-110 USB microscope module
# =====================================================================================================================
MIC = dict(d=33.0, cap_d=34.0, cap_l=16.0, a_l=20.0, wheel_d=34.4, wheel_l=18.0, b_l=34.0, ring_l=2.0, tail_l=20.0,
           tail_d=15.0, relief_l=8.0, relief_d=8.0)      # ASSUMED (generic 1000x 8-LED USB microscope): measure yours
MIC["len"] = MIC["cap_l"] + MIC["a_l"] + MIC["wheel_l"] + MIC["b_l"] + MIC["ring_l"] + MIC["tail_l"]
MIC_X = -16.0                                     # rail index the module sits at
MIC_ZC = 38.0                                     # optical axis, local Z (machine y = FACE_Y - 38)
MIC_FRONT_Z = -58.0                               # lens front, machine z: 52 above the tip end, so with the tip at its
#                                                   deepest (CNC z -45) the lens is 62 above the bed: clear of the tip
#                                                   rack (57) and the beaker rims (51.2). Was -85 (collided).
CRADLES = [(MIC["cap_l"] + 6.0, MIC["cap_l"] + 16.0),                      # front black section (from the lens front)
           (MIC["cap_l"] + MIC["a_l"] + MIC["wheel_l"] + 1.5, MIC["cap_l"] + MIC["a_l"] + MIC["wheel_l"] + 8.5)]
#   (the rear cradle sits just behind the focus wheel, below the saddle since the lens moved up 27 mm; its tie runs
#   between the wheel and the "Snap" button)
MIC_W = 28.0


def _mic_local_y(d_from_front):
    return MIC_FRONT_Z + d_from_front - RAIL_Z


def microscope_module():
    """TM-110 (module frame). Two 90 deg V-cradles under the arm's front face; a cable tie per cradle runs through a
    tunnel in the arm and around the microscope."""
    from .parts import pocket
    r = MIC["d"] / 2
    apex = MIC_ZC - (r + 0.1) * math.sqrt(2)               # 90 deg V touching at +-45 deg (0.1 mm clearance)
    y_lo = _mic_local_y(CRADLES[0][0]) - 2.0
    extra = []
    for (a, b) in CRADLES:
        extra.append((_mic_local_y(a), _mic_local_y(b), ARM_Z[1] - 0.5, MIC_ZC - r * math.sqrt(0.5) + 0.8))
    plate = module_plate(MIC_W, y_lo, extra)
    with BuildPart() as bp:
        add(plate)
        for (a, b) in CRADLES:
            y0, y1 = _mic_local_y(a), _mic_local_y(b)
            def vee(apex=apex):
                Polygon((0, apex), (60.0, apex + 60.0), (-60.0, apex + 60.0), align=None)
            add(pocket(vee, Plane(origin=(0, y1, 0), x_dir=(-1, 0, 0), z_dir=(0, 1, 0)), y1 - y0, rim=0.4, far_rim=True),
                mode=Mode.SUBTRACT)                                        # V along Y, 45 deg rims at its ends
            # cable-tie tunnel through the arm, along X (vertical in print)
            def tunnel(yc=(y0 + y1) / 2):
                with Locations((yc, 12.7)):
                    Rectangle(3.2, 1.6)                                    # Z 11.9 .. 13.5: ~2 mm walls each side
            add(pocket(tunnel, Plane(origin=(MIC_W / 2, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0)), MIC_W, rim=0.3,
                       far_rim=True), mode=Mode.SUBTRACT)
    return bp.part


def microscope_module_holes():
    out = module_holes()
    for (a, b) in CRADLES:
        out.append(("cable", (0.0, (_mic_local_y(a) + _mic_local_y(b)) / 2, 12.0), "cable-tie tunnel"))
    return out


def mic_axis():
    """(machine x, y) of the microscope's optical axis."""
    return (MIC_X, FACE_Y - MIC_ZC)


# =====================================================================================================================
# TM-120 capacitive probe: rev B (servo-deployed arm) lives in probe.py; the TTP223 + electrode models stay here
# =====================================================================================================================
TTP = dict(l=15.0, w=11.0, t=1.6)                 # TTP223 mini module (red PCB, 3 wire pads)


# =====================================================================================================================
# purchased: microscope, TTP223, electrode rod  (each in its own frame)
# =====================================================================================================================
def _rev(pts):
    from .vendor import _rev as rv
    return rv(pts)


def mic_body():
    """generic 1000x 8-LED USB microscope housing (black ABS), frame: axis +Z, lens front at z = 0."""
    m = MIC
    r = m["d"] / 2
    z1 = m["cap_l"]
    z2 = z1 + m["a_l"]
    z3 = z2 + m["wheel_l"]
    z4 = z3 + m["b_l"]
    z5 = z4 + m["ring_l"]
    z6 = z5 + m["tail_l"]
    pts = [(0, z1), (r - 1.2, z1), (r, z1 + 0.5), (r, z2), (r - 0.6, z2), (r - 0.6, z3),
           (r, z3), (r, z4), (r - 0.4, z4), (r - 0.4, z5), (r, z5 + 0.4), (m["tail_d"] / 2, z6 - 2.0),
           (m["relief_d"] / 2 + 1.0, z6), (m["relief_d"] / 2, z6 + m["relief_l"]), (0, z6 + m["relief_l"])]
    body = _rev(pts)
    with BuildPart() as bp:
        add(body)
        with Locations(Location((0, -r + 0.3, z3 + 10.0), (1, 0, 0), 90)):           # "Snap" button (front, -Y)
            Cylinder(2.6, 1.6, align=CB)
    return bp.part


def mic_cap():
    """clear front cap with the 8-LED ring and lens window (polycarbonate)."""
    m = MIC
    r = m["cap_d"] / 2
    pts = [(0, 0.0), (r - 2.0, 0.0), (r, 2.0), (r, m["cap_l"] - 0.5), (r - 1.2, m["cap_l"]), (0, m["cap_l"])]
    shp = _rev(pts)
    with BuildPart() as bp:
        add(shp)
        with Locations((0, 0, 1.2)):
            Cylinder(r - 1.2, m["cap_l"], align=CB, mode=Mode.SUBTRACT)                 # hollow: LEDs + lens inside
    return bp.part


def mic_wheel():
    """ribbed focus wheel (anodised aluminium look)."""
    m = MIC
    r = m["wheel_d"] / 2
    z0 = m["cap_l"] + m["a_l"]
    with BuildPart() as bp:
        with Locations((0, 0, z0 + 0.2)):
            Cylinder(r - 0.5, m["wheel_l"] - 0.4, align=CB)
        for k in range(30):
            a = 2 * math.pi * k / 30
            with Locations(Location((math.cos(a) * (r - 0.5), math.sin(a) * (r - 0.5), z0 + 1.0), (0, 0, 1),
                                    math.degrees(a))):
                Box(1.0, 0.8, m["wheel_l"] - 2.0, align=CB)
        with Locations((0, 0, z0)):
            Cylinder(MIC["d"] / 2 - 0.6, m["wheel_l"] + 1, align=CB, mode=Mode.SUBTRACT)     # sits on the body's recess
    return bp.part


def mic_leds():
    """8 white SMD LEDs behind the cap (emissive in renders)."""
    with BuildPart() as bp:
        for k in range(8):
            a = 2 * math.pi * k / 8
            with Locations((10.5 * math.cos(a), 10.5 * math.sin(a), 3.0)):
                Box(2.0, 1.2, 0.8, align=CB)
    return bp.part


def mic_label():
    """white '1000X' print on the front black section, front (-Y) side."""
    r = MIC["d"] / 2
    with BuildPart() as bp:
        with BuildSketch(Plane(origin=(0, -r - 0.02, MIC["cap_l"] + 8.0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))):
            Text("1000X", 3.2, rotation=90)
        extrude(amount=0.04)
    return bp.part


def mic_cable_pts():
    """microscope USB lead from the strain relief up to the Z carriage (machine frame)."""
    x, y = mic_axis()
    top = MIC_FRONT_Z + MIC["len"] + MIC["relief_l"]
    return [(x, y, top + 0.3), (x, y, top + 6.0), (x, y, top + 12.0), (x - 2.0, y + 4.0, top + 30.0), (x - 6.0, y + 20.0, 120.0),
            (x - 8.0, y + 40.0, 165.0), (x - 8.0, y + 60.0, 185.0)]


def usb_cable():
    from build123d import BuildLine, Spline, sweep
    from build123d import Vector
    pts = [Vector(*p) for p in mic_cable_pts()]
    with BuildPart() as bp:
        with BuildLine() as ln:
            Spline(*pts)
        with BuildSketch(Plane(origin=pts[0], z_dir=ln.wire().tangent_at(0))):
            Circle(2.0)
        sweep(path=ln.wire())
    return bp.part


def ttp223_pcb():
    """TTP223 mini touch module, red FR4 15 x 11 x 1.6 with 3 wire pads, frame: board in XY (x along its 15 mm),
    z = 0 on its back face."""
    with BuildPart() as bp:
        Box(TTP["l"], TTP["w"], TTP["t"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((TTP["l"] / 2 - 1.27, 0, 0)):
            for k in (-1, 0, 1):
                with Locations((0, k * 2.54, 0)):
                    Cylinder(0.45, 10, mode=Mode.SUBTRACT)
    return bp.part


def ttp223_parts():
    """TTP223 IC + passives (one lump) and the indicator LED on the board, same frame."""
    with BuildPart() as chip:
        with Locations((-2.0, 0, TTP["t"])):
            Box(3.0, 3.0, 1.1, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((-5.5, 3.0, TTP["t"])):
            Box(1.6, 0.8, 0.6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return chip.part


def electrode_rod():
    """Ø2 stainless (A2) rod cut to probe.ROD_L, frame: tip at z = 0, axis +Z, tip edge broken 0.2."""
    from .probe import ROD_D, ROD_L
    s = Solid.make_cylinder(ROD_D / 2, ROD_L)
    return s.chamfer(0.2, None, s.edges().filter_by(lambda e: e.geom_type.name == "CIRCLE"))


def _probe_tools():
    from . import probe as PR
    return {"kind": "cap_probe", "module": "TM-120 rev B",
            "offset": (PR.EX, PR.EY, PR.TIP_Z - LAY.tip_end_z),
            "note": "electrode tip when DEPLOYED; TTP223 OUT -> GRBL probe pin A5, $6=1 (active high). "
                    "cnc.probe only while deployed; retract only with the electrode above the rims",
            "servo": {"ch": 1, "deployed_deg": PR.DEPLOYED_DEG, "retracted_deg": PR.RETRACTED_DEG,
                      "stop_deg": PR.STOP_DEG, "swing_deg": PR.SWING},
            "probe_floor_h": PROBE_FLOOR_H,
            "probe_floor_note": "lowest electrode-tip height above the bed when probing a beaker of the matrix: the "
                                "syringe tip then sits in the gap between 4 beakers, 2 mm over the holder top + labels"}


PROBE_FLOOR_H = 21.8 + 2.0        # deck/deck.py: holder top 21.2 + 0.6 raised labels


TOOLS = {   # tool points relative to the syringe tip end (deck_cal tool offsets: spindle = target - offset)
    "pipette": {"kind": "syringe", "module": "head", "offset": (0.0, 0.0, 0.0)},
    "microscope": {"kind": "usb_microscope", "module": "TM-110",
                   "offset": (MIC_X, FACE_Y - MIC_ZC, MIC_FRONT_Z - LAY.tip_end_z),
                   "note": "lens front; focus it on the target at this offset, then teach"},
}
from . import probe as _probe  # noqa: E402,F401  (registers TOOLS["probe"])
