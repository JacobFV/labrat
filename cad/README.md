# CAD

| dir | what |
|---|---|
| [`syringe/`](syringe) | **Syringe head v1.0** for the 3018 spindle clamp. A 12 mL syringe's plunger is driven by a continuous-rotation FS90R through a 12.01:1 printed gear train and a rack on an MGN7 guide. Written in build123d with part numbers and a BOM, STEP/STL/3MF/DXF exports, coupled gear-train and sweep checks, microfab design checks, and Cycles renders. See [`syringe/README.md`](syringe/README.md). |
| [`cnc3018/`](cnc3018) | Parametric 3018 PRO model (build123d + trimesh), with the syringe head (from `syringe/twin`) in the clamp. It produces `cnc3018.glb` and `web/models/syringe_rig.json` for the web twin, plus matplotlib renders. `render_bench.py` takes full-bench screenshots from the three.js twin. |

## Rebuild
```bash
cd cad/syringe   # 1. head: exports + checks (fails on any check)
uv run --python 3.12 --with build123d==0.13.0 --with bd_warehouse==0.4.0 --with trimesh --with networkx \
    --with lxml --with numpy --with pyyaml python -m syrhead.build
~/Documents/microfab_3d_atlas_bundle/.venv/bin/python -m syrhead.render     # 2. Cycles stills (optional, local)
cd ../cnc3018    # 3. 3018 + head -> GLB for the twin, renders
uv run --python 3.12 --with build123d --with trimesh --with matplotlib --with numpy --with networkx --with lxml \
    python cnc3018.py
cd ../.. && uv run --python 3.12 --with playwright python cad/cnc3018/render_bench.py --chrome /path/to/chrome
```

## How the head sits in the machine
- The plug drops into the 52 mm clamp from above and fills the clamp's full 26 mm. Its lip rests on the clamp's top
  face. The tip end is then 60 mm above the bed at CNC z = 0 (the twin's `SAFE_H` and the simulator's Z table).
- The syringe is coaxial with the old spindle, so the commanded XY is the tip XY, and `deck_cal` `pipette_offset`
  defaults to 0, 0, 0. If you stored the old offset (dy = −45), reset it in the Deck tab.
- The MGN7 rail is on the machine's −X side of the syringe. The gearbox and servo are on the +X side, in front, facing
  the phone camera.
- Everything above the Z-stepper line stays at y ≤ 16.5, against the stepper front at 26 (checked).

## Frames
`cnc3018.py` models in machine coordinates with Y and Z swapped: X = machine x − 150, Y = up, Z = machine y − 90.
three.js shows this mirrored, which is the twin's long-standing convention. The head is built in true machine axes and
mapped by the same swap. The Cycles bench renders use the true-handed machine frame; the 3018's decal text, which was
authored for the mirrored twin, is left out there.
