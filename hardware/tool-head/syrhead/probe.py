"""TM-120: servo-deployed capacitive liquid-level probe on the tool rail.

A positional SG90 (9 g) servo swings an ARM that carries the Ø2 stainless electrode and the TTP223 touch module
between DEPLOYED (arm hanging straight down, electrode tip level with the syringe tip end) and RETRACTED (arm swung
150 deg forward and up). Why a swing and not a slide: the servo output IS the pivot (no linkage, no guide to bind), a
150 deg turn lifts the electrode by 70 + 61 mm with a 9 g servo, and with the arm vertical at deployed the tip HEIGHT
is first-order insensitive to the arm angle (R (1 - cos a): 0.1 mm at 3 deg), so the height comes from the arm and the
pivot, not from the servo. A slide would need a rack or a crank plus a guide and gives the same repeatability only if
the guide is stiff.

Deployed repeatability: the arm's back face lands on a HARD STOP (the stop block of the bracket, 8.5 .. 13.5 mm below
the pivot) and the servo is commanded 3 deg past the contact (``DEPLOYED_DEG``), so it presses the arm lightly onto the
stop; the stop, not the servo, sets the angle. Gravity keeps the arm on the same side of the servo's shaft play.

The TTP223 rides ON the arm next to the electrode, so the sensing lead (pad -> rod, 5 mm) never moves relative to the
electrode: its stray capacitance is constant and the TTP223's baseline does not shift when the arm swings. The only
lead that crosses the joint is the 3-core supply / output cable (GND, 5 V, OUT), as a slack silicone service loop in
front of the hub; it is not capacitance-sensitive.

Parts (all modelled in the HEAD frame, at rail index x = 11):
  TM-120  bracket: rail saddle + servo plate (SG90 by its tabs, 2x M2x8 + nuts) + the deployed hard stop
  TM-122        probe arm: horn pocket + M2x16 to the spline, TTP223 pocket, electrode bore with an M2 pinch clamp
  TM-121  TTP223 lid on the arm (1x M2x6 into an insert)

Kinematics: the arm (motion group ``probe``) turns about machine +X through PIVOT by -phi (phi = 0 deployed .. SWING
retracted): the electrode end swings forward (-Y) and up. Servo command deg = STOP_DEG + phi (STOP_DEG = contact).
Print: the bracket on its +X face (servo plate down), the arm on its -X face (screw-head side down), the lid flat.
"""
from __future__ import annotations

import math

from build123d import (Axis, BuildPart, BuildSketch, Circle, Location, Locations, Mode, Plane, Polygon, Rectangle,
                       RectangleRounded, SlotOverall, Solid, add, extrude)

from . import tools as TL
from .spec import LAY

# ---------------------------------------------------------------------------------------------------------------------
# layout (head frame, mm)
# ---------------------------------------------------------------------------------------------------------------------
PROBE_X = 11.0                    # rail index of the bracket's M3 screw
EX, EY = 9.5, -47.5               # electrode axis (machine x, y): see the README "probe position" note
PIVOT = (EY, -40.0)               # swing axis (y, z), along machine X, on the servo output
TIP_Z = LAY.tip_end_z             # deployed electrode tip: level with the syringe tip end (-110)
R_TIP = PIVOT[1] - TIP_Z          # 70
SWING = 150.0                     # deployed -> retracted (deg)
STOP_DEG = 18.0                   # servo command at which the arm meets the stop (calibrate)
DEPLOYED_DEG = STOP_DEG - 3.0     # 3 deg past the stop: the servo presses the arm lightly onto it
RETRACTED_DEG = STOP_DEG + SWING  # 168

ARM_X = (4.0, 15.0)               # arm slab (its -X face prints on the bed; +X face toward the servo)
CLAMP_X1 = 18.5                   # the electrode clamp is 3.5 mm thicker toward +X (pinch insert + its roof)
ARM_Y = (EY - 7.5, EY + 7.5)      # (-55, -40): the back face (-40) lands on the stop
HUB_R = 7.5
CLAMP_Z = (-80.0, -70.0)
SV = LAY.servo
XT = ARM_X[1] + SV["spline_top_above_tab"] + SV["horn_over_spline"]   # servo tab plane (23.9): horn top = arm face
PLATE_X = (XT - 3.5, XT)          # bracket servo plate (3.5 like gear plate 2)
HORN_POCKET = SV["spline_top_above_tab"] + SV["horn_over_spline"] - (1.2 + SV["above_tab"] + SV["tower_h"])  # 1.4
SERVO_C = (EY, PIVOT[1] + SV["L"] / 2 - SV["shaft_from_end"])           # case centre (y, z): the case stands up
TAB_Z = [SERVO_C[1] - SV["tab_pitch"] / 2, SERVO_C[1] + SV["tab_pitch"] / 2]
STATIC_BOTTOM = -53.5             # lowest point of everything that does not swing (bracket, servo, stop)
STOP = dict(y=(ARM_Y[1], ARM_Y[1] + 2.5), z=(STATIC_BOTTOM, STATIC_BOTTOM + 5.0))   # behind the arm, below the pivot
PLATE_Y = (EY - 10.0, EY + 10.0)  # (-57.5, -37.5)

TTP = TL.TTP                      # 15 x 11 x 1.6 board
TTP_POCKET = dict(x0=ARM_X[1] - 3.0, y=(EY - 5.8, EY + 5.8), z=(PIVOT[1] - HUB_R - 15.6, PIVOT[1] - HUB_R))
LID = dict(x=(ARM_X[1] + 0.05, ARM_X[1] + 2.05), y=(EY - 6.5, EY + 6.5), z=(CLAMP_Z[1] + 0.4, PIVOT[1] - HUB_R - 3.0))
LID_SCREW = (EY - 4.0, -66.3)     # (y, z): M2x6 into an insert below the pocket, beside the electrode bore
ROD_D, ROD_L = 2.0, 48.0          # Ø2 x 48 stainless electrode: tip at -110, top end at -62 (under the pocket)
BORE_TOP = TIP_Z + ROD_L + 0.5
LEAD_HOLE_Z = TIP_Z + ROD_L - 1.0
PINCH = dict(x=14.0, z=(CLAMP_Z[0] + CLAMP_Z[1]) / 2, cb_depth=3.0)       # M2x12 across the slit, insert from the back

M2_CLR = 2.4
M2_INSERT = TL.M2_INSERT
M3_CLR = 3.4


def deg_to_phi(deg):
    return max(0.0, deg - STOP_DEG)


def arm_motion(phi) -> Location:
    """motion of the arm group (head frame) at swing angle phi (deg, 0 = deployed)."""
    o = (0.0, PIVOT[0], PIVOT[1])
    return Location(o) * Location((0, 0, 0), (1, 0, 0), -phi) * Location((-o[0], -o[1], -o[2]))


def _yz(x, normal=+1):
    from .parts import yz_plane
    return yz_plane(x, normal)


# =====================================================================================================================
# TM-120 bracket
# =====================================================================================================================
def bracket():
    """rail saddle (x 4 .. 23.9) + servo plate and web (x 20.4 .. 23.9) + the deployed stop (x 4 .. 23.9). Head frame.
    Prints on its +X face: every feature is a prism from that face, holes along X are vertical, the index screw hole
    along Y is a teardrop (roof toward -X = print up)."""
    from .parts import chamfer_all, drill, pocket, rbox, teardrop
    loc = TL.station_loc(PROBE_X)
    x0, x1 = ARM_X[0] - PROBE_X, PLATE_X[1] - PROBE_X                  # saddle in the module frame (local x)
    sad = TL.saddle(x0, x1, up=(-1.0, 0.0)).moved(loc)
    xi, xo = PLATE_X
    py0, py1 = PLATE_Y
    solids = [
        sad,
        rbox(xi, xo, py0, py1, STATIC_BOTTOM, 0.0),                            # servo plate
        rbox(xi, xo, -40.5, -30.0, -12.0, TL.RAIL_Z - 5.0),                    # web up into the saddle
        rbox(ARM_X[0], xo, STOP["y"][0], STOP["y"][1] + 0.5, STOP["z"][0], STOP["z"][1]),   # hard stop (+0.5 into
    ]                                                                          # the plate's back wall)
    with BuildPart() as bp:
        for s in solids:
            add(s)
        sc = (EY, (TAB_Z[0] + TAB_Z[1]) / 2)

        def servo_cut():
            with Locations(sc):
                RectangleRounded(SV["W"] + 0.4, SV["L"] + 0.4, 0.5)
                SlotOverall(SV["tab_pitch"] + M2_CLR, M2_CLR, rotation=90)
        add(pocket(servo_cut, _yz(xo), xo - xi, far_rim=True), mode=Mode.SUBTRACT)

        def tie_slots():
            for dy in (-TIE_DY, TIE_DY):
                with Locations((TIE[0] + dy, TIE[1])):
                    Rectangle(3.0, 3.2)
        add(pocket(tie_slots, _yz(xo), xo - xi, rim=0.3, far_rim=True), mode=Mode.SUBTRACT)
    return bp.part


TIE = (EY - 2.0, -9.0)            # cable tie on the plate's +X face (y, z): probe cable + servo lead
TIE_DY = 4.5


def bracket_holes():
    xi, xo = PLATE_X
    return [("access", (xi + 1.0, EY, (TAB_Z[0] + TAB_Z[1]) / 2), "SG90 case passes through"),
            ("access", (PROBE_X, TL.FACE_Y - 2.0, TL.RAIL_Z), "saddle groove: slides on the tool rail"),
            ("cable", (xi + 1.0, TIE[0] - TIE_DY, TIE[1]), "cable-tie slot"),
            ("cable", (xi + 1.0, TIE[0] + TIE_DY, TIE[1]), "cable-tie slot")]


# =====================================================================================================================
# TM-122 probe arm (deployed pose, head frame)
# =====================================================================================================================
def arm():
    from .parts import chamfer_all, drill, pocket, rbox, teardrop
    ax0, ax1 = ARM_X
    y0, y1 = ARM_Y
    with BuildSketch(_yz(ax0)) as sk:
        with Locations(PIVOT):
            Circle(HUB_R)
        with Locations(((y0 + y1) / 2, (CLAMP_Z[0] + PIVOT[1]) / 2)):
            Rectangle(y1 - y0, PIVOT[1] - CLAMP_Z[0])
    slab = chamfer_all(extrude(sk.sketch, amount=ax1 - ax0), 0.5)
    clamp = rbox(ax1 - 0.5, CLAMP_X1, y0, y1, CLAMP_Z[0], CLAMP_Z[1])
    sv = SV
    with BuildPart() as bp:
        add(slab)
        add(clamp)
        # ---- servo: trimmed cross horn in a pocket on the +X face (as G1), M2x16 from the -X face into the spline
        def horn():
            with Locations(PIVOT):
                for a in (0, 90):
                    Rectangle(2 * sv["horn_trim_r"] + 0.4, sv["horn_arm_w"] + 0.3, rotation=a)
                Circle(sv["horn_hub_d"] / 2 + 0.2)
        add(pocket(horn, _yz(ax1), HORN_POCKET, rim=0.3), mode=Mode.SUBTRACT)
        add(drill(M2_CLR, ax1 - ax0 + 1, Location((ax1, PIVOT[0], PIVOT[1]), (0, 1, 0), 90), 0.0), mode=Mode.SUBTRACT)
        add(drill(M2_CLR, 0.01, Location((ax0, PIVOT[0], PIVOT[1]), (0, 1, 0), -90), 0.4), mode=Mode.SUBTRACT)
        # ---- TTP223 pocket on the +X face (prints up); the lid leaves its top 3 mm open for the leads
        tp = TTP_POCKET

        def ttp():
            with Locations(((tp["y"][0] + tp["y"][1]) / 2, (tp["z"][0] + tp["z"][1]) / 2)):
                Rectangle(tp["y"][1] - tp["y"][0], tp["z"][1] - tp["z"][0])
        add(pocket(ttp, _yz(ax1), ax1 - tp["x0"], rim=0.3), mode=Mode.SUBTRACT)
        # lid insert (along X: vertical in print)
        add(drill(M2_INSERT[0], M2_INSERT[1] + 0.4, Location((ax1, LID_SCREW[0], LID_SCREW[1]), (0, 1, 0), 90), 0.3),
            mode=Mode.SUBTRACT)
        # sensing lead: pocket floor -> electrode bore
        add(drill(1.6, tp["x0"] - EX, Location((tp["x0"], EY, LEAD_HOLE_Z), (0, 1, 0), 90), 0.2),
            mode=Mode.SUBTRACT)
        # ---- electrode bore (diamond: 45 deg walls, a V seat for the rod), from the clamp bottom up under the pocket
        h = (ROD_D + 0.1) / math.sqrt(2)

        def bore():
            Polygon((-h, 0), (0, -h), (h, 0), (0, h), align=None)
        add(pocket(bore, Plane(origin=(EX, EY, CLAMP_Z[0]), x_dir=(1, 0, 0), z_dir=(0, 0, -1)), BORE_TOP - CLAMP_Z[0],
                   rim=0.3), mode=Mode.SUBTRACT)
        # ---- pinch: slit from the bore to the clamp's +X face (plane XZ, vertical in print) + M2x12 across it
        add(Solid.make_box(CLAMP_X1 + 1.0 - EX, 0.8, CLAMP_Z[1] - CLAMP_Z[0] + 1.0,
                           Plane((EX, EY - 0.4, CLAMP_Z[0] - 1.0))), mode=Mode.SUBTRACT)
        px, pz = PINCH["x"], PINCH["z"]
        front = Plane(origin=(px, y0, pz), x_dir=(1, 0, 0), z_dir=(0, -1, 0))      # local (x, y) = (X, Z)
        add(pocket(teardrop(4.2, (1.0, 0.0)), front, PINCH["cb_depth"], rim=0.3), mode=Mode.SUBTRACT)
        add(pocket(teardrop(M2_CLR, (1.0, 0.0)), front.offset(-PINCH["cb_depth"]), (y1 - 4.4) - (y0 + PINCH["cb_depth"]),
                   rim=0.0, above=0.5), mode=Mode.SUBTRACT)
        back = Plane(origin=(px, y1, pz), x_dir=(1, 0, 0), z_dir=(0, 1, 0))         # local (x, y) = (X, -Z)
        add(pocket(teardrop(M2_INSERT[0], (1.0, 0.0)), back, M2_INSERT[1] + 0.4, rim=0.3), mode=Mode.SUBTRACT)
    return bp.part


def arm_holes():
    tp = TTP_POCKET
    return [("shaft", (ARM_X[0] + 2.0, PIVOT[0], PIVOT[1]), "servo spline / horn screw"),
            ("cable", (tp["x0"] + 1.0, EY, (tp["z"][0] + tp["z"][1]) / 2), "TTP223 pocket (leads exit its top end)"),
            ("cable", (tp["x0"] - 1.0, EY, LEAD_HOLE_Z), "sensing lead: TTP223 pad -> electrode"),
            ("access", (EX, EY, CLAMP_Z[0] + 3.0), "electrode bore (pinched)")]


def lid():
    """TM-121 TTP223 lid on the arm's +X face (head frame, deployed). Prints flat."""
    from .parts import chamfer_all, drill
    x0, x1 = LID["x"]
    b = chamfer_all(Solid.make_box(x1 - x0, LID["y"][1] - LID["y"][0], LID["z"][1] - LID["z"][0],
                                   Plane((x0, LID["y"][0], LID["z"][0]))), 0.4)
    with BuildPart() as bp:
        add(b)
        add(drill(M2_CLR, x1 - x0 + 1, Location((x1, LID_SCREW[0], LID_SCREW[1]), (0, 1, 0), 90), 0.3),
            mode=Mode.SUBTRACT)
        add(drill(M2_CLR, 0.01, Location((x0, LID_SCREW[0], LID_SCREW[1]), (0, 1, 0), -90), 0.3), mode=Mode.SUBTRACT)
    return bp.part


# =====================================================================================================================
# purchased-part placements + cable routes (head frame, deployed)
# =====================================================================================================================
def servo_frame():
    """SG90 frame (vendor.servo_case): z = output axis (machine -X), z = 0 on the tab top (the plate's +X face), x along
    the case from the shaft toward its far end (machine +Z: the case stands up)."""
    from .assembly import frame
    return frame((XT, PIVOT[0], PIVOT[1]), z=(-1, 0, 0), x=(0, 0, 1))


def ttp_frame():
    """TTP223 board frame (tools.ttp223_pcb): back face on the pocket floor, long axis along +Z (pads toward the hub)."""
    from .assembly import frame
    tp = TTP_POCKET
    return frame((tp["x0"], EY, (tp["z"][0] + tp["z"][1]) / 2), z=(1, 0, 0), x=(0, 0, 1))


def sense_lead_pts():
    """TTP223 touch pad (board back) -> lead hole -> wrapped on the rod's top end (moves with the arm)."""
    tp = TTP_POCKET
    zc = (tp["z"][0] + tp["z"][1]) / 2
    return [(tp["x0"] + 0.3, EY + 2.5, zc - 3.0), (tp["x0"] + 0.25, EY + 0.8, LEAD_HOLE_Z + 1.2),
            (tp["x0"] - 1.0, EY, LEAD_HOLE_Z), (EX + 1.2, EY, LEAD_HOLE_Z), (EX + 0.6, EY, LEAD_HOLE_Z - 0.6)]


def cable_arm_pts():
    """3-core probe cable, the part fixed to the arm: TTP223 pads -> out of the pocket's open top -> over the +X face
    to the front of the hub (moves with the arm)."""
    tp = TTP_POCKET
    zt = tp["z"][1]
    x = ARM_X[1] + 1.6
    return [(tp["x0"] + 2.4, EY, zt - 2.2), (ARM_X[1] + 0.9, EY - 1.0, zt - 1.6), (x, EY - 4.0, zt - 1.2),
            (x, EY - 7.0, PIVOT[1] - 3.5), (x, EY - 9.5, PIVOT[1] + 1.0)]


def cable_loop_pts():
    """the service loop across the joint (slack, silicone 3 x 28 AWG) -> round the plate's front edge -> tie on the
    plate's +X face -> up to the 3018 controller (static; drawn in the deployed pose)."""
    p0 = cable_arm_pts()[-1]
    return [p0, (p0[0] + 0.6, EY - 13.0, PIVOT[1] + 9.0), (19.0, EY - 14.5, -20.0), (24.0, PLATE_Y[0] - 3.0, -14.0),
            (PLATE_X[1] + 1.8, TIE[0] - 1.6, TIE[1] - 3.0), (PLATE_X[1] + 1.8, TIE[0] - 1.6, TIE[1] + 4.0),
            (PLATE_X[1] + 2.5, EY + 2.0, 10.0), (27.0, -38.0, 45.0), (22.0, -42.0, 80.0), (14.0, -40.0, 120.0),
            (10.0, -20.0, 150.0), (10.0, 10.0, 185.0)]


def servo_lead_pts():
    """SG90 lead: out of the case's top end -> down onto the plate's +X face -> through the tie -> up beside the probe
    cable to the servo hub (ch 1)."""
    from .vendor import servo_cable_exit
    p, _ = servo_cable_exit()
    q = servo_frame() * Location(p)
    x0, y0, z0 = q.position.X, q.position.Y, q.position.Z
    return [(x0, y0, z0), (x0, y0, z0 + 3.0), (x0 - 4.0, y0 - 0.5, z0 + 8.0), (PLATE_X[1] + 6.0, TIE[0] + 0.8, TIE[1] - 6.0),
            (PLATE_X[1] + 1.4, TIE[0] + 1.2, TIE[1] - 2.0), (PLATE_X[1] + 1.4, TIE[0] + 1.2, TIE[1] + 4.0),
            (PLATE_X[1] + 2.0, EY + 4.6, 10.0), (29.5, -36.0, 45.0), (24.5, -40.0, 80.0), (16.5, -38.0, 120.0),
            (13.0, -18.0, 150.0), (13.0, 12.0, 178.0)]


def tie_args():
    """(centre, bundle direction, loop inner (along the plate normal X, along the slot pair Y)) for vendor.cable_tie."""
    xo = PLATE_X[1]
    outer = xo + 3.6
    return ((PLATE_X[0] + outer) / 2, TIE[0], TIE[1]), (0, 0, 1), (outer - PLATE_X[0], 2 * TIE_DY - 1.6)


def tip_retracted():
    """(y, z) of the electrode tip when retracted."""
    a = math.radians(SWING)
    return (PIVOT[0] - R_TIP * math.sin(a), PIVOT[1] - R_TIP * math.cos(a))


TL.TOOLS["probe"] = TL._probe_tools()
