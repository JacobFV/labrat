"""3018 PRO-style desktop CNC + LabRat rack-and-pinion syringe head, parametric (build123d + trimesh).

Frame = three.js / digital-twin frame, millimetres, Y UP:
  X = deck x - 150   (machine X, 0..300 -> -150..150)
  Z = deck y - 90    (machine Y, front y=0 at Z=-90, back at +Z; gantry sits at the back)
  Y = height; bed top surface (deck) = 0.
Every moving node is modelled at machine centre x=150, y=90, z=0, so its node transform is identity
and the twin only has to set:
  cnc_y_bed.position.z      = -(y - 90)     (bed moves, gantry fixed - like a real 3018)
  cnc_x_carriage.position.x =  x - 150
  cnc_z.position.y          =  z            (0 .. -45)
  syringe_rack.position.y   = pos_mm - stroke_mm   (plunger height above its lowest point, 0 .. stroke)
  syringe_pinion.rotation.z = (pos_mm - stroke_mm) / pitch_r
The spindle motor is gone: the syringe actuator (cad/syringe) sits in the 52 mm clamp, coaxial with the old
spindle, so the commanded XY is the tip XY (deck_cal pipette_offset 0,0,0). The tip end is 60 mm above the
deck at z=0 (twin SAFE_H). The models are modelled at the top of the stroke (pos_mm = stroke_mm).

Run (after cad/syringe/syringe_3018_rack.py has written its STLs + report.json):
      uv run --python 3.12 --with build123d --with trimesh --with matplotlib --with numpy \
        --with networkx --with lxml python cnc3018.py
"""
import json, math, os, shutil
import numpy as np
import trimesh
from trimesh.transformations import rotation_matrix as rotm, translation_matrix as trm
from build123d import (Box, Cylinder, Polygon, Rectangle, Circle, SlotOverall, Pos, Rot, extrude,
                       Location, Plane, Text, Align)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REN = os.path.join(HERE, "renders"); os.makedirs(REN, exist_ok=True)

# ---------------- parameters ----------------
FLOOR = -72.0                 # feet bottom
FOOT_H = 8.0
RAIL_X = 160.0                # base side rails (2040) centre X
RAIL_Y0 = FLOOR + FOOT_H      # rail bottom
FRONT_Z, BACK_Z = -165.0, 175.0  # inner faces of the end plates
END_T = 8.0
YROD_X, YROD_Y = 62.0, -30.0
YSCREW_Y = -36.0
BED_W, BED_D, BED_T = 300.0, 180.0, 15.0
UP_X0, UP_T = 170.0, 10.0     # upright plate inner face X, thickness
UP_TOP = 205.0
XROD_Z = 73.0
XROD_Y = (120.0, 184.0); XSCREW_Y = 152.0
BEAM_Z = (95.0, 115.0)        # 2040 X beam
SPINDLE_Z = 0.0               # spindle/syringe axis (world Z) - commanded Y
HOLDER_Y = (139.0, 165.0)     # 52 mm clamp, 26 tall
ZPLATE_Z = (32.0, 40.0); ZROD_Z = 47.0; ZROD_X = 28.0
CARR_Z = (54.0, 61.0); CARR_Y = (70.0, 232.0)
SAFE_H = 60.0                 # tip end above the deck at CNC z=0
TIP_LEN, TIP_OVERLAP = 51.0, 5.0   # 200 uL tip, pressed 5 mm onto the luer nozzle
_rep = json.load(open(os.path.join(HERE, "..", "syringe", "report.json")))
SY = {**_rep["params"], "gear_center_x": _rep["gear_center_x"], "pitch_r": _rep["pitch_r"], "nozzle_end_z": _rep["nozzle_end_z"]}

TOL, ATOL = 0.12, 0.35        # tessellation

# ---------------- materials ----------------
MAT = {
    "alu":     ("#B8BCC2", 0.85, 0.38),
    "black":   ("#1A1A1C", 0.25, 0.55),
    "steel":   ("#D9DCE0", 1.0, 0.22),
    "stepper": ("#2B2B2E", 0.3, 0.5),
    "cap":     ("#C4C8CC", 0.9, 0.32),
    "brass":   ("#C9A54C", 1.0, 0.3),
    "spindle": ("#3A3F44", 0.6, 0.4),
    "collet":  ("#C0C4C8", 1.0, 0.2),
    "rubber":  ("#101010", 0.0, 0.9),
    "pcb":     ("#1E6B3C", 0.0, 0.55),
    "chip":    ("#151515", 0.1, 0.4),
    "purple":  ("#6B3FA0", 0.6, 0.4),
    "terminal": ("#2E9E4F", 0.0, 0.6),
    "label":   ("#E8E8E8", 0.0, 0.6),
    "labeltxt": ("#202020", 0.0, 0.6),
    "mount":   ("#2F6FBF", 0.0, 0.55),
    "rack":    ("#F28A1E", 0.0, 0.5),
    "pinion":  ("#D9473A", 0.0, 0.5),
    "servo":   ("#1E3A8A", 0.0, 0.45),
    "syringe": ("#E6ECF2", 0.0, 0.15),
    "plunger": ("#B7BCC4", 0.0, 0.4),
    "tip":     ("#F2EEDC", 0.0, 0.25),
    "copper":  ("#B87333", 1.0, 0.35),
}

PARTS = {}   # (node, mat) -> [trimesh]
def add(node, mat, mesh):
    PARTS.setdefault((node, mat), []).append(mesh)

def tess(shape):
    v, t = shape.tessellate(TOL, ATOL)
    return trimesh.Trimesh(np.array([(p.X, p.Y, p.Z) for p in v]), np.array(t), process=True)

def Rz2(axis):  # matrix mapping +Z to axis
    return {"x": rotm(math.pi / 2, [0, 1, 0]), "y": rotm(-math.pi / 2, [1, 0, 0]), "z": np.eye(4),
            "-x": rotm(-math.pi / 2, [0, 1, 0]), "-y": rotm(math.pi / 2, [1, 0, 0]), "-z": rotm(math.pi, [1, 0, 0])}[axis]

def box(node, mat, x0, x1, y0, y1, z0, z1):
    add(node, mat, trimesh.creation.box(bounds=[[x0, y0, z0], [x1, y1, z1]]))

def cyl(node, mat, r, axis, p0, length, sections=40):
    """cylinder of radius r along axis, starting at point p0 going +length."""
    m = trimesh.creation.cylinder(r, length, sections=sections)
    m.apply_translation([0, 0, length / 2])
    m.apply_transform(Rz2(axis)); m.apply_translation(p0)
    add(node, mat, m)

def cone(node, mat, r0, r1, axis, p0, length, sections=40):
    m = trimesh.creation.cylinder(1, length, sections=sections)
    z = m.vertices[:, 2]
    s = np.where(z < 0, r0, r1)
    m.vertices[:, 0] *= s; m.vertices[:, 1] *= s
    m.apply_translation([0, 0, length / 2]); m.apply_transform(Rz2(axis)); m.apply_translation(p0)
    add(node, mat, m)

def shape(node, mat, s, M=None):
    m = tess(s)
    if M is not None: m.apply_transform(M)
    add(node, mat, m)

# ---------------- T-slot extrusion profile (20-series) ----------------
def slot_poly(sx, sy, nx, ny):
    """slot polygon at surface point (sx,sy), inward normal (nx,ny)."""
    tx, ty = -ny, nx
    pts = [(-3.1, -0.2), (-3.1, 1.8), (-5.6, 1.8), (-3.0, 6.0), (3.0, 6.0), (5.6, 1.8), (3.1, 1.8), (3.1, -0.2)]
    return Polygon(*[(sx + u * tx + v * nx, sy + u * ty + v * ny) for u, v in pts], align=None)

def tslot_profile(cw, ch):
    """cw x ch cells of 20 mm. returns 2D sketch centred at origin (XY)."""
    W, H = 20 * cw, 20 * ch
    sk = Rectangle(W, H)
    for i in range(cw):
        cx = -W / 2 + 10 + 20 * i
        sk -= slot_poly(cx, H / 2, 0, -1); sk -= slot_poly(cx, -H / 2, 0, 1)
    for j in range(ch):
        cy = -H / 2 + 10 + 20 * j
        sk -= slot_poly(W / 2, cy, -1, 0); sk -= slot_poly(-W / 2, cy, 1, 0)
    for i in range(cw):
        for j in range(ch):
            sk -= Pos(-W / 2 + 10 + 20 * i, -H / 2 + 10 + 20 * j) * Circle(2.1)
    if cw * ch == 2:  # inner cavity of 2040
        if cw == 2: sk -= Rectangle(5, 7.5)
        else: sk -= Rectangle(7.5, 5)
    return sk

_prof_cache = {}
def extrusion(node, cw, ch, axis, p0, length, mat="alu"):
    """profile cw(local X) x ch(local Y) cells, extruded along axis from p0; local X/Y follow Rz2(axis)."""
    key = (cw, ch, round(length, 2))
    if key not in _prof_cache:
        s = extrude(tslot_profile(cw, ch), length)
        _prof_cache[key] = tess(s)
    m = _prof_cache[key].copy()
    m.apply_transform(Rz2(axis)); m.apply_translation(p0)
    add(node, mat, m)

# ---------------- NEMA17 stepper ----------------
_step_cache = {}
def stepper_local(L=40.0):
    if L in _step_cache: return _step_cache[L]
    c = 4.5; a = 21.0
    oct_ = Polygon((-a + c, -a), (a - c, -a), (a, -a + c), (a, a - c), (a - c, a), (-a + c, a), (-a, a - c), (-a, -a + c), align=None)
    capT = 8.0
    front = extrude(oct_, capT)
    for sx in (-15.5, 15.5):
        for sy in (-15.5, 15.5):
            front -= Pos(sx, sy, capT - 4) * Cylinder(1.5, 8.5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    a2, c2 = 20.5, 4.4
    oct2 = Polygon((-a2 + c2, -a2), (a2 - c2, -a2), (a2, -a2 + c2), (a2, a2 - c2), (a2 - c2, a2), (-a2 + c2, a2), (-a2, a2 - c2), (-a2, -a2 + c2), align=None)
    mid = Pos(0, 0, capT) * extrude(oct2, L - 2 * capT)
    # fins on the black stack
    for k in range(1, 6):
        mid -= Pos(0, 0, capT + k * (L - 2 * capT) / 6) * (Box(50, 50, 0.8) - Box(40.2, 40.2, 2))
    back = Pos(0, 0, L - capT) * extrude(oct_, capT)
    boss = Pos(0, 0, -2) * Cylinder(11, 2, align=(Align.CENTER, Align.CENTER, Align.MIN))
    shaft = Pos(0, 0, -24) * (Cylinder(2.5, 22, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Pos(4.5, 0, 0) * Box(5, 6, 30))
    conn = Pos(0, -21 - 3, L - 9) * Box(16, 6, 7)
    label = Pos(21.05, 0, L / 2) * Box(0.4, 30, 20)
    meshes = {"cap": [tess(front), tess(back)], "stepper": [tess(mid)], "steel": [tess(shaft)],
              "alu": [tess(boss)], "rubber": [tess(conn)], "label": [tess(label)]}
    try:
        txt = Pos(21.3, 0, L / 2) * Rot(0, 90, 0) * Rot(0, 0, 90) * extrude(Text("NEMA17  42-40", 3.2), 0.3)
        txt2 = Pos(21.3, 0, L / 2 - 5) * Rot(0, 90, 0) * Rot(0, 0, 90) * extrude(Text("1.8deg 1.5A", 2.6), 0.3)
        meshes["labeltxt"] = [tess(txt), tess(txt2)]
    except Exception as e:
        print("text skipped:", e)
    _step_cache[L] = meshes
    return meshes

def stepper(node, M, L=40.0):
    """stepper: local +Z = away from the shaft (body), shaft points -Z. M places it."""
    for mat, ms in stepper_local(L).items():
        for m in ms:
            m2 = m.copy(); m2.apply_transform(M); add(node, mat, m2)

def coupler(node, axis, p0, L=25.0):
    s = Cylinder(9.5, L, align=(Align.CENTER, Align.CENTER, Align.MIN))
    for k in range(4):
        s -= Pos(0, 0, 7 + k * 3) * (Cylinder(12, 1.2, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Cylinder(7, 2, align=(Align.CENTER, Align.CENTER, Align.MIN)))
    M = trm(p0) @ Rz2(axis); shape(node, "alu", s, M)

def lm10_block(node, axis, center, L=34.0, W=34.0, H=24.0):
    """SC10UU-style aluminium bearing block centred on a rod along axis."""
    s = Box(W, H, L)
    s -= Cylinder(5.6, L + 2)
    for sx in (-12, 12):
        for sz in (-L / 2 + 6, L / 2 - 6):
            s -= Pos(sx, H / 2, sz) * Cylinder(1.6, 8, rotation=(90, 0, 0))
    M = trm(center) @ Rz2(axis); shape(node, "alu", s, M)

def leadscrew(node, axis, p0, L):
    cyl(node, "brass", 4.0, axis, p0, L, 24)
    # thread rings for a visual thread
    n = int(L / 4)
    d = np.array({"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[axis], float)
    for k in range(n):
        m = trimesh.creation.torus(3.9, 0.45, major_sections=20, minor_sections=6)
        m.apply_transform(Rz2(axis)); m.apply_translation(np.array(p0) + d * (k * 4 + 2))
        add(node, "brass", m)

# ================= BASE (static) =================
B = "cnc_base"
# side rails 2040 along Z (40 tall)
for sx in (-RAIL_X, RAIL_X):
    extrusion(B, 1, 2, "z", (sx, RAIL_Y0 + 20, FRONT_Z), BACK_Z - FRONT_Z)
# cross member 2020 under the gantry
extrusion(B, 1, 1, "x", (-RAIL_X + 10, RAIL_Y0 + 10, 105), 2 * RAIL_X - 20)

# end plates (black, 8 mm) with lightening cutouts
def end_plate(z0):
    W, H = 2 * RAIL_X + 20, 58.0
    s = Pos(0, H / 2, 0) * Box(W, H, END_T)
    for cx in (-118, -84, 84, 118):
        s -= Pos(cx, H / 2 + 2, 0) * Rot(0, 0, 90) * extrude(SlotOverall(30, 14), END_T + 2, both=True)
    s -= Pos(0, H - 4, 0) * Box(60, 12, END_T + 2)        # leadscrew window top
    for sx in (-RAIL_X, RAIL_X):
        for sy in (10, 30):
            s -= Pos(sx, sy, 0) * Cylinder(2.6, END_T + 2)
    return s
shape(B, "black", end_plate(0), trm([0, RAIL_Y0, FRONT_Z - END_T / 2]))
shape(B, "black", end_plate(0), trm([0, RAIL_Y0, BACK_Z + END_T / 2]))
# bolt heads on end plates
for z in (FRONT_Z - END_T, BACK_Z + END_T):
    sgn = -1 if z < 0 else 1
    for sx in (-RAIL_X, RAIL_X):
        for sy in (10, 30):
            cyl(B, "steel", 3.0, "z" if sgn > 0 else "-z", (sx, RAIL_Y0 + sy, z), 3, 16)
# Y rods + rod holders
for sx in (-YROD_X, YROD_X):
    cyl(B, "steel", 5.0, "z", (sx, YROD_Y, FRONT_Z), BACK_Z - FRONT_Z, 40)
    for z in (FRONT_Z + 6, BACK_Z - 6):
        s = Box(36, 16, 12) - Pos(0, 4, 0) * Cylinder(5.05, 14)
        shape(B, "alu", s, trm([sx, YROD_Y - 4, z]))
# Y leadscrew, bearing, coupler, stepper (behind the back plate)
leadscrew(B, "z", (0, YSCREW_Y, FRONT_Z), BACK_Z - FRONT_Z - 25)
cyl(B, "steel", 8, "z", (0, YSCREW_Y, FRONT_Z - 2), 6, 30)
coupler(B, "z", (0, YSCREW_Y, BACK_Z - 25))
stepper(B, trm([0, YSCREW_Y, BACK_Z + END_T + 2]) @ np.eye(4))
# rubber feet
for sx in (-RAIL_X, RAIL_X):
    for z in (FRONT_Z + 15, BACK_Z - 15):
        cyl(B, "rubber", 11, "y", (sx, FLOOR, z), FOOT_H, 32)
        cyl(B, "steel", 3.5, "y", (sx, FLOOR + FOOT_H - 1, z), 1.5, 16)

# gantry uprights (black alu plates, trapezoid with cutouts)
def upright():
    zb0, zb1, zt0, zt1 = 10.0, 160.0, 48.0, 128.0
    h = UP_TOP - RAIL_Y0
    poly = Polygon((zb0, 0), (zb1, 0), (zt1, h), (zt0, h), align=None)
    s = extrude(poly, UP_T)   # in local XY = (Z, Y), thickness along +local Z
    s -= Pos(85, 62, 0) * extrude(SlotOverall(70, 28), UP_T * 3, both=True)
    s -= Pos(88, 118, 0) * Cylinder(15, UP_T * 3)
    s -= Pos(88, 158, 0) * Cylinder(9, UP_T * 3)
    for (zz, yy) in ((25, 10), (60, 10), (110, 10), (145, 10), (25, 30), (145, 30)):
        s -= Pos(zz, yy, 0) * Cylinder(2.6, UP_T * 3)
    return s
up = tess(upright())
for side in (-1, 1):
    m = up.copy()
    # local (u=Z, v=Y, w=thickness) -> world (X = side*(UP_X0 + w), Y = RAIL_Y0 + v, Z = u)
    M = np.array([[0, 0, side, side * UP_X0], [0, 1, 0, RAIL_Y0], [1, 0, 0, 0], [0, 0, 0, 1]], float)
    m.apply_transform(M)
    add(B, "black", m)
# X beam 2040 + top 2020 between uprights
extrusion(B, 1, 2, "x", (-UP_X0, 150, sum(BEAM_Z) / 2), 2 * UP_X0)
extrusion(B, 1, 1, "x", (-UP_X0, UP_TOP - 12, sum(BEAM_Z) / 2), 2 * UP_X0)
# X rods + leadscrew
for y in XROD_Y:
    cyl(B, "steel", 5.0, "x", (-UP_X0, y, XROD_Z), 2 * UP_X0, 40)
leadscrew(B, "x", (-UP_X0, XSCREW_Y, XROD_Z), 2 * UP_X0 - 26)
coupler(B, "x", (UP_X0 - 26, XSCREW_Y, XROD_Z))
stepper(B, trm([UP_X0 + UP_T + 1, XSCREW_Y, XROD_Z]) @ Rz2("x"))

# GRBL controller board on the left upright, outside face, USB toward the back
PX = -(UP_X0 + UP_T)
for (zz, yy) in ((60, 30), (130, 30), (60, 90), (130, 90)):
    cyl(B, "copper", 2.5, "-x", (PX, RAIL_Y0 + yy, zz), 8, 12)
box(B, "pcb", PX - 9.6, PX - 8, RAIL_Y0 + 22, RAIL_Y0 + 98, 50, 140)
for (y0, z0) in ((40, 62), (40, 92)):   # stepper drivers w/ heatsinks
    box(B, "chip", PX - 12, PX - 9.6, RAIL_Y0 + y0, RAIL_Y0 + y0 + 18, z0, z0 + 22)
    box(B, "purple", PX - 18, PX - 12, RAIL_Y0 + y0 + 3, RAIL_Y0 + y0 + 15, z0 + 4, z0 + 18)
    for k in range(5):
        box(B, "purple", PX - 24, PX - 18, RAIL_Y0 + y0 + 3, RAIL_Y0 + y0 + 15, z0 + 4 + k * 3.2, z0 + 5.6 + k * 3.2)
box(B, "chip", PX - 11.5, PX - 9.6, RAIL_Y0 + 70, RAIL_Y0 + 84, 70, 84)            # MCU
box(B, "collet", PX - 21, PX - 9.6, RAIL_Y0 + 68, RAIL_Y0 + 80, 128, 142)          # USB-B
box(B, "rubber", PX - 18, PX - 12, RAIL_Y0 + 71, RAIL_Y0 + 77, 141, 142.5)
box(B, "chip", PX - 21, PX - 9.6, RAIL_Y0 + 32, RAIL_Y0 + 44, 130, 142)            # DC jack
for k in range(4):
    box(B, "terminal", PX - 18, PX - 9.6, RAIL_Y0 + 86, RAIL_Y0 + 96, 56 + k * 16, 70 + k * 16)
cyl(B, "black", 3.5, "-x", (PX - 9.6, RAIL_Y0 + 60, 120), 9, 20)  # spindle MOSFET
cyl(B, "chip", 4.5, "-x", (PX - 9.6, RAIL_Y0 + 55, 135), 12, 20)  # cap

# ================= Y BED =================
YB = "cnc_y_bed"
def bed():
    W, T = BED_W, BED_T
    sk = Pos(0, -T / 2) * Rectangle(W, T)
    for k in range(6):
        cx = -W / 2 + 25 + k * 50
        sk -= Pos(cx, -1.2) * Rectangle(6.4, 2.6)
        sk -= Polygon((cx - 6, -2.5), (cx + 6, -2.5), (cx + 4, -7), (cx - 4, -7), align=None)
    return extrude(sk, BED_D)
shape(YB, "alu", bed(), trm([0, 0, -BED_D / 2]))
# end caps on the T-slot table (front/back) - thin black
box(YB, "black", -BED_W / 2, BED_W / 2, -BED_T, 0, -BED_D / 2 - 3, -BED_D / 2)
box(YB, "black", -BED_W / 2, BED_W / 2, -BED_T, 0, BED_D / 2, BED_D / 2 + 3)
# carriage: 4 bearing blocks + nut block
for sx in (-YROD_X, YROD_X):
    for z in (-55, 55):
        lm10_block(YB, "z", (sx, YROD_Y, z), L=34, W=34, H=28)
s = Box(30, 22, 26) - Pos(0, -3, 0) * Cylinder(5, 30)
shape(YB, "alu", s, trm([0, -BED_T - 11, 0]))
cyl(YB, "brass", 11, "z", (0, YSCREW_Y, 13), 3, 32)
cyl(YB, "brass", 7, "z", (0, YSCREW_Y, -13), 26, 32)

# ================= X CARRIAGE =================
XC = "cnc_x_carriage"
for y in XROD_Y:
    lm10_block(XC, "x", (0, y, XROD_Z), L=80, W=30, H=24) if False else None
    s = Box(80, 24, 24) - Cylinder(5.6, 90, rotation=(0, 90, 0))
    shape(XC, "alu", s, trm([0, y, XROD_Z]))
s = Box(34, 22, 24) - Cylinder(4.6, 40, rotation=(0, 90, 0))
shape(XC, "alu", s, trm([0, XSCREW_Y, XROD_Z]))
cyl(XC, "brass", 11, "x", (17, XSCREW_Y, XROD_Z), 3, 32)
# carriage plate (black) with cutouts
cp = Box(90, CARR_Y[1] - CARR_Y[0], CARR_Z[1] - CARR_Z[0])
for yy in (-40, 40):
    cp -= Pos(0, yy, 0) * Rot(0, 0, 0) * extrude(SlotOverall(40, 14), 20, both=True)
for sx in (-36, 36):
    for yy in (-70, -25, 25, 70):
        cp -= Pos(sx, yy, 0) * Cylinder(1.7, 20)
shape(XC, "black", cp, trm([0, sum(CARR_Y) / 2, sum(CARR_Z) / 2]))
for sx in (-36, 36):
    for yy in (-70, -25, 25, 70):
        cyl(XC, "steel", 2.8, "-z", (sx, sum(CARR_Y) / 2 + yy, CARR_Z[0]), 3, 16)
# Z rods + holders
for sx in (-ZROD_X, ZROD_X):
    cyl(XC, "steel", 5.0, "y", (sx, CARR_Y[0] + 4, ZROD_Z), CARR_Y[1] - CARR_Y[0] - 8, 32)
for y0 in (CARR_Y[0], CARR_Y[1] - 12):
    s = Box(80, 12, 18)
    for sx in (-ZROD_X, ZROD_X): s -= Pos(sx, 0, -2) * Cylinder(5.05, 14, rotation=(90, 0, 0))
    shape(XC, "black", s, trm([0, y0 + 6, ZROD_Z - 2]))
# Z leadscrew + coupler + stepper on top
leadscrew(XC, "y", (0, CARR_Y[0] + 6, ZROD_Z), CARR_Y[1] - CARR_Y[0] - 30)
coupler(XC, "y", (0, CARR_Y[1] - 24, ZROD_Z), L=22)
stepper(XC, trm([0, CARR_Y[1] + 1, ZROD_Z]) @ Rz2("y"))

# ================= Z ASSEMBLY (moves with z) =================
Z = "cnc_z"
zp = Box(76, 92, ZPLATE_Z[1] - ZPLATE_Z[0])
zp -= Pos(0, 20, 0) * Cylinder(10, 20)
for sx in (-30, 30):
    for yy in (-38, 38):
        zp -= Pos(sx, yy, 0) * Cylinder(1.7, 20)
ZP_Y = (122.0, 214.0)
shape(Z, "alu", zp, trm([0, sum(ZP_Y) / 2, sum(ZPLATE_Z) / 2]))
for sx in (-ZROD_X, ZROD_X):
    for yc in (ZP_Y[0] + 16, ZP_Y[1] - 16):
        s = Box(22, 30, ZROD_Z - ZPLATE_Z[1] + 9) - Pos(0, 0, 0) * Cylinder(5.4, 34, rotation=(90, 0, 0))
        shape(Z, "alu", s, trm([sx, yc, ZROD_Z]))
s = Box(24, 18, 18) - Cylinder(4.5, 22, rotation=(90, 0, 0))
shape(Z, "alu", s, trm([0, 175, ZROD_Z]))
cyl(Z, "brass", 11, "y", (0, 184, ZROD_Z), 3, 32)
# 52 mm spindle clamp
hb = Box(68, HOLDER_Y[1] - HOLDER_Y[0], ZPLATE_Z[0] - (SPINDLE_Z - 34))
hb = Pos(0, 0, (ZPLATE_Z[0] + SPINDLE_Z - 34) / 2) * hb
hb -= Pos(0, 0, SPINDLE_Z) * Cylinder(26, 60, rotation=(90, 0, 0))
hb -= Pos(0, 0, SPINDLE_Z - 30) * Box(3, 60, 12)          # split
hb = hb.fillet(1.5, []) if False else hb
shape(Z, "black", hb, trm([0, sum(HOLDER_Y) / 2, 0]))
for yy in (HOLDER_Y[0] + 7, HOLDER_Y[1] - 7):
    cyl(Z, "steel", 2.8, "x", (12, yy, SPINDLE_Z - 30), 5, 16)
    cyl(Z, "steel", 1.6, "-x", (12, yy, SPINDLE_Z - 30), 24, 12)
# ---------------- LabRat syringe actuator (replaces the 775 spindle; ../syringe/syringe_3018_rack.py) ----------------
# part frame: z up from the plug bottom, syringe axis x=y=0, +y = toward the machine back (the clamp block),
# rack/pinion at +x, servo plate at -y.  -> world: X=-x, Y=z+SY_Z0, Z=y (proper rotation, det +1)
SY_Z0 = HOLDER_Y[1] - SY["plug_len"]   # the plug lip rests on the clamp top face
Mp = np.array([[-1, 0, 0, 0], [0, 0, 1, SY_Z0], [0, 1, 0, SPINDLE_Z], [0, 0, 0, 1]], float)
assert abs(np.linalg.det(Mp[:3, :3]) - 1) < 1e-9
SYSTL = os.path.join(ROOT, "cad", "syringe", "stl")
def sy(node, mat, fn, M=Mp):
    m = trimesh.load(os.path.join(SYSTL, fn)); m.apply_transform(M); add(node, mat, m)
sy(Z, "mount", "mount_body.stl")
sy(Z, "servo", "mock_servo.stl")
sy(Z, "syringe", "mock_syringe.stl")
sy("syringe_rack", "rack", "rack.stl")              # rack + fork move with the plunger
sy("syringe_rack", "plunger", "mock_plunger.stl")
# pinion: node frame at the servo shaft so the twin only sets rotation.z (shaft axis = part y = world Z)
PIVOT = Mp @ np.array([SY["gear_center_x"], 0, SY["servo_shaft_z"], 1.0])
sy("syringe_pinion", "pinion", "pinion.stl", trm(-PIVOT[:3]) @ Mp)
# servo lead
box(Z, "rubber", -(SY["gear_center_x"] + 18), -(SY["gear_center_x"] + 14), SY_Z0 + SY["servo_shaft_z"] - 2,
    SY_Z0 + SY["servo_shaft_z"] + 2, -40, -26)
# disposable 200 uL tip pressed onto the luer nozzle (separate node so the twin can hide it)
NOZZLE_Y = SY_Z0 + SY["nozzle_end_z"]
TIP_Y = NOZZLE_Y + TIP_OVERLAP - TIP_LEN
assert abs(TIP_Y - SAFE_H) < 0.5, f"tip end {TIP_Y:.1f} != SAFE_H {SAFE_H}: change syringe plug_len"
cone("pipette_tip", "tip", 0.45, 2.75, "y", (0, TIP_Y, SPINDLE_Z), TIP_LEN - 8, 32)
cone("pipette_tip", "tip", 2.75, 3.1, "y", (0, TIP_Y + TIP_LEN - 8, SPINDLE_Z), 8, 32)

# ================= assemble scene =================
HIER = {"cnc_base": None, "cnc_y_bed": None, "cnc_x_carriage": None, "cnc_z": "cnc_x_carriage",
        "syringe_rack": "cnc_z", "syringe_pinion": "cnc_z", "pipette_tip": "cnc_z"}
NODE_M = {n: np.eye(4) for n in HIER}; NODE_M["syringe_pinion"] = trm(PIVOT[:3])

def pbr(mat):
    c, met, rough = MAT[mat]
    rgb = [int(c[i:i + 2], 16) for i in (1, 3, 5)]
    return trimesh.visual.material.PBRMaterial(name=mat, baseColorFactor=rgb + [255], metallicFactor=met,
                                               roughnessFactor=rough, doubleSided=False)

def merged():
    out = {}
    for (node, mat), ms in PARTS.items():
        m = trimesh.util.concatenate(ms)
        m.merge_vertices(); m.remove_unreferenced_vertices()
        out[(node, mat)] = m
    return out

MER = merged()

# clearance: everything on the syringe head above the clamp must stay in front of the Z plate / Z stepper
_hv = np.vstack([m.vertices for (n, mat), m in MER.items() if n.startswith("syringe") or mat in ("mount", "servo", "syringe")])
_hv[:, 1] += 0  # rack is modelled at its top position = worst case
_up = _hv[_hv[:, 1] > HOLDER_Y[1] + 4]
print(f"syringe head: max world Z above the clamp {_up[:, 2].max():.1f} (Z plate front {ZPLATE_Z[0]}), top Y {_hv[:, 1].max():.1f}")
assert _up[:, 2].max() < ZPLATE_Z[0] - 2, "syringe head hits the Z plate / Z stepper"

def build_scene():
    sc = trimesh.Scene(base_frame="cnc3018")
    for n in HIER:
        sc.graph.update(frame_from=HIER[n] or "cnc3018", frame_to=n, matrix=NODE_M[n])
    for (node, mat), m in MER.items():
        g = m.copy()
        g.visual = trimesh.visual.TextureVisuals(material=pbr(mat))
        sc.add_geometry(g, geom_name=f"{node}__{mat}", node_name=f"{node}__{mat}", parent_node_name=node)
    return sc

sc = build_scene()
glb = os.path.join(HERE, "cnc3018.glb")
sc.export(glb)
os.makedirs(os.path.join(ROOT, "web", "models"), exist_ok=True)
shutil.copy(glb, os.path.join(ROOT, "web", "models", "cnc3018.glb"))
tot = sum(len(m.faces) for m in MER.values())
print(f"GLB {os.path.getsize(glb)/1e6:.2f} MB, {tot} tris, {len(MER)} meshes")
allv = np.vstack([m.vertices for m in MER.values()])
print("bounds", allv.min(0).round(1), allv.max(0).round(1))

# ================= renders (matplotlib, painter's sort, simple lambert + spec) =================
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection

def posed(y=90, x=150, z=0, pos=None):
    """vertices posed like the twin does it; pos = plunger height above its lowest point (default: top)."""
    stroke, pr = SY["syringe_stroke"], SY["pitch_r"]
    pos = stroke if pos is None else pos
    off = {"cnc_base": [0, 0, 0], "cnc_y_bed": [0, 0, -(y - 90)], "cnc_x_carriage": [x - 150, 0, 0]}
    off["cnc_z"] = [x - 150, z, 0]
    for n in ("pipette_tip", "syringe_pinion"): off[n] = off["cnc_z"]
    off["syringe_rack"] = [x - 150, z + pos - stroke, 0]
    out = []
    for (n, mat), m in MER.items():
        V = m.vertices
        if n == "syringe_pinion":
            V = trimesh.transform_points(V, trm(PIVOT[:3]) @ rotm((pos - stroke) / pr, [0, 0, 1]))
        out.append((V + np.array(off[n]), m.faces, mat))
    return out

def render(path, items, azim, elev, zoom=1.0, center=None, size=(12, 9), dpi=130, fov_pad=1.05):
    a, e = math.radians(azim), math.radians(elev)
    # camera looks from direction d (Y up)
    d = np.array([math.cos(e) * math.sin(a), math.sin(e), math.cos(e) * math.cos(a)])
    right = np.cross([0, 1, 0], d); right /= np.linalg.norm(right)
    up = np.cross(d, right)
    light = np.array([0.35, 0.85, 0.4]); light /= np.linalg.norm(light)
    fill = -d * 0.5 + np.array([0, 0.3, 0])
    polys, cols, depth = [], [], []
    for V, F, mat in items:
        if center is not None:
            pass
        c, met, rough = MAT[mat]
        base = np.array([int(c[i:i + 2], 16) for i in (1, 3, 5)]) / 255
        tri = V[F]
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        ln = np.linalg.norm(n, axis=1); ok = ln > 1e-9
        tri, n = tri[ok], n[ok] / ln[ok, None]
        facing = (n @ d) > -0.02
        tri, n = tri[facing], n[facing]
        lam = np.clip(n @ light, 0, 1)
        h = (light + d); h /= np.linalg.norm(h)
        spec = np.clip(n @ h, 0, 1) ** (8 + 60 * (1 - rough))
        amb = 0.38 + 0.12 * n[:, 1]
        k = amb + 0.62 * lam
        col = base[None] * k[:, None] * (1 - 0.35 * met) + (0.25 + 0.6 * met) * (1 - rough) * spec[:, None]
        col += met * 0.10 * (0.6 + 0.4 * n[:, 1:2])  # fake environment for metals
        col = np.clip(col, 0, 1)
        P2 = np.stack([tri @ right, tri @ up], axis=-1)
        polys.append(P2); cols.append(col); depth.append((tri @ d).mean(1))
    P = np.concatenate(polys); C = np.concatenate(cols); D = np.concatenate(depth)
    o = np.argsort(D)
    fig = plt.figure(figsize=size, dpi=dpi); ax = fig.add_axes([0, 0, 1, 1])
    fig.patch.set_facecolor("#F4F5F7"); ax.set_facecolor("#F4F5F7")
    pc = PolyCollection(P[o], facecolors=C[o], edgecolors=C[o], linewidths=0.15, antialiaseds=True)
    ax.add_collection(pc)
    if center is None:
        lo, hi = P.reshape(-1, 2).min(0), P.reshape(-1, 2).max(0); cx, cy = (lo + hi) / 2; half = max(hi - lo) / 2 * fov_pad / zoom
    else:
        cc = np.array(center, float); cx, cy = cc @ right, cc @ up; half = 120 / zoom
    asp = size[0] / size[1]
    ax.set_xlim(cx - half * asp, cx + half * asp) if asp > 1 else ax.set_xlim(cx - half, cx + half)
    ax.set_ylim(cy - half, cy + half) if asp > 1 else ax.set_ylim(cy - half / asp, cy + half / asp)
    ax.set_aspect("equal"); ax.axis("off")
    fig.savefig(path, facecolor=fig.get_facecolor()); plt.close(fig)
    print("wrote", path)

if os.environ.get("NORENDER") != "1":
    it = posed(y=60, x=110, z=-10, pos=SY["syringe_stroke"] * 0.6)
    render(os.path.join(REN, "cnc3018_iso.png"), it, azim=-145, elev=26)
    render(os.path.join(REN, "cnc3018_front.png"), it, azim=180, elev=8)
    render(os.path.join(REN, "cnc3018_syringe_head.png"), it, azim=-130, elev=14, zoom=0.85,
           center=(-40, 190, -25))
