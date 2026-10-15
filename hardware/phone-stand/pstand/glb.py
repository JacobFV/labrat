"""Minimal glTF 2.0 binary writer: named node hierarchy (translation/rotation), one mesh per node, PBR materials,
node extras. Units as given (mm)."""
from __future__ import annotations

import json
import struct

import numpy as np


def _hex(c):
    c = c.lstrip("#")
    return [int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


def _srgb_to_lin(v):
    return [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in v]


def write_glb(path, nodes, colors, extras=None):
    """nodes: list of dicts {name, parent|None, t (3), q (xyzw) optional, mesh: (V, N, F, material) | None, extras}.
    colors: {material: (hex, metallic, roughness)}."""
    bin_ = bytearray()
    views, accs, meshes, mats, mat_idx = [], [], [], [], {}

    def add_view(data: bytes, target):
        while len(bin_) % 4:
            bin_.append(0)
        off = len(bin_)
        bin_.extend(data)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data), "target": target})
        return len(views) - 1

    def mat(name):
        if name not in mat_idx:
            hexc, met, rough = colors.get(name, ("#888888", 0.0, 0.6))
            mats.append({"name": name, "pbrMetallicRoughness": {
                "baseColorFactor": _srgb_to_lin(_hex(hexc)) + [1.0], "metallicFactor": met, "roughnessFactor": rough},
                "doubleSided": False})
            mat_idx[name] = len(mats) - 1
        return mat_idx[name]

    out_nodes = []
    index = {}
    for n in nodes:
        nd = {"name": n["name"]}
        if n.get("t") is not None and any(abs(v) > 1e-9 for v in n["t"]):
            nd["translation"] = [float(v) for v in n["t"]]
        if n.get("q") is not None:
            nd["rotation"] = [float(v) for v in n["q"]]
        if n.get("extras"):
            nd["extras"] = n["extras"]
        if n.get("mesh") is not None:
            V, N, F, m = n["mesh"]
            V = np.asarray(V, np.float32)
            N = np.asarray(N, np.float32)
            F = np.asarray(F, np.uint32).reshape(-1)
            vp = add_view(V.tobytes(), 34962)
            accs.append({"bufferView": vp, "componentType": 5126, "count": len(V), "type": "VEC3",
                         "min": V.min(0).tolist(), "max": V.max(0).tolist()})
            ap = len(accs) - 1
            vn = add_view(N.tobytes(), 34962)
            accs.append({"bufferView": vn, "componentType": 5126, "count": len(N), "type": "VEC3"})
            an = len(accs) - 1
            vi = add_view(F.tobytes(), 34963)
            accs.append({"bufferView": vi, "componentType": 5125, "count": len(F), "type": "SCALAR"})
            ai = len(accs) - 1
            meshes.append({"name": n["name"], "primitives": [{"attributes": {"POSITION": ap, "NORMAL": an},
                                                              "indices": ai, "material": mat(m)}]})
            nd["mesh"] = len(meshes) - 1
        index[n["name"]] = len(out_nodes)
        out_nodes.append(nd)
    roots = []
    for k, n in enumerate(nodes):
        p = n.get("parent")
        if p is None:
            roots.append(k)
        else:
            out_nodes[index[p]].setdefault("children", []).append(k)
    gl = {"asset": {"version": "2.0", "generator": "labrat pstand (build123d)"}, "scene": 0,
          "scenes": [{"name": "phone_stand", "nodes": roots}], "nodes": out_nodes, "meshes": meshes,
          "materials": mats, "accessors": accs, "bufferViews": views, "buffers": [{"byteLength": len(bin_)}]}
    if extras:
        gl["scenes"][0]["extras"] = extras
    js = json.dumps(gl, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    while len(bin_) % 4:
        bin_.append(0)
    total = 12 + 8 + len(js) + 8 + len(bin_)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A))
        f.write(js)
        f.write(struct.pack("<II", len(bin_), 0x004E4942))
        f.write(bin_)
