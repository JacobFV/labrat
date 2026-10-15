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
from . import probe as PR  # noqa: E402
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
    "head_on_machine": V((-280, -440, 170), (15, -10, 40), 40),
}
STUDIO = {  # head alone (no machine)
    "studio_front_left":  V((-320, -450, 170), (15, -10, 45), 40),
    "studio_probe":       V((260, -210, -30), (12, -50, -72), 50),
}
STUDIO_RETRACTED = {   # same head, probe arm retracted (the pipetting state)
    "studio_probe_retracted":      V((260, -230, 10), (12, -62, -38), 50),
}
BENCH_PROBING = {      # probing a beaker: arm deployed, electrode on the liquid in B8 (head frame: CNC at PROBE_CNC)
    "bench_probing": V((130, -260, -40), (9.5, -47.5, -105), 45),
}
PROBE_CNC = [0.0, 0.0, 0.0]


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
    export_station(mf_assembly(POS), "syr_head", out=usd)                    # probe deployed
    export_station(mf_assembly(POS, PR.SWING), "syr_head_ret", out=usd)     # probe retracted
    head = usd / "stations" / "syr_head.scene.json"
    head_ret = usd / "stations" / "syr_head_ret.scene.json"
    ctx = write_context(usd, cnc=CNC)
    # probing: the electrode (deployed) touching the red stock in B8 (4.5 mL: surface 33.8 above the bed)
    from . import tools as TL
    ex, ey, _ = TL.TOOLS["probe"]["offset"]
    bx, by = 153.25 + 7 * 19.0, 32.0 + 19.0
    PROBE_CNC[:] = [bx - ex, by - ey, 33.8 - S.safe_tip_h]
    ctx_probe = write_context(usd, cnc=tuple(PROBE_CNC), name="context_probing")
    sweep = write_studio(usd)
    for name, views, stations, room in (("bench", BENCH, [head_ret, ctx], True),
                                        ("bench_probing", BENCH_PROBING, [head, ctx_probe], True),
                                        ("studio", STUDIO, [head, sweep], False),
                                        ("studio_retracted", STUDIO_RETRACTED, [head_ret, sweep], False)):
        v = {k: x for k, x in views.items() if not a.only or k in a.only}
        if not v:
            continue
        job, frames = _job(v, stations, OUT / f"frames_{name}", QUALITY[a.quality], room)
        run_job(job, OUT / f"job_{name}.json", log=OUT / f"blender_{name}.log")
        _develop(frames, OUT / f"frames_{name}")


if __name__ == "__main__":
    main()
