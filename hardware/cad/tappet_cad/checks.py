"""Design checks: kinematics vs SPEC FK, pin-joint coincidence, printability, interference."""
from __future__ import annotations

import itertools
import json
import math

import numpy as np

from . import parts as P
from .assembly import arm_items, arm_part_poses, fk, solve_arm
from .lib.util import place
from .params import (CRANK_A, JOINTS, LINKAGE, PIN_HOLE, PLA_DENSITY, EFFECTIVE_FILL, PRINT_BED, REPO, L1, L2)


# ---------------------------------------------------------------------------------- kinematics
def cad_tip(items, arm):
    """Stylus tool point measured from the placed CAD tip solid: lowest point of the dome, on its axis."""
    tip = next(i for i in items if i.name == f"stylus_tip_{arm}")
    bb = tip.shape.bounding_box()
    return np.array([(bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2, bb.min.Z])


def kinematic_check(tol=1.0):
    poses = []
    q = {k: v["home"] for k, v in JOINTS.items()}
    poses.append(("home", q, "LR"))
    poses.append(("park", {k: v["park"] for k, v in JOINTS.items()}, "LR"))
    vec = json.loads((REPO / "config" / "kinematics_vectors.json").read_text())["vectors"]
    n = 0
    per_arm = {"L": 0, "R": 0}
    for v in vec:
        if "q" in v and "err" not in v and per_arm[v["arm"]] < 4:
            per_arm[v["arm"]] += 1
            arm = v["arm"]
            qq = dict(q)
            qq[f"{arm}_YAW"], qq[f"{arm}_SHOULDER"], qq[f"{arm}_ELBOW"] = v["q"]
            poses.append((f"vector{n}_{arm}{tuple(v['xyz'])}", qq, arm))
            n += 1
    rows, ok = [], True
    for name, qq, arms in poses:
        for arm in arms:
            psi, t1, t2 = qq[f"{arm}_YAW"], qq[f"{arm}_SHOULDER"], qq[f"{arm}_ELBOW"]
            items = [i for i in arm_items(arm, psi, t1, t2) if i.name.startswith("stylus_tip")]
            got = cad_tip(items, arm)
            ref = fk(arm, psi, t1, t2)
            err = float(np.linalg.norm(got - ref))
            sol = solve_arm(psi if arm == "L" else -psi, t1, t2)
            tilt = sol.angles["stylus_holder"]
            good = err <= tol and abs(tilt) < 0.05
            ok &= good
            rows.append(dict(pose=name, arm=arm, q=[psi, t1, t2], fk=ref.round(3).tolist(), cad=got.round(3).tolist(),
                             err_mm=round(err, 4), stylus_tilt_deg=round(tilt, 4), ok=good))
    return ok, rows


# ---------------------------------------------------------------------------------- pin joints
def _pin_centres(shape, arm_pose):
    """Centres (u, w) of all M3 pin holes of a part placed in the arm frame."""
    placed = place(shape, arm_pose)
    pts = []
    for e in placed.edges():
        try:
            if e.geom_type.name != "CIRCLE" or abs(e.radius - PIN_HOLE / 2) > 1e-3:
                continue
        except Exception:
            continue
        c = e.arc_center
        pts.append((c.X, c.Z))
    uniq = []
    for p in pts:
        if all(math.dist(p, q) > 0.01 for q in uniq):
            uniq.append(p)
    return uniq


JOINT_MAP = {   # joint -> parts that must have a pin hole there
    "E": ["upper_arm", "forearm", "elbow_triangle"],
    "W": ["forearm", "stylus_holder"],
    "Cp": ["elbow_drive_link", "linkage_rod"],
    "Fr": ["forearm", "linkage_rod"],
    "G": ["turret", "wrist_link_a"],
    "Pd": ["wrist_link_a", "elbow_triangle"],
    "Pe": ["elbow_triangle", "wrist_link_b"],
    "Pb": ["wrist_link_b", "stylus_holder"],
}


def joint_check(psi=-35.0, th1=100.0, th2=-20.0, tol=0.02):
    sol = solve_arm(psi, th1, th2)
    poses = arm_part_poses(sol)
    rows, ok = [], True
    for j, names in JOINT_MAP.items():
        target = sol.pts[j]
        for n in names:
            cs = _pin_centres(P.model(n), poses[n])
            d = min(math.dist(c, target) for c in cs) if cs else float("inf")
            good = d <= tol
            ok &= good
            rows.append(dict(joint=j, part=n, dist_mm=round(d, 4), ok=good))
    rel = th1 - th2
    rows.append(dict(joint="linkage rel (theta1-theta2)", part=f"{rel:.1f} in [{LINKAGE['rel_min']},{LINKAGE['rel_max']}]",
                     dist_mm=0.0, ok=LINKAGE["rel_min"] <= rel <= LINKAGE["rel_max"]))
    return ok, rows


# ---------------------------------------------------------------------------------- printability
def printability(export_items):
    rows, ok = [], True
    from .lib.util import bbox_dims
    for name, shape, meta, qty in export_items:
        n_sol = len(shape.solids())
        valid = bool(shape.is_valid)
        dims = sorted(bbox_dims(shape)[:2]) + [bbox_dims(shape)[2]]
        bx, by, bz = bbox_dims(shape)
        fits = (max(bx, by) <= max(PRINT_BED[:2]) and min(bx, by) <= min(PRINT_BED[:2]) and bz <= PRINT_BED[2])
        good = valid and n_sol == 1 and fits
        ok &= good
        v = shape.volume / 1000.0
        mass = v * PLA_DENSITY * EFFECTIVE_FILL
        rows.append(dict(part=name, qty=qty, valid=valid, solids=n_sol, bbox=[round(bx, 1), round(by, 1), round(bz, 1)],
                         fits_bed=fits, volume_cm3=round(v, 2), mass_g=round(mass, 1), material=meta["material"], ok=good))
    return ok, rows


# ---------------------------------------------------------------------------------- interference
def _overlap(b1, b2, eps=0.05):
    return (b1.min.X < b2.max.X - eps and b2.min.X < b1.max.X - eps and b1.min.Y < b2.max.Y - eps and
            b2.min.Y < b1.max.Y - eps and b1.min.Z < b2.max.Z - eps and b2.min.Z < b1.max.Z - eps)


def interference(items, vol_tol=0.5):
    """Pairwise boolean intersection of all assembly items (bbox pre-filtered)."""
    bbs = [(it, it.shape.bounding_box()) for it in items]
    hits, tested = [], 0
    for (a, ba), (b, bb) in itertools.combinations(bbs, 2):
        if not _overlap(ba, bb):
            continue
        tested += 1
        try:
            r = a.shape.intersect(b.shape)
            v = 0.0 if r is None else (sum(s.volume for s in r.solids()) if hasattr(r, "solids") else r.volume)
        except Exception as e:  # pragma: no cover
            v = float("nan")
        if not (v <= vol_tol):
            hits.append(dict(a=a.name, b=b.name, volume_mm3=round(v, 2)))
    return len(hits) == 0, hits, tested
