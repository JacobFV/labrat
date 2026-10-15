"""Cycles stills of the stand: posed GLBs (+ servo leads and the USB-C cable) -> Blender 4.0 (blender_scene.py).

    uv run python -m pstand.render [--draft] [--only name ...]

Writes renders/<name>.png. The bench shots add the 3018 twin model (apps/console/models/cnc3018.glb) in front of which
the stand sits (twin placement: stand axis at three Z = -225).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import trimesh

from . import assembly as A
from . import geo as G
from . import kin as K
from . import parts as P
from . import spec as SP
from . import vendor as V
from .build import shaded, tess
from .glb import write_glb

HERE = Path(__file__).resolve().parent.parent
REN = HERE / "renders"
SCN = REN / "_scene"
ROOT = HERE.parent.parent


def _tube(pts, r=1.2, n=10):
    """smooth tube through control points (Catmull-Rom), S frame."""
    P_ = np.asarray(pts, float)
    P_ = np.vstack([P_[0] * 2 - P_[1], P_, P_[-1] * 2 - P_[-2]])
    out = []
    for k in range(1, len(P_) - 2):
        p0, p1, p2, p3 = P_[k - 1], P_[k], P_[k + 1], P_[k + 2]
        seg = max(2, int(np.linalg.norm(p2 - p1) / 3))
        for t in np.linspace(0, 1, seg, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(P_[-2])
    path = np.array(out)
    ring = [(r * math.cos(a), r * math.sin(a)) for a in np.linspace(0, 2 * math.pi, n, endpoint=False)]
    V_, F = [], []
    prev = None
    for k, p in enumerate(path):
        tdir = path[min(k + 1, len(path) - 1)] - path[max(k - 1, 0)]
        tdir /= np.linalg.norm(tdir) + 1e-12
        a = np.array([0, 0, 1.0]) if abs(tdir[2]) < 0.9 else np.array([1.0, 0, 0])
        u = np.cross(tdir, a)
        u /= np.linalg.norm(u)
        if prev is not None and np.dot(u, prev) < 0:
            u = -u
        prev = u
        v = np.cross(tdir, u)
        for cx, cy in ring:
            V_.append(p + cx * u + cy * v)
    nr = len(ring)
    for k in range(len(path) - 1):
        for j in range(nr):
            a, b = k * nr + j, k * nr + (j + 1) % nr
            F += [[a, b, b + nr], [a, b + nr, a + nr]]
    return trimesh.Trimesh(np.array(V_), np.array(F), process=False)


def cables(pan, el):
    """servo leads (pan: housing tunnel -> post slot -> lid groove -> off the base; tilt: arm -> service loop -> post)
    and a USB-C cable from the phone's bottom edge, S frame at the pose."""
    Mp = A.group_to_S("pan", pan)
    Mt = A.group_to_S("tilt", pan, el)
    ex = np.r_[V.servo_lead_exit(), 1.0]
    zt = SP.Z_TH
    hb = zt + SP.FLOOR_BOT - SP.PYR_H - SP.HOUSING_SOCKET
    pan_exit = (P.pan_servo_frame() @ ex)[:3]
    p_lead = [pan_exit, pan_exit + [8, 0, 0], [SP.RIM_R[1] - 14, 0, pan_exit[2] - 2], [SP.RIM_R[1] - 15, 2, hb + 6],
              [11.5, 0, hb - 6], [10.6, 0, hb - 20]]
    p_base = [[10.6, 0, SP.BOSS["top"] + 8], [12.0, 0, SP.BOSS["top"] - 4], [20, 0, SP.BASE_TOP + SP.LID_T - 1.1],
              [SP.BASE_X - 4, 0, SP.BASE_TOP + SP.LID_T - 1.1], [SP.BASE_X + 14, 6, 14], [SP.BASE_X + 40, 30, 1.2],
              [SP.BASE_X + 140, 120, 1.2]]
    tilt_exit = (A.group_to_S("pan", pan) @ G.trans(0, 0, SP.H_T - SP.Z_TH) @ P.tilt_servo_frame() @ ex)[:3]
    loc = lambda p: (Mp @ np.r_[p, 1.0])[:3]
    zb = SP.FLOOR_Z + SP.H_T - SP.Z_TH        # bridge floor in P
    t_lead = [tilt_exit, loc([SP.ARM_OUT + 14, -18, SP.H_T - SP.Z_TH - 30]), loc([SP.ARM_OUT + 4, -10, zb - 4]),
              loc([40, -10, zb - 22]), loc([30, -14, 9]), [40, -30, zt - 30], [26, -14, hb - 26], [12.0, -1, hb - 34],
              [10.6, 0, hb - 46]]
    T = lambda p: (Mt @ np.r_[p, 1.0])[:3]
    yb = SP.PHONE_BACK_Y - SP.PHONE_NOM["t"] / 2
    zl = -SP.PHONE_NOM["l"] / 2
    usb = [T([0, yb, zl - 9]), T([0, yb, zl - 30]), T([0, yb - 10, zl - 55])]
    usb += [loc([-30, -40, SP.H_T - SP.Z_TH - 140]), [-24, -30, zt - 40], [-11.5, -2, hb - 40], [-10.8, 0, hb - 80],
            [-11.0, 0, SP.BOSS["top"] + 6], [-24, -16, SP.BASE_TOP + SP.LID_T + 2], [-SP.BASE_X - 10, -60, 6],
            [-SP.BASE_X - 120, -140, 2]]
    plug = G.box(-5.6, 5.6, yb - 3.2, yb + 3.2, zl - 20, zl - 0.6)
    plug_m = tess(plug, 0.05, 0.2)
    plug_m.apply_transform(Mt)
    tip = G.box(-4.1, 4.1, yb - 1.2, yb + 1.2, zl - 1.0, zl + 6.0)
    tip_m = tess(tip, 0.05, 0.2)
    tip_m.apply_transform(Mt)
    return {"cable": [_tube(p_lead, 0.9), _tube(p_base, 1.0), _tube(t_lead, 0.9), _tube(usb, 1.7), plug_m],
            "steel": [tip_m]}


def posed_glb(path, pan, el, w=None, with_cables=True, section_x=None, groups=None):
    """the assembly at (pan, el) as one GLB in the stand's three frame. section_x: keep S x >= section_x only (a cut
    through the pan axis, capped where the mesh is closed)."""
    insts = A.instances(w)
    acc = {}
    for i in insts:
        if groups and i.group not in groups:
            continue
        M = A.group_to_S(i.group, pan, el) @ i.M
        m = tess(A.inst_shape(i), 0.03 if A.PARTS[i.pn].process == "fdm_print" else 0.05, 0.15)
        m.apply_transform(M)
        if section_x is not None:
            if m.bounds[1][0] < section_x:
                continue
            if m.bounds[0][0] < section_x:
                try:
                    m = m.slice_plane([section_x, 0, 0], [1, 0, 0], cap=True)
                except Exception:  # noqa: BLE001
                    m = m.slice_plane([section_x, 0, 0], [1, 0, 0])
        acc.setdefault(A.finish(i.pn), []).append(m)
    if with_cables and section_x is None and not groups:
        for fin, ms in cables(pan, el).items():
            acc.setdefault(fin, []).extend(ms)
    nodes = []
    for fin, ms in acc.items():
        m = trimesh.util.concatenate(ms)
        m.apply_transform(A.S_TO_THREE)
        nodes.append({"name": f"stand__{fin}", "parent": None, "mesh": (*shaded(m, 35.0), fin)})
    write_glb(path, nodes, SP.COLORS)


def S2B(p):
    return [-p[0], -p[1], p[2]]


def jobs(quality):
    ref = K.references()
    p0, e0 = ref["pan0"], ref["el0"]
    po, eo = ref["operator_pan"], ref["operator_el"]
    H = SP.H_T
    J = {
        "studio_deck_view": dict(pose=(p0, e0), eye=(-700, -980, H + 260), target=(0, 0, H * 0.55), lens=50),
        "studio_operator_view": dict(pose=(po, eo), eye=(760, -1000, H + 120), target=(0, 0, H * 0.55), lens=50),
        "studio_back": dict(pose=(-25, -12), eye=(-560, 880, H + 160), target=(0, 0, H * 0.6), lens=50),
        "detail_head": dict(pose=(30, -48), eye=(-330, -380, H + 190), target=(0, 0, H - 30), lens=60),
        "detail_tilt_servo": dict(pose=(0, -20), eye=(430, -260, H + 120), target=(40, 0, H - 30), lens=60),
        "detail_turntable": dict(pose=(0, -20), eye=(300, -340, SP.Z_TH + 140), target=(0, 0, SP.Z_TH - 25), lens=70),
        "section_turntable": dict(pose=(0, -20), eye=(-470, -120, SP.Z_TH + 70), target=(0, 0, SP.Z_TH - 28), lens=95,
                                  section=0.0),
        "detail_clamp": dict(pose=(0, 0), eye=(-90, -260, H + 60), target=(0, 0, H), lens=60, groups=("tilt", "clamp"),
                             w=64.0),
        "bench_deck_view": dict(pose=(p0, e0), eye=(-420, -1350, 900), target=(0, 120, 200), lens=40, bench=True),
        "bench_operator_side": dict(pose=(po, eo), eye=(1100, -650, 650), target=(0, 140, 220), lens=38, bench=True),
    }
    q = {"final": dict(samples=256, res=[1600, 1066]), "draft": dict(samples=48, res=[900, 600])}[quality]
    out = []
    for name, j in J.items():
        out.append({"name": name, "pose": j["pose"], "eye": S2B(j["eye"]), "target": S2B(j["target"]),
                    "lens": j["lens"], "bench": j.get("bench", False), "section": j.get("section"),
                    "groups": j.get("groups"), "w": j.get("w"), **q})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--gpu", action="store_true", help="CUDA (Blender 4.0 has no kernels for the GB10: renders black)")
    a = ap.parse_args(argv)
    SCN.mkdir(parents=True, exist_ok=True)
    js = [j for j in jobs("draft" if a.draft else "final") if not a.only or j["name"] in a.only]
    poses = {}
    for j in js:
        key = tuple(round(v, 2) for v in j["pose"]) + (j["section"], j["groups"] and tuple(j["groups"]), j["w"])
        if key not in poses:
            f = SCN / f"pose_{len(poses)}.glb"
            posed_glb(f, *key[:2], w=j["w"], section_x=j["section"], groups=j["groups"])
            poses[key] = str(f)
        j["glb"] = poses[key]
        j["out"] = str(REN / f"{j['name']}.png")
    cnc = ROOT / "apps" / "console" / "models" / "cnc3018.glb"
    job = {"cpu": not a.gpu, "jobs": js, "cnc_glb": str(cnc) if cnc.exists() else None, "stand_z": SP.STAND_Z, "bed_h": SP.BED_H}
    jf = SCN / "job.json"
    jf.write_text(json.dumps(job, indent=1))
    # Blender brings its own Python: keep the uv venv out of its environment
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "UV_PROJECT_ENVIRONMENT")}
    venv_bin = os.environ.get("VIRTUAL_ENV")
    if venv_bin:
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if not p.startswith(venv_bin))
    if (shutil.which("blender") or "").startswith(("/usr/bin", "/bin")):
        env["PYTHONHOME"] = "/usr"     # distro Blender: its Python is the system one (don't let ~/.local pythons win)
    subprocess.run(["blender", "-b", "--factory-startup", "--python", str(HERE / "pstand" / "blender_scene.py"), "--",
                    str(jf)], check=True, env=env, cwd=str(SCN))


if __name__ == "__main__":
    main()
