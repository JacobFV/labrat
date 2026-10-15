"""Build the head: manufacturing exports, BOM, twin meshes and the head-specific checks.

    python -m syrhead.build                 # everything (exports + checks), pure build123d / trimesh
    python -m syrhead.build --no-checks
    python -m syrhead.build --checks-only --poses 41
    python -m syrhead.build --mf-checks --render [--quality draft]
                                            # + microfab checks / Cycles renders, run with the microfab venv; skipped
                                            #   with a note when ~/Documents/microfab_3d_atlas_bundle (or
                                            #   $MICROFAB_BUNDLE) is not there

Outputs (hardware/tool-head/):
    step/<PN>.step, step/syringe_head_assembly.step   editable solids (make parts) + the assembly at pos 0
    stl/<PN>.stl                                       FDM parts in print orientation
    plate_fdm_<n>.3mf                                  FDM parts except the body, packed onto 220 x 220 plates
    bom.csv                                            purchased + make parts, rolled up by product
    twin/*.stl + twin/rig.json                         meshes per motion group / finish for hardware/cnc3018 + the web twin
    tools.json                                         tool offsets (syringe, microscope, probe) for server/deck_cal
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
from build123d import Compound, export_step

from .assembly import SRC_HASH, build, kin
from .parts import PRINT_ORIENT
from .spec import LAY, S

HERE = Path(__file__).resolve().parent.parent


def tess(shape, tol=0.03, ang=0.15):
    v, t = shape.tessellate(tol, ang)
    return trimesh.Trimesh(np.array([(p.X, p.Y, p.Z) for p in v]), np.array(t), process=True)


def exports():
    H = build()
    (HERE / "step").mkdir(exist_ok=True)
    (HERE / "stl").mkdir(exist_ok=True)
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
            if pn in PRINT_ORIENT:
                ax, deg = PRINT_ORIENT[pn]
                m.apply_transform(trimesh.transformations.rotation_matrix(math.radians(deg), ax))
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
    for f in HERE.glob("plate_fdm*.3mf"):
        f.unlink()
    for k, sc in enumerate(_pack(plate), 1):
        sc.export(HERE / f"plate_fdm_{k}.3mf")
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
    tools_json()


def tools_json():
    """tool points (relative to the syringe tip end) + rail index positions -> tools.json, for the server's deck_cal
    tool offsets and the bridge."""
    from . import tools as TL
    out = {"frame": "offset = tool point - syringe tip end, machine axes (mm); spindle = target - offset",
           "rail_index_x": TL.RAIL_INDEX, "tools": {}}
    for k, t in TL.TOOLS.items():
        out["tools"][k] = {**t, "offset": [round(v, 3) for v in t["offset"]]}
    (HERE / "tools.json").write_text(json.dumps(out, indent=1))


def _pack(scene: trimesh.Scene, bed=215.0, gap=6.0):
    """shelf-pack the parts (largest first) onto as many bed-sized plates as needed; returns the plates."""
    plates = [trimesh.Scene()]
    x = y = row_h = 0.0
    items = sorted(scene.geometry.items(), key=lambda kv: -kv[1].extents[1])
    for name, g in items:
        g = g.copy()
        g.apply_translation(-g.bounds[0])
        w, h = g.extents[0], g.extents[1]
        if w > bed or h > bed:
            raise SystemExit(f"{name}: {w:.0f} x {h:.0f} does not fit a {bed} mm bed")
        if x + w > bed:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        if y + h > bed:
            plates.append(trimesh.Scene())
            x = y = row_h = 0.0
        g.apply_translation([x, y, 0])
        plates[-1].add_geometry(g, geom_name=name)
        x += w + gap
        row_h = max(row_h, h)
    return plates


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
            if pn == "SYR-240A":
                name = "TowerPro SG90 positional micro servo (case, spline, horn, lead) - probe arm"
            if pn == "SYR-250A":
                name = "subminiature hinge-lever microswitch (D2F-01L class / KW10) - syr_home"
            w.writerow([pn, round(rows[pn]["qty"], 2), rows[pn].get("unit", "ea"), name, d.kind, d.process, d.material, d.vendor, d.vendor_pn,
                        d.cost if d.cost is not None else "", d.notes])


def print_up(pn, loc):
    """unit print direction (the print's +Z) of an FDM part instance, in the head frame."""
    v = np.array([0.0, 0.0, 1.0])
    if pn in PRINT_ORIENT:
        ax, deg = PRINT_ORIENT[pn]
        R = trimesh.transformations.rotation_matrix(math.radians(deg), ax)[:3, :3]
        v = R.T @ v                                         # print frame -> part frame
    from build123d import Location
    w = (loc * Location(tuple(v))).position - loc.position
    w = np.array([w.X, w.Y, w.Z])
    return (w / np.linalg.norm(w)).round(6).tolist()


def twin(H, shapes):
    """meshes per (motion group, finish) in the head frame + the rig (pivots, angle per mm) for cnc3018.py / twin.js.
    FDM parts get a mesh each (with their print direction ``print_up``, head frame) so the twin can draw layer lines."""
    from . import probe as PR
    tw = HERE / "twin"
    tw.mkdir(exist_ok=True)
    for f in tw.glob("*.stl"):
        f.unlink()
    acc = {}
    files = []
    for i in H.insts:
        d = H.parts[i.pn]
        if d.make is None or d.category == "fastener" and d.kind == "buy":
            continue
        fin = d.finish or d.material
        grp = "tip" if i.pn == "SYR-202" else i.group
        m = tess(shapes[i.pn].moved(i.loc), 0.05, 0.25)
        if d.process == "fdm_print":
            fn = f"{grp}__{fin}__{i.name}.stl"
            m.export(tw / fn)
            files.append({"group": grp, "finish": fin, "file": fn, "part": i.pn, "inst": i.name,
                          "print_up": print_up(i.pn, i.loc)})
            continue
        acc.setdefault((grp, fin), []).append(m)
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
                      **{g: {"type": "rotate", "axis": [1, 0, 0], "pivot": [0.0, c[0], c[1]], "deg_per_mm": k1[g]}
                         for g, c in (("gear_a", LAY.A), ("gear_b", LAY.B), ("gear_c", LAY.C))},
                      "probe": {"type": "rotate", "axis": [-1, 0, 0], "pivot": [PR.EX, PR.PIVOT[0], PR.PIVOT[1]],
                                "note": "angle phi = servo deg - stop_deg (0 = deployed, modelled) .. swing_deg; "
                                        "right-handed about axis",
                                "stop_deg": PR.STOP_DEG, "deployed_deg": PR.DEPLOYED_DEG,
                                "retracted_deg": PR.RETRACTED_DEG, "swing_deg": PR.SWING, "modelled_deg": PR.STOP_DEG,
                                "servo_ch": 1}},
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


def microfab_python():
    """the microfab bundle's venv python, or None when the bundle is not on this machine."""
    import os
    root = Path(os.environ.get("MICROFAB_BUNDLE", Path.home() / "Documents" / "microfab_3d_atlas_bundle"))
    py = root / ".venv" / "bin" / "python"
    return py if py.exists() else None


def run_microfab(module, args=()):
    import subprocess
    py = microfab_python()
    if py is None:
        print(f"skip {module}: microfab bundle not found (set MICROFAB_BUNDLE) - the pure build123d outputs and "
              "checks above are complete without it")
        return 0
    print(f"run {module} with {py}", flush=True)
    return subprocess.call([str(py), "-m", f"syrhead.{module}", *args], cwd=str(HERE))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-checks", action="store_true")
    ap.add_argument("--checks-only", action="store_true")
    ap.add_argument("--poses", type=int, default=25)
    ap.add_argument("--mf-checks", action="store_true", help="also run syrhead.check_mf (optional: microfab)")
    ap.add_argument("--render", action="store_true", help="also run syrhead.render (optional: microfab + Blender)")
    ap.add_argument("--quality", default="final")
    a = ap.parse_args(argv)
    if not a.checks_only:
        exports()
    if not a.no_checks:
        checks(a.poses)
    rc = 0
    if a.mf_checks:
        rc |= run_microfab("check_mf")
    if a.render:
        rc |= run_microfab("render", ("--quality", a.quality))
    if rc:
        raise SystemExit(rc)


if __name__ == "__main__":
    main()
