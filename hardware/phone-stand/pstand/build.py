"""Build the phone stand: exports, BOM, kinematics files, twin model, checks.

    uv run --with build123d --with trimesh python -m pstand.build             # everything
    uv run --with build123d --with trimesh python -m pstand.build --no-checks
    uv run --with build123d --with trimesh python -m pstand.build --checks-only

Outputs (hardware/phone-stand/):
    step/PS-1xx.step, step/phone_stand_assembly.step    printed parts (design frame) + the assembly (pan 0, el 0)
    stl/PS-1xx.stl                                       printed parts in print orientation
    plate_<n>.3mf                                        printed parts packed onto 220 x 220 plates
    bom.csv, stand.json (kinematics + the cloud's cal:stand values), report.json (checks; fails the build)
    ../../apps/console/models/phone_stand/phone_stand.glb + rig.json   twin model (three.js frame, Y up, mm)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import trimesh
from build123d import Compound, export_step

from . import assembly as A
from . import geo as G
from . import kin as K
from . import parts as P
from . import spec as SP
from .glb import write_glb

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent.parent
TWIN = ROOT / "apps" / "console" / "models" / "phone_stand"
SRC_HASH = hashlib.sha1(b"".join(p.read_bytes() for p in sorted((HERE / "pstand").glob("*.py")))).hexdigest()[:10]


def tess(shape, tol=0.05, ang=0.2):
    v, t = shape.tessellate(tol, ang)
    return trimesh.Trimesh(np.array([(p.X, p.Y, p.Z) for p in v]), np.array(t), process=True)


def shaded(m: trimesh.Trimesh, angle=30.0):
    """split vertices along creases sharper than `angle` so flat faces shade flat and round ones smooth."""
    sm = trimesh.graph.smooth_shade(m, angle=math.radians(angle))
    return sm.vertices, sm.vertex_normals, sm.faces


# ------------------------------------------------------------------------------------------------------- exports
def exports():
    for d in ("step", "stl"):
        (HERE / d).mkdir(exist_ok=True)
        for f in (HERE / d).glob("*.*"):
            f.unlink()
    insts = A.instances()
    qty = Counter(i.pn for i in insts)
    plate = []
    for pn, fn in P.MAKERS.items():
        s = A.shape(pn)
        export_step(s, str(HERE / "step" / f"{pn}.step"))
        m = tess(s, 0.02, 0.1)
        m.apply_transform(P.PRINT[pn])
        m.apply_translation(-np.array([(m.bounds[0][0] + m.bounds[1][0]) / 2, (m.bounds[0][1] + m.bounds[1][1]) / 2,
                                       m.bounds[0][2]]))
        m.export(HERE / "stl" / f"{pn}.stl")
        for k in range(qty[pn]):
            plate.append((f"{pn}_{k + 1}", m))
    for f in HERE.glob("plate_*.3mf"):
        f.unlink()
    plates = _pack(plate)
    for k, sc in enumerate(plates, 1):
        sc.export(HERE / f"plate_{k}.3mf")
    # assembly STEP at pan 0, el 0 (phone upright, screen to the operator)
    sol = []
    for i in insts:
        s = A.inst_shape(i).moved(_loc(A.group_to_S(i.group) @ i.M))
        s.label = f"{i.name} [{i.pn}]"
        sol.append(s)
    export_step(Compound(children=sol, label="phone_stand"), str(HERE / "step" / "phone_stand_assembly.step"))
    bom(insts)
    return insts, len(plates)


def _loc(M):
    from .geo import mat_to_loc
    return mat_to_loc(M)


def _pack(items, bed=215.0, gap=6.0):
    """shelf-pack (largest first) onto as many 220 x 220 plates as needed (parts centred, z = 0 on the bed)."""
    plates = [trimesh.Scene()]
    x = y = row_h = 0.0
    items = sorted(items, key=lambda kv: -max(kv[1].extents[0], kv[1].extents[1]) * 1000 - kv[1].extents[1])
    for name, g in items:
        g = g.copy()
        if g.extents[0] < g.extents[1] and g.extents[1] > bed - x:     # stand tall parts upright on the plate
            pass
        g.apply_translation(-g.bounds[0])
        w, h = g.extents[0], g.extents[1]
        if w > bed or h > bed:
            raise SystemExit(f"{name}: {w:.0f} x {h:.0f} does not fit a {bed} mm bed")
        if x + w > bed:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        if y + h > bed:
            plates.append(trimesh.Scene())
            x = y = row_h = 0.0
        g.apply_translation([x + 2.5, y + 2.5, 0])
        plates[-1].add_geometry(g, geom_name=name)
        x += w + gap
        row_h = max(row_h, h)
    return plates


def bom(insts):
    rows = {}
    for i in insts:
        d = A.PARTS[i.pn]
        if d.product:          # sub-parts of a product: count the product once per case / phone body
            if i.pn.endswith("-C") or i.pn.endswith("phone_body"):
                rows.setdefault(d.product, {"qty": 0.0})["qty"] += 1
            continue
        rows.setdefault(i.pn, {"qty": 0.0})["qty"] += 1
    for pn, d in A.PARTS.items():
        if d.qty_extra and pn not in rows:
            rows[pn] = {"qty": d.qty_extra}
    with (HERE / "bom.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pn", "qty", "unit", "name", "kind", "process", "material", "vendor", "vendor_pn", "unit_cost_usd", "notes"])
        for pn in sorted(rows, key=lambda k: (not k.startswith("PS-1"), k)):
            d = A.PARTS[pn]
            w.writerow([pn, round(rows[pn]["qty"], 2), d.unit, d.name, d.kind, d.process, d.material, d.vendor,
                        d.vendor_pn, d.cost if d.cost is not None else "", d.notes])


# ------------------------------------------------------------------------------------------------------- twin
C3 = A.S_TO_THREE[:3, :3]


def _to3(v):
    return (C3 @ np.asarray(v, float)).tolist()


def _mesh3(shape, M=np.eye(4), tol=0.08, ang=0.25):
    m = tess(shape, tol, ang)
    m.apply_transform(A.S_TO_THREE @ M)
    return m


def twin(insts):
    TWIN.mkdir(parents=True, exist_ok=True)
    w = SP.PHONE_NOM["w"]
    nodes = [{"name": "phone_stand", "parent": None, "t": [0, 0, 0],
              "extras": {"src": SRC_HASH, "units": "mm", "frame": "twin bench-top frame at the stand: place at (0,0,-225)"}},
             {"name": "stand_base", "parent": "phone_stand"},
             {"name": "pan", "parent": "phone_stand", "t": [0.0, SP.Z_TH, 0.0]},
             {"name": "tilt", "parent": "pan", "t": [0.0, SP.H_T - SP.Z_TH, 0.0]},
             {"name": "phone", "parent": "tilt"},
             {"name": "clamp", "parent": "tilt"},
             {"name": "clamp_jaw_l", "parent": "clamp", "t": _to3([-w / 2, 0, 0])},
             {"name": "clamp_jaw_r", "parent": "clamp", "t": _to3([w / 2, 0, 0]), "q": [0.0, 0.0, 1.0, 0.0]}]
    hw = {}
    for i in insts:
        d = A.PARTS[i.pn]
        parent = {"base": "stand_base", "pan": "pan", "tilt": "tilt", "clamp": "clamp", "phone": "phone"}[i.group]
        M = i.M
        if i.name in ("jaw_l", "jaw_r"):
            parent, M = f"clamp_{i.name}", np.eye(4)
        if i.group == "pan":
            pass                                  # P frame already (origin on the pan pivot)
        fin = A.finish(i.pn)
        if d.process == "fdm_print":
            m = _mesh3(A.inst_shape(i), M)
            up = _to3((M[:3, :3] @ np.array(P.print_up(i.pn))).tolist())
            nodes.append({"name": f"{i.name}__{i.pn}", "parent": parent, "mesh": (*shaded(m), fin),
                          "extras": {"pn": i.pn, "print_up": [round(v, 6) for v in up], "material": "PETG",
                                     "layer_mm": 0.2}})
        else:
            shp = A.inst_shape(i)
            if i.pn == "PS-203":            # twin: the spring as a plain tube (the coil model is ~100 rings)
                L = float(i.shape_key.split(":")[1])
                shp = G.cyl_x(SP.SPRING["od"] / 2, 0.0, L) - G.cyl_x(SP.SPRING["od"] / 2 - SP.SPRING["wire"], -0.1, L + 0.1)
            hw.setdefault((parent, fin), []).append(_mesh3(shp, M, 0.15, 0.5))
    for (parent, fin), ms in hw.items():
        m = trimesh.util.concatenate(ms)
        nodes.append({"name": f"{parent}__{fin}", "parent": parent, "mesh": (*shaded(m), fin)})
    write_glb(TWIN / "phone_stand.glb", nodes, SP.COLORS)
    ref = K.references()
    rig = {
        "src": SRC_HASH,
        "frame": "three.js, Y up, mm. Node 'phone_stand' is the stand root: put it at the twin bench-top position "
                 "(0, 0, -225). pivot_stand is in the phone_stand frame (informational); each moving node's origin is on its pivot.",
        "place": {"position": [0.0, 0.0, SP.STAND_Z]},
        # twin (apps/console/twin/rigs.js NodeRotor): rotation = sign * (protocol deg - modelled_deg) about the node
        # origin, which sits on the pivot; modelled_deg = -zero because the GLB is modelled at mechanical 0
        "pan": {"node": "pan", "axis": [0.0, 1.0, 0.0], "pivot_stand": [0.0, round(SP.Z_TH, 3), 0.0],
                "modelled_deg": -round(ref["pan0"], 2), "sign": 1,
                "min": round(SP.PAN_SOFT[0] - ref["pan0"], 1), "max": round(SP.PAN_SOFT[1] - ref["pan0"], 1),
                "angle": "node rotation about axis = mechanical pan (deg, CCW from above; 0 = rear camera toward +Z, "
                         "the machine) = protocol pan + zero.pan"},
        "tilt": {"node": "tilt", "axis": [-1.0, 0.0, 0.0], "pivot_stand": [0.0, round(SP.H_T, 3), 0.0],
                 "modelled_deg": -round(ref["el0"], 2), "sign": 1,
                 "min": round(SP.EL_SOFT[0] - ref["el0"], 1), "max": round(SP.EL_SOFT[1] - ref["el0"], 1),
                 "angle": "node rotation about axis = camera elevation el (deg; 0 = phone upright, screen to the "
                          "operator; -90 = rear camera straight down) = protocol tilt + zero.tilt"},
        "zero": {"pan": round(ref["pan0"], 2), "tilt": round(ref["el0"], 2),
                 "note": "protocol aim (0, 0) = rear camera on the deck centre (bed at machine y = 90). The GLB is "
                         "modelled at mechanical pan 0, el 0."},
        "limits": {"pan_mech": list(SP.PAN_SOFT), "el": list(SP.EL_SOFT),
                   "pan": [round(SP.PAN_SOFT[0] - ref["pan0"], 1), round(SP.PAN_SOFT[1] - ref["pan0"], 1)],
                   "tilt": [round(SP.EL_SOFT[0] - ref["el0"], 1), round(SP.EL_SOFT[1] - ref["el0"], 1)]},
        "clamp": {"nodes": ["clamp_jaw_l", "clamp_jaw_r"], "axis": [1.0, 0.0, 0.0],
                  "x_of_width": "clamp_jaw_l.position.x = +w/2, clamp_jaw_r.position.x = -w/2 (three X = -machine x)",
                  "modelled_width": w, "range": list(SP.PHONE_W)},
        "cameras": {"rear": {"node": "tilt", "pos": [round(v, 2) for v in _to3(K.cam_local()[0])], "dir": _to3([0, 1, 0]),
                             "fov_deg": {"across": SP.CAM_FOV[0], "along": SP.CAM_FOV[1]}},
                    "front": {"node": "tilt", "pos": [round(v, 2) for v in _to3(K.cam_local(front=True)[0])],
                              "dir": _to3([0, -1, 0])}},
        "print_up": "glTF extras.print_up on each printed part node: unit print-direction (layer normal) in the node frame",
    }
    (TWIN / "rig.json").write_text(json.dumps(rig, indent=1))
    return ref


# ------------------------------------------------------------------------------------------------------- stand.json
def stand_json(ref):
    p0, el0 = ref["pan0"], ref["el0"]
    pivot_S = np.array([0.0, 0.0, SP.H_T])
    sp, st = K.servo_deg(p0, el0)
    # the cloud driver treats the camera as one fixed point `pivot`: fit the best such point (it sits near the tilt
    # axis) and report its residual pointing error against the exact model over the deck
    err, piv = K.fit_pivot()
    eff = K.S_to_deck(piv)
    op_deck = K.S_to_deck(K.OPERATOR_FACE_S)
    out = {
        "src": SRC_HASH,
        "frames": {
            "S": "stand frame, mm: origin on the bench top on the pan axis; x = machine +x (operator's right), "
                 "y = toward the CNC (machine back), z = up",
            "bench_three": "twin bench-top frame (three.js, Y up): (X, Y, Z) = (-x, z, y - 225)",
            "deck": "cloud deck coords (bed frame, z up from the bed) at bed machine y = 90: "
                    "deck = (x + 150, y - 135, z - 72)",
        },
        "pan": {"channel": 2, "axis_S": [0, 0, 1], "pivot_S": [0.0, 0.0, round(SP.Z_TH, 2)],
                "positive": "counter-clockwise from above (rear camera from the deck toward the operator's left, so_a)",
                "mech_zero": "yoke square to the machine: rear camera azimuth +y (machine back)",
                "servo_deg_at_mech_zero": 90.0, "servo_dir": SP.PAN_DIR,
                "limits_mech_deg": list(SP.PAN_SOFT), "servo_travel_deg": SP.SERVO_RANGE},
        "tilt": {"channel": 3, "axis_P": [1, 0, 0], "pivot_P_from_pan_pivot": [0.0, 0.0, round(SP.H_T - SP.Z_TH, 2)],
                 "pivot_S": [0.0, 0.0, round(SP.H_T, 2)],
                 "angle": "el = rear-camera elevation (deg): 0 = phone upright, screen to the operator; -90 = straight down",
                 "positive": "raises the rear camera (rotation about +x)",
                 "servo_deg_at_el": f"90 + {SP.TILT_DIR} * (el - ({SP.EL_MID}))", "el_mid": SP.EL_MID,
                 "servo_dir": SP.TILT_DIR, "limits_el_deg": list(SP.EL_SOFT)},
        "camera": {"rear_T": [round(v, 2) for v in K.cam_local()[0]], "rear_axis_T": [0, 1, 0],
                   "front_T": [round(v, 2) for v in K.cam_local(front=True)[0]], "front_axis_T": [0, -1, 0],
                   "T": "tilt frame at el = 0: origin on the tilt axis, axes = S at pan 0",
                   "fov_deg": {"across_phone": SP.CAM_FOV[0], "along_phone": SP.CAM_FOV[1]},
                   "note": f"nominal {SP.PHONE_NOM['name']} centred by the clamp: main camera {SP.CAM_NOM[0]} mm right "
                           f"of the phone centre (seen from the screen), {SP.CAM_NOM[1]} mm above it. Measure yours."},
        "reference": {"deck_center_S": ref["deck_center_S"], "pan_mech": round(p0, 2), "el": round(el0, 2),
                      "servo_deg": [round(sp, 1), round(st, 1)],
                      "note": "protocol aim (0, 0) = this pose: pan = pan_mech - pan_mech_ref, tilt = el - el_ref"},
        "operator": {"face_S": ref["operator_S"], "pan_mech": round(ref["operator_pan"], 2),
                     "el": round(ref["operator_el"], 2), "note": "screen (front camera) toward the operator's face"},
        "aim_reference_impl": "pstand/kin.py aim(): fixed-point iteration putting the target on the camera axis "
                              "including the camera offset from both axes",
        "cloud_cal_stand": {
            "pan_ch": 2, "tilt_ch": 3,
            "pan_center_deg": round(sp, 1), "tilt_center_deg": round(st, 1),
            "pan_dir": SP.PAN_DIR, "tilt_dir": SP.TILT_DIR,
            "pan_limits": [round(SP.PAN_SOFT[0] - p0, 1), round(SP.PAN_SOFT[1] - p0, 1)],
            "tilt_limits": [round(SP.EL_SOFT[0] - el0, 1), round(SP.EL_SOFT[1] - el0, 1)],
            "pivot": [round(v, 1) for v in eff.tolist()],
            "deck_center": [150, 90, 0],
            "operator": [round(v, 1) for v in op_deck.tolist()],
            "note": "values for cloud/src/devices/stand.ts (cal:stand). Its model puts the camera at `pivot`; this is "
                    f"the best-fit single point (S {piv.round(1).tolist()}, near the tilt axis): max pointing error {err:.1f} deg "
                    "over the deck plane and the beaker rims. The exact model above (aim_reference_impl) has none."},
        "keepout_S": {"cylinder": {"axis_xy": [0, 0], "r": 100.0, "z": [0.0, round(SP.H_T + 95.0, 1)]},
                      "base_box": {"x": [-SP.BASE_X, SP.BASE_X], "y": [SP.BASE_Y[0], SP.TONGUE["y1"]],
                                   "z": [0.0, SP.BOSS["top"]]},
                      "note": "for the SO-101 planners: the phone sweeps a 95 mm sphere about the tilt axis"},
    }
    (HERE / "stand.json").write_text(json.dumps(out, indent=1))
    return out


# ------------------------------------------------------------------------------------------------------- checks
def checks():
    insts = A.instances()
    rows = []
    fov, ref = K.fov_checks()
    rows += fov
    rows += K.torque_checks(insts)
    rows += K.tipping_checks(insts)
    rows += K.layout_checks()
    rows += K.sweep_checks()
    rows += K.print_checks()
    rows.append({"check": "range", "name": "tilt range covers straight down .. screen facing a standing operator",
                 "ok": SP.EL_SOFT[0] <= -90 and SP.EL_SOFT[1] >= max(0.0, ref["operator_el"]) and
                 abs(SP.EL_SOFT[0] - SP.EL_MID) <= SP.SERVO_RANGE / 2 and abs(SP.EL_SOFT[1] - SP.EL_MID) <= SP.SERVO_RANGE / 2,
                 "value": {"el_soft": SP.EL_SOFT, "el_servo": [SP.EL_MID - 90, SP.EL_MID + 90],
                           "operator_el": round(ref["operator_el"], 1), "deck_el": round(ref["el0"], 1)}})
    rep = {"src": SRC_HASH, "ok": all(r["ok"] for r in rows), "checks": rows,
           "layout": {"H_T": SP.H_T, "Z_TH": SP.Z_TH, "post_L": SP.POST_L}}
    (HERE / "report.json").write_text(json.dumps(rep, indent=1, default=float))
    for r in rows:
        print(("ok  " if r["ok"] else "FAIL"), r["check"], "|", r["name"], "|", r["value"])
    bad = [r for r in rows if not r["ok"]]
    if bad:
        raise SystemExit(f"{len(bad)} check(s) failed")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-checks", action="store_true")
    ap.add_argument("--checks-only", action="store_true")
    a = ap.parse_args(argv)
    if not a.checks_only:
        insts, n = exports()
        ref = twin(insts)
        stand_json(ref)
        print(f"exported: {len(P.MAKERS)} printed parts, {n} plate(s), twin -> {TWIN}")
    if not a.no_checks:
        checks()


if __name__ == "__main__":
    main()
