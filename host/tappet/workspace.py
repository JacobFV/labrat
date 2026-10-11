"""Reachability of the target screen for each arm (joint limits + linkage included).

A screen point counts as reachable for an arm when IK succeeds both at the press depth
(``surface_z - press_mm``) and at the approach height (``surface_z + approach_mm``), i.e. the arm
can perform a full tap there.  Writes an SVG (always) and PNG (if matplotlib is installed) to
``host/out/``.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .calibration import default_calibration, uv_to_world
from .config import load_config
from .kinematics import Kinematics

COLORS = {"L": "#3b82f6", "R": "#f59e0b", "both": "#22c55e", "none": "#ef4444"}


def out_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "out"


def screen_coverage(cfg: dict | None = None, cal: dict | None = None, nu: int = 33, nv: int = 71,
                    press_mm: float | None = None, approach_mm: float | None = None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    cal = cal if cal is not None else default_calibration(cfg)
    kin = Kinematics(cfg)
    press = cfg["tool"]["default_press_mm"] if press_mm is None else press_mm
    approach = cfg["tool"]["default_approach_mm"] if approach_mm is None else approach_mm
    z = float(cal["surface_z"])
    us = np.linspace(0, 1, nu)
    vs = np.linspace(0, 1, nv)
    reach = {a: np.zeros((nv, nu), dtype=bool) for a in ("L", "R")}
    pts = np.zeros((nv, nu, 2))
    for i, v in enumerate(vs):
        for k, u in enumerate(us):
            x, y = uv_to_world(cal, u, v)
            pts[i, k] = (x, y)
            for a in ("L", "R"):
                reach[a][i, k] = kin.reachable(a, x, y, z - press) and kin.reachable(a, x, y, z + approach)
    tot = nu * nv
    cov = {
        "L": 100.0 * reach["L"].sum() / tot,
        "R": 100.0 * reach["R"].sum() / tot,
        "either": 100.0 * (reach["L"] | reach["R"]).sum() / tot,
        "both": 100.0 * (reach["L"] & reach["R"]).sum() / tot,
    }
    return {"us": us, "vs": vs, "pts": pts, "reach": reach, "coverage": cov,
            "z_press": z - press, "z_approach": z + approach, "cal": cal}


def desk_reach(cfg: dict, z: float, step: float = 4.0, xr=(-150, 150), yr=(-10, 290)) -> dict:
    kin = Kinematics(cfg)
    xs = np.arange(xr[0], xr[1] + 1e-9, step)
    ys = np.arange(yr[0], yr[1] + 1e-9, step)
    return {"xs": xs, "ys": ys, "L": kin.reach_grid("L", xs, ys, z), "R": kin.reach_grid("R", xs, ys, z)}


def _svg(cfg: dict, sc: dict, desk: dict, scale: float = 2.0) -> str:
    xr = (-150.0, 150.0)
    yr = (-20.0, 290.0)
    W = (xr[1] - xr[0]) * scale
    Hh = (yr[1] - yr[0]) * scale

    def X(x):
        return (x - xr[0]) * scale

    def Y(y):  # +y (toward the user) drawn downward
        return (y - yr[0]) * scale

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{Hh + 80:.0f}" '
         f'viewBox="0 0 {W:.0f} {Hh + 80:.0f}" font-family="sans-serif" font-size="12">',
         f'<rect width="100%" height="100%" fill="#ffffff"/>']
    st = desk["xs"][1] - desk["xs"][0]
    for a in ("L", "R"):
        g = desk[a]
        d = []
        for i, y in enumerate(desk["ys"]):  # run-length encode each row into one rect
            k = 0
            row = g[i]
            while k < len(row):
                if row[k]:
                    k0 = k
                    while k < len(row) and row[k]:
                        k += 1
                    x0 = desk["xs"][k0] - st / 2
                    d.append(f"M{X(x0):.1f} {Y(y - st / 2):.1f}h{(k - k0) * st * scale:.1f}"
                             f"v{st * scale:.1f}h{-(k - k0) * st * scale:.1f}z")
                else:
                    k += 1
        o.append(f'<path d="{"".join(d)}" fill="{COLORS[a]}" fill-opacity="0.12"/>')
    tray = cfg["tray"]
    o.append(f'<rect x="{X(tray["center_x"] - tray["pocket_w"] / 2):.1f}" '
             f'y="{Y(tray["center_y"] - tray["pocket_l"] / 2):.1f}" width="{tray["pocket_w"] * scale:.1f}" '
             f'height="{tray["pocket_l"] * scale:.1f}" fill="none" stroke="#666" stroke-dasharray="4 3"/>')
    pts, rL, rR = sc["pts"], sc["reach"]["L"], sc["reach"]["R"]
    du = (sc["us"][1] - sc["us"][0]) * cal_w(sc) * scale
    dv = (sc["vs"][1] - sc["vs"][0]) * cal_l(sc) * scale
    rad = max(1.0, min(du, dv) / 2.2)
    for i in range(pts.shape[0]):
        for k in range(pts.shape[1]):
            key = "both" if rL[i, k] and rR[i, k] else "L" if rL[i, k] else "R" if rR[i, k] else "none"
            x, y = pts[i, k]
            o.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{rad:.1f}" fill="{COLORS[key]}"/>')
    corners = [uv_to_world(sc["cal"], u, v) for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))]
    o.append('<polygon points="' + " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in corners)
             + '" fill="none" stroke="#111" stroke-width="1.5"/>')
    tx, ty = uv_to_world(sc["cal"], 0.5, 0.0)
    o.append(f'<text x="{X(tx):.1f}" y="{Y(ty) + 14:.1f}" text-anchor="middle" fill="#111">screen top</text>')
    for a, arm in cfg["arms"].items():
        o.append(f'<circle cx="{X(arm["base_x"]):.1f}" cy="{Y(arm["base_y"]):.1f}" r="7" fill="{COLORS[a]}" '
                 f'stroke="#111"/><text x="{X(arm["base_x"]):.1f}" y="{Y(arm["base_y"]) - 10:.1f}" '
                 f'text-anchor="middle">arm {a}</text>')
    nk = cfg["neck"]
    o.append(f'<circle cx="{X(nk["pan_axis_x"]):.1f}" cy="{Y(nk["pan_axis_y"]):.1f}" r="5" fill="#111"/>'
             f'<text x="{X(nk["pan_axis_x"]) + 8:.1f}" y="{Y(nk["pan_axis_y"]) + 4:.1f}">neck</text>')
    c = sc["coverage"]
    ly = Hh + 20
    o.append(f'<text x="10" y="{ly}">Screen reachability (tap at z={sc["z_press"]:.1f}..{sc["z_approach"]:.1f} mm): '
             f'L {c["L"]:.1f}%  R {c["R"]:.1f}%  either {c["either"]:.1f}%  both {c["both"]:.1f}%</text>')
    lx = 10
    for key, lab in (("L", "L only"), ("R", "R only"), ("both", "both"), ("none", "unreachable")):
        o.append(f'<circle cx="{lx + 6}" cy="{ly + 20}" r="6" fill="{COLORS[key]}"/>'
                 f'<text x="{lx + 16}" y="{ly + 24}">{lab}</text>')
        lx += 110
    o.append(f'<text x="10" y="{ly + 48}" fill="#666">world mm, +y (toward user) down, '
             f'faint = desk reach at surface</text>')
    o.append("</svg>")
    return "\n".join(o)


def cal_w(sc):
    return sc["cal"]["device"]["screen_w"]


def cal_l(sc):
    return sc["cal"]["device"]["screen_l"]


def _png(cfg: dict, sc: dict, desk: dict, path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    fig, ax = plt.subplots(figsize=(7, 7.5))
    for a in ("L", "R"):
        g = desk[a].astype(float)
        ax.contourf(desk["xs"], desk["ys"], g, levels=[0.5, 1.5], colors=[COLORS[a]], alpha=0.12)
    pts, rL, rR = sc["pts"], sc["reach"]["L"], sc["reach"]["R"]
    for key, mask in (("both", rL & rR), ("L", rL & ~rR), ("R", rR & ~rL), ("none", ~rL & ~rR)):
        if mask.any():
            ax.scatter(pts[mask][:, 0], pts[mask][:, 1], s=6, c=COLORS[key], label=key)
    corners = [uv_to_world(sc["cal"], u, v) for u, v in ((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))]
    ax.plot([p[0] for p in corners], [p[1] for p in corners], "k-", lw=1)
    for a, arm in cfg["arms"].items():
        ax.plot(arm["base_x"], arm["base_y"], "o", color=COLORS[a], mec="k", ms=10)
        ax.annotate(f"arm {a}", (arm["base_x"], arm["base_y"] - 14), ha="center")
    c = sc["coverage"]
    ax.set_title(f"Screen reach: L {c['L']:.1f}%  R {c['R']:.1f}%  either {c['either']:.1f}%  both {c['both']:.1f}%")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_aspect("equal")
    ax.invert_yaxis()
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return True


def run(cfg: dict | None = None, out: str | Path | None = None, cal: dict | None = None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    sc = screen_coverage(cfg, cal)
    desk = desk_reach(cfg, sc["z_press"])
    o = Path(out) if out else out_dir()
    o.mkdir(parents=True, exist_ok=True)
    svg_p = o / "workspace.svg"
    svg_p.write_text(_svg(cfg, sc, desk))
    png_p = o / "workspace.png"
    has_png = _png(cfg, sc, desk, png_p)
    summary = {"coverage_pct": {k: round(float(v), 2) for k, v in sc["coverage"].items()},
               "z_press": sc["z_press"], "z_approach": sc["z_approach"],
               "grid": [len(sc["us"]), len(sc["vs"])],
               "unreachable_uv": [[round(float(sc["us"][k]), 3), round(float(sc["vs"][i]), 3)]
                                  for i in range(len(sc["vs"])) for k in range(len(sc["us"]))
                                  if not (sc["reach"]["L"][i, k] or sc["reach"]["R"][i, k])],
               "files": [str(svg_p)] + ([str(png_p)] if has_png else [])}
    (o / "workspace.json").write_text(json.dumps(summary, indent=2))
    return summary
