"""Photoreal stills of the head through microfab's Cycles pipeline (Blender 4.0 + CUDA on this machine, OIDN with
albedo/normal, AgX, metered exposure). Head station from mf.py + bench context from context.py.

    ~/Documents/microfab_3d_atlas_bundle/.venv/bin/python -m syrhead.render [--quality final|draft] [--only name ...]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

os.environ.setdefault("MICROFAB_RENDER_HOST", "local")

import yaml  # noqa: E402
from microfab import spec as MFSPEC  # noqa: E402
from microfab.export.usd import export_station  # noqa: E402
from microfab.film import post  # noqa: E402
from microfab.film.render.launch import run_job  # noqa: E402

from .context import write_context, write_studio  # noqa: E402
from .mf import mf_assembly  # noqa: E402
from .spec import LAY, S  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "build"
REN = HERE.parent / "renders"
HDRI = Path.home() / "Documents/microfab_3d_atlas_bundle/build/assets/hdri/aircraft_workshop_01_2k.hdr"
QUALITY = {"final": {"samples": 160, "res": [1920, 1280], "threshold": 0.015},
           "draft": {"samples": 32, "res": [960, 640], "threshold": 0.05}}
POS = 14.0                      # plunger height for the stills (mm above pressed)
LIFT = 1.0 + 0.225 + 0.012       # head frame -> room: bench top 1.0 m above the floor (bench top is 237 mm below z = 0)
CNC = (150.0, 60.0, -12.0)      # machine position for the bench shots


def mm(p):
    return [p[0] / 1000.0, p[1] / 1000.0, p[2] / 1000.0 + LIFT]


def V(eye, target, lens=50.0, fstop=None):
    c = {"eye": mm(eye), "target": mm(target), "lens": lens}
    if fstop:
        c["fstop"], c["focus"] = fstop, mm(target)
    return c


# head frame, mm. Operator stands at -Y; +X right.
BENCH = {   # head on the 3018, bench context (tip rack left of the head, beaker matrix right)
    "bench_full":      V((-560, -1050, 430), (60, 60, -70), 35),
    "bench_deck":      V((-120, -430, 60), (20, 40, -130), 35),
    "head_on_machine": V((-280, -440, 170), (15, -10, 40), 40),
    "head_tip":        V((110, -190, -60), (0, 0, -85), 50),
}
STUDIO = {  # head alone (no machine): every part visible
    "studio_front_left":  V((-320, -450, 170), (15, -10, 45), 40),
    "studio_gearbox":     V((300, -330, 200), (34, -12, 88), 50),
    "studio_servo_side":  V((420, 160, 150), (48, -10, 85), 50),
    "studio_rear_left":   V((-300, 260, 170), (0, 0, 50), 42),
    "studio_carriage":    V((-150, -190, 150), (0, 0, 72), 60),
    "studio_tip":         V((70, -130, -50), (0, 0, -75), 60),
    "studio_tool_rail":   V((150, -330, 30), (-8, -45, -35), 45),
}


def materials():
    m = dict(MFSPEC.materials())
    m.update(yaml.safe_load((HERE / "materials.yaml").read_text()))
    return m


def _job(views, stations, out_dir, quality, room=True):
    frames = [{"index": i, "t": 0.0, "name": k, "camera": v, "focus": []} for i, (k, v) in enumerate(views.items())]
    job = {"materials": materials(), "hdri": str(HDRI), "world_strength": 0.45 if room else 0.9, "room": room,
           "room_args": {"size": (6.0, 5.0), "height": 2.8, "center": (0.0, 0.8)}, "key_target": (0.0, 0.0, LIFT),
           "stations": [{"sidecar": str(st), "offset": (0, 0, LIFT)} for st in stations],
           "quality": quality, "out_dir": str(out_dir), "overwrite": True, "frames": frames}
    return job, frames


def _develop(frames, out_dir):
    g = MFSPEC.film()["grade"]
    REN.mkdir(exist_ok=True)
    from PIL import Image
    for fr in frames:
        exr = out_dir / f"{fr['index']:06d}.exr"
        ev = post.auto_exposure([post.scene_key(exr)], [0.0], g)[0]
        img, _ = post.develop(exr, exposure=ev, punch=g["punch"])
        Image.fromarray(post.to_uint8(img)).save(REN / f"{fr['name']}.png")
        print("wrote", REN / f"{fr['name']}.png", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quality", default="final", choices=list(QUALITY))
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args(argv)
    usd = OUT / "usd"
    shutil.rmtree(usd / "stations", ignore_errors=True)
    export_station(mf_assembly(POS), "syr_head", out=usd)
    head = usd / "stations" / "syr_head.scene.json"
    ctx = write_context(usd, cnc=CNC)
    sweep = write_studio(usd)
    for name, views, stations, room in (("bench", BENCH, [head, ctx], True), ("studio", STUDIO, [head, sweep], False)):
        v = {k: x for k, x in views.items() if not a.only or k in a.only}
        if not v:
            continue
        job, frames = _job(v, stations, OUT / f"frames_{name}", QUALITY[a.quality], room)
        run_job(job, OUT / f"job_{name}.json", log=OUT / f"blender_{name}.log")
        _develop(frames, OUT / f"frames_{name}")


if __name__ == "__main__":
    main()
