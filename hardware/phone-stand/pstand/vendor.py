"""Purchased parts, modelled as the product (catalogue dimensions), each in its own frame and of ONE material.

servo frame (tool-head convention): shaft axis = z, z = 0 on the tab top plane, +z = output, +x = along the case from
the shaft end toward the far end (where the lead leaves).
"""
from __future__ import annotations

import math

from build123d import Align, Axis, Box, Cylinder, Pos, Rot, fillet

from . import geo as G
from .spec import HORN, PHONE_NOM, CAM_NOM, FRONT_CAM_NOM, POST, SV

CB = (Align.CENTER, Align.CENTER, Align.MIN)


# ---------------------------------------------------------------------------------------------- MG90S / SG90 servo
def servo_case():
    L, W = SV["L"], SV["W"]
    zb = -(SV["tab_from_bottom"] + SV["tab_t"])
    zt = SV["above_tab"]
    cx = SV["cx"]
    s = G.box(cx - L / 2, cx + L / 2, -W / 2, W / 2, zb, zb + 3.0)
    s += G.box(cx - L / 2 + 0.15, cx + L / 2 - 0.15, -W / 2 + 0.15, W / 2 - 0.15, zb + 3.0, zb + 3.3)
    s += G.box(cx - L / 2, cx + L / 2, -W / 2, W / 2, zb + 3.3, -SV["tab_t"])
    s += G.box(cx - SV["tab_len"] / 2, cx + SV["tab_len"] / 2, -W / 2, W / 2, -SV["tab_t"], 0.0)
    s += G.box(cx - L / 2, cx + L / 2, -W / 2, W / 2, 0.0, zt)
    try:
        s = fillet(s.edges().filter_by(Axis.Z), 0.6)
    except Exception:
        pass
    for sx in (-1, 1):
        hx = cx + sx * SV["tab_pitch"] / 2
        s -= G.cyl_z(SV["tab_hole"] / 2, -3, 1, hx, 0)
        s -= G.box(hx + sx * 1.0, hx + sx * 2.6 + sx * 0.01, -0.5, 0.5, -3, 1) if sx > 0 else \
            G.box(hx - 2.61, hx - 1.0, -0.5, 0.5, -3, 1)
    s -= G.box(cx - L / 2 - 0.1, cx + L / 2 + 0.1, -W / 2 - 0.1, W / 2 + 0.1, zt - 0.3, zt - 0.05) - \
        G.box(cx - L / 2 + 0.2, cx + L / 2 - 0.2, -W / 2 + 0.2, W / 2 - 0.2, zt - 0.4, zt)
    s += G.cyl_z(SV["tower_d"] / 2, zt, zt + SV["tower_h"])
    s += G.cyl_z(2.8, zt, zt + 1.0, 6.2, 0)
    s += G.box(cx + L / 2, cx + L / 2 + 1.6, -2.1, 2.1, zb + 1.1, zb + 3.3)           # lead exit boss
    return s


def servo_label():
    cx = SV["cx"]
    return G.box(cx - SV["L"] / 2 + 1.5, cx + SV["L"] / 2 - 1.5, SV["W"] / 2, SV["W"] / 2 + 0.08, -14.0, -5.0)


def servo_spline():
    zt = SV["above_tab"] + SV["tower_h"]
    s = G.cyl_z(SV["spline_d"] / 2 - 0.25, zt, zt + SV["spline_h"])
    for k in range(20):
        a = 2 * math.pi * k / 20
        s += G.cyl_z(0.28, zt, zt + SV["spline_h"], math.cos(a) * (SV["spline_d"] / 2 - 0.25),
                     math.sin(a) * (SV["spline_d"] / 2 - 0.25))
    s -= G.cyl_z(0.8, zt + SV["spline_h"] - 3, zt + SV["spline_h"] + 0.1)
    return s


def servo_horn(float_mm=0.4):
    """stock double-arm horn, arms along servo-frame x; top face at spline top + 0.6 + float (screwed to the driven
    part, no screw in the spline)."""
    zs = SV["spline_top"] + float_mm
    zt = zs + HORN["over_spline"]
    s = G.cyl_z(HORN["hub_d"] / 2, zt - HORN["h"], zt)
    r, wh, wt = HORN["r"], HORN["w_hub"], HORN["w_tip"]
    arm = G.poly([(0, -wh / 2), (r - wt / 2, -wt / 2), (r - wt / 2, wt / 2), (0, wh / 2)]) + G.circ(wt / 2, r - wt / 2, 0)
    for a in (0, 180):
        s += G.prism(Rot(0, 0, a) * arm, "z", zt - HORN["arm_t"], zt)
    s -= G.cyl_z(SV["spline_d"] / 2 + 0.05, zt - HORN["h"] - 0.1, zs)
    s -= G.cyl_z(1.1, zt - 1.0, zt + 0.1)
    for a in (1, -1):
        for hr in HORN["holes"]:
            s -= G.cyl_z(0.5, zt - 2, zt + 1, a * hr, 0)
    return s


def servo_lead_exit():
    zb = -(SV["tab_from_bottom"] + SV["tab_t"])
    return (SV["L"] - SV["shaft_from_end"] + 1.6, 0.0, zb + 2.2)


# ---------------------------------------------------------------------------------------------- bearings, post ...
def bearing_623():
    """623ZZ 3 x 10 x 4, axis z, z = 0 .. 4. Returns (rings, shields)."""
    rings = G.cyl_z(5.0, 0, 4) - G.cyl_z(4.2, -0.1, 4.1)
    rings += G.cyl_z(2.6, 0, 4) - G.cyl_z(1.5, -0.1, 4.1)
    shields = G.cyl_z(4.2, 0.25, 3.75) - G.cyl_z(2.6, 0, 4)
    return rings, shields


def extrusion_2020(length):
    """2020 B-type profile (6 mm slot), axis z, z = 0 .. length, centre bore 4.2 (tap M5)."""
    W = POST
    sk = G.rect(-W / 2, W / 2, -W / 2, W / 2)
    for k in range(4):
        slot = G.poly([(-3.1, W / 2 + 0.1), (3.1, W / 2 + 0.1), (3.1, W / 2 - 1.8), (5.6, W / 2 - 1.8), (3.0, W / 2 - 6.0),
                       (-3.0, W / 2 - 6.0), (-5.6, W / 2 - 1.8), (-3.1, W / 2 - 1.8)])
        sk -= Rot(0, 0, 90 * k) * slot
    sk -= G.circ(2.1)
    for sx in (-1, 1):
        for sy in (-1, 1):
            sk -= G.circ(0.9, sx * 7.1, sy * 7.1)
    return G.prism(sk, "z", 0.0, length)


def slot_cover(length):
    """PVC slot cover strip in one slot face (+x), axis z."""
    return G.box(POST / 2 - 0.9, POST / 2 + 0.05, -3.0, 3.0, 0.0, length)


def heat_insert_m3(depth=5.7):
    """M3 brass heat-set insert, axis z, top at z = 0 going down."""
    s = G.cyl_z(2.3, -depth, 0) - G.cyl_z(1.25, -depth - 0.1, 0.1)
    for k in range(3):
        s -= G.cyl_z(2.35, -1.2 - k * 1.7, -0.6 - k * 1.7) - G.cyl_z(2.05, -2 - k * 1.7, 0)
    return s


def screw(d, length, head="button", hex_key=True):
    """screw along -z: head on z = 0 (bearing face), shank to z = -length."""
    if head == "button":
        h, dk = {2.5: (1.4, 4.7), 3: (1.65, 5.7), 5: (2.75, 9.5)}[d]
        hd = G.cyl_z(dk / 2, 0, h * 0.45) + G.cone_z(dk / 2, dk * 0.3, h * 0.45, h)
    elif head == "pan":
        h, dk = {2: (1.6, 4.0), 2.5: (1.75, 5.0), 3: (2.1, 6.0)}[d]
        hd = G.cyl_z(dk / 2, 0, h)
    elif head == "csk":
        dk = {3: 6.0, 5: 10.4}[d]
        h = (dk - d) / 2
        hd = G.cone_z(d / 2, dk / 2, -h, 0.0)
        s = hd + G.cyl_z(d / 2 * 0.92, -length, -h)
        return s - G.cyl_z(d * 0.3, -1.5, 0.1)
    else:   # socket head
        h, dk = {3: (3.0, 5.5), 2: (2.0, 3.8)}[d], None
        h, dk = h
        hd = G.cyl_z(dk / 2, 0, h)
    s = hd + G.cyl_z(d / 2 * 0.92, -length, 0.0)
    if hex_key:
        s -= G.cyl_z(d * 0.3, max(h * 0.4, h - 1.6), h + 0.1)
    return s


def washer(d_in, d_out, t):
    return G.cyl_z(d_out / 2, 0, t) - G.cyl_z(d_in / 2, -0.1, t + 0.1)


def extension_spring(length, od=5.0, wire=0.6):
    """extension spring between two hook centres on the x axis (0 .. length): close-wound coils (rings, no booleans)
    + a full loop at each end, in the x-z plane."""
    from build123d import Compound, Torus
    ro = od / 2
    rc = ro - wire / 2
    body0, body1 = 2 * rc + 0.4, length - 2 * rc - 0.4
    n = max(2, int((body1 - body0) / (1.5 * wire)))      # every 1.5th coil: reads as close-wound
    rings = [Pos(body0 + (k + 0.5) * (body1 - body0) / n, 0, 0) * Rot(0, 90, 0) * Torus(rc, wire / 2) for k in range(n)]
    rings += [Pos(xc, 0, 0) * Rot(90, 0, 0) * Torus(rc, wire / 2) for xc in (rc, length - rc)]
    return Compound(children=rings)


def silicone_pad(d, t):
    return G.cyl_z(d / 2, 0, t)


# ---------------------------------------------------------------------------------------------- phone (placeholder)
def phone_parts(w=PHONE_NOM["w"], l=PHONE_NOM["l"], t=PHONE_NOM["t"], back_y=4.0):
    """nominal phone in the T frame at el = 0: centred on x and z, back face on y = back_y, screen toward -y.
    Returns {finish: shape}."""
    r = PHONE_NOM["r"]
    y0, y1 = back_y - t, back_y
    body = G.prism(G.rrect(-w / 2, w / 2, -l / 2, l / 2, r), "y", y0 + 0.4, y1)
    try:
        body = fillet(body.edges().filter_by(Axis.Y, reverse=True).group_by(Axis.Y)[-1], 1.6)
    except Exception:
        pass
    glass = G.prism(G.rrect(-w / 2 + 0.3, w / 2 - 0.3, -l / 2 + 0.3, l / 2 - 0.3, r - 0.3), "y", y0, y0 + 0.45)
    # USB-C port, speaker holes at the bottom edge
    body -= G.box(-4.5, 4.5, (y0 + y1) / 2 - 1.6, (y0 + y1) / 2 + 1.6, -l / 2 - 1, -l / 2 + 7)
    for sx in (-1, 1):
        for k in range(5):
            body -= G.cyl_z(0.55, -l / 2 - 1, -l / 2 + 2, sx * (12 + k * 2.2), (y0 + y1) / 2)
    # side buttons: power (+x), volume (-x), above the jaws
    btn = G.box(w / 2 - 0.2, w / 2 + 0.6, (y0 + y1) / 2 - 1.2, (y0 + y1) / 2 + 1.2, 18, 36)
    btn += G.box(-w / 2 - 0.6, -w / 2 + 0.2, (y0 + y1) / 2 - 1.2, (y0 + y1) / 2 + 1.2, 22, 32)
    btn += G.box(-w / 2 - 0.6, -w / 2 + 0.2, (y0 + y1) / 2 - 1.2, (y0 + y1) / 2 + 1.2, 36, 46)
    # camera bump on the back, top corner on +x (top-left seen from the back)
    bx, bz = w / 2 - 4 - 16.0, l / 2 - 4 - 16.0
    bump = G.prism(G.rrect(bx - 16, bx + 16, bz - 16, bz + 16, 7), "y", y1 - 0.1, y1 + 1.4)
    lens = G.cyl_y(5.4, y1 + 1.3, y1 + 2.6, CAM_NOM[0], CAM_NOM[1])
    lens += G.cyl_y(5.4, y1 + 1.3, y1 + 2.6, bx - 8, bz - 8)
    lens += G.cyl_y(1.6, y1 + 1.3, y1 + 1.6, bx + 8, bz - 8)
    lens_glass = G.cyl_y(3.6, y1 + 2.6, y1 + 2.75, CAM_NOM[0], CAM_NOM[1]) + G.cyl_y(3.6, y1 + 2.6, y1 + 2.75, bx - 8, bz - 8)
    front_cam = G.cyl_y(1.6, y0 - 0.05, y0 + 0.1, FRONT_CAM_NOM[0], FRONT_CAM_NOM[1])
    return {"phone_body": body + btn, "phone_glass": glass + lens_glass + front_cam, "phone_bump": bump,
            "phone_lens": lens}
