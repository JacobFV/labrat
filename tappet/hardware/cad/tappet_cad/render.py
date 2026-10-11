"""Headless PNG renders: build123d tessellation -> matplotlib Poly3DCollection (orthographic,
flat Lambert shading with a key + fill light, per-part colours)."""
from __future__ import annotations

import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import to_rgb  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

BG = "#f5f6f8"


def tess(shape, tol=0.25, ang=0.35):
    v, f = shape.tessellate(tol, ang)
    V = np.array([[p.X, p.Y, p.Z] for p in v], float)
    F = np.array(f, int)
    return V, F


def tess_item(item, tol=0.3, ang=0.4):
    """List of (V, F, colour) for an assembly Item (per-face colouring for 'highlight' faces)."""
    if not getattr(item, "highlight", None):
        return [(*tess(item.shape, tol, ang), item.color)]
    n, col = item.highlight
    n = np.asarray(n, float) / np.linalg.norm(n)
    faces = item.shape.faces()
    def h(f):
        c = f.center()
        return float(np.dot([c.X, c.Y, c.Z], n))
    cands = []
    for f in faces:
        try:
            fn = f.normal_at()
            if np.dot([fn.X, fn.Y, fn.Z], n) > 0.99:
                cands.append(f)
        except Exception:
            pass
    top = max((h(f) for f in cands), default=None)
    out = []
    for f in faces:
        c = col if (f in cands and abs(h(f) - top) < 0.05) else item.color
        out.append((*tess(f, tol, ang), c))
    return out


def _subdivide(V, F, max_edge):
    if max_edge is None or len(F) == 0:
        return V, F
    try:
        import trimesh
        V2, F2 = trimesh.remesh.subdivide_to_size(V, F, max_edge=max_edge, max_iter=6)
        return V2, F2
    except Exception:
        return V, F


def view_dir(elev, azim):
    e, a = math.radians(elev), math.radians(azim)
    return np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])


def render(meshes, path, elev=25, azim=-60, title=None, subtitle=None, size=(10, 8), dpi=130,
           max_edge=None, zoom=1.0, bounds=None):
    """meshes: list of (V, F, color)."""
    vd = view_dir(elev, azim)
    key = vd + np.array([0.35, -0.25, 0.65])
    key /= np.linalg.norm(key)
    fill = vd + np.array([-0.5, 0.4, 0.1])
    fill /= np.linalg.norm(fill)
    polys, cols = [], []
    allV = []
    for V, F, color in meshes:
        V, F = _subdivide(V, F, max_edge)
        if len(F) == 0:
            continue
        tri = V[F]
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        ln = np.linalg.norm(n, axis=1)
        keep = ln > 1e-12
        tri, n = tri[keep], n[keep] / ln[keep, None]
        facing = n @ vd
        n = np.where(facing[:, None] < 0, -n, n)       # two-sided
        base = np.array(to_rgb(color))
        k = np.clip(n @ key, 0, 1)
        f_ = np.clip(n @ fill, 0, 1)
        shade = 0.42 + 0.48 * k + 0.16 * f_
        rgb = np.clip(base[None, :] * shade[:, None] + 0.06 * (k[:, None] ** 8), 0, 1)
        polys.append(tri)
        cols.append(rgb)
        allV.append(V)
    if not polys:
        return
    polys = np.concatenate(polys)
    cols = np.concatenate(cols)
    allV = np.concatenate(allV)
    fig = plt.figure(figsize=size, dpi=dpi, facecolor=BG)
    ax = fig.add_subplot(111, projection="3d", proj_type="ortho", facecolor=BG)
    pc = Poly3DCollection(polys, facecolors=cols, edgecolors=cols, linewidths=0.15)
    ax.add_collection3d(pc)
    lo, hi = (allV.min(0), allV.max(0)) if bounds is None else bounds
    c = (lo + hi) / 2
    r = (hi - lo).max() / 2 / zoom
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    fig.subplots_adjust(0, 0, 1, 0.9)
    if title:
        fig.text(0.03, 0.96, title, fontsize=15, weight="bold", color="#222", va="top", family="DejaVu Sans")
    if subtitle:
        fig.text(0.03, 0.915, subtitle, fontsize=10, color="#555", va="top", family="DejaVu Sans")
    # axis triad legend
    fig.text(0.97, 0.03, "+x right  +y toward user  +z up", fontsize=8, color="#888", ha="right")
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
