# LabRat deck: printed beaker matrix, tip rack, layout and reach

[`deck.py`](deck.py) (build123d) holds the deck layout and is the source of truth for it. It writes `deck.json`, which
the simulator, the twin and the renders all use. It also models the printed beaker matrix and the labware, and checks
whether the head can reach everything with the 3018's Z travel (`build/report.json`).

```bash
cd hardware/deck
uv run --python 3.12 --with build123d==0.13.0 --with bd_warehouse==0.4.0 --with trimesh --with numpy --with pyyaml python deck.py
```

## DECK-001 beaker matrix (print it)

- **Beakers:** 6 rows (A–F, front to back) × 8 columns (1–8, left to right) of 15.3 × 50 mm beakers, on a 19 mm pitch.
- **Pockets:** Ø15.8 (slide fit) and 10 deep on a 1.2 mm floor (the holder top is 11.2 mm above the bed), so the
  rims are 51.2 mm above the bed. Each pocket has a Ø8 hole for draining and pushing beakers out.
- **Structure:** the tubes overlap their neighbours, leaving a 3.2 mm web between pockets.
- **Labels:** raised row letters on the left and column numbers on the front. Column 8 is labelled **S**.
- **Clamping:** two ears, front and back, have 20 mm M5 slots running along X for T-slot clamps.
- **Size:** 160 × 146 × 11.2 mm, about 72 cm³. It prints as modelled (base on the bed) with no supports, and every
  pocket and gap opens upward.
- **Columns 1–7:** mixing beakers, the simulator's `plate` grid A1–F7, up to 5 mL each.
- **Column 8:** stocks. A8 water, B8 red, C8 yellow and D8 blue (4.5 mL each) are the simulator's reservoirs; E8 and F8
  are free.

Files: `step/DECK-001.step`, `stl/DECK-001.stl`.

## Layout (machine mm, commanded spindle XY; the tip is on the spindle axis)

| item | where |
|---|---|
| FT-200L-RS tip rack (117 × 81.5 box, 8 × 12 on 9 mm) | box corner (8, 10), A1 at (17, 19.25) |
| beaker matrix | A1 at (153.25, 32); stocks at x = 286.25 |
| waste / tip eject bar | (280, 170) / (10, 170) |

The tip end is 55 mm above the bed at z = 0, which is 3.8 mm above the beaker rims. The lowest it reaches is 10 mm
above the bed (Z travel 45). That means the tip reaches liquid only while a beaker holds about 1.23 mL or more.

## Reach report: tip pick-up does not fit
`build/report.json` finds two failures with the FT-200L-RS on the bed:

- **Seating a tip** needs z = −49.5. The adapter cone is 101.3 mm above the bed at z = 0 and has to reach 5.2 mm below
  the tip rims (57 mm, assumed), but Z stops at −45.
- **Pulling a tip out of the rack** needs about 51 mm of lift, because a seated tip hangs about 49 mm below the rack's
  deck plate. The 3018 has 45 mm of Z in total.

No head height fixes both. Moving the tip up to clear the rack takes it out of reach of the beakers, and the reverse
also fails. The fix needs a decision: a shorter tip holder, more Z travel, or tips loaded another way. Until then, the
simulator uses Z_TIP = −45 (the travel limit), and teaching `tip_rack` points overrides it.

## Assumed (measure first)
- Beaker wall and bottom: 0.9 / 1.2 mm, so ID 13.5 and 143 µL per mm.
- FT-200L-RS heights: the deck plate at 55.4 mm and the tip rims at 57 mm above the bed.

## Open issue: tip-rack height (measure before the first run)

`TIPRACK["rim_h"]` (57 mm, tip rims above the bed) is an assumption. With it, the tool-head build
(`hardware/tool-head/build/report.json`, `deck` rows) finds the syringe tip and the deployed probe electrode 2 mm *below* the
tip rims at CNC z 0, so travel over a full rack would hit tips, and the rack lid modelled upright behind the rack
(127 mm) cannot be passed over at all. Measure the real rim height; if it is really 57 mm, lower the rack (drop its
riser / sink it into the bed slot) or remove the lid before running, then rebuild the tool head to refresh the checks.
