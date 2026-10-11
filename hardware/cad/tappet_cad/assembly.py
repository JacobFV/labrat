"""Assembly: poses every part in the world frame from joint angles (SPEC §3 conventions).

Arm chain (built in the L-arm frame; the R arm is the mirror image about the world YZ plane, using
psi_L = -psi_R, which reproduces SPEC FK for base_x = +85):

    world_from_arm = T(bx, by, 0) . Rz(psi) . Rz(90)        (arm x == radial u, z == up)
    shoulder S0 = (r0, H); elbow E = S0 + L1 dir(theta1); wrist W = E + L2 dir(theta2)

Driven links are posed directly by the joints; the passive links (linkage rod, wrist parallelogram
links, elbow triangle, stylus holder) are *solved* from circle intersections of their pin distances, so
the stylus pose is a genuine consequence of the linkage geometry and not copied from the FK formula.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from build123d import Plane

from . import parts as P
from .lib.bearing import bearing_608
from .lib.phones import phone_body, phone_screen
from .lib.servos import horn_model, servo_model
from .lib.util import T, Rx, Ry, Rz, apply, frame, place
from .params import (ARM_BASE, BEARING_608, CRANK_A, D_ANG, D_LEN, D_VEC, DECK_Z, E_ANG, E_LEN, E_VEC,
                     GEAR_CENTER, H, L1, L2, PAN_X, PAN_Y, R0, TILT_Z, TRAY_CX, TRAY_CY, TRAY_FLOOR_Z,
                     Z_SERVO_GEAR, Z_TURRET_GEAR, yaw_pinion_xy)
from .parts._common import ARM_SERVO, HORN_IN_ELBOW_LINK, HORN_IN_SHOULDER_LINK, SMALL_SERVO
from .parts._neck import (PAN_HORN_FRAME, PAN_SERVO_FRAME, PHONE_BACK_Y, PHONE_CZ, TILT_SERVO_FRAME, TILT_HORN_FRAME)
from .parts.stylus_plunger import tip_mock
from .parts.turret import SERVO_ORIGIN_V
from .parts.yaw_gear_servo import YAW_SERVO_Z

COLORS = dict(servo="#2b4c7e", servo_mg="#2d2d2d", horn="#f5f5f5", bearing="#9aa3ad", tip="#111111",
              phone="#1c1c1e", screen="#3b6ea5", head_screen="#2ecc71")


def d2(a):
    return np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])


def ang(v):
    return math.degrees(math.atan2(v[1], v[0]))


def circ_int(c0, r0, c1, r1, near):
    c0, c1 = np.asarray(c0, float), np.asarray(c1, float)
    d = np.linalg.norm(c1 - c0)
    if d > r0 + r1 + 1e-9 or d < abs(r0 - r1) - 1e-9:
        raise ValueError("linkage cannot close")
    a = (r0 ** 2 - r1 ** 2 + d ** 2) / (2 * d)
    h = math.sqrt(max(r0 ** 2 - a ** 2, 0.0))
    m = c0 + a * (c1 - c0) / d
    perp = np.array([-(c1 - c0)[1], (c1 - c0)[0]]) / d
    cands = [m + h * perp, m - h * perp]
    return min(cands, key=lambda p: np.linalg.norm(p - np.asarray(near)))


def link_pose(p2, theta):
    """Arm-frame pose of a link whose local x points at angle theta in the (u, w) plane."""
    return T(p2[0], 0.0, p2[1]) @ Ry(-theta)


@dataclass
class ArmSolution:
    psi: float
    th1: float
    th2: float
    pts: dict = field(default_factory=dict)
    angles: dict = field(default_factory=dict)


def solve_arm(psi, th1, th2) -> ArmSolution:
    s = ArmSolution(psi, th1, th2)
    S0 = np.array([R0, H])
    E = S0 + L1 * d2(th1)
    W = E + L2 * d2(th2)
    c_ang = th2 + 180.0
    Cp = S0 + CRANK_A * d2(c_ang)
    Fr = E + CRANK_A * d2(c_ang)                 # forearm rear pin (forearm local x = -a)
    G = S0 + np.array(D_VEC)
    Pd = circ_int(G, L1, E, D_LEN, near=E + np.array(D_VEC))
    t_ang = ang(Pd - E) - D_ANG
    Pe = E + E_LEN * d2(E_ANG + t_ang)
    Pb = circ_int(W, E_LEN, Pe, L2, near=W + np.array(E_VEC))
    h_ang = ang(Pb - W) - E_ANG
    s.pts = dict(S0=S0, E=E, W=W, Cp=Cp, Fr=Fr, G=G, Pd=Pd, Pe=Pe, Pb=Pb)
    s.angles = dict(upper_arm=th1, forearm=th2, elbow_drive_link=c_ang, linkage_rod=ang(Fr - Cp),
                    wrist_link_a=ang(Pd - G), elbow_triangle=t_ang, wrist_link_b=ang(Pb - Pe), stylus_holder=h_ang)
    return s


def arm_part_poses(sol: ArmSolution) -> dict:
    """Arm-frame 4x4 pose of each moving/arm part (model frames as built in parts/*)."""
    p, a = sol.pts, sol.angles
    poses = {
        "turret": np.eye(4),
        "yaw_gear_turret": np.eye(4),
        "upper_arm": link_pose(p["S0"], a["upper_arm"]),
        "forearm": link_pose(p["E"], a["forearm"]),
        "elbow_drive_link": link_pose(p["S0"], a["elbow_drive_link"]),
        "linkage_rod": link_pose(p["Cp"], a["linkage_rod"]),
        "wrist_link_a": link_pose(p["G"], a["wrist_link_a"]),
        "elbow_triangle": link_pose(p["E"], a["elbow_triangle"]),
        "wrist_link_b": link_pose(p["Pe"], a["wrist_link_b"]),
        "stylus_holder": link_pose(p["W"], a["stylus_holder"]),
    }
    poses["stylus_plunger"] = poses["stylus_holder"]
    poses["stylus_tip"] = poses["stylus_holder"]
    poses["horn_shoulder"] = poses["upper_arm"] @ HORN_IN_SHOULDER_LINK
    poses["horn_elbow"] = poses["elbow_drive_link"] @ HORN_IN_ELBOW_LINK
    poses["servo_shoulder"] = frame((R0, -SERVO_ORIGIN_V, H), (0, 0, 1), (1, 0, 0), (0, 1, 0))
    poses["servo_elbow"] = frame((R0, SERVO_ORIGIN_V, H), (0, 0, 1), (-1, 0, 0), (0, -1, 0))
    poses["bearing_608"] = T(0, 0, DECK_Z - BEARING_608["B"])
    return poses


def world_from_arm_L(psi):
    bx, by = ARM_BASE["L"]
    return T(bx, by, 0) @ Rz(psi) @ Rz(90)


def pinion_angle(psi_L):
    """Pinion rotation (deg, world, L arm) that meshes with the turret gear at yaw psi_L."""
    bx, by = ARM_BASE["L"]
    px, py = yaw_pinion_xy("L")
    phi = math.atan2(py - by, px - bx)
    psi_t = math.radians(psi_L + 90.0)
    phase_t = Z_TURRET_GEAR * (phi - psi_t)
    th = phi + math.pi - (math.pi - phase_t) / Z_SERVO_GEAR
    return math.degrees(th)


# explode offsets (arm frame for arm parts, world frame for the rest)
ARM_EXPLODE = {
    "bearing_608": (0, 0, 14), "yaw_gear_turret": (0, 0, 30), "turret": (0, 0, 55),
    "servo_shoulder": (0, -45, 55), "servo_elbow": (0, 45, 55),
    "upper_arm": (0, -32, 75), "horn_shoulder": (0, -38, 75), "elbow_drive_link": (0, 32, 75),
    "horn_elbow": (0, 38, 75),
    "elbow_triangle": (0, -22, 95), "wrist_link_a": (0, -10, 85), "wrist_link_b": (0, -10, 105),
    "forearm": (0, 2, 95), "linkage_rod": (0, 16, 85), "stylus_holder": (0, 20, 105),
    "stylus_plunger": (0, 20, 75), "stylus_tip": (0, 20, 60),
}
WORLD_EXPLODE = {
    "base_lid": (0, 0, -45), "column": (0, -30, 40), "servo_pan": (0, -30, 95), "neck_pan_mount": (0, -30, 125),
    "horn_pan": (0, -30, 110), "servo_tilt": (40, -30, 135), "neck_tilt_bracket": (0, -30, 150),
    "horn_tilt": (22, -30, 150), "phone_clamp": (0, 5, 165), "phone_clamp_jaw": (0, 5, 200),
    "head_phone": (0, 55, 165), "head_screen": (0, 55, 165),
    "tray": (0, 70, 0), "tray_shim": (0, 70, 30), "target_phone": (0, 70, 60), "target_screen": (0, 70, 60),
    "yaw_servo": (0, 0, -25), "yaw_gear_servo": (0, 0, 30),
}


@dataclass
class Item:
    name: str
    shape: object
    color: str
    group: str        # 'arm_L', 'arm_R', 'body', 'neck', 'tray', 'mock'
    printed: bool = True
    highlight: tuple | None = None   # (world normal, colour): render the outermost faces along it in colour


def arm_items(arm: str, psi: float, th1: float, th2: float, explode: bool = False) -> list[Item]:
    psi_L = psi if arm == "L" else -psi
    sol = solve_arm(psi_L, th1, th2)
    poses = arm_part_poses(sol)
    W = world_from_arm_L(psi_L)
    shapes = {
        "turret": P.model("turret"), "yaw_gear_turret": P.model("yaw_gear_turret"),
        "upper_arm": P.model("upper_arm"), "forearm": P.model("forearm"),
        "elbow_drive_link": P.model("elbow_drive_link"), "linkage_rod": P.model("linkage_rod"),
        "wrist_link_a": P.model("wrist_link_a"), "wrist_link_b": P.model("wrist_link_b"),
        "elbow_triangle": P.model("elbow_triangle"), "stylus_holder": P.model("stylus_holder"),
        "stylus_plunger": P.model("stylus_plunger"), "stylus_tip": tip_mock(),
        "horn_shoulder": horn_model(ARM_SERVO), "horn_elbow": horn_model(ARM_SERVO),
        "servo_shoulder": servo_model(ARM_SERVO), "servo_elbow": servo_model(ARM_SERVO),
        "bearing_608": bearing_608(),
    }
    mocks = {"stylus_tip": COLORS["tip"], "horn_shoulder": COLORS["horn"], "horn_elbow": COLORS["horn"],
             "servo_shoulder": COLORS["servo_mg"], "servo_elbow": COLORS["servo_mg"], "bearing_608": COLORS["bearing"]}
    out = []
    for n, shp in shapes.items():
        M = poses[n]
        if explode:
            M = T(*ARM_EXPLODE.get(n, (0, 0, 0))) @ M
        out.append(Item(f"{n}_{arm}", place(shp, W @ M), mocks.get(n, P.meta(n)["color"] if n in P.NAMES else "#888"),
                        f"arm_{arm}", printed=n not in mocks))
    # yaw servo + pinion (world-fixed, in the base)
    px, py = yaw_pinion_xy("L")
    Ms = frame((px, py, YAW_SERVO_Z), (0, 1, 0), (-1, 0, 0), (0, 0, 1))
    Mp = T(px, py, 0) @ Rz(pinion_angle(psi_L))
    if explode:
        Ms = T(*WORLD_EXPLODE["yaw_servo"]) @ Ms
        Mp = T(*WORLD_EXPLODE["yaw_gear_servo"]) @ Mp
    out.append(Item(f"yaw_servo_{arm}", place(servo_model(SMALL_SERVO), Ms), COLORS["servo"], f"arm_{arm}", False))
    out.append(Item(f"yaw_gear_servo_{arm}", place(P.model("yaw_gear_servo"), Mp), P.meta("yaw_gear_servo")["color"], f"arm_{arm}"))
    if arm == "R":
        for it in out:
            it.shape = it.shape.mirror(Plane.YZ)
    return out


def neck_matrices(pan, tilt):
    Mpan = T(PAN_X, PAN_Y, 0) @ Rz(pan) @ T(-PAN_X, -PAN_Y, 0)
    Mtilt = Mpan @ T(0, PAN_Y, TILT_Z) @ Rx(tilt) @ T(0, -PAN_Y, -TILT_Z)
    return Mpan, Mtilt


def body_items(q: dict, explode=False, shim="tray_shim_iphone_61") -> list[Item]:
    Mpan, Mtilt = neck_matrices(q["NECK_PAN"], q["NECK_TILT"])
    phone_M = frame((0, PHONE_BACK_Y, PHONE_CZ), (0, 0, 1), (1, 0, 0), (0, 1, 0))
    tgt_M = T(TRAY_CX, TRAY_CY, TRAY_FLOOR_Z)
    rows = [
        ("base", P.model("base"), np.eye(4), None, "body", True),
        ("base_lid", P.model("base_lid"), np.eye(4), None, "body", True),
        ("column", P.model("column"), np.eye(4), None, "body", True),
        ("servo_pan", servo_model(SMALL_SERVO), PAN_SERVO_FRAME, COLORS["servo"], "neck", False),
        ("neck_pan_mount", P.model("neck_pan_mount"), Mpan, None, "neck", True),
        ("horn_pan", horn_model(SMALL_SERVO), Mpan @ PAN_HORN_FRAME, COLORS["horn"], "neck", False),
        ("servo_tilt", servo_model(SMALL_SERVO), Mpan @ TILT_SERVO_FRAME, COLORS["servo"], "neck", False),
        ("neck_tilt_bracket", P.model("neck_tilt_bracket"), Mtilt, None, "neck", True),
        ("horn_tilt", horn_model(SMALL_SERVO), Mtilt @ TILT_HORN_FRAME, COLORS["horn"], "neck", False),
        ("phone_clamp", P.model("phone_clamp"), Mtilt, None, "neck", True),
        ("phone_clamp_jaw", P.model("phone_clamp_jaw"), Mtilt, None, "neck", True),
        ("head_phone", phone_body("android_head") + phone_screen("android_head"), Mtilt @ phone_M, COLORS["phone"], "neck", False),
        ("tray", P.model("tray"), np.eye(4), None, "tray", True),
        ("tray_shim", P.model(shim), np.eye(4), None, "tray", True),
        ("target_phone", phone_body("iphone_15_61") + phone_screen("iphone_15_61"), tgt_M, COLORS["phone"], "tray", False),
    ]
    out = []
    for n, shp, M, col, grp, printed in rows:
        if explode:
            M = T(*WORLD_EXPLODE.get(n, (0, 0, 0))) @ M
        color = col or P.meta(shim if n == "tray_shim" else n)["color"]
        hl = None
        if n in ("head_phone", "target_phone"):
            hl = (M[:3, 2].copy(), COLORS["head_screen"] if n == "head_phone" else COLORS["screen"])
        out.append(Item(n, place(shp, M), color, grp, printed, hl))
    return out


def full_assembly(q: dict, explode=False) -> list[Item]:
    items = body_items(q, explode)
    for arm in ("L", "R"):
        items += arm_items(arm, q[f"{arm}_YAW"], q[f"{arm}_SHOULDER"], q[f"{arm}_ELBOW"], explode)
    return items


def fk(arm, psi, th1, th2):
    """SPEC §3 forward kinematics (reference for the CAD check)."""
    from .params import ARMS
    a = ARMS[arm]
    r = a["r0"] + a["L1"] * math.cos(math.radians(th1)) + a["L2"] * math.cos(math.radians(th2)) + a["tool_dx"]
    z = a["shoulder_h"] + a["L1"] * math.sin(math.radians(th1)) + a["L2"] * math.sin(math.radians(th2)) - a["tool_dz"]
    x = a["base_x"] - r * math.sin(math.radians(psi))
    y = a["base_y"] + r * math.cos(math.radians(psi))
    return np.array([x, y, z])
