"""Design parameters for the 3018 syringe head (v2) and the layout derived from them.

Frame (the head frame): machine axes, millimetres. +X = machine +X (operator's right), +Y = machine +Y (toward the
gantry / Z plate), +Z up. Origin = syringe axis on the plane of the spindle clamp's TOP face (the body's seat ring
sits on it). The CNC moves the spindle clamp, so this frame moves with machine Z; at CNC z = 0 the clamp top is
165 mm above the bed (hardware/cnc3018/cnc3018.py: HOLDER_Y[1]).

Architecture (v2, every part printable without supports):
    z-stack:  plug (Ø42 x 34, in the spindle clamp)  ->  rectangular mid body (syringe barrel inside)  ->  top section
    top section, an x-stack:  carriage + plunger | gear plate 1 (+ the rack's guide rib) | gears | gear plate 2 | servo
Gear axes run along +X. The carriage is an L: jaw (thumb disc) + bottom arm (through a window in gear plate 1) + rack
bar. The rack slides directly on its own back: a V-groove in the rack's back face rides a V-ridge on a rib of gear plate
1, right behind the mesh, so the gear's separating force goes straight into the guide and makes no moment.

Everything here is either a measured / catalogue value (marked) or derived. Representative purchased-part numbers are
"verify at procurement".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Spec:
    # ---------------- machine (cnc3018.py) ----------------
    clamp_bore: float = 42.0          # measured: the 3018's motor holder takes a 42.0 mm cylinder ...
    clamp_h: float = 34.0             # ... 34.0 mm high
    clamp_top_above_bed: float = 165.0
    clamp_front_y: float = -34.0      # clamp block front face
    zplate_front_y: float = 32.0      # Z plate front face (moves with the head)
    zstep_front_y: float = 26.0       # Z stepper front face (on the X carriage, above the head at z >= 68)
    zstep_bottom_z: float = 68.0
    z_travel: float = 45.0            # 3018 PRO Z travel (sim BOUNDS z -45 .. 0)
    safe_tip_h: float = 55.0          # tip end above bed at CNC z = 0 (twin SAFE_H, sim Z table): clears the beaker
    #                                   rims (51.2 mm) by 3.8 mm with a tip on; the lowest tip end is 10 mm above the bed
    # ---------------- plug + seat ----------------
    plug_od: float = 41.9             # 42.0 holder bore - 0.1 (slide fit, the holder clamps it)
    seat_od: float = 41.9             # the plug flares out at 45 deg from its own OD: a conical seat on the holder's
    #                                   bore edge (self-centring, and no flat ledge that would need support)
    # ---------------- syringe: 12 mL luer-slip, PP barrel, rubber stopper (representative) ----------------
    barrel_od: float = 20.2           # measured
    barrel_len: float = 80.0          # measured: flange underside -> barrel bottom face
    barrel_id: float = 17.8           # estimate (1.2 mm wall) -> 249 uL/mm
    flange_l: float = 34.0            # finger flange, long axis (along Y when installed)
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
    w5: float = 6.0                   # rack face width (G5 is 1 mm longer: the rack clears G4)
    # ---------------- purchased motion parts ----------------
    slide_clr: float = 0.15           # clearance between the rack's back and its guide rib
    vee: float = 1.5                  # depth of the 90 deg V-groove in the rack's back (the rib's ridge runs in it)
    guide_l: float = 32.0             # carriage bearing length on the rail
    pin_d: float = 3.0                # ISO 2338 3 m6 dowel
    pin_len: float = 30.0
    brg_d: float = 3.0                # MR63ZZ
    brg_D: float = 6.0
    brg_B: float = 2.5
    # ---------------- printed-part rules ----------------
    wall: float = 2.4                 # structural FDM wall: 6 perimeters of a 0.4 mm nozzle
    clr: float = 0.2                  # FDM sliding / hole clearance (diametral 0.4)
    run: float = 1.0                  # running clearance between a moving part and anything it passes


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


def _polar(c, d, deg):
    """point at distance d from c = (y, z), direction ``deg`` measured from +Z toward -Y."""
    a = math.radians(deg)
    return (c[0] - d * math.sin(a), c[1] + d * math.cos(a))


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
    v["cone_end_z"] = v["cone_top_z"] - s.adapter_cone_l
    v["adapter_top_z"] = v["cone_top_z"] + s.adapter_body_l
    v["nozzle_end_z"] = v["adapter_top_z"] - s.adapter_luer_seat
    v["barrel_bottom_z"] = v["nozzle_end_z"] + s.luer_l + s.hub_l
    v["flange_z"] = v["barrel_bottom_z"] + s.barrel_len           # flange underside
    v["flange_top_z"] = v["flange_z"] + s.flange_t
    # ---- body heights ----
    v["plug_bottom_z"] = -s.clamp_h
    v["table_top_z"] = v["flange_z"] + 1.0                          # flange sits 1.0 deep in a locating pocket
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
    # ---- carriage / rail (left side, -X). The carriage's back face (+Y) is one plane: it prints on it. ----
    v["jaw_x"] = (-12.5, 13.0)                                      # >= 1.8 mm walls beside the Ø20.4 disc seat
    v["jaw_y"] = (-11.5, 16.5)
    v["y_back"] = v["jaw_y"][1]
    v["spine_back_x"] = -31.0                                       # mid body's -X end (carries the tool rail)
    v["spine_top_z"] = 110.0                                         # top of the back wall
    # ---- body outline ----
    v["body_x"] = (v["spine_back_x"], None)                         # +X end = gear plate 1 outer face (below)
    v["body_y"] = (-25.0, 24.9)                                     # back < zstep_front_y - 1 everywhere
    v["back_wall_y"] = (22.5, v["body_y"][1])
    # ---- X planes of the gear section (gear axes along +X) ----
    v["plate1_x"] = (v["jaw_x"][1] + 2.0, v["jaw_x"][1] + 8.0)      # 6 thick: dowel seats + M3 inserts
    x1 = v["plate1_x"][1]
    v["body_x"] = (v["spine_back_x"], x1)
    v["x_rack"] = (x1 + s.run, x1 + s.run + s.w5)                   # rack face width, 1.0 off plate 1 and G4
    v["x_g5"] = (v["x_rack"][0], v["x_rack"][1] + s.run)            # output pinion (moves with G4)
    v["x_g4"] = (v["x_g5"][1], v["x_g5"][1] + s.w34)
    v["x_g3"] = (v["x_g4"][0], v["x_g4"][1] + s.run)                # G3 runs straight into G2: no neck
    v["x_g2"] = (v["x_g3"][1], v["x_g3"][1] + s.w12)
    v["g1_w"] = s.w12
    v["x_g1"] = v["x_g2"]                                           # G1 and G2 share the plane
    # servo stack (FS90R / SG90 family): tab top on gear plate 2's outer face, output toward -X
    v["servo"] = dict(L=22.8, W=12.4, case_h=22.7, tab_from_bottom=15.9, tab_t=2.5, tab_len=32.2, tab_pitch=27.8,
                      tab_hole=2.2, shaft_from_end=5.9, tower_d=11.8, tower_h=2.0, spline_d=4.8, spline_h=2.0,
                      horn_hub_d=7.0, horn_arm_t=1.6, horn_over_spline=0.6, horn_trim_r=4.6, horn_arm_w=4.2)
    sv = v["servo"]
    sv["above_tab"] = sv["case_h"] - sv["tab_from_bottom"] - sv["tab_t"]          # 4.3
    sv["spline_top_above_tab"] = sv["above_tab"] + sv["tower_h"] + sv["spline_h"]  # 8.3
    # the output tower clears the G1/G2 servo faces by 1.2; the trimmed horn arms then stand 0.2 proud of G1's face
    # (1.0 from the tower) and sit in a 1.4 deep pocket
    v["servo_tab_x"] = v["x_g2"][1] + 1.2 + sv["above_tab"] + sv["tower_h"]
    v["plate2_t"] = 3.5
    v["plate2_x"] = (v["servo_tab_x"] - v["plate2_t"], v["servo_tab_x"])
    v["horn_pocket"] = v["x_g1"][1] - (v["servo_tab_x"] - sv["spline_top_above_tab"] - sv["horn_over_spline"])
    # ---- rack (teeth toward -Y) + output pinion ----
    p3 = math.pi * s.m3
    v["rack_pitch_y"] = 2.0
    v["rack_tip_y"] = v["rack_pitch_y"] - s.m3
    v["rack_root_y"] = v["rack_pitch_y"] + 1.25 * s.m3
    v["rack0_z0"] = v["jaw0_bottom_z"]                              # rack bottom (pos 0) = jaw bottom
    v["zc"] = v["rack0_z0"] + s.stroke + 3 * p3
    v["rack_len"] = s.stroke + 5.5 * p3
    v["rack0_z1"] = v["rack0_z0"] + v["rack_len"]
    r5 = s.z5 * s.m3 / 2
    v["C"] = (v["rack_pitch_y"] - r5 - s.backlash_cd, v["zc"])
    # ---- gear centres (Y, Z), axes along +X: B nearly above C (clear of the rack), A down-forward of B ----
    d_cb = (s.z3 + s.z4) * s.m2 / 2 + s.backlash_cd
    d_ba = (s.z1 + s.z2) * s.m1 / 2 + s.backlash_cd
    v["B"] = _polar(v["C"], d_cb, 12.0)
    v["A"] = _polar(v["B"], d_ba, 180.0 - 65.0)
    v["ratio"] = (s.z2 / s.z1) * (s.z4 / s.z3)
    v["mm_per_servo_rad"] = r5 / v["ratio"]
    v["r_tip"] = {"g1": (s.z1 + 2) * s.m1 / 2, "g2": (s.z2 + 2) * s.m1 / 2, "g3": (s.z3 + 2) * s.m2 / 2,
                  "g4": (s.z4 + 2) * s.m2 / 2, "g5": (s.z5 + 2) * s.m3 / 2}
    # ---- gear plate 1 window: the carriage's arm passes through it ----
    v["arm_y"] = (v["rack_root_y"] + 0.75, v["y_back"])             # carriage cross members (y band)
    v["window_y"] = (v["arm_y"][0] - s.run, v["y_back"] + s.slide_clr)  # the back edge is the rib's guide plane
    v["arm_h"] = 8.0                                                 # carriage arm (jaw -> rack), crosses plate 1
    v["window_z"] = (v["jaw0_bottom_z"] - s.run, v["jaw0_bottom_z"] + v["arm_h"] + s.stroke + s.run)
    # the rack's guide rib: on gear plate 1's back member, behind the rack, spanning the mesh over the whole stroke
    v["rib_x"] = (v["plate1_x"][1] - 2.0, v["x_rack"][1] - 0.5)    # 2 mm into plate 1: no thin wedge at its foot
    v["rib_y0"] = v["y_back"] + s.slide_clr
    v["rib_z"] = (v["zc"] - 23.0, v["zc"] + 25.0)
    # ---- standoffs plate 2 -> plate 1 (Y, Z): clear of every gear disc, the rack and the carriage ----
    v["standoffs"] = [(-12.0, 47.0), (20.6, v["rib_z"][1] + 6.0), (-25.0, 58.0)]
    v["standoff_d"] = 6.2
    # ---- servo case (outside plate 2), its long axis along -Z from the output shaft ----
    v["servo_case_c"] = (v["A"][0], v["A"][1] - (sv["L"] / 2 - sv["shaft_from_end"]))
    return L


LAY = layout()
