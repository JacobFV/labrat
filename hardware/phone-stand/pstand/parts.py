"""Printed parts (PS-1xx), each built in its design frame:
    S frame  PS-100 base, PS-101 lid, PS-110 pan housing, PS-111 retainer
    T frame  PS-120 yoke, PS-130 cradle, PS-132 pinion, PS-133 spacer (origin on the tilt axis, el = 0, pan = 0)
    J frame  PS-131 jaw: the LEFT jaw with its inner (phone) face on x = 0; in T it sits at x = -w/2. The right jaw is
             the same part turned 180 deg about y (x -> -x, z -> -z).

FDM rules: every part is support-free in its print orientation (PRINT below): holes parallel to the bed are teardrops,
downward faces are >= 45 deg (chamfers, flares, pyramids, V roofs, 45 deg windows). Heat-set insert holes = insert OD
(4.0 for M3), clearance M3 3.4 / M5 5.5, self-tapper pilots M2 1.6 / M2.5 2.1, walls >= 1.6.
"""
from __future__ import annotations

import math

import numpy as np
from build123d import Align, Pos, Rectangle, Rot, Text, extrude

from . import geo as G
from .spec import *  # noqa: F401,F403
from . import spec as SP

# print orientation: rotation (4x4) from the design frame to the print frame (+z up), and the bed face
PRINT = {
    "PS-100": np.eye(4),                 # base: flat on its bottom
    "PS-101": np.eye(4),                 # lid: flat
    "PS-110": np.eye(4),                 # pan housing: upright, post socket on the bed
    "PS-111": np.eye(4),                 # retainer: flat
    "PS-120": np.eye(4),                 # yoke: upright, the disc on the bed
    "PS-130": G.rot_x(-90),              # cradle: on its back (design -y up)
    "PS-131": G.rot_x(-90),              # jaw: on its back (design -y up)
    "PS-132": G.rot_x(-90),              # pinion: flat
    "PS-133": G.rot_y(-90),              # spacer: on end
}


def print_up(pn):
    """the print's +z expressed in the part's design frame."""
    R = PRINT[pn][:3, :3]
    return (R.T @ np.array([0.0, 0.0, 1.0])).round(6).tolist()


# ===================================================================================================================
# PS-100 base: weighted tray (steel-shot pocket), 2020 socket, 45 deg ramp + 4 mm tongue under the 3018 front plate
# ===================================================================================================================
def _base_outline():
    t = SP.TONGUE
    sk = G.rrect(-BASE_X, BASE_X, BASE_Y[0], BASE_Y[1] + 10, 14)
    sk += G.poly([(-BASE_X, 10.0), (BASE_X, 10.0), (t["half_w"], RAMP_Y + 5), (-t["half_w"], RAMP_Y + 5)])
    sk += G.rrect(-t["half_w"], t["half_w"], RAMP_Y - 10, t["y1"], 12)
    return sk


def ballast_pocket():
    from build123d import offset
    pk = offset(_base_outline(), -POCKET["wall"]) & G.rect(-200, 200, -200, POCKET["y1"])
    return pk - G.rrect(-BOSS["half"] - 2, BOSS["half"] + 2, -BOSS["half"] - 2, BOSS["half"] + 2, 6)


def base():
    t = SP.TONGUE
    z0 = BASE_Z0
    top = G.prism(_base_outline(), "z", z0, BASE_TOP)
    side = G.poly([(BASE_Y[0] - 1, z0 - 0.1), (BASE_Y[0] - 1, BASE_TOP), (BASE_Y[1], BASE_TOP),
                   (RAMP_Y, t["top"]), (t["y1"] + 1, t["top"]), (t["y1"] + 1, z0 - 0.1)])
    s = top & G.prism(side, "x", -BASE_X - 1, BASE_X + 1)
    # ballast pocket around the post boss
    s -= G.prism(ballast_pocket(), "z", POCKET["z0"], BASE_TOP + 0.01)
    # boss + 2020 socket
    s += G.prism(G.rrect(-BOSS["half"], BOSS["half"], -BOSS["half"], BOSS["half"], 4), "z", z0, BOSS["top"])
    h = POST / 2 + SOCKET_CLR
    s -= G.box(-h, h, -h, h, POST_Z0, BOSS["top"] + 0.01)
    s -= extrude(Pos(0, 0, BOSS["top"] + 0.01) * Rectangle(2 * h + 1.6, 2 * h + 1.6), amount=0.8, taper=45,
                 dir=(0, 0, -1))                                                                  # 45 deg lead-in
    # M5 countersunk screw up into the post end (90 deg countersink = 45 deg faces)
    s -= G.cyl_z(2.75, z0 - 0.1, POST_Z0 + 0.1)
    s -= G.cone_z(5.3, 2.75, z0 - 0.01, z0 + 2.55)
    # lid inserts (M3 heat-set, 4.0 x 6)
    for (x, y) in LID_INSERTS:
        s -= G.drill_z(4.0, BASE_TOP, 6.0, x, y)
    return s


def lid():
    s = G.prism(_base_outline(), "z", BASE_TOP, BASE_TOP + LID_T) & G.box(-100, 100, -200, BASE_Y[1], 0, 100)
    h = BOSS["half"] + 0.3
    s -= G.prism(G.rrect(-h, h, -h, h, 4.3), "z", BASE_TOP - 0.1, BASE_TOP + LID_T + 0.1)
    for (x, y) in LID_INSERTS:
        s -= G.cyl_z(1.7, BASE_TOP - 0.1, BASE_TOP + LID_T + 0.1, x, y)
    # lead groove: from the post's +x face to the base's right edge
    s -= G.box(h - 0.5, BASE_X + 1, -3.5, 3.5, BASE_TOP + LID_T - 2.0, BASE_TOP + LID_T + 0.1)
    # name, engraved 0.6
    txt = Pos(0, -62, 0) * Text("labrat", 14.0)
    s -= extrude(Pos(0, 0, BASE_TOP + LID_T - 0.6) * txt, amount=0.7)
    return s


# ===================================================================================================================
# PS-110 pan housing: on the post top. Pan servo upright (case toward +x), its spline on the pan axis; thrust face at
# Z_TH, rim around the yoke disc, three M3 inserts for the retainer. Post socket with a 45 deg pyramid roof (the post's
# end edges seat on the pyramid faces, self-centring), M5 button screw from the servo pocket into the post end.
# ===================================================================================================================
def housing():
    Z = Z_TH
    zb = Z + FLOOR_BOT - PYR_H - HOUSING_SOCKET
    r_lo, r_hi = HOUSING_R_LOW, RIM_R[1]
    s = G.cyl_z(r_lo, zb, Z + FLARE[0]) + G.cone_z(r_lo, r_hi, Z + FLARE[0], Z + FLARE[1]) + \
        G.cyl_z(r_hi, Z + FLARE[1], Z + H_RIM)
    # rim pocket + horn bore
    s -= G.cyl_z(RIM_R[0], Z, Z + H_RIM + 0.1)
    s -= G.cyl_z(BORE_R, Z - 4.0, Z + 0.01)
    # servo: tab pocket from the top, body pocket below (case along +x from the shaft)
    cx = SV["cx"]
    tab_bot = Z + TAB_TOP - SV["tab_t"]
    s -= G.box(cx - SV["tab_len"] / 2 - 0.25, cx + SV["tab_len"] / 2 + 0.25, -SV["W"] / 2 - 0.25, SV["W"] / 2 + 0.25,
               tab_bot, Z + 0.01)
    s -= G.box(cx - SV["L"] / 2 - 0.25, cx + SV["L"] / 2 + 0.25, -SV["W"] / 2 - 0.25, SV["W"] / 2 + 0.25,
               Z + POCKET_FLOOR, tab_bot + 0.01)
    for sx in (-1, 1):
        s -= G.drill_z(1.6, tab_bot, 7.0, cx + sx * SV["tab_pitch"] / 2, 0)
    # lead tunnel out through the +x wall (pointed roof), 9 wide for the 3-way connector
    z0 = Z + POCKET_FLOOR
    tun = G.poly([(-4.5, z0), (4.5, z0), (4.5, z0 + 4.5), (0.0, z0 + 9.0), (-4.5, z0 + 4.5)])
    s -= G.prism(tun, "x", cx + SV["L"] / 2 - 0.5, r_hi + 1)
    # M5 button head counterbore + clearance down to the post end
    s -= G.cyl_z(M5_CB["d"] / 2, Z + POCKET_FLOOR - M5_CB["depth"], Z + POCKET_FLOOR + 0.01)
    s -= G.cyl_z(2.75, Z + FLOOR_BOT - 0.5, Z + POCKET_FLOOR - M5_CB["depth"] + 0.01)
    # 2020 socket with a 45 deg pyramid roof
    h = POST / 2 + SOCKET_CLR
    zs = Z + FLOOR_BOT - PYR_H
    s -= G.box(-h, h, -h, h, zb - 0.01, zs + 0.001)
    s -= extrude(Pos(0, 0, zs) * Rectangle(2 * h, 2 * h), amount=PYR_H, taper=45)
    s -= extrude(Pos(0, 0, zb - 0.01) * Rectangle(2 * h + 1.6, 2 * h + 1.6), amount=0.8, taper=45)   # lead-in
    # retainer inserts
    for a in RET_INSERT_ANG:
        x, y = RET_INSERT_R * math.cos(math.radians(a)), RET_INSERT_R * math.sin(math.radians(a))
        s -= G.drill_z(4.0, Z + H_RIM, 6.0, x, y)
    return s


def retainer():
    Z = Z_TH + H_RIM
    s = G.cyl_z(RET_R[1], Z, Z + RET_T) - G.cyl_z(RET_R[0], Z - 0.1, Z + RET_T + 0.1)
    s -= G.cone_z(RET_R[0] - 0.01, RET_R[0] + 0.8, Z + RET_T - 0.8, Z + RET_T + 0.01)   # chamfer the bore top
    for a in RET_INSERT_ANG:
        x, y = RET_INSERT_R * math.cos(math.radians(a)), RET_INSERT_R * math.sin(math.radians(a))
        s -= G.cyl_z(1.7, Z - 0.1, Z + RET_T + 0.1, x, y)
    return s


# ===================================================================================================================
# PS-120 yoke (T frame): disc on the thrust face, neck through the retainer, 45 deg underside flaring to a U whose
# floor clears a 170 mm phone at any tilt, two arms. Right arm: tilt servo outside, 45 deg window. Left arm: 623ZZ.
# ===================================================================================================================
def _arm_profile(right: bool):
    sk = G.circ(ARM_TOP_R)
    if right:                               # flag backing the servo tabs (edges at 45 deg)
        dy, dz = SERVO_DIAG
        e = (-dz, dy)                       # in-plane perpendicular to the case direction
        s0, s1 = SV["cx"] - SV["tab_len"] / 2 - 3.0, SV["cx"] + SV["tab_len"] / 2 + 3.0
        t = SV["W"] / 2 + 3.0
        pts = [(dy * s + e[0] * tt, dz * s + e[1] * tt) for s, tt in ((s0, -t), (s1, -t), (s1, t), (s0, t))]
        sk += G.poly(pts)
    return sk


def tilt_servo_frame():
    """servo frame -> T: output along -x, case along SERVO_DIAG, tab top on the arm's outer face."""
    d = np.array([0.0, *SERVO_DIAG])
    ez = np.array([-1.0, 0.0, 0.0])
    ey = np.cross(ez, d)
    return G.frame((ARM_OUT, 0.0, 0.0), d, ey, ez)


def pan_servo_frame():
    """servo frame -> S: output up, case along +x, tab top at Z_TH + TAB_TOP."""
    return G.frame((0.0, 0.0, Z_TH + TAB_TOP), (1, 0, 0), (0, 1, 0), (0, 0, 1))


def yoke():
    zd = DISC_BOT_T
    s = G.cyl_z(DISC_R, zd, zd + DISC_T) + G.cyl_z(NECK_R, zd + DISC_T, NECK_TOP_T)
    xn = 25.0                                   # bridge bottom edge (inside the neck circle at |y| = 12)
    zo = NECK_TOP_T + (ARM_OUT - xn)            # underside meets the arm's outer face
    zc = FLOOR_Z + (ARM_IN - FLOOR_FLAT)        # inner chamfer meets the arm's inner face
    prof = [(-xn, NECK_TOP_T), (xn, NECK_TOP_T), (ARM_OUT, zo), (ARM_OUT, 0.0), (ARM_IN, 0.0), (ARM_IN, zc),
            (FLOOR_FLAT, FLOOR_Z), (-FLOOR_FLAT, FLOOR_Z), (-ARM_IN, zc), (-ARM_IN, 0.0), (-ARM_OUT, 0.0), (-ARM_OUT, zo)]
    s += G.prism(G.poly(prof), "y", -ARM_HALF_Y, ARM_HALF_Y)
    s += G.prism(_arm_profile(True), "x", ARM_IN, ARM_OUT)
    s += G.prism(_arm_profile(False), "x", -ARM_OUT, -ARM_IN)
    # right arm: 45 deg servo window (case cross-section + 0.3) and the tab screw pilots (teardrops along x)
    dy, dz = SERVO_DIAG
    e = (-dz, dy)
    s0, s1 = -SV["shaft_from_end"] - 0.3, SV["L"] - SV["shaft_from_end"] + 0.3
    t = SV["W"] / 2 + 0.3
    win = G.poly([(dy * a + e[0] * b, dz * a + e[1] * b) for a, b in ((s0, -t), (s1, -t), (s1, t), (s0, t))])
    s -= G.prism(win, "x", ARM_IN - 0.1, ARM_OUT + 0.1)
    for sx in (-1, 1):
        a = SV["cx"] + sx * SV["tab_pitch"] / 2
        s -= G.prism(G.teardrop(1.6, dy * a, dz * a, up=(0.0, 1.0)), "x", ARM_IN - 0.1, ARM_OUT + 0.1)
    # left arm: 623ZZ seat from outside (press, 10.0), 6.2 through for the spacer
    B = BEARING_623
    s -= G.prism(G.teardrop(B["D"], 0, 0, up=(0.0, 1.0)), "x", -ARM_OUT - 0.1, -ARM_OUT + B["B"])
    s -= G.prism(G.teardrop(6.2, 0, 0, up=(0.0, 1.0)), "x", -ARM_OUT + B["B"] - 0.01, -ARM_IN + 0.1)
    # pan horn screws up into the disc (pilots with drill points)
    for sx in (-1, 1):
        s -= G.drill_z(1.6, zd, 7.0, sx * HORN_SCREW_R, 0.0, down=False)
    return s


# ===================================================================================================================
# PS-130 cradle (T frame): two bars (phone rest at y = 4) over a dovetail channel for the jaws, floor, back with two
# V-roofed spring lanes, ears on the tilt axis. Loading gate (|x| < 11, no undercut) to drop the jaw blocks in.
# ===================================================================================================================
def _ch_w(y):
    return CH["w_mouth"] + (y - CH["y_mouth"]) * math.tan(math.radians(CH["undercut_deg"]))


def cradle():
    xe0, xe1 = EAR_X
    s = G.box(-xe0, xe0, PHONE_BACK_Y, BACK_Y, -BAR_Z, BAR_Z)
    y0, y1 = CH["y_mouth"] - 0.1, CH["y_floor"]
    s -= G.prism(G.poly([(y0, -_ch_w(y0)), (y0, _ch_w(y0)), (y1, _ch_w(y1)), (y1, -_ch_w(y1))]), "x", -xe0 - 0.01, xe0 + 0.01)
    s -= G.box(-GATE_X, GATE_X, y0, y1, -_ch_w(y1), _ch_w(y1))
    ear = G.rect(PHONE_BACK_Y, BACK_Y, -BAR_Z, BAR_Z) + \
        G.poly([(PHONE_BACK_Y + 0.01, -BAR_Z), (PHONE_BACK_Y + 0.01, BAR_Z), (-5.0, 13.0), (-5.0, -13.0)]) + G.circ(13.0)
    s += G.prism(ear, "x", xe0 - 0.01, xe1) + G.prism(ear, "x", -xe1, -xe0 + 0.01)
    # floor slots for the spring posts (M2.5 shank)
    xs = SPRING_SCREW["dx"]
    for sx in (-1, 1):
        for sz in (-1, 1):
            s -= G.box(*sorted((sx * (JAW_MIN + xs - 1.5), sx * (JAW_OPEN + xs + 1.5))), FLOOR_Y[0] - 0.1, FLOOR_Y[1] + 0.1,
                       sz * GROOVE["z"] - 1.6, sz * GROOVE["z"] + 1.6)
    # spring lanes in the back face (bed face in print): vertical walls, 45 deg V roof
    w = GROOVE["w"] / 2
    for sz in (-1, 1):
        zc = sz * GROOVE["z"]
        lane = G.poly([(BACK_Y + 0.1, zc - w), (BACK_Y - GROOVE["wall"], zc - w), (BACK_Y - GROOVE["wall"] - w, zc),
                       (BACK_Y - GROOVE["wall"], zc + w), (BACK_Y + 0.1, zc + w)])
        s -= G.prism(lane, "x", -(JAW_OPEN + xs + 6), JAW_OPEN + xs + 6)
    # pinion: M3 insert in the floor (axis y), drilled from the channel side
    s -= G.cyl_y(2.0, FLOOR_Y[0] - 0.01, FLOOR_Y[0] + 5.8) + _cone_y(2.0, FLOOR_Y[0] + 5.8)
    # left ear: M3 insert for the idler screw (teardrop along x, print-up = -y)
    s -= G.prism(G.teardrop(4.0, 0, 0, up=(-1.0, 0.0)), "x", -xe1 - 0.01, -xe1 + 6.5)
    # right ear: horn screw pilots
    for sz in (-1, 1):
        s -= G.prism(G.teardrop(1.5, 0, sz * HORN_SCREW_R, up=(-1.0, 0.0)), "x", xe1 - 7.0, xe1 + 0.01)
    return s


def _cone_y(r, y):
    """90 deg drill point at the end of a hole along +y ending at y."""
    from build123d import Cone
    return Pos(0, y, 0) * Rot(-90, 0, 0) * Cone(r, 0.0, r, align=(Align.CENTER, Align.CENTER, Align.MIN))


# ===================================================================================================================
# PS-131 jaw (J frame, left jaw, phone face on x = 0): dovetail block, finger with a 45 deg lip (V grip), rack arm
# (m1, 20 deg) meshing the pinion, two M2.5 spring posts.
# ===================================================================================================================
def rack_phase(w_nom=PHONE_NOM["w"]):
    p = math.pi * PINION["m"]
    return (w_nom / 2 - p / 2) % p     # tooth centres u_k = phase + k p put a gap over the pinion at nominal width


def jaw():
    J = JAW
    c = J["clr"]
    ya, yb = J["slide_y"]
    blk = G.poly([(ya, -(_ch_w(ya) - c)), (ya, _ch_w(ya) - c), (yb, _ch_w(yb) - c), (yb, -(_ch_w(yb) - c))])
    s = G.prism(blk, "x", -J["block"], 0.0)
    # finger + lip (profile in x, y), extruded along z
    fp = G.poly([(-J["finger"], ya + 0.01), (0.0, ya + 0.01), (0.0, J["chamfer_from"]),
                 (J["lip"], J["chamfer_from"] - J["lip"]), (J["lip"], J["lip_top"]), (-J["finger"] + 2.5, J["lip_top"]),
                 (-J["finger"], J["lip_top"] + 2.5)])
    s += G.prism(fp, "z", -J["finger_z"], J["finger_z"])
    # centre mark: V groove across the lip top at z = 0
    s -= G.prism(G.poly([(J["lip_top"] - 0.1, -0.7), (J["lip_top"] + 0.7, 0.0), (J["lip_top"] - 0.1, 0.7)]), "x",
                 -J["finger"] - 0.1, J["lip"] + 0.1)
    # rack arm (upper half of the channel) with the teeth pointing -z
    za, zb = J["arm_z"]
    s += G.box(-0.01, J["arm_len"], ya, yb, za, zb)
    p = math.pi * PINION["m"]
    ha, hf = PINION["m"], 1.25 * PINION["m"]
    tpa = math.tan(math.radians(PINION["pa"]))
    hb = p / 4 - 0.05 / 2
    ph = rack_phase()
    pr = PINION["r"]
    teeth = None
    k0, k1 = int(math.floor((0 - ph) / p)), int(math.ceil((J["arm_len"] - ph) / p))
    for k in range(k0, k1 + 1):
        u = ph + k * p
        tooth = G.poly([(u - hb - hf * tpa, za + 0.01), (u - hb + ha * tpa, pr - ha), (u + hb - ha * tpa, pr - ha),
                        (u + hb + hf * tpa, za + 0.01)])
        teeth = tooth if teeth is None else teeth + tooth
    teeth &= G.rect(1.5, J["arm_len"] - 0.5, 0, za + 1)
    s += G.prism(teeth, "y", ya, yb)
    s -= G.prism(G.poly([(J["arm_len"] - 2, zb + 0.1), (J["arm_len"] + 0.1, zb + 0.1), (J["arm_len"] + 0.1, zb - 2.1)]),
                 "y", ya - 0.1, yb + 0.1)                                                      # tip chamfer
    # spring posts: M2.5 pilots up from the back face (drill points)
    for sz in (-1, 1):
        x, z = -SPRING_SCREW["dx"], sz * SPRING_SCREW["z"]
        s -= G.cyl_y(SPRING_SCREW["pilot"] / 2, yb - SPRING_SCREW["depth"], yb + 0.1, x, z)
        s -= Pos(x, 0, z) * _cone_y_down(SPRING_SCREW["pilot"] / 2, yb - SPRING_SCREW["depth"])
    return s


def _cone_y_down(r, y):
    """drill point at the end of a hole drilled toward -y, ending at y."""
    from build123d import Cone
    return Pos(0, y, 0) * Rot(90, 0, 0) * Cone(r, 0.0, r, align=(Align.CENTER, Align.CENTER, Align.MIN))


def jaw_place(w, side):
    """J -> T for a phone of width w. side -1 = left jaw (x = -w/2), +1 = right (turned 180 deg about y)."""
    if side < 0:
        return G.trans(-w / 2, 0, 0)
    return G.trans(w / 2, 0, 0) @ G.rot_y(180)


# ===================================================================================================================
# PS-132 pinion (T frame, axis y), PS-133 idler spacer (T frame, axis x)
# ===================================================================================================================
def pinion():
    pts = G.involute_gear_pts(PINION["m"], PINION["z"], PINION["pa"])
    # rotate so a tooth points +z at the nominal width (racks have a gap over x = 0 there)
    a = math.pi / 2
    pts = [(x * math.cos(a) - z * math.sin(a), x * math.sin(a) + z * math.cos(a)) for x, z in pts]
    s = G.prism(G.poly(pts), "y", *PINION["y"])
    s -= G.cyl_y(PINION["bore"] / 2, PINION["y"][0] - 0.1, PINION["y"][1] + 0.1)
    return s


def spacer():
    L = (ARM_OUT - BEARING_623["B"]) - EAR_X[1]
    x0, x1 = -EAR_X[1] - L, -EAR_X[1]
    return G.cyl_x(IDLER_SPACER["od"] / 2, x0, x1) - G.cyl_x(IDLER_SPACER["id"] / 2, x0 - 0.1, x1 + 0.1)


MAKERS = {"PS-100": base, "PS-101": lid, "PS-110": housing, "PS-111": retainer, "PS-120": yoke, "PS-130": cradle,
          "PS-131": jaw, "PS-132": pinion, "PS-133": spacer}
