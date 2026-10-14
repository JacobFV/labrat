"""Head-specific checks that the generic microfab checks cannot do (they sweep one joint at a time):

* spec      - tip height, ratio, stroke volume, rack coverage, contact ratios (analytic, with the backlash centre distance)
* mesh      - the coupled drive train (rack -> G5/G4 -> G3/G2 -> G1) swept over the stroke with exact OCCT booleans:
              every mesh pair must have zero overlap AND stay engaged (minimum tooth distance below the backlash bound)
* sweep     - every moving part against every static part over the stroke (exact booleans after an AABB prefilter),
              except contacts the design declares (bearings on pins / spacers, stopper in the barrel)
* machine   - clearance to the 3018: Z plate (moves with the head), Z stepper (above the head at CNC z = 0), clamp
* print     - every FDM part in its print orientation (parts.PRINT_ORIENT): no face that needs support (downward faces
              flatter than 45 deg, other than the bed face), i.e. the part prints without supports
Pure build123d: runs in CI without microfab.
"""
from __future__ import annotations

import fnmatch
import math

from build123d import Location

from .assembly import build, group_motion, kin
from .spec import LAY, S

OVERLAP_MM3 = 0.05
DECLARED = [("bearing_*", "spacer_*"), ("bearing_*", "pin_*"), ("stopper", "barrel"),
            ("plunger", "barrel"), ("lead_*", "servo"), ("servo_spline", "servo"), ("servo_horn", "servo"),
            ("lead_*", "lead_tie"), ("disc_cap", "plunger"), ("carriage", "plunger")]
MESHES = [("gear_a", "gear_b"), ("gear_b", "gear_c"), ("gear_c", "carriage")]


def _declared(a, b):
    for p, q in DECLARED:
        if (fnmatch.fnmatch(a, p) and fnmatch.fnmatch(b, q)) or (fnmatch.fnmatch(b, p) and fnmatch.fnmatch(a, q)):
            return True
    return False


_SHP = {}


def shape(inst):
    if inst.name not in _SHP:
        H = build()
        _SHP[inst.name] = H.parts[inst.pn].make().moved(inst.loc) if H.parts[inst.pn].make else None
    return _SHP[inst.name]


def posed(inst, pos):
    s = shape(inst)
    return None if s is None else s.moved(group_motion(inst.group, pos))


def _bb_overlap(a, b, pad=0.0):
    A, B = a.bounding_box(), b.bounding_box()
    return (A.min.X - pad <= B.max.X and B.min.X - pad <= A.max.X and A.min.Y - pad <= B.max.Y and B.min.Y - pad <= A.max.Y
            and A.min.Z - pad <= B.max.Z and B.min.Z - pad <= A.max.Z)


def _overlap(a, b):
    if not _bb_overlap(a, b):
        return 0.0
    try:
        return float((a & b).volume)
    except Exception:
        return -1.0


def contact_ratio_ext(m, z1, z2, pa_deg, a):
    pa = math.radians(pa_deg)
    r1, r2 = z1 * m / 2, z2 * m / 2
    rb1, rb2 = r1 * math.cos(pa), r2 * math.cos(pa)
    ra1, ra2 = r1 + m, r2 + m
    aw = math.acos((r1 + r2) * math.cos(pa) / a)
    return (math.sqrt(ra1 ** 2 - rb1 ** 2) + math.sqrt(ra2 ** 2 - rb2 ** 2) - a * math.sin(aw)) / (math.pi * m * math.cos(pa))


def contact_ratio_rack(m, z, pa_deg, extra):
    pa = math.radians(pa_deg)
    r = z * m / 2
    rb, ra = r * math.cos(pa), r + m
    ha = m - extra                                      # rack addendum seen by the pinion (pinion moved away by extra)
    return (math.sqrt(ra ** 2 - rb ** 2) - r * math.sin(pa) + ha / math.sin(pa)) / (math.pi * m * math.cos(pa))


def spec_checks():
    out = []
    L = LAY

    def chk(name, ok, value, need):
        out.append({"check": "spec", "name": name, "ok": bool(ok), "value": value, "need": need})

    chk("tip end above bed at CNC z=0 (mm)", abs((S.clamp_top_above_bed + L.tip_end_z) - S.safe_tip_h) < 1e-6,
        round(S.clamp_top_above_bed + L.tip_end_z, 3), S.safe_tip_h)
    chk("gear ratio servo:pinion", abs(L.ratio - 12.0) < 0.1, round(L.ratio, 3), "12 +- 0.1")
    chk("plunger travel per servo rad (mm)", L.mm_per_servo_rad <= 0.6, round(L.mm_per_servo_rad, 4), "<= 0.6")
    chk("volume per servo degree (uL)", True, round(L.ul_per_mm * L.mm_per_servo_rad * math.pi / 180, 3), "info")
    chk("stroke volume (mL)", S.stroke * L.ul_per_mm / 1000 >= 5, round(S.stroke * L.ul_per_mm / 1000, 2), ">= 5")
    eps = {
        "G1/G2": contact_ratio_ext(S.m1, S.z1, S.z2, S.pa, (S.z1 + S.z2) * S.m1 / 2 + S.backlash_cd),
        "G3/G4": contact_ratio_ext(S.m2, S.z3, S.z4, S.pa, (S.z3 + S.z4) * S.m2 / 2 + S.backlash_cd),
        "G5/rack": contact_ratio_rack(S.m3, S.z5, S.pa, S.backlash_cd),
    }
    for k, e in eps.items():
        chk(f"contact ratio {k}", e >= 1.15, round(e, 3), ">= 1.15")
    # rack teeth cover the pinion over the whole stroke (pinion at zc meets rack heights zc - pos)
    from .parts import rack_tooth_centres
    tc = rack_tooth_centres()
    p = math.pi * S.m3
    need_lo, need_hi = L.zc - S.stroke - 1.5 * p, L.zc + 1.5 * p
    chk("rack teeth cover the stroke (+-1.5 pitch)", min(tc) <= need_lo + p / 2 and max(tc) >= need_hi - p / 2,
        [round(min(tc), 2), round(max(tc), 2)], [round(need_lo, 2), round(need_hi, 2)])
    chk("carriage jaw above the flange clamp at pos 0 (mm)", L.jaw0_bottom_z - L.clampplate_top_z >= 1.0,
        round(L.jaw0_bottom_z - L.clampplate_top_z, 3), ">= 1.0")
    return out


def mesh_and_sweep(n=25):
    H = build()
    moving = [i for i in H.insts if i.group != "static" and H.parts[i.pn].make]
    static = [i for i in H.insts if i.group == "static" and H.parts[i.pn].make]
    groups = {g: [i for i in moving if i.group == g] for g in ("carriage", "gear_a", "gear_b", "gear_c")}
    main = {"carriage": "carriage", "gear_a": "gear_a", "gear_b": "gear_b", "gear_c": "gear_c"}
    rows = []
    worst = {}
    for k in range(n):
        pos = S.stroke * k / (n - 1)
        P = {i.name: posed(i, pos) for i in moving}
        # drive-train meshes: overlap must be ~0 and the teeth must be close (engaged)
        for ga, gb in MESHES:
            a, b = P[main[ga]], P[main[gb]]
            ov = _overlap(a, b)
            d = a.distance_to(b) if ov <= OVERLAP_MM3 else 0.0
            key = f"mesh {ga}/{gb}"
            w = worst.setdefault(key, {"max_overlap_mm3": 0.0, "max_gap_mm": 0.0})
            w["max_overlap_mm3"] = max(w["max_overlap_mm3"], ov)
            w["max_gap_mm"] = max(w["max_gap_mm"], d)
        # moving vs static
        for m in moving:
            for s in static:
                if _declared(m.name, s.name):
                    continue
                ov = _overlap(P[m.name], shape(s))
                if ov > OVERLAP_MM3 or ov < 0:
                    rows.append({"pos": round(pos, 2), "moving": m.name, "static": s.name, "overlap_mm3": round(ov, 3)})
        # moving groups against each other (other than the meshes)
        gl = list(groups)
        for i1 in range(len(gl)):
            for i2 in range(i1 + 1, len(gl)):
                for m1 in groups[gl[i1]]:
                    for m2 in groups[gl[i2]]:
                        if (m1.name, m2.name) in [(main[a], main[b]) for a, b in MESHES] + [(main[b], main[a]) for a, b in MESHES]:
                            continue
                        if _declared(m1.name, m2.name):
                            continue
                        ov = _overlap(P[m1.name], P[m2.name])
                        if ov > OVERLAP_MM3 or ov < 0:
                            rows.append({"pos": round(pos, 2), "moving": m1.name, "static": m2.name, "overlap_mm3": round(ov, 3)})
    mesh = [{"check": "mesh", "name": k, "ok": v["max_overlap_mm3"] <= OVERLAP_MM3 and v["max_gap_mm"] <= 0.35,
             "value": {"max_overlap_mm3": round(v["max_overlap_mm3"], 4), "max_gap_mm": round(v["max_gap_mm"], 4)},
             "need": "overlap <= 0.05 mm3 and gap <= 0.35 mm at every pose"} for k, v in worst.items()]
    sweep = [{"check": "sweep", "name": f"{r['moving']} vs {r['static']} @ pos {r['pos']}", "ok": False,
              "value": r["overlap_mm3"], "need": 0} for r in rows]
    if not rows:
        sweep = [{"check": "sweep", "name": f"all moving vs static + group pairs, {n} poses", "ok": True, "value": 0, "need": 0}]
    return mesh + sweep


def machine_checks():
    """head vs the 3018 parts it can reach (cnc3018.py frame converted to the head frame)."""
    H = build()
    out = []
    worst_y_above = -1e9
    worst_y_plate = -1e9
    for i in H.insts:
        if not H.parts[i.pn].make:
            continue
        for pos in (0.0, S.stroke):
            s = posed(i, pos)
            bb = s.bounding_box()
            if bb.max.Z > S.zstep_bottom_z:
                # only the part of the solid above the stepper bottom matters: slice by bbox of the top region
                worst_y_above = max(worst_y_above, _max_y_above(s, S.zstep_bottom_z))
            if bb.min.Z < 49.0 and bb.max.Z > -43.0:
                worst_y_plate = max(worst_y_plate, bb.max.Y)
    # X envelope: the gantry uprights (cnc3018.py UP_X0 = 170 from the machine centre) limit how far the spindle can go
    xs = [b for i in H.insts if H.parts[i.pn].make for b in [posed(i, 0.0).bounding_box()]]
    xmin, xmax = min(b.min.X for b in xs), max(b.max.X for b in xs)
    lo, hi = max(0.0, 150.0 - 170.0 - xmin), min(300.0, 150.0 + 170.0 - xmax)
    out.append({"check": "machine", "name": "usable spindle X travel next to the gantry uprights (info)", "ok": True,
                "value": [round(lo, 1), round(hi, 1)], "need": "info (0..300 nominal; head x extent "
                f"{xmin:.0f}..{xmax:.0f})"})
    out.append({"check": "machine", "name": "max Y above the Z stepper bottom (z > 68)", "ok": worst_y_above < S.zstep_front_y - 1.0,
                "value": round(worst_y_above, 2), "need": f"< {S.zstep_front_y - 1.0}"})
    out.append({"check": "machine", "name": "max Y beside the Z plate (-43 < z < 49)", "ok": worst_y_plate < S.zplate_front_y - 1.0,
                "value": round(worst_y_plate, 2), "need": f"< {S.zplate_front_y - 1.0}"})
    return out


def _max_y_above(s, z0):
    from build123d import Box, Pos
    bb = s.bounding_box()
    cut = Pos((bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2, (z0 + bb.max.Z) / 2 + 0.5) * Box(
        bb.size.X + 2, bb.size.Y + 2, bb.max.Z - z0 + 1)
    try:
        top = s & cut
        return top.bounding_box().max.Y if top.volume > 0 else -1e9
    except Exception:
        return bb.max.Y


PRINT_MAX_DEG = 45.0              # overhang limit from the horizontal (45 deg chamfers / teardrops / flare pass)
PRINT_TESS_DEG = 3.0              # facets of a tessellated 45 deg cone read up to ~48 deg
PRINT_TOL_MM2 = 2.0


def print_checks():
    import numpy as np
    import trimesh

    from .parts import PRINT_ORIENT
    H = build()
    out = []
    lim = -math.sin(math.radians(PRINT_MAX_DEG + PRINT_TESS_DEG))
    for pn, d in H.parts.items():
        if d.process != "fdm_print":
            continue
        v, t = d.make().tessellate(0.01, 0.04)          # fine: a coarse facet on a 45 deg cone reads steeper
        m = trimesh.Trimesh(np.array([tuple(p) for p in v]), np.array(t), process=True)
        if pn in PRINT_ORIENT:
            ax, deg = PRINT_ORIENT[pn]
            m.apply_transform(trimesh.transformations.rotation_matrix(math.radians(deg), ax))
        z0 = m.bounds[0][2]
        n = m.face_normals
        bed = np.all(np.abs(m.vertices[m.faces][:, :, 2] - z0) < 0.05, axis=1)
        sel = (n[:, 2] < lim) & ~bed
        area = float(m.area_faces[sel].sum())
        where = m.triangles_center[sel][np.argmax(m.area_faces[sel])].round(1).tolist() if sel.any() else None
        out.append({"check": "print", "name": f"{pn} {d.name}: overhang needing support (mm2, print frame)",
                    "ok": area <= PRINT_TOL_MM2, "value": round(area, 2), "need": f"<= {PRINT_TOL_MM2}",
                    "where": where})
    return out


def run_all(n=25):
    return spec_checks() + print_checks() + mesh_and_sweep(n) + machine_checks()
