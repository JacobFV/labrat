# CAD

| dir | what |
|---|---|
| [`syringe/`](syringe) | **Rack-and-pinion syringe head** that replaces the 3018's spindle motor: one printed body (50.2 mm plug in the spindle clamp + syringe collar + rack guide + servo plate), a rack with a plunger fork, and a pinion on a continuous-rotation 9 g servo. CadQuery, parametric, STEP/STL/3MF, fit + kinematic checks. |
| [`cnc3018/`](cnc3018) | Parametric 3018 PRO model (build123d + trimesh) with the syringe head in the clamp → `cnc3018.glb` for the web twin, plus matplotlib renders. `render_bench.py` renders the full bench (CNC, deck with tip rack / plate / reservoirs, SO-101s, phone, laptop, desk) from the real three.js twin. |

## Rebuild
```bash
# 1. syringe head: STEP/STL/3MF, report.json (fails on any interference), preview.png
cd cad/syringe && uv run --python 3.12 --with cadquery --with trimesh --with matplotlib --with numpy \
    --with pillow --with networkx --with lxml python syringe_3018_rack.py
# 2. 3018 + head -> cnc3018.glb (also copied to web/models), clearance assert, renders
cd ../cnc3018 && uv run --python 3.12 --with build123d --with trimesh --with matplotlib --with numpy \
    --with networkx --with lxml python cnc3018.py
# 3. full-bench renders from the twin (headless Chromium)
cd ../.. && uv run --python 3.12 --with playwright python cad/cnc3018/render_bench.py [--chrome /path/to/chrome]
```
CadQuery and build123d pin different OCP builds, so steps 1 and 2 use separate `uv run` environments. Step 2 reads
`syringe/stl/*.stl` and `syringe/report.json`, so run step 1 first after changing a syringe parameter.

## Renders
- `cnc3018/renders/bench_full_assembly.png`, `bench_cnc_syringe_closeup.png`, `bench_syringe_head_detail.png`,
  `setup_front.png`, `setup_iso.png`, `lab_iso.png`: full bench, from the twin.
- `cnc3018/renders/cnc3018_iso.png`, `cnc3018_front.png`, `cnc3018_syringe_head.png`: machine only.
- `syringe/preview.png`: head only, four shaded views.

## How the head sits in the machine
- The plug drops into the 52 mm clamp from above, and its lip rests on the clamp top. `plug_len = 18` puts the end of a
  51 mm, 200 µL tip (pressed 5 mm onto the luer nozzle) 60 mm above the bed at CNC z = 0, which is the twin's `SAFE_H`
  and the height the simulator's Z table assumes. `cnc3018.py` asserts this.
- The syringe is **coaxial with the old spindle**, so the commanded XY is the tip XY. `deck_cal` `pipette_offset`
  defaults to 0,0,0. If you stored an old offset (dy = −45), reset it in the Deck tab.
- The rack and pinion sit on the machine's −X (left) side of the syringe. The servo body hangs toward the front, toward the phone camera.
  Everything above the clamp stays ≥ 16 mm in front of the Z plate and Z stepper (asserted).

See [`syringe/README.md`](syringe/README.md) for the parts, assembly, actuation numbers and open issues. See
[`../bridge/README.md`](../bridge/README.md) for the servo calibration.
