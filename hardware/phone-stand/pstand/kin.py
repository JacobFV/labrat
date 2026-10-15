"""Kinematics (aiming), and the design checks: camera FOV over the deck, servo torque, tipping, sweep clearances,
support-free printing.

Bench frame used here = S (stand frame, mm): x = machine x (operator's right), y = toward the CNC, z = up from the
bench, origin on the pan axis. Deck point (deck x, deck y, height above the bed h) with the bed at machine y = bed_y:
    S = (deck_x - 150, (deck_y - 90) - (bed_y - 90) - STAND_Z, BED_H + h)
"""
from __future__ import annotations

import math

import numpy as np

from . import geo as G
from . import spec as SP

PRINT_MAX_DEG = 45.0
PRINT_TESS_DEG = 3.0
PRINT_TOL_MM2 = 3.0


# ------------------------------------------------------------------------------------------------- frames / aiming
def deck_to_S(x, y, h=0.0, bed_y=90.0):
    return np.array([x - 150.0, (y - 90.0) - (bed_y - 90.0) - SP.STAND_Z, SP.BED_H + h])


def S_to_deck(p, bed_y=90.0):
    return np.array([p[0] + 150.0, p[1] + SP.STAND_Z + 90.0 + (bed_y - 90.0), p[2] - SP.BED_H])


def cam_local(w=None, cam=SP.CAM_NOM, back_y=SP.PHONE_BACK_Y, front=False):
    """camera position (T frame) and its optical axis at el = 0 for a phone centred by the clamp."""
    t = SP.PHONE_NOM["t"]
    if front:
        return np.array([SP.FRONT_CAM_NOM[0], back_y - t, SP.FRONT_CAM_NOM[1]]), np.array([0.0, -1.0, 0.0])
    return np.array([cam[0], back_y + 2.7, cam[1]]), np.array([0.0, 1.0, 0.0])


def T_to_S(pan, el):
    return G.rot_z(pan) @ G.trans(0, 0, SP.H_T) @ G.rot_x(el)


def camera(pan, el, front=False, cam=SP.CAM_NOM):
    p, d = cam_local(cam=cam, front=front)
    M = T_to_S(pan, el)
    return (M @ np.r_[p, 1])[:3], M[:3, :3] @ d, M[:3, :3] @ np.array([1.0, 0, 0]), M[:3, :3] @ np.array([0, 0, 1.0])


def aim(target, front=False, cam=SP.CAM_NOM, iters=30):
    """(pan, el) [deg] putting the target on the camera's optical axis (the rear camera, or with front=True the
    front camera = the screen faces the target). Exact for the camera offset from the axes (fixed-point iteration)."""
    target = np.asarray(target, float)
    pan, el = 0.0, -45.0
    for _ in range(iters):
        c, _, _, _ = camera(pan, el, front, cam)
        d = target - c
        if front:
            d = -d                   # the rear camera points away from what the screen faces
        pan = math.degrees(math.atan2(-d[0], d[1]))
        el = math.degrees(math.atan2(d[2], math.hypot(d[0], d[1])))
    return pan, el


def view_margins(pan, el, pts, fov=SP.CAM_FOV, cam=SP.CAM_NOM):
    """half-angle use of the image (across, along) for points, and the margin to the FOV edge (deg, min over pts)."""
    c, n, ex, ez = camera(pan, el, cam=cam)
    a_ac, a_al = [], []
    for p in pts:
        d = np.asarray(p) - c
        z = d @ n
        a_ac.append(math.degrees(math.atan2(abs(d @ ex), z)))
        a_al.append(math.degrees(math.atan2(abs(d @ ez), z)))
    m = min(fov[0] / 2 - max(a_ac), fov[1] / 2 - max(a_al))
    return {"across_deg": round(max(a_ac), 2), "along_deg": round(max(a_al), 2), "margin_deg": round(m, 2)}


def deck_corners(h=0.0, bed_y=90.0):
    return [deck_to_S(x, y, h, bed_y) for x in (0.0, 300.0) for y in (0.0, 180.0)]


def best_view(pts, cam=SP.CAM_NOM):
    """aim maximising the worst FOV margin over pts (coarse grid + refine)."""
    best = None
    for pan in np.arange(-10, 10.1, 1.0):
        for el in np.arange(-89, -20, 0.5):
            m = view_margins(pan, el, pts, cam=cam)["margin_deg"]
            if best is None or m > best[0]:
                best = (m, pan, el)
    return best


# ------------------------------------------------------------------------------------------------- reference aims
OPERATOR_FACE_S = np.array([0.0, -425.0, 700.0])   # standing operator's eyes ~ 1.6 m above the floor, 0.2 m off the
                                                    # bench edge (twin Z = -650), centred


def references():
    deck_c = deck_to_S(150.0, 90.0, 0.0)
    p0, el0 = aim(deck_c)
    po, elo = aim(OPERATOR_FACE_S, front=True)
    return {"deck_center_S": deck_c.round(2).tolist(), "pan0": p0, "el0": el0,
            "operator_S": OPERATOR_FACE_S.tolist(), "operator_pan": po, "operator_el": elo}


def _driver_targets():
    ref = references()
    p0, el0 = ref["pan0"], ref["el0"]
    targets = [deck_to_S(x, y, h) for x in np.linspace(10, 290, 6) for y in np.linspace(10, 170, 5) for h in (0, 51.2)]
    return targets, [np.array(aim(t)) - [p0, el0] for t in targets]


def driver_error(pivot, tex=None):
    """max pointing error (deg) of the cloud driver's model (camera at `pivot`, angles relative to the deck-centre
    reference) against the exact aim, over beaker rims and the deck plane."""
    targets, exact = tex or _driver_targets()
    pv = np.asarray(pivot, float)
    dc = deck_to_S(150.0, 90.0, 0.0) - pv
    a0 = math.degrees(math.atan2(-dc[0], dc[1]))
    e0 = math.degrees(math.atan2(dc[2], math.hypot(dc[0], dc[1])))
    m = 0.0
    for t, ex in zip(targets, exact):
        d = t - pv
        pa = math.degrees(math.atan2(-d[0], d[1])) - a0
        ea = math.degrees(math.atan2(d[2], math.hypot(d[0], d[1]))) - e0
        m = max(m, abs(pa - ex[0]), abs(ea - ex[1]))
    return m


def fit_pivot():
    """single 'camera' point for the cloud driver's model minimising driver_error (grid around the tilt axis)."""
    best = None
    tex = _driver_targets()
    for x in np.arange(-10, 15.1, 2.5):
        for y in np.arange(-20, 20.1, 4):
            for z in np.arange(SP.H_T - 20, SP.H_T + 40.1, 4):
                e = driver_error([x, y, z], tex)
                if best is None or e < best[0]:
                    best = (e, np.array([x, y, z]))
    return best


def servo_deg(pan, el):
    """hub `servo` deg (0..180, 90 = 1500 us) for mechanical pan / camera elevation."""
    return 90.0 + SP.PAN_DIR * pan, 90.0 + SP.TILT_DIR * (el - SP.EL_MID)


def fov_checks():
    ref = references()
    out = []
    for name, h in (("deck plane", 0.0), ("beaker rims (51.2 above the bed)", 51.2)):
        for bed_y in (90.0, 0.0, 180.0):
            pts = deck_corners(h, bed_y)
            c = deck_to_S(150.0, 90.0, 0.0, bed_y)
            pa, ea = aim(c)
            vm = view_margins(pa, ea, pts)
            bm, bp, be = best_view(pts)
            out.append({"check": "fov", "name": f"{name}, bed y={bed_y:g}: aimed at the deck centre",
                        "ok": vm["margin_deg"] >= (2.0 if (h == 0 and bed_y == 90) else -99), "value": vm,
                        "aim": {"pan": round(pa, 2), "el": round(ea, 2)},
                        "best_aim": {"pan": round(bp, 1), "el": round(be, 1), "margin_deg": round(bm, 2)},
                        "need": ">= 2 deg margin on the deck plane at bed y = 90 (others informative)"})
    return out, ref


# ------------------------------------------------------------------------------------------------- masses
PRINT_DENSITY = 1.27     # PETG g/cm3
WALL_MM = 1.6            # 4 perimeters x 0.4
INFILL = 0.20


def printed_mass(shape):
    V = shape.volume / 1000.0
    A = shape.area / 100.0
    shell = min(V, A * WALL_MM / 10.0)
    return PRINT_DENSITY * (shell + (V - shell) * INFILL)


def mass_props(insts, pan=0.0, el=0.0, groups=None, ballast=True):
    """total mass [g] and CoM (S) of instances in groups (None = all) at a pose."""
    from .assembly import PARTS, group_to_S, inst_shape
    m_tot, mom = 0.0, np.zeros(3)
    rows = []
    for i in insts:
        if groups and i.group not in groups:
            continue
        d = PARTS[i.pn]
        s = inst_shape(i)
        if d.process == "fdm_print":
            m = printed_mass(s)
        elif d.product == "PS-200":
            m = 13.4 if i.pn.endswith("-C") else 0.0
        elif d.product == "LIB-PHONE":
            m = SP.PHONE_MASS * 1000 if i.pn.endswith("phone_body") else 0.0
        elif d.density:
            m = d.density * s.volume / 1000.0
        else:
            m = 7.85 * s.volume / 1000.0
        if m == 0.0:
            continue
        c = s.center()
        c = (group_to_S(i.group, pan, el) @ i.M @ np.r_[c.X, c.Y, c.Z, 1.0])[:3]
        m_tot += m
        mom += m * c
        rows.append((i.name, m))
    if ballast and (groups is None or "base" in groups):
        mb, cb = ballast_mass()
        m_tot += mb
        mom += mb * cb
        rows.append(("ballast", mb))
    return m_tot, mom / m_tot, rows


def ballast_mass():
    """steel shot filling the base pocket to 95 %: mass [g] and centroid (S)."""
    from .parts import ballast_pocket
    v = G.prism(ballast_pocket(), "z", SP.POCKET["z0"], SP.BASE_TOP - 1.0)
    c = v.center()
    return SP.BALLAST_DENSITY * v.volume / 1000.0 * 0.95, np.array([c.X, c.Y, c.Z])


# ------------------------------------------------------------------------------------------------- torque
def torque_checks(insts):
    """static holding torque about each axis (worst pose) vs servo stall, incl. allowances; kg cm."""
    out = []
    g_cm = 1 / 1000.0  # g mm -> kg cm: m[g] * r[mm] / 1000 / 10 * 10 ... (kg * cm = g/1000 * mm/10)
    # ---- tilt: everything turning about the tilt axis
    m, c, rows = mass_props(insts, 0.0, 0.0, groups=("tilt", "clamp", "phone"))
    rel = c - np.array([0.0, 0.0, SP.H_T])
    r_cg = math.hypot(rel[1], rel[2])
    t_cg = m * r_cg / 10000.0                       # g * mm -> kg cm
    misplace = SP.PHONE_MASS * 1000 * 8.0 / 10000.0  # phone off-centre 8 mm along its length (centre marks on the lips)
    thick = SP.PHONE_MASS * 1000 * 1.5 / 10000.0     # 6.5 .. 9.5 mm phones: mid-plane within +-0.75, + 0.75 CoM/bump
    usb = 0.10                                       # USB-C cable drag at the phone's bottom edge (~0.12 N at 85 mm)
    t_hold = t_cg + misplace + thick + usb
    # inertia about the tilt axis (phone as a slab + the rest lumped), for a 90 deg move eased over 0.6 s (hub `ms`)
    L, W = SP.PHONE_NOM["l"] / 1000, SP.PHONE_NOM["t"] / 1000
    I_ph = SP.PHONE_MASS * (L ** 2 + W ** 2) / 12
    I_rest = (m / 1000 - SP.PHONE_MASS) * (0.025 ** 2)
    alpha = math.radians(90) * math.pi ** 2 / (2 * 0.6 ** 2)       # cosine profile peak acceleration
    t_dyn = (I_ph + I_rest) * alpha / 0.0980665                     # N m -> kg cm
    for sv, stall in SP.SERVO_STALL.items():
        out.append({"check": "torque", "name": f"tilt holding torque vs {sv} stall ({stall} kg cm)",
                    "ok": stall / (t_hold + t_dyn) >= 2.0,
                    "value": {"tilt_group_g": round(m, 1), "cg_from_axis_mm": round(r_cg, 2), "t_cg": round(t_cg, 3),
                              "t_phone_misplaced_8mm": round(misplace, 3), "t_thickness": round(thick, 3),
                              "t_usb_cable": usb, "t_hold_kgcm": round(t_hold, 3), "t_dyn_90deg_0.6s": round(t_dyn, 3),
                              "margin_x": round(stall / (t_hold + t_dyn), 1)},
                    "need": ">= 2x"})
    # ---- pan: plain thrust ring (PETG on PETG, greased mu 0.15, dry 0.3) + lead loop drag + acceleration
    mp, cp, _ = mass_props(insts, 0.0, 0.0, groups=("pan", "tilt", "clamp", "phone"))
    N = mp / 1000 * 9.81
    r_m = (max(SP.BORE_R, SP.SV["cx"] + SP.SV["tab_len"] / 2 + 0.25) + SP.DISC_R) / 2 / 1000
    t_fric = 0.3 * N * r_m / 0.0980665
    t_lead = 0.15
    rad = np.array([0.0, 0.0])
    I_p = (mp / 1000) * 0.045 ** 2
    t_acc = I_p * math.radians(170) * math.pi ** 2 / (2 * 0.6 ** 2) / 0.0980665
    t_pan = t_fric + t_lead + t_acc
    for sv, stall in SP.SERVO_STALL.items():
        out.append({"check": "torque", "name": f"pan torque vs {sv} stall ({stall} kg cm)", "ok": stall / t_pan >= 2.0,
                    "value": {"pan_group_g": round(mp, 1), "t_thrust_friction_dry": round(t_fric, 3),
                              "t_lead_loop": t_lead, "t_accel_170deg_0.6s": round(t_acc, 3),
                              "t_total_kgcm": round(t_pan, 3), "margin_x": round(stall / t_pan, 1),
                              "cg_off_pan_axis_mm": round(math.hypot(cp[0], cp[1]), 1)}, "need": ">= 2x"})
    return out


# ------------------------------------------------------------------------------------------------- tipping
def tipping_checks(insts):
    out = []
    worst = None
    for pan, el in ((0, 0), (0, -90), (85, -90), (-85, -90), (0, 40)):
        m, c, _ = mass_props(insts, pan, el)
        edges = {"+y (tongue tip, under the CNC)": SP.TONGUE["y1"] - c[1],
                 "-y (operator side)": c[1] - (SP.PADS[0][1] - SP.PAD_D / 2),
                 "+x": SP.PADS[1][0] + SP.PAD_D / 2 - c[0], "-x": c[0] - (SP.PADS[0][0] - SP.PAD_D / 2)}
        for k, dist in edges.items():
            F = m / 1000 * 9.81 * dist / SP.H_T      # horizontal push at the tilt axis that tips the stand
            ang = math.degrees(math.atan2(dist, c[2]))
            if worst is None or F < worst[0]:
                worst = (F, k, pan, el, ang, m, c)
    F, k, pan, el, ang, m, c = worst
    out.append({"check": "tipping", "name": "push at the phone (tilt-axis height) that tips the stand, worst edge/pose",
                "ok": F >= 3.0, "value": {"force_N": round(F, 2), "edge": k, "pose": [pan, el],
                                          "static_tip_angle_deg": round(ang, 1), "total_mass_g": round(m),
                                          "cg_S": c.round(1).tolist()}, "need": ">= 3 N (a firm screen tap)"})
    return out


# ------------------------------------------------------------------------------------------------- sweeps
def _pts(shape, n=20000, M=np.eye(4)):
    import trimesh
    v, t = shape.tessellate(0.2, 0.3)
    m = trimesh.Trimesh(np.array([tuple(p) for p in v]), np.array(t), process=False)
    p, _ = trimesh.sample.sample_surface(m, n, seed=1)
    p = np.vstack([p, m.vertices])
    return (M[:3, :3] @ p.T).T + M[:3, 3]


def sweep_checks():
    """tilt group (cradle, jaws fully open, a 85 x 170 x 9.5 phone) vs the yoke over a full turn; pan group vs the
    static parts over the pan range (above the turntable)."""
    from scipy.spatial import cKDTree

    from .assembly import group_to_S, inst_shape, instances, shape
    out = []
    # worst-case tilt content: open jaws, biggest phone as a box
    import time
    t0 = time.time()
    I = instances(w=2 * SP.JAW_OPEN)
    # designed contacts excluded: the idler screw / washer / spacer ride in the 623ZZ (pan group), the horn sits on
    # the spline. Everything else must clear the yoke, the tilt servo case and the bearing.
    skip_t = {"idler_screw", "idler_washer", "idler_spacer"}
    skip_p = {"tilt_servo_S"}
    tilt_sel = [i for i in I if i.group in ("tilt", "clamp") and i.name not in skip_t]
    tilt_pts = [_pts(inst_shape(i), 1500, i.M) for i in tilt_sel]
    labels = sum(([i.name] * len(p) for i, p in zip(tilt_sel, tilt_pts)), [])
    big = G.box(-SP.PHONE_W[1] / 2, SP.PHONE_W[1] / 2, SP.PHONE_BACK_Y - SP.PHONE_T_MAX, SP.PHONE_BACK_Y,
                -SP.PHONE_L_MAX / 2, SP.PHONE_L_MAX / 2)
    tilt_pts.append(_pts(big, 12000))
    labels += ["phone 85 x 170 x 9.5"] * len(tilt_pts[-1])
    TP = np.vstack(tilt_pts)                                   # T frame
    lab = np.array(labels)
    yoke_pts = []
    for i in I:
        if i.group == "pan" and i.name not in skip_p:
            M = G.trans(0, 0, SP.Z_TH - SP.H_T) @ i.M          # P -> T (el = 0)
            yoke_pts.append(_pts(inst_shape(i), 25000 if i.pn == "PS-120" else 2000, M))
    YT = cKDTree(np.vstack(yoke_pts))
    print(f"  sweep: sampled in {time.time() - t0:.0f} s", flush=True)
    dmin, at, who = 1e9, None, None
    per = {}
    for el in np.arange(-180, 180, 6.0):
        R = G.rot_x(el)[:3, :3]
        d, _ = YT.query((R @ TP.T).T, workers=-1)
        k = int(np.argmin(d))
        if d[k] < dmin:
            dmin, at, who = float(d[k]), float(el), labels[k]
        for nm in ("phone 85 x 170 x 9.5", "jaw_l", "jaw_r", "cradle"):
            sel = lab == nm
            if sel.any():
                per[nm] = min(per.get(nm, 1e9), float(d[sel].min()))
    out.append({"check": "sweep", "name": "tilt group (open jaws, 85 x 170 x 9.5 phone) vs yoke + tilt servo, full turn "
                "(min gap mm; the horn/servo-tower gap is 0.7 by design)",
                "ok": dmin >= 0.5, "value": round(dmin, 2), "at_el": at, "closest": who,
                "per_part": {k: round(v, 1) for k, v in per.items()}, "need": ">= 0.5 (sampled surfaces)"})
    # pan: pan + tilt content vs the static stack, above the retainer
    stat = [_pts(inst_shape(i), 4000, i.M) for i in I if i.group == "base"]
    SPt = np.vstack(stat)
    SPt = SPt[SPt[:, 2] > SP.Z_TH + SP.H_RIM + SP.RET_T + 0.5]
    ST = cKDTree(SPt)
    dmin2, at2 = 1e9, None
    pan_pts = []
    for i in I:
        if i.group == "pan" and i.pn != "PS-120":
            pan_pts.append((i, _pts(inst_shape(i), 1500, i.M)))
    yk = _pts(shape("PS-120"), 20000, G.trans(0, 0, SP.H_T - SP.Z_TH))
    yk = yk[yk[:, 2] > -SP.Z_TH + SP.Z_TH + SP.H_RIM + SP.RET_T - 0.0]       # above the retainer top (P frame)
    for pan in np.arange(-90, 91, 15.0):
        Mp = group_to_S("pan", pan)
        pts = [yk] + [p for _, p in pan_pts]
        A = np.vstack(pts)
        A = (Mp[:3, :3] @ A.T).T + Mp[:3, 3]
        for el in (-90.0, 0.0, 40.0):
            Mt = group_to_S("tilt", pan, el)
            B = (Mt[:3, :3] @ TP.T).T + Mt[:3, 3]
            d, _ = ST.query(np.vstack([A, B]), workers=-1, distance_upper_bound=50.0)
            k = float(d.min())
            if k < dmin2:
                dmin2, at2 = k, (float(pan), el)
    out.append({"check": "sweep", "name": "pan + tilt groups vs base/post/housing above the retainer, pan +-90 (min gap mm)",
                "ok": dmin2 >= 0.8, "value": round(dmin2, 2), "at": at2, "need": ">= 0.8"})
    return out


# ------------------------------------------------------------------------------------------------- print check
def print_checks():
    import trimesh

    from .parts import MAKERS, PRINT
    out = []
    lim = -math.sin(math.radians(PRINT_MAX_DEG + PRINT_TESS_DEG))
    for pn, fn in MAKERS.items():
        s = fn()
        v, t = s.tessellate(0.01, 0.05)
        m = trimesh.Trimesh(np.array([tuple(p) for p in v]), np.array(t), process=True)
        m.apply_transform(PRINT[pn])
        z0 = m.bounds[0][2]
        n = m.face_normals
        bed = np.all(np.abs(m.vertices[m.faces][:, :, 2] - z0) < 0.05, axis=1)
        sel = (n[:, 2] < lim) & ~bed
        area = float(m.area_faces[sel].sum())
        where = m.triangles_center[sel][np.argmax(m.area_faces[sel])].round(1).tolist() if sel.any() else None
        ext = (m.bounds[1] - m.bounds[0]).round(1).tolist()
        out.append({"check": "print", "name": f"{pn}: overhang needing support (mm2, print frame)",
                    "ok": area <= PRINT_TOL_MM2, "value": round(area, 2), "need": f"<= {PRINT_TOL_MM2}",
                    "where": where, "print_extent_mm": ext, "fits_220": bool(max(ext[:2]) <= 215 and ext[2] <= 245)})
    return out


# ------------------------------------------------------------------------------------------------- arms / CNC
def layout_checks():
    out = []
    # SO-101 pan axes at twin (+-250, -300): S (-+250, -75). Reach ~ 0.35 m from the shoulder.
    for nm, X in (("so_a", 250.0), ("so_b", -250.0)):
        p = np.array([-X, -300.0 - SP.STAND_Z])
        d = float(np.linalg.norm(p))
        out.append({"check": "layout", "name": f"{nm} pan axis -> stand axis (mm); base footprint edge",
                    "ok": d - 95.0 > 150.0, "value": {"axis_to_axis": round(d, 1),
                                                     "to_phone_sweep_r95": round(d - 95.0, 1),
                                                     "to_base_corner": round(float(np.linalg.norm(p - np.array([np.sign(p[0]) * SP.BASE_X, SP.BASE_Y[0]]))), 1)},
                    "need": "> 150 mm clear of the phone sweep (arm bases ~ 60 mm r)"})
    gap = SP.CNC_FRONT_PLATE_Y[0] - SP.RAMP_Y
    out.append({"check": "layout", "name": "base ramp end -> 3018 front end plate (mm); tongue top under its bottom edge",
                "ok": gap >= 1.0 and SP.TONGUE["top"] <= SP.CNC_FRONT_PLATE_Z0 - 2.0,
                "value": {"ramp_to_plate_y": gap, "tongue_top": SP.TONGUE["top"], "plate_bottom": SP.CNC_FRONT_PLATE_Z0},
                "need": ">= 1 mm, tongue >= 2 mm under the plate"})
    return out
