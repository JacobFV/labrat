"""Global parameters for the Tappet CAD.

Everything kinematic comes from ``config/robot.json`` (the single source of truth, SPEC §3).
Everything else (print tolerances, hardware sizes, layout of non-kinematic features) lives here.

Frames
------
World (robot.json ``frames.world``): origin on the desk below the neck pan axis' column, +y toward the
user / tray, +z up, +x robot's right.  Arm L is at x<0, arm R at x>0.

Arm frame (per arm, used to model every arm part): origin on the desk directly under the turret
(yaw) axis.  ``u`` = radial tool direction, ``w`` = up, ``v = w x u`` (lateral, along the shoulder
axis).  Arm parts are modelled in a link-local frame (x along the link, y == v lateral, z in-plane
normal) so that a pose is a pure rotation about the lateral axis plus a translation.
The R arm is the exact mirror image of the L arm about the world YZ plane.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

CAD_DIR = Path(__file__).resolve().parents[1]
REPO = CAD_DIR.parents[1]
ROBOT_JSON = REPO / "config" / "robot.json"

CFG = json.loads(ROBOT_JSON.read_text())

# --------------------------------------------------------------------------------------------
# Kinematics (from robot.json, SPEC §3)
# --------------------------------------------------------------------------------------------
ARMS = CFG["arms"]
JOINTS = CFG["joints"]
LINKAGE = CFG["linkage"]
_A = ARMS["L"]
H = float(_A["shoulder_h"])          # shoulder pivot height above desk
R0 = float(_A["r0"])                 # shoulder radial offset from turret axis
L1 = float(_A["L1"])                 # upper arm pivot-to-pivot
L2 = float(_A["L2"])                 # forearm pivot-to-pivot (elbow -> wrist)
TOOL_DX = float(_A["tool_dx"])       # wrist pivot -> stylus tip, radial
TOOL_DZ = float(_A["tool_dz"])       # wrist pivot -> stylus tip, down
for _k in ("shoulder_h", "r0", "L1", "L2", "tool_dx", "tool_dz", "base_y"):
    assert ARMS["L"][_k] == ARMS["R"][_k], f"arms L/R differ in {_k}; CAD assumes mirror symmetry"
assert ARMS["L"]["base_x"] == -ARMS["R"]["base_x"]
ARM_BASE = {a: (float(ARMS[a]["base_x"]), float(ARMS[a]["base_y"])) for a in ("L", "R")}

YAW_GEAR = float(JOINTS["L_YAW"]["gear"])  # 1.5
GEAR_MODULE = 1.0
Z_SERVO_GEAR = 20
Z_TURRET_GEAR = int(round(Z_SERVO_GEAR * YAW_GEAR))
assert abs(Z_TURRET_GEAR / Z_SERVO_GEAR - YAW_GEAR) < 1e-9, "tooth counts must realise robot.json yaw gear"
GEAR_CENTER = GEAR_MODULE * (Z_SERVO_GEAR + Z_TURRET_GEAR) / 2.0  # 25 mm
GEAR_BACKLASH = 0.10                 # mm of circular backlash (split across both gears)

HOME = {k: float(v["home"]) for k, v in JOINTS.items()}
PARK = {k: float(v["park"]) for k, v in JOINTS.items()}

TRAY = CFG["tray"]
TRAY_FLOOR_Z = float(TRAY["floor_z"])
TRAY_CX, TRAY_CY = float(TRAY["center_x"]), float(TRAY["center_y"])
TRAY_POCKET_W, TRAY_POCKET_L = float(TRAY["pocket_w"]), float(TRAY["pocket_l"])
DEVICE = CFG["default_device"]

NECK = CFG["neck"]
PAN_X, PAN_Y = float(NECK["pan_axis_x"]), float(NECK["pan_axis_y"])
COLUMN_TOP_Z = float(NECK["column_top_z"])
TILT_Z = float(NECK["tilt_axis_z"])

TOOL = CFG["tool"]
STYLUS_TRAVEL = float(TOOL["stylus_travel"])
TIP_D = float(TOOL["tip_diameter"])

# --------------------------------------------------------------------------------------------
# Print tolerances / hardware
# --------------------------------------------------------------------------------------------
CLR = 0.2            # general sliding / fit clearance (per side)
M2_CLEAR = 2.3       # through hole
M2_TAP = 1.7         # self-tapping pilot into PLA
M3_CLEAR = 3.3       # through hole (pins: M3 shoulder/socket screws + nylock)
M3_TAP = 2.6         # self-tapping / thread-forming pilot
M3_HEAD_D = 6.0      # socket head + clearance
M3_HEAD_H = 3.2
M3_NUT_AF = 5.6      # hex nut trap across flats (+clearance)
PIN_HOLE = M3_CLEAR  # every revolute pin is an M3 screw used as an axle

BEARING_608 = dict(d=8.0, D=22.0, B=7.0, inner_land_d=12.0)

# --------------------------------------------------------------------------------------------
# Layout (non-kinematic)
# --------------------------------------------------------------------------------------------
DECK_Z = 30.0        # top of the base deck
DECK_T = 3.0
WALL = 2.6
BASE_X0, BASE_X1 = -105.0, 105.0
BASE_Y0, BASE_Y1 = -50.0, 70.0
BASE_R = 14.0
LID_T = 2.6

# yaw servo pinion sits GEAR_CENTER inboard of each turret axis (toward x=0)
def yaw_pinion_xy(arm: str) -> tuple[float, float]:
    bx, by = ARM_BASE[arm]
    s = -1.0 if bx < 0 else 1.0
    return (bx - s * GEAR_CENTER, by)

GEAR_Z0 = DECK_Z + 1.0           # bottom of both yaw gears
TURRET_GEAR_T = 6.0
PINION_T = 5.5
TURRET_PLATFORM_Z0 = GEAR_Z0 + TURRET_GEAR_T + 1.5   # turret printed body starts here
TURRET_PLATFORM_T = 4.0

# --- arm lateral (v) planes, L arm (R is mirrored) -----------------------------------------
# shoulder servo on -v, elbow servo on +v, horns face inward at |v| = HORN_FACE_V
HORN_FACE_V = 14.5
PLANES = {
    "upper_arm": (-16.0, -11.0),
    "elbow_triangle": (-10.0, -6.0),
    "ground_post": (-10.0, -6.0),
    "wrist_link_a": (-5.0, -1.0),
    "wrist_link_b": (-5.0, -1.0),
    "forearm": (0.0, 4.0),
    "linkage_rod": (5.0, 10.0),
    "stylus_holder": (5.0, 10.0),
    "elbow_drive_link": (11.0, 16.0),
}

CRANK_A = 25.0                       # elbow crank radius == forearm rear-arm length (parallelogram)
D_LEN, D_ANG = 22.0, 160.0           # wrist parallelogram #1 ground offset (fixed in turret)
E_LEN, E_ANG = 22.0, 42.0            # wrist parallelogram #2 offset (elbow triangle -> stylus holder)
D_VEC = (D_LEN * math.cos(math.radians(D_ANG)), D_LEN * math.sin(math.radians(D_ANG)))
E_VEC = (E_LEN * math.cos(math.radians(E_ANG)), E_LEN * math.sin(math.radians(E_ANG)))

# stylus holder geometry in the holder frame (origin = wrist pivot, axes = arm u/v/w)
SLEEVE_OD = 12.0
SLEEVE_BORE = 7.0
PLUNGER_D = 6.6
SLEEVE_Z0 = -27.0                    # sleeve bottom (relative to wrist pivot)
SLEEVE_Z1 = 3.0                      # sleeve top (closed cap)
PLUNGER_Z0 = -31.0                   # plunger bottom face (rest = fully extended)
PLUNGER_Z1 = -9.0
RETAIN_SCREW_Z = -20.0               # M2 retaining screw height in the sleeve

PRINT_BED = (220.0, 220.0, 250.0)

PLA_DENSITY = 1.24                   # g/cm^3
EFFECTIVE_FILL = 0.30                # mass fraction of a solid part (walls + infill)
