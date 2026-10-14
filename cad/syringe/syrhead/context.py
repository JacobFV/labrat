"""Bench context for renders: the 3018 (cad/cnc3018/cnc3018.glb, machine frame), deck labware at the sim's deck
positions, the bench top and the laptop - written as a microfab-style station (sidecar + parts/*.npz) in the HEAD
frame at a given CNC position, so Blender loads it next to the head station.

cnc3018.py models in (X = machine x - 150, Y = up, Z = machine y - 90) with the bed top at Y = 0; nodes are
modelled at machine (150, 90, 0). Its own syringe-head meshes are skipped (the B-rep head replaces them).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import trimesh
from build123d import Align, Box, BuildPart, Locations, Mode

from .spec import LAY, S

ROOT = Path(__file__).resolve().parents[3]
GLB = ROOT / "cad" / "cnc3018" / "cnc3018.glb"
SKIP_NODES = ("syringe_", "pipette_")
SKIP_MATS = ("mount", "servo", "syringe", "plunger", "rack", "pinion", "tip", "labeltxt")   # decal text is authored for
# the twin's mirrored view; in the true-handed machine frame it would read backwards
MAT_MAP = {"alu": "aluminum_6063_t5_clear_anodized", "black": "aluminum_6061_t6_black_anodized",
           "steel": "steel_hardened_ground", "stepper": "stepper_motor", "cap": "aluminum_6061_t6_machined",
           "brass": "brass_c360", "spindle": "steel_black_oxide", "collet": "stainless_304_brushed",
           "rubber": "epdm_rubber", "pcb": "pcb_green", "chip": "abs_black", "purple": "syr_heatsink_purple",
           "terminal": "terminal_green_yellow", "label": "label_vinyl", "labeltxt": "print_black",
           "copper": "copper_c101_ofhc"}


def _split_smooth(m: trimesh.Trimesh, angle=35.0) -> trimesh.Trimesh:
    return trimesh.graph.smooth_shade(m, angle=math.radians(angle))


def _write_npz(path: Path, m: trimesh.Trimesh):
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, points=m.vertices.astype(np.float32), normals=m.vertex_normals.astype(np.float32),
                        tris=m.faces.astype(np.int32))


def _tess(shape, tol=0.02, ang=0.12) -> trimesh.Trimesh:
    v, t = shape.tessellate(tol, ang)
    return trimesh.Trimesh(np.array([(p.X, p.Y, p.Z) for p in v]), np.array(t), process=True)


# ---------------------------------------------------------------------------------------------------------------------
# labware: cad/deck/deck.py (deck frame: machine x, y on the bed, z up from the bed top)
# ---------------------------------------------------------------------------------------------------------------------
def _deck():
    import importlib.util
    spec = importlib.util.spec_from_file_location("labrat_deck", ROOT / "cad" / "deck" / "deck.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def laptop():
    """thin 13" aluminium laptop: base 304 x 212 x 11, lid opened to 110 deg; frame: hinge line along x at y = 0."""
    with BuildPart() as base:
        with Locations((0, -106, 0)):
            Box(304, 212, 11, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, -80, 11)):
            Box(270, 110, 0.6, align=(Align.CENTER, Align.CENTER, Align.MAX), mode=Mode.SUBTRACT)   # keyboard well
        with Locations((0, -176, 11)):
            Box(120, 75, 0.15, align=(Align.CENTER, Align.CENTER, Align.MAX), mode=Mode.SUBTRACT)   # trackpad
    with BuildPart() as keys:
        for r in range(5):
            for c in range(14):
                with Locations((-123 + c * 19.0, -42 - r * 19.0, 10.4)):
                    Box(16.5, 16.5, 0.5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with BuildPart() as lid:
        with Locations((0, 0, 0)):
            Box(304, 5.5, 212, align=(Align.CENTER, Align.MIN, Align.MIN))
    with BuildPart() as screen:
        with Locations((0, -0.05, 106)):
            Box(286, 0.1, 186, align=(Align.CENTER, Align.MIN, Align.CENTER))
    return base.part, keys.part, lid.part, screen.part


# ---------------------------------------------------------------------------------------------------------------------
def write_context(out: Path, cnc=(150.0, 60.0, -10.0), tips_used=("A1", "A2", "B1"), liquid_wells=None) -> Path:
    """write parts/CTX-*.npz + stations/context.scene.json; return the sidecar path."""
    x, y, z = cnc
    parts = out / "parts"
    inst = {}

    def place(name, pn, mesh: trimesh.Trimesh, finish, M=np.eye(4), tags=("context",)):
        _write_npz(parts / f"{pn}.npz", mesh)
        inst[name] = {"prim": f"/context/{name}", "path": f"context/{name}", "pn": pn, "name": name, "finish": finish,
                      "material": finish, "kind": "buy", "process": "purchased", "category": "frame", "tags": list(tags),
                      "matrix": M.tolist()}

    # deck (machine x, y, bed top) -> head frame
    def deck_M(dx, dy, dz=0.0):
        return trimesh.transformations.translation_matrix([dx - x, dy - y, dz - (S.clamp_top_above_bed + z)])

    # ---- the 3018 from the GLB ----
    sc = trimesh.load(GLB)
    off = {"cnc_base": [0, 0, 0], "cnc_y_bed": [0, 0, -(y - 90)], "cnc_x_carriage": [x - 150, 0, 0], "cnc_z": [x - 150, z, 0]}
    # twin (X, Y, Z) -> machine (X + 150, Z + 90, Y) -> head frame (... - spindle position)
    T = np.array([[1, 0, 0, 150 - x], [0, 0, 1, 90 - y], [0, 1, 0, -(S.clamp_top_above_bed + z)], [0, 0, 0, 1]], float)
    # T has det -1 (axis relabel); trimesh flips the winding for it
    for gname in sc.graph.nodes_geometry:
        node, mat = gname.split("__")
        if node.startswith(SKIP_NODES) or mat in SKIP_MATS:
            continue
        root = next((k for k in off if node == k or node.startswith(k)), None)
        if root is None:
            continue
        M_world, gkey = sc.graph[gname]
        m = sc.geometry[gkey].copy()
        m.apply_transform(M_world)
        o = off[root]
        m.apply_translation(o)
        m.apply_transform(T)
        place(f"cnc_{gname}", f"CTX-CNC-{gname}", _split_smooth(m), MAT_MAP.get(mat, "aluminum_6061_t6_machined"),
              tags=("context", "cnc"))
    # ---- labware (cad/deck) ----
    D = _deck()
    tray, plate, lid = D.tip_rack()
    place("tip_rack_box", "CTX-TIPRACK-BOX", _tess(tray), "syr_pp_blue", deck_M(0, 0))
    place("tip_rack_plate", "CTX-TIPRACK-PLATE", _tess(plate), "syr_pp_natural", deck_M(0, 0))
    place("tip_rack_lid", "CTX-TIPRACK-LID", _tess(lid), "syr_pp_blue", deck_M(0, 0))
    from .vendor import pipette_tip
    _write_npz(parts / "CTX-TIP.npz", _tess(pipette_tip(), 0.01, 0.1))
    ROWS = "ABCDEFGH"
    ax, ay = D.TIPRACK_A1
    for r in range(8):
        for c in range(12):
            if f"{ROWS[r]}{c + 1}" in tips_used:
                continue
            M = deck_M(ax + c * 9.0, ay + r * 9.0, D.TIPRACK["rim_h"])
            inst[f"tip_{ROWS[r]}{c + 1}"] = {"prim": f"/context/tip_{ROWS[r]}{c + 1}", "path": "context/tip", "pn": "CTX-TIP",
                                             "name": "tip", "finish": "syr_tip_yellow", "material": "syr_tip_yellow", "kind": "buy",
                                             "process": "purchased", "category": "consumable", "tags": ["context", "tips"],
                                             "matrix": M.tolist()}
    holder, labels = D.matrix_holder()
    place("beaker_matrix", "DECK-001", _tess(holder, 0.03, 0.15), "syr_petg_white", deck_M(0, 0))
    place("beaker_matrix_labels", "DECK-001-LBL", _tess(labels, 0.02, 0.1), "print_black", deck_M(0, 0))
    _write_npz(parts / "CTX-BEAKER.npz", _split_smooth(_tess(D.beaker(), 0.02, 0.12)))
    liquid_wells = liquid_wells or {"A1": ("syr_dye_red", 2400), "A2": ("syr_dye_orange", 2600), "A3": ("syr_dye_yellow", 2400),
                                    "B1": ("syr_dye_orange", 1800), "B2": ("syr_dye_green", 2200), "C1": ("syr_dye_purple", 2000)}
    stocks = {"A8": "syr_water", "B8": "syr_dye_red", "C8": "syr_dye_yellow", "D8": "syr_dye_blue"}
    floor = D.M["floor"]
    for r in range(D.M["rows"]):
        for c in range(D.M["cols"]):
            w = f"{'ABCDEF'[r]}{c + 1}"
            x, y = D.well_xy(w)
            inst[f"beaker_{w}"] = {"prim": f"/context/beaker_{w}", "path": "context/beaker", "pn": "CTX-BEAKER",
                                   "name": "beaker", "finish": "syr_pp_clear", "material": "syr_pp_clear", "kind": "buy",
                                   "process": "purchased", "category": "fluidics", "tags": ["context"],
                                   "matrix": deck_M(x, y, floor).tolist()}
            fin, ul = (stocks[w], 4500) if w in stocks else liquid_wells.get(w, (None, 0))
            if fin:
                place(f"liq_{w}", f"CTX-LIQ-{w}", _tess(D.liquid(ul)), fin, deck_M(x, y, floor), tags=("context", "liquid"))
    # ---- bench top under the feet (twin Y = -72) + laptop to the operator's right ----
    feet_z = -72.0 - (S.clamp_top_above_bed + z)
    bench = trimesh.creation.box(extents=[1500.0, 800.0, 30.0])
    bench.apply_translation([150 - x, 120 - y, feet_z - 15.0])
    place("bench", "CTX-BENCH", bench, "syr_laminate_white")
    for sx in (-1, 1):                                    # powder-coated steel frame legs down to the floor
        for sy in (-1, 1):
            leg = trimesh.creation.box(extents=[50.0, 50.0, 1000.0 - 30.0])
            leg.apply_translation([150 - x + sx * 700, 120 - y + sy * 350, feet_z - 30.0 - (1000.0 - 30.0) / 2])
            place(f"bench_leg_{sx}{sy}", "CTX-BENCH-LEG", leg, "steel_powdercoat_grey")
    base, keys, lid, screen = laptop()
    Ml = trimesh.transformations.rotation_matrix(math.radians(-20), [0, 0, 1])
    Ml[:3, 3] = [150 - x + 420, 40 - y - 60, feet_z]
    place("laptop_base", "CTX-LAPTOP-BASE", _tess(base, 0.05), "aluminum_6063_t5_clear_anodized", Ml)
    place("laptop_keys", "CTX-LAPTOP-KEYS", _tess(keys, 0.05), "abs_black", Ml)
    Rl = Ml @ trimesh.transformations.rotation_matrix(math.radians(-20), [1, 0, 0])
    place("laptop_lid", "CTX-LAPTOP-LID", _tess(lid, 0.05), "aluminum_6063_t5_clear_anodized", Rl)
    place("laptop_screen", "CTX-LAPTOP-SCREEN", _tess(screen, 0.05), "hmi_screen", Rl)
    sidecar = {"station": "context", "units": "mm", "instances": inst, "assemblies": {}, "joints": [], "fx": [],
               "anchors": {}, "meta": {"cnc": list(cnc)}}
    p = out / "stations" / "context.scene.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sidecar))
    return p


def write_studio(out: Path) -> Path:
    """photo sweep (floor curving up into a back wall) under the head, for the head-only studio stills."""
    parts = out / "parts"
    R, W, floor_z, back_y = 260.0, 6000.0, -125.0, 340.0
    prof = [(-3000.0, floor_z)]
    for k in range(17):                                     # quarter circle from the floor to the wall
        a = -math.pi / 2 + (math.pi / 2) * k / 16
        prof.append((back_y - R + R * math.cos(a), floor_z + R + R * math.sin(a)))
    prof.append((back_y, floor_z + 3000.0))
    verts, faces = [], []
    for i, (y, z) in enumerate(prof):
        verts += [(-W / 2, y, z), (W / 2, y, z)]
        if i:
            a, b, c, d = 2 * i - 2, 2 * i - 1, 2 * i, 2 * i + 1
            faces += [(a, c, b), (b, c, d)]
    m = trimesh.Trimesh(np.array(verts), np.array(faces), process=True)
    m.fix_normals()
    if m.face_normals[:, 2].mean() < 0:
        m.invert()
    _write_npz(parts / "STUDIO-SWEEP.npz", _split_smooth(m, 60))
    sidecar = {"station": "studio", "units": "mm", "assemblies": {}, "joints": [], "fx": [], "anchors": {}, "meta": {},
               "instances": {"sweep": {"prim": "/studio/sweep", "path": "studio/sweep", "pn": "STUDIO-SWEEP", "name": "sweep",
                                       "finish": "syr_backdrop", "material": "syr_backdrop", "kind": "buy", "process": "purchased",
                                       "category": "frame", "tags": ["studio"], "matrix": np.eye(4).tolist()}}}
    p = out / "stations" / "studio.scene.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sidecar))
    return p
