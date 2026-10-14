# CAD

| dir | what |
|---|---|
| [`syringe/`](syringe) | **Tool head v2** for the 3018's 42 mm motor holder. A 12 mL syringe's plunger is driven by a continuous-rotation FS90R through a 12.01:1 printed gear train and a rack on a carriage that slides on a plain square bar moulded on the body. A tool rail takes interchangeable modules: a USB microscope and a capacitive probe. Every printed part prints without supports, and a print check enforces it. Written in build123d with part numbers and a BOM, STEP/STL/3MF exports, coupled gear-train and sweep checks, microfab design checks, and Cycles renders. See [`syringe/README.md`](syringe/README.md). |
| [`deck/`](deck) | **Deck**: the printed 6 × 8 beaker matrix (`DECK-001`, 15.3 × 50 mm beakers), the FT-200L-RS tip rack, the deck layout (`deck.json`, used by the simulator, the twin and the renders) and the reach report against the 3018's Z travel. |
| [`cnc3018/`](cnc3018) | Parametric 3018 PRO model (build123d + trimesh), with the syringe head (from `syringe/twin`) in the holder. It produces `cnc3018.glb` and `web/models/syringe_rig.json` for the web twin, plus matplotlib renders. `render_bench.py` takes full-bench screenshots from the three.js twin. |

## Rebuild
```bash
U="uv run --python 3.12 --with build123d==0.13.0 --with bd_warehouse==0.4.0 --with trimesh --with networkx --with lxml --with numpy --with pyyaml"
cd cad/syringe && $U python -m syrhead.build    # 1. head: exports + checks (fails on any check)
cd ../deck && $U python deck.py                  # 2. deck: beaker matrix + deck.json + reach report
~/Documents/microfab_3d_atlas_bundle/.venv/bin/python -m syrhead.render   # 3. Cycles stills (optional, local; from cad/syringe)
cd ../cnc3018                                    # 4. 3018 + head -> GLB for the twin, renders
uv run --python 3.12 --with build123d --with trimesh --with matplotlib --with numpy --with networkx --with lxml \
    python cnc3018.py
cd ../.. && uv run --python 3.12 --with playwright python cad/cnc3018/render_bench.py --chrome /path/to/chrome
```

## How the head sits in the machine
- The Ø41.9 plug drops into the 42 mm holder and fills its full 34 mm height. Above the holder the plug flares out at
  45°, and that cone seats on the holder's bore edge. The tip end is then 55 mm above the bed at CNC z = 0 (the
  twin's `SAFE_H` and the simulator's Z table).
- The syringe is coaxial with the old spindle, so the commanded XY is the tip XY, and `deck_cal` `pipette_offset`
  defaults to 0, 0, 0.
- The printed slide rail is on the machine's −X side of the syringe; the tool rail runs along the front, with the
  modules hanging in front of the holder. The gears are on the +X side, with their axes along X, and
  the servo hangs on the outside of gear plate 2.
- Everything above the Z-stepper line stays at y ≤ 24.9, in front of the stepper's front face at 26 (checked).

## Frames
`cnc3018.py` models in machine coordinates with Y and Z swapped: X = machine x − 150, Y = up, Z = machine y − 90.
three.js shows this mirrored, which is the twin's long-standing convention. The head is built in true machine axes and
mapped by the same swap. The Cycles bench renders use the true-handed machine frame; the 3018's decal text, which was
authored for the mirrored twin, is left out there.
