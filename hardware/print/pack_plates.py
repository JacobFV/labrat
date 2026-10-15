"""Pack LabRat printable parts onto Ender-3 V3 SE plates (220 x 220).

usage: uv run --with trimesh --with numpy --with lxml --with networkx python pack_plates.py <out dir> <work dir>

Writes <out>/plate_XX_<name>.3mf (one object per part, bed coords 0..220)
and <work>/plate_XX_<name>/<part>.stl (each part translated to its bed
position, for CuraEngine), plus <work>/plates.json (read by slice_plates.py).
"""
import json, sys, os
import numpy as np
import trimesh

OUT, WORK = sys.argv[1], sys.argv[2]
BED = 220.0
GAP = 5.0

H = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # hardware/
# (file, qty, per-mesh slice overrides, note)
W6 = {"wall_line_count": 6}
TOOLHEAD = [
    ("tool-head/stl/SYR-100.stl", 1, {}, "head body, upright on the plug"),
    ("tool-head/stl/SYR-101.stl", 1, W6, "gear plate 2, outer face down, standoffs up"),
    ("tool-head/stl/SYR-102.stl", 1, {}, "flange clamp, upside down"),
    ("tool-head/stl/SYR-103.stl", 1, W6, "plunger carriage + rack, on its back face"),
    ("tool-head/stl/SYR-105.stl", 1, {}, "thumb-disc cap, upside down"),
    ("tool-head/stl/SYR-110.stl", 1, W6, "G1, gear face down"),
    ("tool-head/stl/SYR-111.stl", 1, W6, "G2/G3, G2 on the bed"),
    ("tool-head/stl/SYR-112.stl", 1, W6, "G4/G5, G4 on the bed"),
    ("tool-head/stl/TM-110.stl", 1, {}, "microscope module, on its side"),
    ("tool-head/stl/TM-120.stl", 1, {}, "probe bracket, +X face down"),
    ("tool-head/stl/TM-121.stl", 1, {}, "probe lid, flat"),
    ("tool-head/stl/TM-122.stl", 1, {}, "probe arm, -X face down"),
]
DECK = [("deck/stl/DECK-001.stl", 1, {}, "beaker matrix, base down")]
W4 = {"wall_line_count": 4}
W5 = {"wall_line_count": 5}
STAND = [
    ("phone-stand/stl/PS-101.stl", 1, W4, "base lid, flat"),
    ("phone-stand/stl/PS-110.stl", 1, W5, "pan housing, upright, socket down"),
    ("phone-stand/stl/PS-111.stl", 1, W4, "retainer ring, flat"),
    ("phone-stand/stl/PS-120.stl", 1, {"wall_line_count": 5, "infill_sparse_density": 25}, "yoke, upright, disc down (optional brim)"),
    ("phone-stand/stl/PS-130.stl", 1, W4, "cradle, on its back"),
    ("phone-stand/stl/PS-131.stl", 2, {"wall_line_count": 4, "infill_sparse_density": 100}, "clamp jaw (L and R identical), on its back"),
    ("phone-stand/stl/PS-132.stl", 1, {"wall_line_count": 4, "infill_sparse_density": 100}, "pinion, flat"),
    ("phone-stand/stl/PS-133.stl", 1, W4, "idler spacer, on end"),
]


def load(rel):
    m = trimesh.load(os.path.join(H, rel), force="mesh")
    m.apply_translation(-m.bounds[0])  # min corner to origin, z=0 on bed
    return m


class Plate:
    def __init__(self, name, material, margin, adhesion="skirt"):
        self.name, self.material, self.margin = name, material, margin
        self.adhesion = adhesion
        lo, hi = margin, BED - margin + GAP  # every rect carries its +x/+y gap
        self.free = [(lo, lo, hi - lo, hi - lo)]  # maxrects free list (x, y, w, h)
        self.items = []

    def try_place(self, w, h):
        best = None
        for (fx, fy, fw, fh) in self.free:
            for rot, (pw, ph) in ((0, (w, h)), (90, (h, w))):
                if pw + GAP <= fw + 1e-6 and ph + GAP <= fh + 1e-6:
                    score = (min(fw - pw, fh - ph), fy, fx)
                    if best is None or score < best[0]:
                        best = (score, fx, fy, rot, pw, ph)
        return best

    def occupy(self, x, y, w, h):
        # rect with the spacing gap included
        ox, oy, ow, oh = x, y, w + GAP, h + GAP
        nf = []
        for (fx, fy, fw, fh) in self.free:
            if ox >= fx + fw or ox + ow <= fx or oy >= fy + fh or oy + oh <= fy:
                nf.append((fx, fy, fw, fh)); continue
            if ox > fx: nf.append((fx, fy, ox - fx, fh))
            if ox + ow < fx + fw: nf.append((ox + ow, fy, fx + fw - ox - ow, fh))
            if oy > fy: nf.append((fx, fy, fw, oy - fy))
            if oy + oh < fy + fh: nf.append((fx, oy + oh, fw, fy + fh - oy - oh))
        # prune contained rects
        self.free = [a for i, a in enumerate(nf) if not any(
            j != i and a[0] >= b[0] - 1e-9 and a[1] >= b[1] - 1e-9 and a[0] + a[2] <= b[0] + b[2] + 1e-9
            and a[1] + a[3] <= b[1] + b[3] + 1e-9 and (a != b or j < i) for j, b in enumerate(nf))]


def pack(group, material, base_index, margin=5.0):
    items = []
    for rel, qty, ov, note in group:
        m = load(rel)
        for k in range(qty):
            items.append(dict(rel=rel, k=k, mesh=m, ov=ov, note=note))
    items.sort(key=lambda it: -it["mesh"].extents[0] * it["mesh"].extents[1])
    plates = []
    for it in items:
        w, h = it["mesh"].extents[:2]
        placed = False
        for p in plates:
            b = p.try_place(w, h)
            if b:
                placed = True; break
        if not placed:
            p = Plate(None, material, margin); plates.append(p)
            b = p.try_place(w, h)
            if not b:
                raise SystemExit(f"{it['rel']} ({w:.1f} x {h:.1f}) does not fit the bed")
        _, x, y, rot, pw, ph = b
        p.occupy(x, y, pw, ph)
        p.items.append(dict(it, x=x, y=y, rot=rot))
    return plates


def placed_mesh(it):
    m = it["mesh"].copy()
    if it["rot"]:
        m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 0, 1]))
        m.apply_translation(-m.bounds[0])
    m.apply_translation([it["x"], it["y"], 0])
    return m


def centre(meshes):
    """Shift a whole plate so its footprint is centred on the bed."""
    lo = np.min([m.bounds[0] for m in meshes], axis=0)
    hi = np.max([m.bounds[1] for m in meshes], axis=0)
    d = (BED - (hi[:2] - lo[:2])) / 2 - lo[:2]
    for m in meshes:
        m.apply_translation([d[0], d[1], 0])


plates = []
# PLA: tool head + deck
pla = pack(TOOLHEAD + DECK, "PLA", 0)
for p in pla: p.label = "pla_toolhead_deck"
plates += pla
# PETG: phone stand base alone (164 x 212: no room for a skirt), then the rest
base = Plate(None, "PETG", 0.0, adhesion="none")
m = load("phone-stand/stl/PS-100.stl")
base.items.append(dict(rel="phone-stand/stl/PS-100.stl", k=0, mesh=m, ov={"wall_line_count": 3, "infill_sparse_density": 10},
                       note="base, flat (164 x 212: no skirt, the start-gcode purge line primes)", x=0, y=0, rot=0))
base.label = "petg_stand_base"
plates.append(base)
st = pack(STAND, "PETG", 0)
for p in st: p.label = "petg_stand"
plates += st

manifest = []
os.makedirs(OUT, exist_ok=True)
for i, p in enumerate(plates, 1):
    name = f"plate_{i:02d}_{p.label}"
    meshes = [placed_mesh(it) for it in p.items]
    centre(meshes)
    sc = trimesh.Scene()
    pdir = os.path.join(WORK, name); os.makedirs(pdir, exist_ok=True)
    parts = []
    for it, mm in zip(p.items, meshes):
        pn = os.path.splitext(os.path.basename(it["rel"]))[0] + (f"_{it['k'] + 1}" if it["k"] else "")
        sc.add_geometry(mm, node_name=pn, geom_name=pn)
        f = os.path.join(pdir, pn + ".stl"); mm.export(f)
        lo, hi = mm.bounds
        parts.append(dict(pn=pn, src="hardware/" + it["rel"], stl=f, overrides=it["ov"], note=it["note"],
                          rot=it["rot"], bbox=[round(v, 1) for v in [*lo, *hi]]))
    sc.export(os.path.join(OUT, name + ".3mf"))
    lo = np.min([m.bounds[0] for m in meshes], axis=0); hi = np.max([m.bounds[1] for m in meshes], axis=0)
    manifest.append(dict(name=name, material=p.material, adhesion=p.adhesion, parts=parts,
                         footprint=[round(v, 1) for v in [*lo, *hi]]))

json.dump(manifest, open(os.path.join(WORK, "plates.json"), "w"), indent=1)
for pl in manifest:
    print(pl["name"], pl["material"], pl["footprint"], [p["pn"] for p in pl["parts"]])
