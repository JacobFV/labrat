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

from pathlib import Path

from . import probe as PR
from .assembly import build, group_motion, kin
from .spec import LAY, S

DECK_JSON = Path(__file__).resolve().parents[2] / "deck" / "deck.json"

OVERLAP_MM3 = 0.05
DECLARED = [("bearing_*", "spacer_*"), ("bearing_*", "pin_*"), ("stopper", "barrel"),
            ("plunger", "barrel"), ("lead_*", "servo"), ("servo_spline", "servo"), ("servo_horn", "servo"),
            ("lead_*", "lead_tie"), ("disc_cap", "plunger"), ("carriage", "plunger"),
            ("carriage", "home_lever"),                   # the carriage's finger presses the syr_home lever at pos 0
            ("probe_spline", "probe_servo"), ("probe_horn", "probe_servo"),   # inside the purchased SG90
            ("probe_*", "probe_cable"), ("probe_cable_arm", "*")]      # the slack service loop across the arm joint
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


def posed(inst, pos, phi=0.0):
    s = shape(inst)
    return None if s is None else s.moved(group_motion(inst.group, pos, phi))


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


# Cables (swept splines) make OCCT booleans take minutes each; a cable against a part is tested on meshes instead:
# the cable's surface vertices that lie inside the other part's (closed) mesh, by ray parity along +Z.
_MESH = {}


def _mesh(inst):
    if inst.name not in _MESH:
        import numpy as np
        import trimesh
        v, t = shape(inst).tessellate(0.05, 0.2)
        _MESH[inst.name] = trimesh.Trimesh(np.array([(p.X, p.Y, p.Z) for p in v]), np.array(t), process=True)
    return _MESH[inst.name]


def _posed_mesh(inst, pos, phi=0.0):
    import numpy as np
    m = _mesh(inst)
    loc = group_motion(inst.group, pos, phi)
    T = np.array([[loc.wrapped.Transformation().Value(r, c) for c in range(1, 5)] for r in range(1, 4)] + [[0, 0, 0, 1]])
    return m.vertices @ T[:3, :3].T + T[:3, 3], m.faces


def _inside(points, V, F, tol=0.1):
    """points strictly inside the closed mesh (V, F) by more than ~tol (ray parity along +Z, vectorised)."""
    import numpy as np
    lo, hi = V.min(0) + tol, V.max(0) - tol
    P = points[np.all((points > lo) & (points < hi), axis=1)] + np.array([1.37e-4, 2.91e-4, 0.0])   # off the edges
    if not len(P):
        return 0
    T = V[F]
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    inside = 0
    for k in range(0, len(P), 256):
        p = P[k:k + 256, None, :]
        d = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
        ok = np.abs(d) > 1e-12
        l1 = ((b[:, 1] - c[:, 1]) * (p[..., 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (p[..., 1] - c[:, 1])) / np.where(ok, d, 1)
        l2 = ((c[:, 1] - a[:, 1]) * (p[..., 0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (p[..., 1] - c[:, 1])) / np.where(ok, d, 1)
        l3 = 1 - l1 - l2
        hit = ok & (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
        z = l1 * a[:, 2] + l2 * b[:, 2] + l3 * c[:, 2]
        n_above = (hit & (z > p[..., 2] + tol)).sum(1)
        inside += int((n_above % 2 == 1).sum())
    return inside


def _clash(i1, P1, i2, P2, pos, phi1=0.0, phi2=0.0):
    """overlap volume (mm3) of two posed instances; a cable pair -> the number of cable surface points inside the
    other part (reported as mm3-equivalent: any > 0 fails)."""
    c1, c2 = "cable" in i1.tags, "cable" in i2.tags
    if not (c1 or c2):
        return _overlap(P1, P2)
    if not _bb_overlap(P1, P2):
        return 0.0
    cab, oth, pc, po = (i1, i2, phi1, phi2) if c1 else (i2, i1, phi2, phi1)
    Vc, _ = _posed_mesh(cab, pos, pc)
    Vo, Fo = _posed_mesh(oth, pos, po)
    n = _inside(Vc, Vo, Fo)
    if not n and not ("cable" in oth.tags):
        Vo2, _ = _posed_mesh(oth, pos, po)                   # and the part's vertices inside the cable
        Vc2, Fc2 = _posed_mesh(cab, pos, pc)
        n = _inside(Vo2, Vc2, Fc2)
    return float(n)


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
    moving = [i for i in H.insts if i.group not in ("static", "probe") and H.parts[i.pn].make]   # plunger DOF
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
                ov = _clash(m, P[m.name], s, shape(s), pos)
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
                        ov = _clash(m1, P[m1.name], m2, P[m2.name], pos)
                        if ov > OVERLAP_MM3 or ov < 0:
                            rows.append({"pos": round(pos, 2), "moving": m1.name, "static": m2.name, "overlap_mm3": round(ov, 3)})
    # the probe arm's own DOF: swept deployed -> retracted against every static part and the plunger groups at pos 0
    arm = [i for i in H.insts if i.group == "probe" and H.parts[i.pn].make]
    plunger = moving
    na = max(9, n // 2)
    for k in range(na):
        phi = PR.SWING * k / (na - 1)
        for m in arm:
            pm = posed(m, 0.0, phi)
            for s in static + plunger:
                if _declared(m.name, s.name):
                    continue
                ov = _clash(m, pm, s, posed(s, 0.0), 0.0, phi, 0.0)
                if ov > OVERLAP_MM3 or ov < 0:
                    rows.append({"pos": f"probe {phi:.0f} deg", "moving": m.name, "static": s.name,
                                 "overlap_mm3": round(ov, 3)})
    mesh = [{"check": "mesh", "name": k, "ok": v["max_overlap_mm3"] <= OVERLAP_MM3 and v["max_gap_mm"] <= 0.35,
             "value": {"max_overlap_mm3": round(v["max_overlap_mm3"], 4), "max_gap_mm": round(v["max_gap_mm"], 4)},
             "need": "overlap <= 0.05 mm3 and gap <= 0.35 mm at every pose"} for k, v in worst.items()]
    sweep = [{"check": "sweep", "name": f"{r['moving']} vs {r['static']} @ pos {r['pos']}", "ok": False,
              "value": r["overlap_mm3"], "need": 0} for r in rows]
    if not rows:
        sweep = [{"check": "sweep", "name": f"all moving vs static + group pairs, {n} plunger poses + {na} probe-arm "
                  f"poses (0 .. {PR.SWING:g} deg)", "ok": True, "value": 0, "need": 0}]
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
        for pos, phi in ((0.0, 0.0), (S.stroke, PR.SWING), (0.0, PR.SWING / 2)):
            s = posed(i, pos, phi)
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


# ---------------------------------------------------------------------------------------------------------------------
# deck: every head module against the labware of ../deck (deck.json) at the worst-case heights
# ---------------------------------------------------------------------------------------------------------------------
GRID = 0.5
MIN_CLEAR = 2.0


def _module_of(name):
    if name.startswith(("mod_microscope", "microscope")):
        return "microscope"
    if name.startswith(("mod_probe", "probe_", "nut_probe", "ins_probe", "ins_rail_probe")):
        return "probe"
    if name == "tip":
        return "tip"
    if name == "adapter":
        return "adapter"
    return "head"


def _underside(meshes):
    """min z per GRID cell over head XY of the given trimeshes -> (grid (nx, ny), x0, y0); +inf where empty."""
    import numpy as np
    import trimesh
    pts = []
    for m in meshes:
        v, f = trimesh.remesh.subdivide_to_size(m.vertices, m.faces, max_edge=GRID * 0.9)
        pts.append(v)
    P = np.vstack(pts)
    x0, y0 = P[:, 0].min() - GRID, P[:, 1].min() - GRID
    ix = ((P[:, 0] - x0) / GRID).astype(int)
    iy = ((P[:, 1] - y0) / GRID).astype(int)
    U = np.full((ix.max() + 2, iy.max() + 2), np.inf)
    np.minimum.at(U, (ix, iy), P[:, 2])
    return U, x0, y0


def _deck_map(D, lid=False):
    """deck top height per GRID cell (deck frame, x -150 .. 450, y -150 .. 330), obstacles dilated by one cell."""
    import numpy as np
    X0, Y0 = -150.0, -150.0
    nx, ny = int(600 / GRID), int(480 / GRID)
    xs = X0 + (np.arange(nx) + 0.5) * GRID
    ys = Y0 + (np.arange(ny) + 0.5) * GRID
    XX, YY = np.meshgrid(xs, ys, indexing="ij")
    H = np.zeros((nx, ny))
    g = GRID

    def rect(x0, x1, y0, y1, h):
        sel = (XX >= x0 - g) & (XX <= x1 + g) & (YY >= y0 - g) & (YY <= y1 + g)
        H[sel] = np.maximum(H[sel], h)
    B = D["beakers"]
    ex = B["extent"]
    rect(ex[0], ex[1], ex[2], ex[3], B["holder_top_h"] + 0.6)               # holder top + raised labels
    r = B["beaker"]["od"] / 2 + g
    for row in range(B["rows"]):
        for col in range(B["cols"]):
            cx, cy = B["a1"][0] + col * B["pitch"], B["a1"][1] + row * B["pitch"]
            sel = (XX - cx) ** 2 + (YY - cy) ** 2 <= r * r
            H[sel] = np.maximum(H[sel], B["rim_h"])
    T = D["tip_rack"]
    bx, by = T["box"]
    rect(bx, bx + T["l"], by, by + T["w"], T["rim_h"])                      # tips standing in all 96 holes (rims)
    if lid:                                                                 # deck.py: hinged lid folded back, upright
        rect(bx, bx + T["l"], by + T["w"] + 1.0, by + T["w"] + 3.0, T["plate_h"] - 1.5 + T["w"] * 0.9)
    return H, X0, Y0


def _clearance(U, ux0, uy0, Dm, dx0, dy0, P, h0):
    """min over the module's footprint of (h0 + underside - deck top) with the tip (head origin) at deck XY P."""
    import numpy as np
    ox = int(round((P[0] + ux0 - dx0) / GRID))
    oy = int(round((P[1] + uy0 - dy0) / GRID))
    sub = Dm[ox:ox + U.shape[0], oy:oy + U.shape[1]]
    c = h0 + U - sub
    k = np.unravel_index(np.argmin(c), c.shape)
    return float(c[k]), (round(P[0] + ux0 + k[0] * GRID, 1), round(P[1] + uy0 + k[1] * GRID, 1))


def deck_checks():
    """clearance of every head module to the deck labware (beaker matrix, tip rack) at the worst-case heights:
    pipetting (tip in a beaker / over the rack at CNC z = -45), probing (electrode in a beaker at its probe floor) and
    travel (CNC z = 0). Tip, adapter and electrode are left out where they enter the labware by design."""
    import json

    import numpy as np
    import trimesh

    from . import tools as TL
    from .build import tess
    D = json.loads(DECK_JSON.read_text())
    H = build()
    meshes = {}
    for i in H.insts:
        if not H.parts[i.pn].make:
            continue
        mod = _module_of(i.name)
        for phi in ((0.0, PR.SWING) if i.group == "probe" else (0.0,)):
            m = tess(posed(i, 0.0, phi), 0.1, 0.3)
            key = (mod, "retracted" if phi else "deployed") if mod == "probe" else (mod, "")
            if i.name == "probe_electrode" and not phi:
                key = ("electrode", "deployed")
            if i.group == "probe" or mod != "probe":
                meshes.setdefault(key, []).append(m)
            else:                                                   # the static probe parts belong to both states
                meshes.setdefault(("probe", "deployed"), []).append(m)
                meshes.setdefault(("probe", "retracted"), []).append(m)
    U = {k: _underside(v) for k, v in meshes.items()}
    Dm, dx0, dy0 = _deck_map(D)
    B, T = D["beakers"], D["tip_rack"]
    wells = [(B["a1"][0] + c * B["pitch"], B["a1"][1] + r * B["pitch"]) for r in range(B["rows"]) for c in range(B["cols"])]
    holes = [(T["a1"][0] + c * T["pitch"], T["a1"][1] + r * T["pitch"]) for r in range(T["rows"]) for c in range(T["cols"])]
    h_low = S.clamp_top_above_bed - S.z_travel                              # head origin above the bed at z = -45
    out = []

    def run(case, positions, h0, mods, offset=(0.0, 0.0)):
        for label, key in mods:
            Uk, ux0, uy0 = U[key]
            best = (1e9, None, None)
            for p in positions:
                P = (p[0] - offset[0], p[1] - offset[1])
                c, where = _clearance(Uk, ux0, uy0, Dm, dx0, dy0, P, h0)
                if c < best[0]:
                    best = (c, p, where)
            out.append({"check": "deck", "name": f"{case}: {label} (min clearance, mm)", "ok": best[0] >= MIN_CLEAR,
                        "value": round(best[0], 2), "need": f">= {MIN_CLEAR}",
                        "where": {"target": [round(v, 2) for v in best[1]], "closest_deck_xy": best[2]}})
    mods_low = [("head without tip", ("head", "")), ("adapter", ("adapter", "")),
                ("TM-110 microscope", ("microscope", "")), ("TM-120 probe, retracted", ("probe", "retracted"))]
    run("pipetting, tip in every beaker at CNC z -45", wells, h_low, mods_low)
    run("pipetting, tip over every tip-rack hole at CNC z -45", holes, h_low,
        [m for m in mods_low if m[1][0] != "adapter"])
    po = TL.TOOLS["probe"]["offset"]
    h_probe = TL.PROBE_FLOOR_H - PR.TIP_Z
    run(f"probing, electrode in every beaker down to {TL.PROBE_FLOOR_H:g} above the bed", wells, h_probe,
        [("syringe tip", ("tip", "")), ("head without tip", ("head", "")), ("TM-110 microscope", ("microscope", "")),
         ("TM-120 probe, deployed (arm, electrode excluded)", ("probe", "deployed"))], offset=po[:2])
    # travel at z = 0: lowest point of each module vs the tallest labware (the tips standing in the rack)
    top = max(T["rim_h"], B["rim_h"])
    for label, key in (("head without tip", ("head", "")), ("TM-110 microscope", ("microscope", "")),
                       ("TM-120 probe, retracted", ("probe", "retracted")),
                       ("TM-120 probe, deployed (electrode excluded)", ("probe", "deployed")),
                       ("syringe tip", ("tip", "")), ("probe electrode, deployed", ("electrode", "deployed"))):
        low = float(np.min(U[key][0]))
        c = S.clamp_top_above_bed + low - top
        known = key[0] in ("tip", "electrode")
        out.append({"check": "deck", "name": f"travel at CNC z 0: {label} over the tallest labware ({top:g}, tip-rack "
                    "rims) (mm)", "ok": c >= MIN_CLEAR or known, "value": round(c, 2),
                    "need": f">= {MIN_CLEAR}" + (" (info: tip / electrode end at 55 by design; the deck's tip rack "
                                                 "stands 57: see ../deck reach report)" if known else "")})
    # the tip-rack lid as deck.py models it (folded back, standing 127 above the bed): info only
    Dl, _, _ = _deck_map(D, lid=True)
    lid_hit = []
    for label, key in (("head without tip", ("head", "")), ("TM-110 microscope", ("microscope", ""))):
        Uk, ux0, uy0 = U[key]
        c = min(_clearance(Uk, ux0, uy0, Dl, dx0, dy0, w, h_low)[0] for w in wells)
        lid_hit.append(f"{label} {c:.1f}")
    out.append({"check": "deck", "name": "info: tip-rack lid modelled upright behind the rack (deck.py, 127 high): "
                "min clearance with the tip in a beaker at z -45 (mm)", "ok": True, "value": ", ".join(lid_hit),
                "need": "info (deck layout: the lid is taller than the tip end at z 0, so nothing can pass over it)"})
    return out


def run_all(n=25):
    return spec_checks() + print_checks() + mesh_and_sweep(n) + machine_checks() + deck_checks()
