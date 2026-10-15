"""microfab design checks on the head: interference, fastener, hole, motion, geometry, manufacturability, edge
(+ the accepted-problems filter), with the head's own permitted-sharp / accepted lists from ../checks.yaml.

    ~/Documents/microfab_3d_atlas_bundle/.venv/bin/python -m syrhead.check_mf [--fast]

Writes ../mf_checks.json and exits non-zero on any unsuppressed error. Warnings are listed with the reasons in
checks.yaml (`reviewed_warnings`).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

from .mf import mf_assembly

HERE = Path(__file__).resolve().parent.parent
CHECKS = ("interference", "fastener", "hole", "motion", "geometry", "manufacturability", "edge", "accepted")


def main(argv=None):
    from microfab.checks import run_checks
    from microfab.checks.base import Context

    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="41 motion poses instead of 401")
    ap.add_argument("--only", nargs="*", help="run only these checks (+ accepted)")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load((HERE / "checks.yaml").read_text())
    asm = mf_assembly()
    ctx = Context.create(sid="syr_head", fast=a.fast, pictures=False, accepted=cfg.get("accepted", []),
                         permitted_sharp=cfg.get("permitted", []))
    t0 = time.time()
    rep = run_checks(asm, ctx, checks=tuple(a.only) + ("accepted",) if a.only else CHECKS)
    fs = rep.findings if hasattr(rep, "findings") else rep
    rows = [{"check": f.check, "severity": f.severity, "pn": f.pn, "path": f.path, "message": f.message,
             "local": [round(float(v), 2) for v in f.local] if f.local is not None else None,
             "suppressed_by": getattr(f, "suppressed_by", None)} for f in fs]
    live = [r for r in rows if not r["suppressed_by"]]
    summary = {}
    for r in live:
        summary.setdefault(r["check"], {}).setdefault(r["severity"], 0)
        summary[r["check"]][r["severity"]] += 1
    errors = [r for r in live if r["severity"] == "error"]
    out = {"seconds": round(time.time() - t0, 1), "summary": summary, "errors": len(errors),
           "suppressed": len(rows) - len(live), "findings": live}
    if not a.only:
        (HERE / "mf_checks.json").write_text(json.dumps(out, indent=1))
    for r in live:
        if r["severity"] != "info":
            print(f"{r['severity']:7s} {r['check']:17s} {r['pn']:22s} {r['message'][:120]} @ {r['local']}")
    print(json.dumps(summary), f"errors={len(errors)} suppressed={out['suppressed']}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
