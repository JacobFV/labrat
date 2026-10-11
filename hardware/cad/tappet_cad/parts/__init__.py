"""Part registry: every printable part, its builder (model frame) and print metadata."""
from __future__ import annotations

from functools import lru_cache

from build123d import Plane

from . import (base, base_lid, cable_clip, column, elbow_drive_link, elbow_triangle, forearm, linkage_rod,
               neck_pan_mount, neck_tilt_bracket, phone_clamp, stylus_holder, stylus_plunger, tray, tray_shims,
               turret, upper_arm, wrist_link, yaw_gear_servo, yaw_gear_turret)

# name -> (builder, meta).  Handed parts are listed once; build.py exports _L and _R.
_REG = {
    "base": (base.make, base.META),
    "base_lid": (base_lid.make, base_lid.META),
    "column": (column.make, column.META),
    "neck_pan_mount": (neck_pan_mount.make, neck_pan_mount.META),
    "neck_tilt_bracket": (neck_tilt_bracket.make, neck_tilt_bracket.META),
    "phone_clamp": (phone_clamp.make, phone_clamp.META),
    "phone_clamp_jaw": (phone_clamp.make_jaw, phone_clamp.META_JAW),
    "turret": (turret.make, turret.META),
    "yaw_gear_servo": (yaw_gear_servo.make, yaw_gear_servo.META),
    "yaw_gear_turret": (yaw_gear_turret.make, yaw_gear_turret.META),
    "upper_arm": (upper_arm.make, upper_arm.META),
    "forearm": (forearm.make, forearm.META),
    "elbow_drive_link": (elbow_drive_link.make, elbow_drive_link.META),
    "linkage_rod": (linkage_rod.make, linkage_rod.META),
    "wrist_link_a": (wrist_link.make_a, wrist_link.META_A),
    "wrist_link_b": (wrist_link.make_b, wrist_link.META_B),
    "elbow_triangle": (elbow_triangle.make, elbow_triangle.META),
    "stylus_holder": (stylus_holder.make, stylus_holder.META),
    "stylus_plunger": (stylus_plunger.make, stylus_plunger.META),
    "tray": (tray.make, tray.META),
    **{k: ((lambda k=k: tray_shims.make_shim(k)), tray_shims.METAS[k]) for k in tray_shims.SHIMS},
    "cable_clip": (cable_clip.make, cable_clip.META),
}

NAMES = list(_REG)


def meta(name):
    return _REG[name][1]


@lru_cache(maxsize=None)
def model(name):
    """Part in its model (assembly) frame.  For handed parts this is the L version."""
    return _REG[name][0]()


def mirrored(name):
    """R version of a handed part: arm-frame models mirror about the lateral (y == v) plane."""
    return model(name).mirror(Plane.XZ)


def export_items():
    """(export_name, shape_in_model_frame, meta, qty) for every file that gets exported."""
    out = []
    for n in NAMES:
        m = meta(n)
        if m["handed"]:
            q = m["qty"] if n == "turret" else max(1, m["qty"] // 2)
            out.append((f"{n}_L", model(n), m, q))
            out.append((f"{n}_R", mirrored(n), m, q))
        else:
            out.append((n, model(n), m, m["qty"]))
    return out
