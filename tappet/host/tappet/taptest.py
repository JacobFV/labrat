"""v0 acceptance test: N random taps at screen coordinates vs. touches reported by touchpad.html
(or the simulator's virtual touchscreen).  Reports registration rate, mean/p95/max error (mm) and
cycle time; writes CSV + JSON."""
from __future__ import annotations

import csv
import json
import random
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .calibration import uv_error_mm, uv_to_world
from .client import BodyError

CSV_FIELDS = ["i", "u", "v", "arm", "x", "y", "registered", "tu", "tv", "dx_mm", "dy_mm",
              "err_mm", "cycle_s", "error"]


def run_taptest(client, touch_log, cal: dict, n: int = 100, arm: str = "auto",
                margin: float = 0.06, seed: Optional[int] = None, hold_ms: Optional[int] = None,
                touch_timeout: float = 2.0, log: Callable[[str], None] | None = None,
                clock: Callable[[], float] = time.monotonic) -> dict:
    """``clock`` measures cycle time (the simulator passes its simulated clock)."""
    log = log or (lambda s: None)
    rng = random.Random(seed)
    z = float(cal["surface_z"])
    samples = []
    t_start = clock()
    for i in range(n):
        u = rng.uniform(margin, 1 - margin)
        v = rng.uniform(margin, 1 - margin)
        x, y = uv_to_world(cal, u, v)
        s = {"i": i, "u": round(u, 5), "v": round(v, 5), "arm": None, "x": round(x, 2),
             "y": round(y, 2), "registered": False, "tu": None, "tv": None, "dx_mm": None,
             "dy_mm": None, "err_mm": None, "cycle_s": None, "error": None}
        mark = touch_log.last_seq
        t0 = clock()
        try:
            r = client.tap(x, y, z, arm=arm, hold_ms=hold_ms, wait=True, timeout=30)
            s["arm"] = r.get("arm")
            ev = touch_log.wait_for(mark, touch_timeout)
        except BodyError as e:
            s["error"] = e.err
            ev = None
        s["cycle_s"] = round(clock() - t0, 3)
        if ev is not None:
            dx, dy, err = uv_error_mm(cal, u, v, ev["u"], ev["v"])
            s.update(registered=True, tu=round(ev["u"], 5), tv=round(ev["v"], 5),
                     dx_mm=round(dx, 3), dy_mm=round(dy, 3), err_mm=round(err, 3))
        samples.append(s)
        log(f"{i + 1:4d}/{n} u={u:.3f} v={v:.3f} arm={s['arm']} "
            + (f"err={s['err_mm']:.2f} mm" if s["registered"] else f"MISS {s['error'] or ''}"))
    duration = clock() - t_start
    errs = np.array([s["err_mm"] for s in samples if s["registered"]], dtype=float)
    reg = int(len(errs))
    results = {
        "n": n,
        "registered": reg,
        "registration_rate": reg / n if n else 0.0,
        "mean_mm": float(errs.mean()) if reg else None,
        "p95_mm": float(np.percentile(errs, 95)) if reg else None,
        "max_mm": float(errs.max()) if reg else None,
        "cycle_s": duration / n if n else None,
        "duration_s": duration,
        "arm": arm,
        "calibration_source": cal.get("source"),
        "samples": samples,
    }
    return results


def summary_text(res: dict) -> str:
    f = lambda v: "n/a" if v is None else f"{v:.2f}"  # noqa: E731
    return (f"taps {res['n']}  registered {res['registered']} ({100 * res['registration_rate']:.1f}%)  "
            f"error mean {f(res['mean_mm'])} mm  p95 {f(res['p95_mm'])} mm  max {f(res['max_mm'])} mm  "
            f"cycle {f(res['cycle_s'])} s")


def write_report(res: dict, out_dir: str | Path, stem: str | None = None) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = stem or time.strftime("taptest-%Y%m%d-%H%M%S")
    csv_p = out / f"{stem}.csv"
    json_p = out / f"{stem}.json"
    with open(csv_p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for s in res["samples"]:
            w.writerow({k: s.get(k) for k in CSV_FIELDS})
    json_p.write_text(json.dumps(res, indent=2))
    return csv_p, json_p
