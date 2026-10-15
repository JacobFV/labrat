"""Adapter: the head as a microfab Assembly (~/Documents/microfab_3d_atlas_bundle), for its design checks
(interference, motion, fastener, hole, edge, geometry, manufacturability) and its Cycles render pipeline.

Run with the microfab venv: ~/Documents/microfab_3d_atlas_bundle/.venv/bin/python (imports ``microfab``).
"""
from __future__ import annotations

import os

os.environ.setdefault("MICROFAB_RENDER_HOST", "local")

from build123d import Location  # noqa: E402
from microfab.cad.core import Assembly, HolePurpose, part  # noqa: E402
from microfab.cad.library import hardware as HW  # noqa: E402

from . import probe as PR  # noqa: E402
from .assembly import SRC_HASH, build, group_motion, kin  # noqa: E402
from .spec import LAY, S  # noqa: E402


def _build_part(pn: str, src: str):
    """microfab builder: the shape of one head part (src = hash of the syrhead sources -> cache key)."""
    return build().parts[pn].make()


_MF: dict = {}


def mf_part(pn: str):
    if pn in _MF:
        return _MF[pn]
    d = build().parts[pn]
    if d.library:
        lib = d.library
        if lib[0] == "screw":
            p = HW.screw(lib[1], lib[2], lib[3])
        elif lib[0] == "heatset":
            p = HW.heatset_insert(lib[1])
        elif lib[0] == "nut":
            p = HW.hex_nut(lib[1])
        else:
            raise ValueError(lib)
    else:
        p = part(d.pn, d.name, builder=_build_part, params={"pn": d.pn, "src": SRC_HASH}, kind=d.kind,
                 material=d.material, process=d.process, category=d.category, vendor=d.vendor, vendor_pn=d.vendor_pn,
                 unit_cost_usd=d.cost, finish=d.finish, notes=(d.notes + (f" [part of {d.product}]" if d.product else "")),
                 unit=d.unit, qty_per=d.qty_per, min_wall_mm=d.min_wall,
                 holes=tuple(HolePurpose(h[0], tuple(float(c) for c in h[1]), h[2]) for h in d.holes),
                 tags=(("product:" + d.product),) if d.product else ())
    _MF[pn] = p
    return p


JOINTS = {
    # group: (kind, axis, origin, lower, upper, contacts)
    "carriage": ("prismatic", (0, 0, 1), (0, 0, 0), 0.0, S.stroke,
                 (("carriage", "gear_c"), ("stopper", "barrel"), ("plunger", "barrel"),
               ("carriage", "home_lever"),   # the carriage's finger presses the syr_home lever at its bottom stop
                  ("carriage", "body"),      # the rack on its guide rib: 0.15 mm, by design
                  ("disc_cap", "plunger"))),
    "gear_c": ("revolute", (1, 0, 0), (0, LAY.C[0], LAY.C[1]), 0.0, 360.0,
               (("gear_c", "carriage"), ("gear_c", "gear_b"), ("bearing_c*", "spacer_c*"), ("bearing_c*", "pin_c"))),
    "gear_b": ("revolute", (1, 0, 0), (0, LAY.B[0], LAY.B[1]), -360.0, 0.0,
               (("gear_b", "gear_c"), ("gear_b", "gear_a"), ("bearing_b*", "spacer_b*"), ("bearing_b*", "pin_b"))),
    "gear_a": ("revolute", (1, 0, 0), (0, LAY.A[0], LAY.A[1]), 0.0, 360.0,
               (("gear_a", "gear_b"),
                # the FS90R's own output shaft turns in its case bore, and its horn rides 0.3 mm above the case
                # tower: the vendor's clearances, inside the purchased product
                ("servo_spline", "servo"), ("servo_horn", "servo"))),
    # TM-120 probe arm: deployed (0, on its hard stop) -> retracted (SWING); +angle about -X lifts the electrode
    # forward and up
    "probe": ("revolute", (-1, 0, 0), (PR.EX, PR.PIVOT[0], PR.PIVOT[1]), 0.0, PR.SWING,
              (("probe_arm", "mod_probe"),         # the deployed hard stop: the arm's back face on the stop block
               ("probe_spline", "probe_servo"), ("probe_horn", "probe_servo"),   # inside the purchased SG90
               ("scr_probe_horn", "probe_servo"),   # its tip turns inside the spline's tapped bore (as scr_horn)
               ("probe_*", "probe_cable"), ("scr_probe_*", "probe_cable"),
               ("probe_cable_arm", "*"))),          # slack service loop across the joint
}


def mf_assembly(pos: float | None = None, phi: float = 0.0) -> Assembly:
    """the head as a microfab Assembly; moving groups are sub-assemblies with joints, posed at plunger ``pos``
    (default 0) and probe-arm angle ``phi`` (deg, 0 = deployed)."""
    H = build()
    root = Assembly("syr_head", tags=("syr_head",))
    subs = {"static": root}
    for g, (kind, axis, origin, lo, hi, contacts) in JOINTS.items():
        sub = root.sub(g, tags=(g,))
        subs[g] = sub
        root.joint(g, kind, sub, axis, origin=origin, lower=lo, upper=hi, home=0.0, contacts=contacts,
                   description={"carriage": "plunger travel (mm)", "gear_c": "output gear", "gear_b": "intermediate gear",
                                "gear_a": "servo gear", "probe": "probe arm swing (deg, 0 = deployed)"}[g])
        if pos or phi:
            sub.loc = group_motion(g, pos or 0.0, phi)
    inst = {}
    for i in H.insts:
        inst[i.name] = subs[i.group].add(mf_part(i.pn), i.loc, name=i.name, tags=i.tags)
    for b in H.bolts:
        tgt = subs[b.group]
        if b.kind == "set":
            raise NotImplementedError
        HW.bolt(tgt, b.head, b.axis, thread=b.thread, length=b.length, kind=b.kind,
                clamped=[inst[c] for c in b.clamped], receiver=inst[b.receiver], name=b.name)
    root.meta["kin"] = kin(S.stroke)
    return root
