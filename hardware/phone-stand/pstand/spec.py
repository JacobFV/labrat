"""Phone stand (PS-*) parameters and derived layout. All mm.

Frames
------
S  stand frame = machine axes at the stand: origin on the bench top on the pan axis, +x = machine +x (operator's
   right), +y = toward the CNC (machine back), +z = up. The stand sits front-centre: twin three.js
   (X, Y, Z) = (-x, z, y + STAND_Z) with STAND_Z = -225 (bench-top frame, Y up; see apps/console/twin.js).
P  pan frame: S rotated about +z by the pan angle, origin on the pan axis at the thrust face (z = Z_TH).
T  tilt frame: P rotated about +x by the camera elevation `el`, origin on the tilt axis (z = H_T above the bench).
   At el = 0 the phone stands upright, screen toward the operator (-y), rear camera toward the deck (+y).
   el = -90 looks straight down. The protocol's tilt = el - el0 (el0 = the deck-centre aim, see kin.py).

Stack (bottom -> top): 1 mm silicone pads | PS-100 base (ballast pocket, 2020 socket, tongue under the 3018's
front end plate) | 2020 post | PS-110 pan housing (pan servo, thrust face, rim) | PS-120 yoke (disc + neck + U)
riding on the thrust face under the PS-111 retainer | tilt servo on the yoke's right arm, 623ZZ in the left arm |
PS-130 cradle between the arms | 2x PS-131 jaws (rack-and-pinion centred, 2 extension springs) | phone.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- placement in the twin (bench-top frame, three)
STAND_Z = -225.0               # three Z of the pan axis (front-centre, 52 mm in front of the 3018 front end plate)
BED_H = 72.0                   # bench -> bed top (deck plane), apps/console/twin.js
CNC_FRONT_PLATE_Y = (-173.0 - STAND_Z, -165.0 - STAND_Z)   # S-frame y of the 3018 front end plate: 52 .. 60
CNC_FRONT_PLATE_Z0 = 8.0       # its bottom edge above the bench (feet 8 mm)

# ---------------------------------------------------------------- phone envelope
PHONE_W = (60.0, 85.0)         # width range the clamp takes
PHONE_T_MAX = 9.5
PHONE_L_MAX = 170.0            # sweep clearance is checked for this length
PHONE_MASS = 0.230             # kg
PHONE_NOM = dict(w=71.6, l=147.6, t=7.8, r=9.0, name="nominal 6.1 in phone (iPhone 15 size)")
# main (wide) camera on the back, top corner on the +x side (= top-left seen from the back); lens centre
CAM_NOM = (22.0, 60.0)         # (x, z) on the phone, T frame, nominal phone centred on the axes
CAM_FOV = (55.0, 70.0)         # deg, (across the phone width, along the phone length): ~4:3 main camera, portrait
FRONT_CAM_NOM = (0.0, 63.0)    # front camera (x, z)

# ---------------------------------------------------------------- servo (SG90 / MG90S case, tool-head conventions)
SV = dict(L=22.8, W=12.4, case_h=22.7, tab_from_bottom=15.9, tab_t=2.5, tab_len=32.2, tab_pitch=27.8,
          tab_hole=2.2, shaft_from_end=5.9, tower_d=11.8, tower_h=2.0, spline_d=4.8, spline_h=2.0)
SV["above_tab"] = SV["case_h"] - SV["tab_from_bottom"] - SV["tab_t"]           # 4.3 case top above the tab top
SV["spline_top"] = SV["above_tab"] + SV["tower_h"] + SV["spline_h"]            # 8.3 above the tab top
SV["cx"] = SV["L"] / 2 - SV["shaft_from_end"]                                  # 5.5 case centre from the shaft
HORN = dict(hub_d=7.0, h=2.3, arm_t=1.6, r=16.0, w_hub=5.6, w_tip=3.6, holes=(8.0, 10.0, 12.0, 14.0), over_spline=0.6)
HORN_FLOAT = 0.4               # the horn is screwed to the driven part, NOT to the spline: 0.4 mm axial float
HORN_SCREW_R = 12.0            # horn screws (2x, the self-tappers in the servo bag) at r = 12
# servo top face -> horn top face (the face screwed to the driven part)
HORN_TOP_ABOVE_TAB = SV["spline_top"] + HORN["over_spline"] + HORN_FLOAT      # 9.3
SERVO_STALL = {"SG90": 1.8, "MG90S": 2.2}                                     # kg cm at 4.8 V (datasheets)
SERVO_SPEED = {"SG90": 0.10, "MG90S": 0.10}                                   # s / 60 deg at 4.8 V
SERVO_RANGE = 180.0            # deg of travel for 500..2500 us (the hub's `servo` deg 0..180)

# ---------------------------------------------------------------- 2020 post and base
PAD_T = 1.0                    # silicone anti-slip pads under the base
POST = 20.0
POST_L = 250.0
BASE_FLOOR = 3.0
BASE_Z0 = PAD_T
BASE_TOP = 25.0                # body top (lid seat)
LID_T = 3.0
BASE_X = 82.0                  # body half-width
BASE_Y = (-100.0, 30.0)        # body (flat top) y range
RAMP_Y = 50.0                  # 45 deg ramp down to the tongue (under the CNC front plate from y = 52)
BALLAST_DENSITY = 4.7          # g/cm3, 4.5 mm steel BBs (random packing ~0.6)
TONGUE = dict(y1=112.0, top=5.0, half_w=55.0)   # 4 mm tongue, 3 mm under the 3018 front plate (bottom at z = 8)
POCKET = dict(wall=6.0, y1=24.0, z0=BASE_Z0 + BASE_FLOOR)   # outline offset inward by `wall`, y <= y1
BOSS = dict(half=15.5, top=44.0)
POST_Z0 = BASE_Z0 + BASE_FLOOR  # post bottom (on the base floor)
POST_Z1 = POST_Z0 + POST_L
SOCKET_CLR = 0.15              # 20.3 socket for the 2020
LID_INSERTS = [(-70.0, -97.0), (70.0, -97.0), (-60.0, 27.0), (60.0, 27.0)]
PADS = [(-72.0, -90.0), (72.0, -90.0), (-66.0, 16.0), (66.0, 16.0)]
PAD_D = 16.0

# ---------------------------------------------------------------- pan housing / turntable
# z positions relative to the thrust face Z_TH
H_RIM = 4.3                    # rim above the thrust face (disc 4.0 + 0.3 axial play under the retainer)
DISC_T = 4.0
DISC_R = 32.0
RIM_R = (32.3, 41.0)
RET_R = (29.8, 41.0)
RET_T = 3.0
RET_INSERT_R = 36.7
RET_INSERT_ANG = (90.0, 210.0, 330.0)
BORE_R = 18.5                  # horn sweep (r 16)
TAB_TOP = -HORN_TOP_ABOVE_TAB  # pan servo tab top (-9.3)
CASE_BOT = TAB_TOP - SV["tab_t"] - SV["tab_from_bottom"]      # -27.7
POCKET_FLOOR = CASE_BOT - 0.3                                 # -28.0
M5_CB = dict(d=9.8, depth=3.0)                                # ISO 7380 M5 button head counterbore
FLOOR_BOT = POCKET_FLOOR - M5_CB["depth"] - 3.0               # -34.0
PYR_H = (POST + 2 * SOCKET_CLR) / 2                           # 45 deg pyramid roof over the post end
HOUSING_SOCKET = 25.0
Z_TH = POST_Z1 - FLOOR_BOT + PYR_H                            # thrust face height above the bench
HOUSING_R_LOW = 21.0
FLARE = (-24.0, -4.0)          # 45 deg flare r 21 -> 41 between these z (relative)

# ---------------------------------------------------------------- yoke (T-frame numbers: relative to the tilt axis)
NECK_R = 28.0
ARM_IN = 67.3                  # arm inner face |x|
ARM_T = 6.0
ARM_OUT = ARM_IN + ARM_T       # 73.3 = the tilt servo's tab plane
FLOOR_Z = -90.0                # U floor (|x| <= FLOOR_FLAT): clears a 170 mm phone (r 85) by 5 mm
FLOOR_FLAT = 52.0
ARM_TOP_R = 16.0
ARM_HALF_Y = 12.0
DISC_BOT_T = None              # set below (tilt-frame z of the disc bottom = thrust face)
NECK_TOP_T = -122.0            # 45 deg underside from (26, -122) to the arm's outer corner
BEARING_623 = dict(d=3.0, D=10.0, B=4.0)
IDLER_SPACER = dict(od=5.5, id=3.2)
SERVO_DIAG = (-1 / math.sqrt(2), -1 / math.sqrt(2))   # tilt servo case direction in (y, z): down, toward the operator

# ---------------------------------------------------------------- cradle + jaws (T frame)
EAR_X = (57.0, 64.0)           # end blocks / ears |x|; the horn's top face is on the right ear's outer face
PHONE_BACK_Y = 4.0             # phone back rests on the bars at y = 4
CH = dict(y_mouth=4.0, y_floor=10.0, w_mouth=13.0, undercut_deg=30.0)
CH["w_floor"] = CH["w_mouth"] + (CH["y_floor"] - CH["y_mouth"]) * math.tan(math.radians(CH["undercut_deg"]))
BAR_Z = 22.0                   # cradle half-height
FLOOR_Y = (10.0, 14.0)
BACK_Y = 18.0
GATE_X = 11.0                  # loading gate (no undercut) |x| <= 11: drop the jaw blocks in here, slide outward
GROOVE = dict(z=10.0, w=6.0, wall=2.0)          # spring lanes on the back face (V roof)
SPRING_SCREW = dict(dx=3.0, z=10.0, pilot=2.1, depth=10.0)   # M2.5 x 12 posts in the jaw blocks
JAW = dict(block=10.0, finger=5.0, finger_z=11.0, lip=3.0, lip_top=-7.5, chamfer_from=-3.0, slide_y=(6.0, 9.9),
           arm_len=54.0, arm_z=(8.25, 12.8), clr=0.2)
JAW_OPEN = 46.0                # |x| of the jaw faces fully open (85 + 7 mm to drop a phone in)
JAW_MIN = 29.0                 # stop (jaw blocks meet the pinion-side end of the travel) - 58 mm between jaws
PINION = dict(m=1.0, z=14, pa=20.0, y=(6.2, 10.0), bore=4.15)
PINION["r"] = PINION["m"] * PINION["z"] / 2   # 7.0 = the racks' pitch lines at z = +-7
SPRING = dict(L0=60.0, k=0.15, T0=2.0, od=5.0, wire=0.6)     # 2x extension springs between the jaws' posts

# ---------------------------------------------------------------- derived heights (bench frame)
H_T = Z_TH + 131.2             # tilt axis above the bench; the yoke disc bottom is 131.2 below the tilt axis
DISC_BOT_T = Z_TH - H_T        # -131.2
assert abs(DISC_BOT_T + H_RIM + RET_T + 1.9 - NECK_TOP_T) < 1e-6   # neck top 1.9 above the retainer

# ---------------------------------------------------------------- tilt / pan kinematic conventions
EL_MID = -40.0                 # camera elevation at the tilt servo's 90 deg (1500 us): range -130 .. +50
PAN_SOFT = (-85.0, 85.0)
EL_SOFT = (-95.0, 40.0)        # straight down (-90) .. the screen tipped 40 deg down; 0 = screen facing the operator
# hobby-servo convention (TowerPro SG90 datasheet): a longer pulse turns the output clockwise seen from the horn.
# pan: shaft up, +pan = counter-clockwise from above -> dir -1. tilt: shaft along -x, +el = clockwise seen from the
# horn side -> dir +1. Verify both during calibration (README).
PAN_DIR = -1
TILT_DIR = +1

# ---------------------------------------------------------------- materials (finish keys for the GLB/renders)
FIN = {
    "PS-100": "petg_graphite", "PS-101": "petg_graphite", "PS-110": "petg_graphite", "PS-111": "petg_graphite",
    "PS-120": "petg_white", "PS-130": "petg_white", "PS-131": "petg_orange", "PS-132": "petg_orange",
    "PS-133": "petg_white",
}
COLORS = {  # name: (rgb hex, metallic, roughness)
    "petg_graphite": ("#2E3135", 0.0, 0.58),
    "petg_white": ("#D9D7CF", 0.0, 0.62),
    "petg_orange": ("#DB4D0D", 0.0, 0.55),
    "alu": ("#B8BCC2", 0.85, 0.35),
    "servo_black": ("#1A1A1C", 0.0, 0.55),
    "servo_label": ("#C9CED6", 0.0, 0.5),
    "horn_white": ("#ECECE6", 0.0, 0.45),
    "steel": ("#C9CBCE", 1.0, 0.22),
    "black_oxide": ("#1C1C1E", 0.6, 0.45),
    "brass": ("#C9A54C", 1.0, 0.3),
    "rubber": ("#151515", 0.0, 0.9),
    "spring_steel": ("#9EA2A6", 1.0, 0.3),
    "phone_body": ("#2A2C30", 0.7, 0.3),
    "phone_glass": ("#07080A", 0.2, 0.06),
    "phone_lens": ("#0A0B10", 0.5, 0.05),
    "phone_bump": ("#3A3D42", 0.5, 0.25),
    "cable": ("#141414", 0.0, 0.5),
}
