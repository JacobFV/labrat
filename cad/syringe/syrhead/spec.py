"""Design parameters for the 3018 syringe head and the layout derived from them.

Frame (the head frame): machine axes, millimetres. +X = machine +X (operator's right), +Y = machine +Y (toward the
gantry / Z plate), +Z up. Origin = syringe axis on the plane of the spindle clamp's TOP face (the plug lip sits on it).
The CNC moves the spindle clamp, so this frame moves with machine Z; at CNC z = 0 the clamp top is 165 mm above the
bed (cad/cnc3018/cnc3018.py: HOLDER_Y[1]).

Everything here is either a measured / catalogue value (marked) or derived. Representative purchased-part numbers are
"verify at procurement".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Spec:
    # ---------------- machine (cnc3018.py) ----------------
    clamp_bore: float = 52.0          # spindle clamp bore (repo model; measure yours)
    clamp_h: float = 26.0             # clamp height
    clamp_top_above_bed: float = 165.0
    clamp_front_y: float = -34.0      # clamp block front face
    zplate_front_y: float = 32.0      # Z plate front face (moves with the head)
    zstep_front_y: float = 26.0       # Z stepper front face (on the X carriage, above the head at z >= 68)
    zstep_bottom_z: float = 68.0
    safe_tip_h: float = 60.0          # tip end above bed at CNC z = 0 (twin SAFE_H, sim Z table)
    # ---------------- plug ----------------
    plug_od: float = 50.2             # user-specified; 0.9 mm radial to the 52 mm bore -> shim or change
    lip_od: float = 56.0
    lip_t: float = 3.0
    # ---------------- syringe: 12 mL luer-slip, PP barrel, rubber stopper (representative) ----------------
    barrel_od: float = 20.2           # measured
    barrel_len: float = 80.0          # measured: flange underside -> barrel bottom face
    barrel_id: float = 17.8           # estimate (1.2 mm wall) -> 249 uL/mm
    flange_l: float = 34.0            # finger flange, long axis (along X when installed)
    flange_w: float = 24.0            # flats
    flange_t: float = 2.2
    hub_d: float = 7.0                # nozzle hub
    hub_l: float = 2.5
    luer_l: float = 7.5               # ISO 80369-7 male slip: 6 % taper, 3.925 at the tip
    luer_tip_d: float = 3.925
    luer_taper: float = 0.06
    disc_d: float = 20.0              # plunger thumb disc
    disc_t: float = 2.0
    rod_span: float = 15.0            # '+' section plunger rod
    rod_fin_t: float = 1.4
    stopper_l: float = 9.0
    disc_gap_pressed: float = 8.0     # disc underside above flange top with the stopper bottomed
    stroke: float = 30.0              # usable plunger travel (7.5 mL)
    # ---------------- luer-to-tip adapter (turned POM) + 200 uL tip ----------------
    adapter_body_d: float = 9.0
    adapter_body_l: float = 10.0
    adapter_socket_depth: float = 7.8
    adapter_luer_seat: float = 7.5    # nozzle end sits this far below the adapter top (taper fit)
    adapter_cone_d0: float = 5.8
    adapter_cone_d1: float = 4.6
    adapter_cone_l: float = 8.0
    tip_len: float = 51.0             # universal 200 uL tip
    tip_top_id: float = 5.4
    tip_top_od: float = 6.9
    tip_end_od: float = 0.85
    tip_seal_gap: float = 0.05        # modelled radial gap at the seal line (press fit in reality)
    # ---------------- gear train: servo -> 17T:50T (m0.8) -> 12T:49T (m1.0) -> 12T pinion -> rack (12.01 : 1) ------
    pa: float = 25.0                  # pressure angle (25 deg: 12T pinions mesh without undercut interference)
    m1: float = 0.8
    z1: int = 17                      # tooth counts bd_warehouse builds as clean solids at PA 25:
    z2: int = 50                      # m0.8: 42..50 (51-56 give slivers / invalid), m1.0: 47..49
    m2: float = 1.0
    z3: int = 12
    z4: int = 49                      # 17:50 x 12:49 = 12.01 : 1
    m3: float = 1.0                   # output pinion + rack
    z5: int = 12
    backlash_cd: float = 0.08         # centre-distance increase per mesh
    w12: float = 4.0                  # face widths
    w34: float = 5.0
    w5: float = 6.0
    # ---------------- purchased motion parts ----------------
    rail_len: float = 70.0            # HIWIN MGNR7 cut length (5 holes on 15 mm pitch)
    pin_d: float = 3.0                # ISO 2338 3 m6 dowel
    pin_len: float = 40.0
    brg_d: float = 3.0                # MR63ZZ
    brg_D: float = 6.0
    brg_B: float = 2.5
    # ---------------- printed-part rules ----------------
    wall: float = 2.4                 # structural FDM wall: 6 perimeters of a 0.4 mm nozzle
    clr: float = 0.2                  # FDM sliding / hole clearance (diametral 0.4)


S = Spec()


# =====================================================================================================================
# derived layout
# =====================================================================================================================
@dataclass
class Layout:
    s: Spec
    v: dict = field(default_factory=dict)

    def __getattr__(self, k):
        try:
            return self.__dict__["v"][k]
        except KeyError:
            raise AttributeError(k)


def tip_bore_d(s: Spec, depth: float) -> float:
    """tip inner diameter at ``depth`` below its top rim (linear taper to ~0.5 mm at the end)."""
    return s.tip_top_id - (s.tip_top_id - 0.5) * depth / s.tip_len


def layout(s: Spec = S) -> Layout:
    L = Layout(s)
    v = L.v
    # ---- vertical chain: tip end -> adapter -> nozzle -> barrel -> flange ----
    v["tip_end_z"] = -(s.clamp_top_above_bed - s.safe_tip_h)
    v["tip_top_z"] = v["tip_end_z"] + s.tip_len
    # tip rim seals where the adapter cone has the rim's ID (+ modelled gap)
    k_cone = (s.adapter_cone_d0 - s.adapter_cone_d1) / s.adapter_cone_l
    d_seal = s.tip_top_id - 2 * s.tip_seal_gap
    v["cone_top_z"] = v["tip_top_z"] + (s.adapter_cone_d0 - d_seal) / k_cone
    v["adapter_top_z"] = v["cone_top_z"] + s.adapter_body_l
    v["nozzle_end_z"] = v["adapter_top_z"] - s.adapter_luer_seat
    v["barrel_bottom_z"] = v["nozzle_end_z"] + s.luer_l + s.hub_l
    v["flange_z"] = v["barrel_bottom_z"] + s.barrel_len           # flange underside
    v["flange_top_z"] = v["flange_z"] + s.flange_t
    # ---- body heights ----
    v["plug_bottom_z"] = -s.clamp_h
    v["table_top_z"] = v["flange_z"] + 1.0                          # flange sits 1.0 deep in a locating pocket
    v["table_t"] = 4.0
    v["clampplate_bottom_z"] = v["table_top_z"] + 0.05
    v["clampplate_t"] = 4.0
    v["clampplate_top_z"] = v["clampplate_bottom_z"] + v["clampplate_t"]
    # ---- plunger ----
    v["disc0_z"] = v["flange_top_z"] + s.disc_gap_pressed          # disc underside at pos 0 (fully pressed)
    # thumb disc: sits in a 1.0 deep pocket in the jaw, clamped by a screwed cap (2x M2 into inserts in the jaw)
    v["jaw0_bottom_z"] = v["disc0_z"] - 3.6
    v["jaw_top_dz"] = 1.0                                           # jaw top above the disc underside
    v["cap_gap"] = 0.1                                              # cap clears the jaw so the screws clamp the disc
    v["cap_t"] = 3.5
    v["cap_screws"] = [(-7.5, 12.8), (7.5, 12.8)]
    assert v["jaw0_bottom_z"] - v["clampplate_top_z"] >= 1.0, "carriage jaw would hit the flange clamp at pos 0"
    v["ul_per_mm"] = math.pi / 4 * s.barrel_id ** 2
    # ---- carriage / rail (left side, -X) ----
    v["jaw_x"] = (-14.5, 13.0)                                      # >= 1.2 mm walls beside the Ø20.4 disc seat
    v["jaw_y"] = (-11.5, 16.5)
    v["mgn_H"], v["mgn_H1"], v["mgn_W"], v["mgn_L"], v["mgn_B"], v["mgn_C"] = 8.0, 1.5, 17.0, 22.5, 12.0, 8.0
    v["rail_W"], v["rail_H"] = 7.0, 4.8
    v["rail_face_x"] = v["jaw_x"][0] - v["mgn_H"]                   # spine face the rail is screwed to
    v["spine_t"] = 6.0
    v["mgn0_z"] = v["disc0_z"] + 16.0                               # MGN7C centre at pos 0
    lo = v["mgn0_z"] - v["mgn_L"] / 2 - 2.0
    hi = v["mgn0_z"] + s.stroke + v["mgn_L"] / 2 + 2.0
    assert hi - lo <= s.rail_len, f"rail too short: need {hi - lo:.1f}"
    v["rail_z0"] = lo - (s.rail_len - (hi - lo)) / 2
    # ---- rack + output pinion ----
    p3 = math.pi * s.m3
    v["rack_x0"] = v["jaw_x"][1]                                    # rack back face on the jaw's right face
    v["rack_body_t"] = 4.25
    v["rack_root_x"] = v["rack_x0"] + v["rack_body_t"]
    v["rack_tip_x"] = v["rack_root_x"] + 2.25 * s.m3
    v["rack_pitch_x"] = v["rack_tip_x"] - s.m3
    r5 = s.z5 * s.m3 / 2
    v["C"] = (v["rack_pitch_x"] + r5 + s.backlash_cd, None)
    v["rack0_z0"] = v["jaw0_bottom_z"]                              # rack bottom (pos 0) = jaw bottom
    v["zc"] = v["rack0_z0"] + s.stroke + 3 * p3
    v["rack_len"] = s.stroke + 5.5 * p3
    v["C"] = (v["C"][0], v["zc"])
    # ---- gear centres (XZ), axes along +Y ----
    a_cb = math.radians(60.0)
    d_cb = (s.z3 + s.z4) * s.m2 / 2 + s.backlash_cd
    v["B"] = (v["C"][0] + d_cb * math.cos(a_cb), v["C"][1] + d_cb * math.sin(a_cb))
    d_ba = (s.z1 + s.z2) * s.m1 / 2 + s.backlash_cd
    v["A"] = (v["B"][0] - d_ba, v["B"][1])
    v["ratio"] = (s.z2 / s.z1) * (s.z4 / s.z3)
    v["mm_per_servo_rad"] = r5 / v["ratio"]
    # ---- Y planes (front = -Y) ----
    v["y_rack"] = (-s.w5 / 2, s.w5 / 2)
    v["y_g4"] = (-13.5 - s.w34, -13.5)                              # G3/G4 plane, 2 mm in front of the jaw
    v["y_g2"] = (v["y_g4"][0] - 1.3 - s.w12, v["y_g4"][0] - 1.3)    # G2 plane (1.3 axial to G4: motion clearance)
    v["g1_w"] = s.w12 + 0.4                                          # G1 is 0.4 wider toward the servo, so G2's
    v["g1_back"] = v["y_g2"][0] - 0.4                               # back face clears the servo tower by 1.2
    # servo stack (FS90R / SG90 family): tab top on the cover's front face
    v["servo"] = dict(L=22.8, W=12.4, case_h=22.7, tab_from_bottom=15.9, tab_t=2.5, tab_len=32.2, tab_pitch=27.8,
                      tab_hole=2.2, shaft_from_end=5.9, tower_d=11.8, tower_h=2.0, spline_d=4.8, spline_h=2.0,
                      horn_hub_d=7.0, horn_arm_t=1.6, horn_over_spline=0.6, horn_trim_r=4.6, horn_arm_w=4.2)
    sv = v["servo"]
    sv["above_tab"] = sv["case_h"] - sv["tab_from_bottom"] - sv["tab_t"]          # 4.3
    sv["spline_top_above_tab"] = sv["above_tab"] + sv["tower_h"] + sv["spline_h"]  # 8.3
    v["cover_t"] = 3.0
    v["cover_front_y"] = v["g1_back"] + 0.2 - (sv["spline_top_above_tab"] + sv["horn_over_spline"] - sv["horn_arm_t"])
    v["cover_back_y"] = v["cover_front_y"] + v["cover_t"]
    v["back_plate_y"] = (6.0, 10.0)
    return L


LAY = layout()
