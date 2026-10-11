"""Build everything: parts -> STL/STEP, assemblies -> STEP, checks, renders, README tables.

    cd hardware/cad && uv run --python 3.12 --with build123d --with trimesh --with matplotlib \
        --with numpy python build.py [--no-render] [--no-interference]

Exit code is non-zero if any check fails.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build123d import Compound, Rot, Pos, export_step, export_stl  # noqa: E402

from tappet_cad import parts as P  # noqa: E402
from tappet_cad.assembly import full_assembly, fk  # noqa: E402
from tappet_cad import checks  # noqa: E402
from tappet_cad.params import HOME, JOINTS, PRINT_BED  # noqa: E402

STL = HERE / "stl"
STEP = HERE / "step"
RENDERS = HERE / "renders"
README = HERE / "README.md"


def log(*a):
    print(*a, flush=True)


def print_orient(shape, rot):
    s = Rot(*rot) * shape if any(rot) else shape
    bb = s.bounding_box()
    return Pos(-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * s


def export_parts():
    items = []
    for name, shape, meta, qty in P.export_items():
        s = print_orient(shape, meta["orient"])
        export_stl(s, str(STL / f"{name}.stl"), tolerance=0.03, angular_tolerance=0.2)
        export_step(s, str(STEP / f"{name}.step"))
        items.append((name, s, meta, qty))
        log(f"  exported {name}")
    return items


def to_compound(items, label):
    children = []
    for it in items:
        sh = it.shape
        sh.label = it.name
        try:
            from build123d import Color
            sh.color = Color(it.color)
        except Exception:
            pass
        children.append(sh)
    c = Compound(children=children)
    c.label = label
    return c


def renders(home_items, exploded_items, export_items):
    from tappet_cad.render import render, tess, tess_item
    RENDERS.mkdir(exist_ok=True)
    log("tessellating assembly ...")
    mesh_home_by_item = [tess_item(it) for it in home_items]
    mesh_home = [m for ms in mesh_home_by_item for m in ms]
    q = ", ".join(f"{k}={v:g}" for k, v in HOME.items())
    sub = "home pose  |  " + q
    views = [
        ("assembly_iso", 22, 55, "Tappet - assembly (home pose)"),
        ("assembly_iso_rear", 25, -130, "Tappet - assembly, rear 3/4 (USB-C ports)"),
        ("assembly_front", 0, 90, "Tappet - front view (from the user, +y)"),
        ("assembly_side", 0, 0, "Tappet - side view (from +x)"),
        ("assembly_top", 90, -90, "Tappet - top view"),
    ]
    for name, el, az, title in views:
        t = time.time()
        render(mesh_home, RENDERS / f"{name}.png", elev=el, azim=az, title=title, subtitle=sub, max_edge=12,
               zoom=1.2)
        log(f"  render {name} ({time.time() - t:.0f}s)")
    mesh_ex = [m for it in exploded_items for m in tess_item(it)]
    render(mesh_ex, RENDERS / "assembly_exploded.png", elev=20, azim=55, title="Tappet - exploded assembly",
           subtitle="arms exploded along the shoulder axis; lid down, tray forward, neck up", max_edge=12, zoom=1.3)
    log("  render assembly_exploded")
    # one arm close-up
    arm = [m for ms, it in zip(mesh_home_by_item, home_items) if it.group == "arm_L" for m in ms]
    render(arm, RENDERS / "arm_L_closeup.png", elev=12, azim=-25, title="Left arm - parallel linkage + wrist parallelograms",
           subtitle="shoulder & elbow MG90S coaxial on the turret; stylus kept vertical", max_edge=8, zoom=1.0)
    for name, shape, meta, qty in export_items:
        V, F = tess(shape, 0.08, 0.25)
        bb = shape.bounding_box()
        sub = f"print orientation  |  {bb.size.X:.1f} x {bb.size.Y:.1f} x {bb.size.Z:.1f} mm  |  qty {qty}  |  {meta['material']}"
        render([(V, F, meta["color"])], RENDERS / f"part_{name}.png", elev=30, azim=-55, title=name, subtitle=sub,
               size=(7, 6), dpi=110, max_edge=6, zoom=1.05)
        log(f"  render part_{name}")


def md_table(rows, cols):
    out = ["| " + " | ".join(c for c, _ in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(f(r)) for _, f in cols) + " |")
    return "\n".join(out)


def update_readme(report):
    if not README.exists():
        return
    pr = report["printability"]["rows"]
    metas = {n: P.meta(n) for n in P.NAMES}

    def base_name(n):
        return re.sub(r"_(L|R)$", "", n)

    total_g = sum(r["mass_g"] * r["qty"] for r in pr)
    # print time estimate: ~11 g/h effective + 8 min per plate start
    total_h = total_g / 11.0 + 0.13 * sum(r["qty"] for r in pr) / 3
    table = md_table(pr, [
        ("part (STL)", lambda r: f"`{r['part']}`"),
        ("qty", lambda r: r["qty"]),
        ("material", lambda r: r["material"]),
        ("bbox as printed (mm)", lambda r: " x ".join(f"{v:g}" for v in r["bbox"])),
        ("volume cm3", lambda r: r["volume_cm3"]),
        ("est. mass g (each)", lambda r: r["mass_g"]),
        ("orientation / settings", lambda r: f"{metas[base_name(r['part'])]['layer']} mm, "
                                             f"{metas[base_name(r['part'])]['walls']} walls, "
                                             f"{metas[base_name(r['part'])]['infill']}% infill. "
                                             f"{metas[base_name(r['part'])]['notes']}"),
    ])
    table += f"\n\n**Total printed mass (all quantities): ~{total_g:.0f} g PLA**  \n" \
             f"**Rough total print time: ~{total_h:.0f} h** (0.2 mm layers on a typical 0.4 mm-nozzle bed-slinger; " \
             f"~11 g/h effective + plate changes; roughly halve on a CoreXY at 'fast' profiles)."
    chk = report
    kin = "\n".join(f"| {r['pose']} | {r['arm']} | {', '.join(f'{v:.2f}' for v in r['q'])} | "
                    f"{', '.join(f'{v:.2f}' for v in r['fk'])} | {', '.join(f'{v:.2f}' for v in r['cad'])} | "
                    f"{r['err_mm']:.4f} | {'PASS' if r['ok'] else 'FAIL'} |" for r in chk["kinematics"]["rows"])
    kin = "| pose | arm | q (yaw, th1, th2) | FK (SPEC §3) | CAD tip | err mm | |\n|---|---|---|---|---|---|---|\n" + kin
    summary = (f"- Kinematics (CAD tip vs SPEC FK, tol 1 mm): **{'PASS' if chk['kinematics']['ok'] else 'FAIL'}**\n"
               f"- Pin-joint coincidence (hole centres of mating links, tol 0.02 mm): **{'PASS' if chk['joints']['ok'] else 'FAIL'}**\n"
               f"- Printability (valid single solid, fits {PRINT_BED[0]:g}x{PRINT_BED[1]:g}x{PRINT_BED[2]:g}): "
               f"**{'PASS' if chk['printability']['ok'] else 'FAIL'}**\n"
               f"- Home-pose interference ({chk['interference'].get('tested', 0)} overlapping-bbox pairs boolean-tested): "
               f"**{'PASS' if chk['interference']['ok'] else 'FAIL'}**"
               + ("".join(f"\n  - {h['a']} x {h['b']}: {h['volume_mm3']} mm3" for h in chk['interference'].get('hits', [])))
               + f"\n- Tap-pose interference (each arm tapping tray centre / corners, other arm parked; "
                 f"{chk.get('interference_tap', {}).get('tested', 0)} pairs): "
                 f"**{'PASS' if chk.get('interference_tap', {}).get('ok', True) else 'FAIL'}**")
    text = README.read_text()
    for tag, body in (("PARTS_TABLE", table), ("CHECKS", summary + "\n\n" + kin)):
        text = re.sub(rf"<!-- {tag}_START -->.*?<!-- {tag}_END -->",
                      f"<!-- {tag}_START -->\n{body}\n<!-- {tag}_END -->", text, flags=re.S)
    README.write_text(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--no-interference", action="store_true")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()
    for d in (STL, STEP, RENDERS):
        d.mkdir(exist_ok=True)
    t0 = time.time()
    report = {}

    log("== parts")
    items = list(P.export_items())
    if not args.no_export:
        oriented = export_parts()
    else:
        oriented = [(n, print_orient(s, m["orient"]), m, q) for n, s, m, q in items]

    log("== checks: printability")
    ok_p, rows_p = checks.printability(oriented)
    report["printability"] = dict(ok=ok_p, rows=rows_p)
    for r in rows_p:
        if not r["ok"]:
            log("  FAIL", r)

    log("== checks: kinematics (CAD stylus tip vs SPEC §3 FK)")
    ok_k, rows_k = checks.kinematic_check()
    report["kinematics"] = dict(ok=ok_k, rows=rows_k)
    for r in rows_k:
        log(f"  {r['pose'][:34]:34s} {r['arm']} fk={r['fk']} cad={r['cad']} err={r['err_mm']:.4f} {'ok' if r['ok'] else 'FAIL'}")

    log("== checks: pin joints")
    ok_j, rows_j = checks.joint_check()
    report["joints"] = dict(ok=ok_j, rows=rows_j)
    log(f"  {sum(r['ok'] for r in rows_j)}/{len(rows_j)} joint checks ok")
    for r in rows_j:
        if not r["ok"]:
            log("  FAIL", r)

    log("== assembly")
    home = full_assembly(HOME)
    exploded = full_assembly(HOME, explode=True)
    if not args.no_export:
        export_step(to_compound(home, "tappet_home"), str(STEP / "assembly.step"))
        export_step(to_compound(exploded, "tappet_exploded"), str(STEP / "assembly_exploded.step"))
        log("  assembly.step, assembly_exploded.step written")

    if not args.no_interference:
        log("== checks: home-pose interference")
        ok_i, hits, tested = checks.interference(home)
        report["interference"] = dict(ok=ok_i, hits=hits, tested=tested)
        log(f"  tested {tested} pairs, {len(hits)} intersections")
        for h in hits:
            log("  HIT", h)
        # extra: each arm tapping the tray centre / a far corner (other arm parked) vs body, tray, phone
        import json as _j
        from tappet_cad.params import PARK, REPO
        tap_hits, tap_tested = [], 0
        for arm, xyz in (("L", (0, 160, 13.8)), ("L", (-30, 95, 13.8)), ("R", (30, 225, 13.8)), ("R", (30, 95, 13.8))):
            v = next(v for v in _j.loads((REPO / "config" / "kinematics_vectors.json").read_text())["vectors"]
                     if v["arm"] == arm and tuple(v["xyz"]) == xyz)
            q = dict(PARK)
            q["NECK_TILT"] = JOINTS["NECK_TILT"]["home"]
            q[f"{arm}_YAW"], q[f"{arm}_SHOULDER"], q[f"{arm}_ELBOW"] = v["q"]
            its = full_assembly(q)
            ok_t, hits_t, n_t = checks.interference(its)
            tap_tested += n_t
            tap_hits += [dict(h, pose=f"{arm} tap {xyz}") for h in hits_t]
        log(f"  tap poses: tested {tap_tested} pairs, {len(tap_hits)} intersections")
        for h in tap_hits:
            log("  HIT", h)
        report["interference_tap"] = dict(ok=not tap_hits, hits=tap_hits, tested=tap_tested)
    else:
        report["interference"] = dict(ok=True, hits=[], tested=0, skipped=True)
        report["interference_tap"] = dict(ok=True, hits=[], tested=0, skipped=True)

    (HERE / "build_report.json").write_text(json.dumps(report, indent=1, default=float))
    update_readme(report)

    if not args.no_render:
        log("== renders")
        renders(home, exploded, oriented)

    ok = all(report[k]["ok"] for k in ("printability", "kinematics", "joints", "interference", "interference_tap"))
    sz = sum(f.stat().st_size for d in (STL, STEP) for f in d.glob("*")) / 1e6
    log(f"== done in {time.time() - t0:.0f}s, STL+STEP = {sz:.1f} MB, checks {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
