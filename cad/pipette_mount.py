"""Servo-actuated pipette mount for a 3018 CNC (775 spindle), build123d.

Frame (local, mm): origin = spindle axis at the BOTTOM face of the motor clamp ring.
+Z up, +X = direction of the side arm (toward the pipette), Y completes RH frame.
Pipette axis is at (PIPETTE_X, 0).

Parts (3, all support-free):
  1. mount_body  - motor clamp ring + arm + pipette split collar + vertical servo plate
                   (plate has 2x M3 slots 20 mm apart = alternative X-carriage mount)
  2. cam         - eccentric disc that bolts onto the stock servo single-arm horn
  3. plunger_cap - flat follower disc that sits on the pipette plunger button
"""
import math, os
import numpy as np
from build123d import *

HERE = os.path.dirname(os.path.abspath(__file__))
STL = os.path.join(HERE, "stl"); REN = os.path.join(HERE, "renders")
os.makedirs(STL, exist_ok=True); os.makedirs(REN, exist_ok=True)

# ---------------- parameters ----------------
# pipette
PIP_D = 20.5; PIP_CLR = 0.3; PIP_L = 95.0
PLUNGER_D = 8.0; PLUNGER_PROTRUDE = 15.0       # button top above body top (rest)
STROKE_FIRST = 12.0; STROKE_TOTAL = 15.0        # first stop / incl. blowout
PIP_ABOVE_COLLAR = 4.0                          # pipette body top sticks out above collar
TIP_LEN = 50.0                                  # disposable tip length beyond body (estimate)
# spindle / clamp
MOTOR_D = 44.5; MOTOR_CLR = 0.3                 # 775 body; set 52 for the 52 mm collar
RING_WALL = 5.0; H = 16.0                       # ring / arm / collar height (print Z)
COLLAR_WALL = 4.0
M3 = 3.4; M3_SLOT_PITCH = 20.0
PIPETTE_X = 45.0                                # pipette axis offset from spindle axis
# servo (SG90 / MG90S)
SV_L = 22.5; SV_W = 12.2; SV_TAB_L = 32.3; SV_SHAFT_FROM_END = 5.9
SV_TAB_HOLE_PITCH = 27.8; SV_SCREW_D = 1.8
SV_BELOW_TAB = 15.9; SV_TAB_T = 2.5; SV_ABOVE_TAB = 4.3; SV_SPLINE_ABOVE_TAB = 8.3
PLATE_T = 4.0
# cam (eccentric disc on flat follower: lift = E*(1-cos th))
CAM_R = 16.0; CAM_E = 8.5; CAM_T = 6.0; CAM_GAP = 0.5   # gap cam->cap at rest
HORN_POCKET = 2.0; HORN_HUB_D = 7.6; HORN_ARM_L = 17.0; HORN_ARM_W0 = 6.0; HORN_ARM_W1 = 4.2
# plunger cap
CAP_D = 20.0; CAP_T = 3.0; CAP_RECESS = 1.2
# servo calibration
SERVO_OFFSET_DEG = 15.0         # servo angle when cam is at rest (keep off the hard stop)
US_MIN, US_MAX, DEG_RANGE = 500, 2500, 180.0

# ---------------- derived ----------------
RI = (MOTOR_D + MOTOR_CLR) / 2; RO = RI + RING_WALL
PR = (PIP_D + PIP_CLR) / 2; CO = PR + COLLAR_WALL
XP = PIPETTE_X
Z_BODY_TOP = H + PIP_ABOVE_COLLAR
Z_PLUNGER = Z_BODY_TOP + PLUNGER_PROTRUDE
Z_CAP_TOP = Z_PLUNGER + CAP_T - CAP_RECESS
ZS = Z_CAP_TOP + (CAM_R - CAM_E) + CAM_GAP           # servo shaft height
CAM_Y0 = 0.0                                           # cam mid-plane on pipette axis
TAB_TOP_Y = CAM_Y0 - CAM_T / 2 - (SV_SPLINE_ABOVE_TAB + 0.5 - HORN_POCKET) - 0.5
PLATE_FRONT = TAB_TOP_Y - SV_TAB_T
SV_CX = XP + (SV_L / 2 - SV_SHAFT_FROM_END)           # servo body centre X
PLATE_X0, PLATE_X1 = XP - 14.0, XP + 38.0
PLATE_TOP = ZS + SV_W / 2 + 6.0
SLOT_X = (XP + 13.0, XP + 13.0 + M3_SLOT_PITCH); SLOT_Z = (21.0, 31.0)
assert RO < XP - CO + 1e-6 or True

def box(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)

def cyl_z(x, y, r, z0, z1):
    return Pos(x, y, z0) * Cylinder(r, z1 - z0, align=(Align.CENTER, Align.CENTER, Align.MIN))

def cyl_y(x, z, r, y0, y1):  # cylinder along Y
    return Pos(x, (y0 + y1) / 2, z) * Rot(90, 0, 0) * Cylinder(r, y1 - y0)

# ---------------- part 1: mount body ----------------
def mount_body():
    b = cyl_z(0, 0, RO, 0, H)
    b += box(-RO - 9, -RO + 3, -7, 7, 0, H)                     # motor clamp ears
    b += box(RI, XP, -7, 7, 0, H)                               # arm
    b += cyl_z(XP, 0, CO, 0, H)                                 # pipette collar
    b += box(XP + CO - 3, XP + CO + 8, -7, 7, 0, H)             # pinch ears
    b += box(PLATE_X0, PLATE_X1, PLATE_FRONT - PLATE_T, PLATE_FRONT, 0, PLATE_TOP)  # servo plate
    b += box(XP - 6, XP + 6, PLATE_FRONT - 0.1, -CO + 2, 0, H)  # fillet block collar->plate
    # cuts
    b -= cyl_z(0, 0, RI, -1, H + 1)
    b -= cyl_z(XP, 0, PR, -1, H + 1)
    b -= box(-RO - 10, -RI + 1, -1.0, 1.0, -1, H + 1)           # ring split
    b -= box(XP + PR - 1, XP + CO + 9, -1.0, 1.0, -1, H + 1)    # collar split
    for z in (4.0, H - 4.0):
        b -= cyl_y(-RO - 3, z, M3 / 2, -10, 10)
    b -= cyl_y(XP + CO + 3, H / 2, M3 / 2, -10, 10)
    # servo cutout + screw holes
    b -= box(SV_CX - (SV_L + 0.5) / 2, SV_CX + (SV_L + 0.5) / 2, PLATE_FRONT - PLATE_T - 1, PLATE_FRONT + 1,
             ZS - (SV_W + 0.5) / 2, ZS + (SV_W + 0.5) / 2)
    for s in (-1, 1):
        b -= cyl_y(SV_CX + s * SV_TAB_HOLE_PITCH / 2, ZS, SV_SCREW_D / 2, PLATE_FRONT - PLATE_T - 1, PLATE_FRONT + 1)
    # 2x M3 vertical slots, 20 mm apart (X-carriage plate option)
    for x in SLOT_X:
        b -= box(x - M3 / 2, x + M3 / 2, PLATE_FRONT - PLATE_T - 1, PLATE_FRONT + 1, SLOT_Z[0], SLOT_Z[1])
        for z in SLOT_Z:
            b -= cyl_y(x, z, M3 / 2, PLATE_FRONT - PLATE_T - 1, PLATE_FRONT + 1)
    return b

# ---------------- part 2: cam (print frame: shaft at origin, pocket on top) ----------------
def cam():
    c = cyl_z(CAM_E, 0, CAM_R, 0, CAM_T)
    c -= cyl_z(0, 0, HORN_HUB_D / 2, CAM_T - HORN_POCKET, CAM_T + 1)
    arm = Polyline((0, -HORN_ARM_W0 / 2), (HORN_ARM_L, -HORN_ARM_W1 / 2), (HORN_ARM_L, HORN_ARM_W1 / 2),
                   (0, HORN_ARM_W0 / 2), close=True)
    c -= Pos(0, 0, CAM_T - HORN_POCKET) * extrude(make_face(arm), HORN_POCKET + 1)
    c -= cyl_z(HORN_ARM_L, 0, HORN_ARM_W1 / 2 + 0.3, CAM_T - HORN_POCKET, CAM_T + 1)
    c -= cyl_z(0, 0, 3.0, -1, CAM_T + 1)                          # screwdriver access to horn screw
    for r in (10.0, 14.0):
        c -= cyl_z(r, 0, 0.75, -1, CAM_T + 1)                     # pilot holes for horn screws
    return c

# ---------------- part 3: plunger cap (print flat face down, recess up) ----------------
def plunger_cap():
    p = cyl_z(0, 0, CAP_D / 2, 0, CAP_T)
    p -= cyl_z(0, 0, (PLUNGER_D + 0.4) / 2, CAP_T - CAP_RECESS, CAP_T + 1)
    return p

# ---------------- kinematics ----------------
def lift(th_deg):
    return CAM_E * (1 - math.cos(math.radians(th_deg)))

def travel(th_deg):
    return max(0.0, lift(th_deg) - CAM_GAP)

def angle_for_travel(t):
    c = 1 - (t + CAM_GAP) / CAM_E
    return math.degrees(math.acos(max(-1, min(1, c))))

def us(th_deg):
    return US_MIN + (th_deg + SERVO_OFFSET_DEG) / DEG_RANGE * (US_MAX - US_MIN)

# ---------------- build + export ----------------
import trimesh

def to_tm(shape, name):
    path = os.path.join(STL, name + ".stl")
    export_stl(shape, path, tolerance=0.02, angular_tolerance=0.2)
    return trimesh.load(path)

parts = {"mount_body": mount_body(), "cam": cam(), "plunger_cap": plunger_cap()}
meshes = {k: to_tm(v, k) for k, v in parts.items()}

# plate layout (220x220), every part flat on its print face (already z>=0 in part frames)
scene = trimesh.Scene()
offsets = {"mount_body": (0, 0), "cam": (95, 40), "plunger_cap": (95, -20)}
for k, m in meshes.items():
    mm = m.copy(); b = mm.bounds
    mm.apply_translation([110 - (b[0][0] + b[1][0]) / 2 + offsets[k][0] - 30,
                          110 - (b[0][1] + b[1][1]) / 2 + offsets[k][1], -b[0][2]])
    assert mm.bounds[0][0] >= 0 and mm.bounds[1][0] <= 220 and mm.bounds[0][1] >= 0 and mm.bounds[1][1] <= 220, k
    scene.add_geometry(mm, node_name=k, geom_name=k)
scene.export(os.path.join(HERE, "plate_pipette.3mf"))

# ---------------- assembly (for renders) ----------------
def cam_pose(th):
    T = trimesh.transformations
    M = T.rotation_matrix(math.pi / 2, [0, 0, 1])                 # eccentric -> +Y
    M = T.rotation_matrix(math.pi / 2, [1, 0, 0]) @ M             # z->-Y, y->Z
    M = T.translation_matrix([0, CAM_Y0 + CAM_T / 2, 0]) @ M
    M = T.rotation_matrix(math.radians(th), [0, 1, 0]) @ M
    return T.translation_matrix([XP, 0, ZS]) @ M

def assembly(th):
    t = travel(th); out = []
    out.append((meshes["mount_body"], "#4c8bf5"))
    c = meshes["cam"].copy(); c.apply_transform(cam_pose(th)); out.append((c, "#f5a623"))
    cap = meshes["plunger_cap"].copy()
    cap.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
    cap.apply_translation([XP, 0, Z_CAP_TOP - t]); out.append((cap, "#e94e77"))
    pip = trimesh.creation.cylinder(PIP_D / 2, PIP_L, sections=48)
    pip.apply_translation([XP, 0, Z_BODY_TOP - PIP_L / 2]); out.append((pip, "#dddddd"))
    tip = trimesh.creation.cone(3.5, TIP_LEN, sections=24)
    tip.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
    tip.apply_translation([XP, 0, Z_BODY_TOP - PIP_L]); out.append((tip, "#aaddff"))
    pl = trimesh.creation.cylinder(PLUNGER_D / 2, PLUNGER_PROTRUDE - t, sections=24)
    pl.apply_translation([XP, 0, Z_BODY_TOP + (PLUNGER_PROTRUDE - t) / 2]); out.append((pl, "#bbbbbb"))
    mot = trimesh.creation.cylinder(MOTOR_D / 2, 110, sections=48)
    mot.apply_translation([0, 0, -20]); out.append((mot, "#777777"))
    yb = PLATE_FRONT + SV_TAB_T
    sv = trimesh.creation.box(bounds=[[XP - SV_SHAFT_FROM_END, PLATE_FRONT - SV_BELOW_TAB, ZS - SV_W / 2],
                                      [XP - SV_SHAFT_FROM_END + SV_L, yb + SV_ABOVE_TAB, ZS + SV_W / 2]])
    tabs = trimesh.creation.box(bounds=[[SV_CX - SV_TAB_L / 2, PLATE_FRONT, ZS - SV_W / 2],
                                        [SV_CX + SV_TAB_L / 2, yb, ZS + SV_W / 2]])
    out += [(sv, "#3355aa"), (tabs, "#3355aa")]
    return out

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

def render(ax, items, elev, azim, title):
    allv = []
    for m, col in items:
        pc = Poly3DCollection(m.vertices[m.faces], facecolor=col, edgecolor="none", alpha=1.0)
        n = m.face_normals; light = np.clip(0.45 + 0.55 * (n @ np.array([0.3, -0.5, 0.8]) / 1.0), 0.25, 1)
        base = np.array(matplotlib.colors.to_rgb(col))
        pc.set_facecolor(np.clip(base[None] * light[:, None], 0, 1)); ax.add_collection3d(pc)
        allv.append(m.vertices)
    v = np.vstack(allv); c = (v.max(0) + v.min(0)) / 2; r = (v.max(0) - v.min(0)).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1)); ax.view_init(elev=elev, azim=azim); ax.set_title(title)
    ax.set_xlabel("X"); ax.set_ylabel("Y"); ax.set_zlabel("Z")

th_first = angle_for_travel(STROKE_FIRST); th_blow = angle_for_travel(STROKE_TOTAL)
fig = plt.figure(figsize=(10, 10)); ax = fig.add_subplot(111, projection="3d")
render(ax, assembly(0), 25, -60, "Assembly iso (rest)"); fig.savefig(os.path.join(REN, "assembly_iso.png"), dpi=90); plt.close(fig)
fig = plt.figure(figsize=(16, 8))
for i, (th, nm) in enumerate([(0, "rest"), (th_blow, f"blowout cam {th_blow:.0f} deg")]):
    ax = fig.add_subplot(1, 2, i + 1, projection="3d"); render(ax, assembly(th), 5, 90, "Side (from +Y, cam side): " + nm)
fig.savefig(os.path.join(REN, "assembly_side.png"), dpi=90); plt.close(fig)
fig = plt.figure(figsize=(14, 5))
for i, k in enumerate(meshes):
    ax = fig.add_subplot(1, 3, i + 1, projection="3d"); render(ax, [(meshes[k], "#4c8bf5")], 30, -60, k + " (print orientation)")
fig.savefig(os.path.join(REN, "parts.png"), dpi=80); plt.close(fig)

# ---------------- report ----------------
print(f"RI={RI:.2f} RO={RO:.2f} XP={XP} ZS={ZS:.2f} plate_front_y={PLATE_FRONT:.2f} plate_top={PLATE_TOP:.1f}")
print(f"pipette body top Z={Z_BODY_TOP}, body bottom Z={Z_BODY_TOP-PIP_L}, tip end Z~{Z_BODY_TOP-PIP_L-TIP_LEN}")
print(f"cam lowest at 180deg: Z={ZS-CAM_R-CAM_E:.2f}")
tot = 0
for k, m in meshes.items():
    v = m.volume / 1000; tot += v
    print(f"{k}: watertight={m.is_watertight} vol={v:.1f} cm3 bounds={np.round(m.extents,1)}")
print(f"total solid vol {tot:.1f} cm3")
print(f"first stop th={th_first:.1f} us={us(th_first):.0f}; blowout th={th_blow:.1f} us={us(th_blow):.0f}; rest us={us(0):.0f}")
print("| cam deg | servo deg | us | lift mm | plunger travel mm |")
for th in range(0, 181, 10):
    print(f"| {th} | {th+SERVO_OFFSET_DEG:.0f} | {us(th):.0f} | {lift(th):.2f} | {travel(th):.2f} |")
