"""Build the head: manufacturing exports, BOM, twin meshes and the head-specific checks.

    python -m syrhead.build                 # everything (exports + checks), pure build123d / trimesh
    python -m syrhead.build --no-checks
    python -m syrhead.build --checks-only --poses 41

Outputs (cad/syringe/):
    step/<PN>.step, step/syringe_head_assembly.step   editable solids (make parts) + the assembly at pos 0
    stl/<PN>.stl                                       FDM parts in print orientation
    dxf/SYR-101.dxf                                    laser-cut cover (outline, holes, slots)
    plate_fdm.3mf                                      small FDM parts on one 220 x 220 plate (the body prints alone)
    bom.csv                                            purchased + make parts, rolled up by product
    twin/*.stl + twin/rig.json                         meshes per motion group / finish for cad/cnc3018 + the web twin
    report.json                                        spec, coupled mesh/sweep and machine checks (fails the build)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
import trimesh
from build123d import Axis, Compound, ExportDXF, export_step

from .assembly import SRC_HASH, build, kin
from .spec import LAY, S

HERE = Path(__file__).resolve().parent.parent
PRINT_ORIENT = {   # part frame -> print bed frame (rotation about X in degrees); body/clamp/gears already print in z
    "SYR-103": 90.0,   # carriage on its side: rack tooth profile in the XY plane
}


def tess(shape, tol=0.03, ang=0.15):
    v, t = shape.tessellate(tol, ang)
    return trimesh.Trimesh(np.array([(p.X, p.Y, p.Z) for p in v]), np.array(t), process=True)


def exports():
    H = build()
    (HERE / "step").mkdir(exist_ok=True)
    (HERE / "stl").mkdir(exist_ok=True)
    (HERE / "dxf").mkdir(exist_ok=True)
    for f in list((HERE / "step").glob("*.step")) + list((HERE / "stl").glob("*.stl")):
        f.unlink()
    shapes = {}
    for pn, d in H.parts.items():
        if d.make is None:
            continue
        shapes[pn] = d.make()
    # make parts: STEP + STL (print orientation for FDM)
    plate = trimesh.Scene()
    x_cursor = 0.0
    for pn, d in H.parts.items():
        if d.kind not in ("make", "stock"):
            continue
        s = shapes[pn]
        export_step(s, str(HERE / "step" / f"{pn}.step"))
        if d.process == "fdm_print":
            m = tess(s, 0.02, 0.1)
            rx = PRINT_ORIENT.get(pn, 0.0)
            if rx:
                m.apply_transform(trimesh.transformations.rotation_matrix(math.radians(rx), [1, 0, 0]))
            m.apply_translation(-np.array([m.bounds[0][0], m.bounds[0][1], m.bounds[0][2]]))
            m.export(HERE / "stl" / f"{pn}.stl")
            if pn != "SYR-100":
                qty = sum(1 for i in H.insts if i.pn == pn)
                for _ in range(qty):
                    mm = m.copy()
                    mm.apply_translation([x_cursor, 0, 0])
                    plate.add_geometry(mm, geom_name=f"{pn}_{_}")
                    x_cursor += m.extents[0] + 6.0
    # wrap the small parts into rows on a 220 x 220 bed
    _pack(plate)
    plate.export(HERE / "plate_fdm.3mf")
    # cover DXF (laser): the part's z = 0 face section
    cov = shapes["SYR-101"]
    back = cov.faces().filter_by(Axis.Z).sort_by(Axis.Z)[0]          # cut profile = the back face (outline + holes)
    dxf = ExportDXF()
    dxf.add_layer("CUT")
    dxf.add_shape(back.wires(), layer="CUT")
    dxf.write(str(HERE / "dxf" / "SYR-101.dxf"))
    # assembly STEP at pos 0 (purchased parts included, library fasteners as simple bodies)
    insts = []
    for i in H.insts:
        d = H.parts[i.pn]
        if d.make is None:
            continue
        s = shapes[i.pn].moved(i.loc)
        s.label = f"{i.name} [{i.pn}]"
        insts.append(s)
    export_step(Compound(children=insts, label="syringe_head"), str(HERE / "step" / "syringe_head_assembly.step"))
    bom(H)
    twin(H, shapes)


def _pack(scene: trimesh.Scene, bed=215.0, gap=6.0):
    x = y = row_h = 0.0
    for name in list(scene.geometry):
        g = scene.geometry[name]
        g.apply_translation(-g.bounds[0])
        w, h = g.extents[0], g.extents[1]
        if x + w > bed:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        g.apply_translation([x, y, 0])
        x += w + gap
        row_h = max(row_h, h)
    if y + row_h > bed:
        raise SystemExit(f"plate_fdm.3mf: parts do not fit a {bed} mm bed")


def bom(H):
    """BOM rolled up by product: sub-parts of a purchased product (label, horn, plunger...) are not ordered."""
    rows = {}
    for i in H.insts:
        d = H.parts[i.pn]
        key = d.product or i.pn
        r = rows.setdefault(key, {"pn": key, "qty": 0, "seen": set()})
        if d.product:
            if i.pn in r["seen"]:
                continue
            r["seen"].add(i.pn)
            r["qty"] = max(r["qty"], 1)
        else:
            r["qty"] += d.qty_per if d.unit == "mm" else 1
            r["unit"] = d.unit
    for b in H.bolts:
        size = b.thread.split("-")[0]
        if b.kind == "shcs":
            pn = f"LIB-SHCS-{size}x{b.length:g}"
            rows.setdefault(pn, {"pn": pn, "qty": 0, "seen": set()})["qty"] += 1
    out = HERE / "bom.csv"
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pn", "qty", "unit", "name", "kind", "process", "material", "vendor", "vendor_pn", "unit_cost_usd", "notes"])
        for pn in sorted(rows):
            d = H.parts[pn]
            name = d.name if not pn.startswith("SYR-200A") else "Feetech FS90R continuous-rotation servo (case, spline, horn, lead)"
            if pn == "SYR-201A":
                name = "12 mL luer-slip syringe (barrel 20.2 OD x 80)"
            if pn == "SYR-211A":
                name = "HIWIN MGN7C carriage"
            w.writerow([pn, round(rows[pn]["qty"], 2), rows[pn].get("unit", "ea"), name, d.kind, d.process, d.material, d.vendor, d.vendor_pn,
                        d.cost if d.cost is not None else "", d.notes])


def twin(H, shapes):
    """meshes per (motion group, finish) in the head frame + the rig (pivots, angle per mm) for cnc3018.py / twin.js."""
    tw = HERE / "twin"
    tw.mkdir(exist_ok=True)
    for f in tw.glob("*.stl"):
        f.unlink()
    acc = {}
    for i in H.insts:
        d = H.parts[i.pn]
        if d.make is None or d.category == "fastener" and d.kind == "buy":
            continue
        fin = d.finish or d.material
        grp = "tip" if i.pn == "SYR-202" else i.group
        acc.setdefault((grp, fin), []).append(tess(shapes[i.pn].moved(i.loc), 0.05, 0.25))
    files = []
    for (grp, fin), ms in acc.items():
        m = trimesh.util.concatenate(ms)
        fn = f"{grp}__{fin}.stl"
        m.export(tw / fn)
        files.append({"group": grp, "finish": fin, "file": fn})
    k1 = kin(1.0)
    rig = {"src": SRC_HASH, "frame": "head frame: machine axes, origin = syringe axis on the clamp top face, mm",
           "stroke_mm": S.stroke, "modelled_pos_mm": 0.0, "ul_per_mm": LAY.ul_per_mm, "ratio": LAY.ratio,
           "mm_per_servo_rad": LAY.mm_per_servo_rad, "tip_end_z": LAY.tip_end_z,
           "groups": {"carriage": {"type": "translate", "axis": [0, 0, 1], "per_mm": 1.0},
                      **{g: {"type": "rotate", "axis": [0, 1, 0], "pivot": [c[0], 0.0, c[1]], "deg_per_mm": k1[g]}
                         for g, c in (("gear_a", LAY.A), ("gear_b", LAY.B), ("gear_c", LAY.C))}},
           "files": files}
    (tw / "rig.json").write_text(json.dumps(rig, indent=1))


def checks(poses):
    from . import kin as K
    rows = K.run_all(poses)
    rep = {"src": SRC_HASH, "ok": all(r["ok"] for r in rows), "checks": rows,
           "layout": {k: (list(v) if isinstance(v, tuple) else v) for k, v in LAY.v.items() if k != "servo"},
           "gear_phases_deg": build().phases}
    (HERE / "report.json").write_text(json.dumps(rep, indent=1, default=float))
    bad = [r for r in rows if not r["ok"]]
    for r in rows:
        print(("ok  " if r["ok"] else "FAIL"), r["check"], "|", r["name"], "|", r["value"])
    if bad:
        raise SystemExit(f"{len(bad)} check(s) failed")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-checks", action="store_true")
    ap.add_argument("--checks-only", action="store_true")
    ap.add_argument("--poses", type=int, default=25)
    a = ap.parse_args(argv)
    if not a.checks_only:
        exports()
    if not a.no_checks:
        checks(a.poses)


if __name__ == "__main__":
    main()
