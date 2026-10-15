"""LabRat deck for the 3018: the printed beaker matrix (DECK-001), the labware that sits on the bed (FT-200L-RS tip
rack, 15.3 x 50 mm beakers) and the deck layout the simulator, the twin and the renders use - plus the reach checks
that tie the deck heights to the syringe head and the 3018's Z travel.

    cd hardware/deck && uv run --python 3.12 --with build123d==0.13.0 --with bd_warehouse==0.4.0 --with trimesh \
        --with numpy --with pyyaml python deck.py

Writes deck.json (layout + heights, read by cloud/src/sim.ts (checked in cloud/test/sim.test.ts) and the twin), build/report.json (reach checks), step/, stl/.
Frame: machine deck coordinates, mm. x right 0..300, y back 0..180, z up from the bed top. Grid positions are the
commanded spindle XY (the syringe tip is on the spindle axis: deck_cal pipette_offset 0, 0, 0).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import trimesh
from build123d import (Align, Box, BuildPart, BuildSketch, Circle, Cylinder, Location, Locations, Mode, Plane,
                       Rectangle, RectangleRounded, SlotCenterToCenter, Solid, Text, add, export_step, extrude,
                       make_hull)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tool-head"))
from syrhead.spec import LAY as HEAD, S as HS  # noqa: E402  (tip height, adapter cone, tip length)

CB = (Align.CENTER, Align.CENTER, Align.MIN)
CT = (Align.CENTER, Align.CENTER, Align.MAX)

# ---------------------------------------------------------------------------------------------------------------------
# labware (catalogue / measured values; ASSUMED ones are marked and are the first things to measure)
# ---------------------------------------------------------------------------------------------------------------------
BEAKER = dict(od=15.3, h=50.0,                 # measured: 15.3 mm OD x 50 mm, blue screw lid (left off)
              wall=0.9, bottom=1.2,            # ASSUMED (PP vial): ID 13.5 -> 143 uL per mm of depth
              thread_l=7.0)
BEAKER["id"] = BEAKER["od"] - 2 * BEAKER["wall"]
BEAKER["ul_per_mm"] = math.pi / 4 * BEAKER["id"] ** 2
TIPRACK = dict(name="FT-200L-RS", l=117.0, w=81.5,  # measured footprint; 96 x 200 uL, 8 x 12 on 9 mm
               pitch=9.0, rows=8, cols=12,
               plate_h=55.4,                    # ASSUMED: top of the deck plate the tip collars rest on
               rim_h=57.0)                      # ASSUMED: tip rims above the bed
TIP_ENGAGE = HEAD.tip_top_z - HEAD.cone_end_z  # adapter cone below the tip rim when the tip seals (4.67)
TIP_PRESS = 0.5                                 # push past the seal to seat the tip

# ---------------------------------------------------------------------------------------------------------------------
# DECK-001 beaker matrix: 6 rows (A-F, along +y) x 8 columns (1-8, along +x) on a 19 mm pitch.
# Columns 1-7 are mixing beakers; column 8 holds the stocks (A8 water, B8 red, C8 yellow, D8 blue, E8 / F8 free).
# ---------------------------------------------------------------------------------------------------------------------
M = dict(rows=6, cols=8, pitch=19.0,
         pocket_d=BEAKER["od"] + 0.5,          # slide fit
         pocket_depth=20.0, floor=1.2,         # beakers stand 1.2 above the bed; rims at 51.2
         tube_od=19.6,                          # neighbouring tubes overlap 0.6 (pocket-to-pocket web 3.2)
         drain_d=8.0,                           # push-out / drain hole in every pocket floor
         border=7.0, label_h=0.6,               # label border (front + left) with raised row / column labels
         ear_l=12.0, ear_t=4.0, slot_l=20.0, slot_d=5.5)    # bed-clamp ears (M5 T-slot), slots along x
STOCKS = {"water": "A8", "red": "B8", "yellow": "C8", "blue": "D8"}
MIX_COLS = 7

# ---------------------------------------------------------------------------------------------------------------------
# deck layout (machine coordinates of grid A1 = commanded spindle XY)
# ---------------------------------------------------------------------------------------------------------------------
TIPRACK_BOX = (8.0, 10.0)                       # front-left corner of the rack box
TIPRACK_A1 = (TIPRACK_BOX[0] + (TIPRACK["l"] - 11 * TIPRACK["pitch"]) / 2,
              TIPRACK_BOX[1] + (TIPRACK["w"] - 7 * TIPRACK["pitch"]) / 2)
MATRIX_A1 = (153.25, 32.0)
WASTE = (280.0, 170.0)
EJECT_BAR = (10.0, 170.0)
Z_TRAVEL = HS.z_travel
BED = (300.0, 180.0)


def matrix_extent():
    p, r = M["pitch"], M["tube_od"] / 2
    x0, y0 = MATRIX_A1
    return (x0 - r - M["border"], x0 + (M["cols"] - 1) * p + r, y0 - r - M["border"], y0 + (M["rows"] - 1) * p + r)


def well_xy(name):
    r, c = "ABCDEF".index(name[0]), int(name[1:]) - 1
    return MATRIX_A1[0] + c * M["pitch"], MATRIX_A1[1] + r * M["pitch"]


# =====================================================================================================================
# parts
# =====================================================================================================================
def matrix_holder():
    """DECK-001 in deck coordinates (A1 at MATRIX_A1). Prints as modelled (base on the bed), 2.5D: tubes rise from the
    base plate, every pocket and void opens upward, labels stand up from the border."""
    p, R = M["pitch"], M["tube_od"] / 2
    x0, y0 = MATRIX_A1
    ex0, ex1, ey0, ey1 = matrix_extent()
    top = M["floor"] + M["pocket_depth"]
    cx = (ex0 + ex1) / 2
    centres = [(x0 + c * p, y0 + r * p) for r in range(M["rows"]) for c in range(M["cols"])]
    with BuildPart() as bp:
        # base plate (+ label border on the front and left) and the two bed-clamp ears (front, back)
        with BuildSketch():
            with Locations(((ex0 + ex1) / 2, (ey0 + ey1) / 2)):
                RectangleRounded(ex1 - ex0, ey1 - ey0, 4.0)
        extrude(amount=M["floor"])
        for ye, sgn in ((ey0, -1), (ey1, +1)):
            with BuildSketch(Plane.XY):
                with Locations((cx, ye + sgn * (M["ear_l"] / 2 - 2.0))):
                    RectangleRounded(M["slot_l"] + 14.0, M["ear_l"] + 4.0, 4.0)
            extrude(amount=M["ear_t"])
        for (x, y) in centres:
            with Locations((x, y, 0)):
                Cylinder(R, top, align=CB)
        # pockets, drain holes, ear slots
        for (x, y) in centres:
            with Locations((x, y, top)):
                Cylinder(M["pocket_d"] / 2, M["pocket_depth"], align=CT, mode=Mode.SUBTRACT)
            with Locations((x, y, 0)):
                Cylinder(M["drain_d"] / 2, 10, align=CB, mode=Mode.SUBTRACT)
        for ye, sgn in ((ey0, -1), (ey1, +1)):
            with BuildSketch(Plane.XY):
                with Locations((cx, ye + sgn * (M["ear_l"] / 2 - 1.0))):
                    SlotCenterToCenter(M["slot_l"], M["slot_d"])
            extrude(amount=M["ear_t"] + 1, mode=Mode.SUBTRACT)
    shp = bp.part
    # broken edges: pocket mouths (0.8 lead-in for the beakers), tube tops, base / ear outlines
    try:
        top_edges = shp.edges().filter_by_position("Z", top - 0.01, top + 0.01)
        shp = shp.chamfer(0.5, None, top_edges)
    except Exception:
        pass
    try:
        low = shp.edges().filter_by_position("Z", -0.01, 0.01)
        shp = shp.chamfer(0.4, None, low)
    except Exception:
        pass
    # raised labels on the border: rows on the left, columns on the front
    with BuildPart() as lb:
        for r in range(M["rows"]):
            with BuildSketch(Plane.XY.offset(M["floor"])):
                with Locations((ex0 + M["border"] / 2 + 0.5, y0 + r * p)):
                    Text("ABCDEF"[r], 4.5)
            extrude(amount=M["label_h"])
        for c in range(M["cols"]):
            with BuildSketch(Plane.XY.offset(M["floor"])):
                with Locations((x0 + c * p, ey0 + M["border"] / 2 + 0.5)):
                    Text(str(c + 1) if c + 1 < M["cols"] else "S", 4.5)
            extrude(amount=M["label_h"])
    return shp, lb.part


def beaker():
    """15.3 x 50 mm PP beaker (vial) with the screw-lid thread, frame: base centre at z = 0."""
    ro, ri, h, b = BEAKER["od"] / 2, BEAKER["id"] / 2, BEAKER["h"], BEAKER["bottom"]
    with BuildPart() as bp:
        Cylinder(ro - 0.4, h, align=CB)                                      # neck below the thread is OD - 0.8
        with Locations((0, 0, 0)):
            Cylinder(ro, h - BEAKER["thread_l"] - 1.0, align=CB)
        for k in range(4):                                                   # thread crests
            with Locations((0, 0, h - BEAKER["thread_l"] + 1.0 + 1.6 * k)):
                Cylinder(ro - 0.05, 0.6, align=CB)
        with Locations((0, 0, b)):
            Cylinder(ri, h, align=CB, mode=Mode.SUBTRACT)
    shp = bp.part
    try:
        shp = shp.fillet(0.4, shp.edges().filter_by_position("Z", -0.01, 0.01))
    except Exception:
        pass
    return shp


def liquid(ul):
    hgt = ul / BEAKER["ul_per_mm"]
    return Solid.make_cylinder(BEAKER["id"] / 2 - 0.02, hgt, Plane((0, 0, BEAKER["bottom"])))


def tip_rack():
    """FT-200L-RS stand-in at the measured footprint: tray, deck plate at plate_h (ASSUMED) with 96 holes, hinged lid
    folded back. Deck frame: rack A1 at TIPRACK_A1."""
    l, w = TIPRACK["l"], TIPRACK["w"]
    bx, by = TIPRACK_BOX
    ph = TIPRACK["plate_h"]
    with BuildPart() as tray:
        with Locations((bx + l / 2, by + w / 2, 0)):
            Box(l, w, ph - 1.5, align=CB)
            with Locations((0, 0, 1.4)):
                Box(l - 3.2, w - 3.2, ph, align=CB, mode=Mode.SUBTRACT)
    with BuildPart() as plate:
        with Locations((bx + l / 2, by + w / 2, ph - 1.5)):
            Box(l - 0.4, w - 0.4, 1.5, align=CB)
        for r in range(TIPRACK["rows"]):
            for c in range(TIPRACK["cols"]):
                with Locations((TIPRACK_A1[0] + c * TIPRACK["pitch"], TIPRACK_A1[1] + r * TIPRACK["pitch"], ph - 2)):
                    Cylinder(3.15, 4, align=CB, mode=Mode.SUBTRACT)
    with BuildPart() as lid:
        with Locations((bx + l / 2, by + w + 1.0, ph - 1.5)):
            Box(l, 2.0, w * 0.9, align=(Align.CENTER, Align.MIN, Align.MIN))
    return tray.part, plate.part, lid.part


# =====================================================================================================================
# reach checks: the head (hardware/tool-head) + the 3018's Z travel against these heights
# =====================================================================================================================
# known failures awaiting a decision (README "Reach report"): reported, but they do not fail the build
OPEN = {"tip pick-up: Z needed to seat a tip (mm)", "tip pick-up: lift to pull the tip out of the rack (mm)"}


def reach_checks():
    H = HS.safe_tip_h                                          # tip end above the bed at CNC z = 0
    cone_end0 = H + (HEAD.cone_end_z - HEAD.tip_end_z)         # adapter cone end at z = 0 (no tip)
    rim = M["floor"] + BEAKER["h"]
    floor_in = M["floor"] + BEAKER["bottom"]
    low = H - Z_TRAVEL                                         # lowest tip end
    z_pick = (TIPRACK["rim_h"] - TIP_ENGAGE - TIP_PRESS) - cone_end0
    tip_end_seated = TIPRACK["rim_h"] - HS.tip_len             # a tip sitting in the rack
    lift_out = TIPRACK["plate_h"] + 2.0 - tip_end_seated       # lift to bring its end 2 mm above the deck plate
    lift_avail = Z_TRAVEL + z_pick if z_pick >= -Z_TRAVEL else 0.0
    rows = []

    def chk(name, ok, value, need, note=""):
        rows.append({"name": name, "ok": bool(ok), "value": round(value, 2) if isinstance(value, float) else value,
                     "need": need, "note": note, "open": (not ok) and name in OPEN})

    chk("tip end above the beaker rims at z = 0 (mm)", H - rim >= 3.0, H - rim, ">= 3",
        "travel with a tip on, over every beaker")
    chk("lowest tip end above a beaker's inside floor (mm)", True, low - floor_in, "info",
        f"liquid below this is out of reach: {BEAKER['ul_per_mm'] * (low - floor_in + 1.0):.0f} uL dead volume "
        "with 1 mm immersion")
    chk("tip pick-up: Z needed to seat a tip (mm)", z_pick >= -Z_TRAVEL, z_pick, f">= {-Z_TRAVEL}",
        "adapter cone 0.5 mm past the seal on a tip standing in the rack")
    chk("tip pick-up: lift to pull the tip out of the rack (mm)", lift_out <= min(Z_TRAVEL, lift_avail or Z_TRAVEL),
        lift_out, f"<= {Z_TRAVEL:g} (all of Z)", "a seated tip hangs ~49 mm below the rack's deck plate")
    in_bed = all(0.0 <= v <= lim for (v, lim) in ((matrix_extent()[1], BED[0]), (matrix_extent()[3], BED[1])))
    chk("beaker matrix inside the bed (mm)", in_bed, [round(v, 1) for v in matrix_extent()], "0..300 x 0..180")
    return rows


# =====================================================================================================================
def tess(shape, tol=0.03, ang=0.15):
    v, t = shape.tessellate(tol, ang)
    return trimesh.Trimesh(np.array([tuple(p) for p in v]), np.array(t), process=True)


def main():
    (HERE / "step").mkdir(exist_ok=True)
    (HERE / "stl").mkdir(exist_ok=True)
    holder, labels = matrix_holder()
    part = holder.fuse(labels)
    export_step(part, str(HERE / "step" / "DECK-001.step"))
    m = tess(part, 0.02, 0.1)
    m.apply_translation(-m.bounds[0])
    m.export(HERE / "stl" / "DECK-001.stl")
    ex = matrix_extent()
    layout = {
        "frame": "machine deck coordinates (mm): x 0..300 right, y 0..180 back, z up from the bed top; grid = spindle XY",
        "tip_rack": {**TIPRACK, "box": list(TIPRACK_BOX), "a1": [round(v, 3) for v in TIPRACK_A1]},
        "beakers": {"a1": list(MATRIX_A1), "pitch": M["pitch"], "rows": M["rows"], "cols": M["cols"],
                    "mix_cols": MIX_COLS, "stocks": STOCKS, "beaker": BEAKER, "floor": M["floor"],
                    "rim_h": M["floor"] + BEAKER["h"], "holder_top_h": M["floor"] + M["pocket_depth"],
                    "extent": [round(v, 2) for v in ex]},
        "waste": list(WASTE), "eject_bar": list(EJECT_BAR),
        "head": {"tip_end_at_z0": HS.safe_tip_h, "z_travel": Z_TRAVEL, "tip_len": HS.tip_len,
                 "cone_engage": round(TIP_ENGAGE, 3)},
    }
    (HERE / "deck.json").write_text(json.dumps(layout, indent=1))
    rows = reach_checks()
    (HERE / "build").mkdir(exist_ok=True)
    (HERE / "build" / "report.json").write_text(json.dumps({"checks": rows, "holder_volume_cm3": round(part.volume / 1000, 1)},
                                                 indent=1))
    for r in rows:
        print(("ok  " if r["ok"] else "OPEN" if r["open"] else "FAIL"), r["name"], "|", r["value"], "|", r["need"], "|",
              r["note"])
    print(f"DECK-001: {part.volume / 1000:.1f} cm3, {m.extents.round(1)} mm")
    bad = [r for r in rows if not r["ok"] and not r["open"]]
    if bad:
        raise SystemExit(f"{len(bad)} deck check(s) failed")


if __name__ == "__main__":
    main()
