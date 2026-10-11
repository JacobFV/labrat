"""Pack the printable parts into ready-to-slice build plates (default: Ender 3 V3 SE, 220 x 220 x 250).

Each plate groups parts that share one print profile, with every quantity already included, so a
plate is "open in Cura, pick the profile from plates/README.md, slice, print".

Outputs (hardware/cad/plates/):
  plateN_<name>.3mf   separate objects at their bed positions (Cura keeps the layout)
  plateN_<name>.stl   the same layout merged into one mesh (any slicer; centred on the bed)
  plateN_<name>.png   top-view preview
  plates.json         layout manifest (used by the README table)

Run:  uv run --with trimesh --with numpy --with matplotlib --with networkx --with lxml python plates.py [--shim 61|67|se] [--bed 220]
Requires the per-part STLs from build.py in stl/.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import trimesh

HERE = Path(__file__).resolve().parent
STL = HERE / "stl"
OUT = HERE / "plates"

MARGIN = 4.0   # keep-out from bed edge (the 210 mm base leaves 5 mm each side: use skirt, not brim)
GAP = 6.0      # spacing between parts

# (plate name, profile key, [(part, qty), ...]) — order matters: big parts first.
PLATES = [
    ("base_column_clamp", "structural", [("base", 1), ("phone_clamp", 1), ("column", 1)]),
    ("lid_turrets_neck", "strong", [("base_lid", 1), ("turret_L", 1), ("turret_R", 1),
                                    ("neck_pan_mount", 1), ("neck_tilt_bracket", 1), ("phone_clamp_jaw", 1)]),
    ("tray_shim", "structural", [("tray", 1), ("tray_shim_iphone_{shim}", 1)]),
    ("arm_links", "links", [("forearm", 2), ("wrist_link_b", 2), ("upper_arm", 2), ("linkage_rod", 2),
                            ("wrist_link_a", 2), ("elbow_triangle", 2), ("elbow_drive_link", 2)]),
    ("gears_stylus", "fine", [("yaw_gear_turret", 2), ("stylus_holder_L", 1), ("stylus_holder_R", 1),
                              ("yaw_gear_servo", 2), ("stylus_plunger", 2)]),
    ("cable_clips_PETG", "petg", [("cable_clip", 12)]),
]

PROFILES = {
    "structural": "PLA, 0.20 mm, 3 walls, 20% gyroid, adhesion: SKIRT (a brim would not fit around the base), supports: OFF",
    "strong": "PLA, 0.20 mm, 4 walls, 30% gyroid, no brim, supports: OFF",
    "links": "PLA, 0.20 mm, 4 walls, 40% gyroid, no brim, supports: OFF",
    "fine": "PLA, 0.12 mm, 4 walls, 100% infill, tree supports ON 'touching buildplate' (only the stylus holders need them), brim OFF",
    "petg": "PETG, 0.20 mm, 3 walls, 100% infill, supports: OFF",
}


def load(name: str) -> trimesh.Trimesh:
    m = trimesh.load(STL / f"{name}.stl", force="mesh")
    m.apply_translation(-m.bounds[0])  # min corner at origin
    return m


def footprint_rotate(m: trimesh.Trimesh) -> trimesh.Trimesh:
    """Lay the long side of the footprint along X (fewer, wider rows)."""
    ext = m.extents
    if ext[1] > ext[0]:
        m = m.copy()
        m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 0, 1]))
        m.apply_translation(-m.bounds[0])
    return m


def pack(meshes: list[tuple[str, trimesh.Trimesh]], bed: float, gap: float = GAP):
    """Shelf packing on a bed x bed plate. Returns [(label, mesh_at_bed_position)]; raises if it doesn't fit."""
    usable = bed - 2 * MARGIN
    placed, x, y, row_h = [], 0.0, 0.0, 0.0
    for label, m in meshes:
        m = footprint_rotate(m)
        w, d = m.extents[0], m.extents[1]
        if w > usable:  # try the other way round
            m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 0, 1]))
            m.apply_translation(-m.bounds[0])
            w, d = m.extents[0], m.extents[1]
        if w > usable or d > usable:
            raise SystemExit(f"{label} ({w:.1f} x {d:.1f}) does not fit a {bed} mm bed")
        if x > 0 and x + w > usable:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        if y + d > usable:
            raise SystemExit(f"plate overflow at {label}: needs y={y + d:.1f} > {usable:.1f}")
        m = m.copy()
        m.apply_translation([MARGIN + x, MARGIN + y, 0])
        placed.append((label, m))
        x += w + gap
        row_h = max(row_h, d)
    return placed


def check_overlaps(placed):
    boxes = [(l, m.bounds) for l, m in placed]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            (la, a), (lb, b) = boxes[i], boxes[j]
            if (a[0][0] < b[1][0] and b[0][0] < a[1][0] and a[0][1] < b[1][1] and b[0][1] < a[1][1]):
                raise SystemExit(f"overlap: {la} / {lb}")


def preview(placed, bed, title, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig, ax = plt.subplots(figsize=(6, 6.4), dpi=110)
    ax.add_patch(Rectangle((0, 0), bed, bed, fc="#eef1f5", ec="#333", lw=1.5))
    cmap = plt.get_cmap("tab20")
    for k, (label, m) in enumerate(placed):
        tris = m.vertices[m.faces][:, :, :2]
        up = m.face_normals[:, 2] > 0.2  # top-facing faces = silhouette
        from matplotlib.collections import PolyCollection
        ax.add_collection(PolyCollection(tris[up], fc=cmap(k % 20), ec="none", alpha=0.95))
        c = m.bounds.mean(axis=0)
        ax.text(c[0], c[1], label, ha="center", va="center", fontsize=6.5,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))
    ax.set_xlim(-5, bed + 5); ax.set_ylim(-5, bed + 5); ax.set_aspect("equal")
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("mm"); ax.tick_params(labelsize=7)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shim", default="61", choices=["61", "67", "se"],
                    help="which tray shim goes on plate 3 (61 = iPhone 6.1in, 67 = 6.7in, se = iPhone SE)")
    ap.add_argument("--bed", type=float, default=220.0)
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("plate*"):
        old.unlink()
    manifest = {"bed_mm": args.bed, "shim": args.shim, "plates": []}

    for n, (name, profile, items) in enumerate(PLATES, 1):
        meshes = []
        for part, qty in items:
            part = part.format(shim=args.shim)
            base = load(part)
            for i in range(qty):
                meshes.append((part if qty == 1 else f"{part} #{i + 1}", base.copy()))
        placed = pack(meshes, args.bed, gap=4.0 if profile == "links" else GAP)
        check_overlaps(placed)

        stem = f"plate{n}_{name}"
        scene = trimesh.Scene()
        for label, m in placed:
            scene.add_geometry(m, node_name=label.replace(" #", "_"), geom_name=label.replace(" #", "_"))
        scene.export(OUT / f"{stem}.3mf")

        merged = trimesh.util.concatenate([m for _, m in placed])
        merged.apply_translation([-args.bed / 2, -args.bed / 2, 0])  # centred like a normal STL import
        merged.export(OUT / f"{stem}.stl")

        preview(placed, args.bed, f"Plate {n}: {name}  ({PROFILES[profile].split(',')[0]})", OUT / f"{stem}.png")

        mass = sum(m.volume for _, m in placed) / 1000.0
        counts: dict[str, int] = {}
        for part, qty in items:
            counts[part.format(shim=args.shim)] = qty
        manifest["plates"].append({
            "n": n, "file": stem, "profile": profile, "settings": PROFILES[profile],
            "parts": counts, "pieces": len(placed),
            "solid_volume_cm3": round(mass, 1),
            "max_height_mm": round(max(m.bounds[1][2] for _, m in placed), 1),
        })
        print(f"plate {n}: {stem:28s} {len(placed):2d} pieces  solid {mass:6.1f} cm3  OK")

    (OUT / "plates.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
