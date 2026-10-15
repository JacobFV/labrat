"""Assembly: every part instance with its motion group, placement and BOM data.

Motion groups and their node frames (= the twin GLB nodes):
    base   S frame (static)
    pan    P frame: origin on the pan axis at the thrust face (S z = Z_TH); rotates about +z by `pan`
    tilt   T frame: origin on the tilt axis (P z = H_T - Z_TH); rotates about +x by `el` (camera elevation)
    clamp  T frame (child of tilt): the jaws, pinion, springs; jaws sit at x = -+w/2 for a phone of width w
    phone  T frame (child of tilt): the phone placeholder
"""
from __future__ import annotations

import functools
import math
from dataclasses import dataclass, field

import numpy as np

from . import geo as G
from . import parts as P
from . import spec as SP
from . import vendor as V


@dataclass
class Part:
    pn: str
    name: str
    kind: str                 # make | buy
    process: str              # fdm_print | purchased | cut
    material: str
    finish: str
    vendor: str = ""
    vendor_pn: str = ""
    cost: float | None = None
    unit: str = "ea"
    qty_extra: float = 0.0    # BOM quantity not modelled as instances
    product: str | None = None  # sub-parts of a purchased product roll up under it
    mass_g: float | None = None  # catalogue mass (else from volume x density)
    density: float | None = None
    notes: str = ""


@dataclass
class Inst:
    name: str
    pn: str
    group: str
    M: np.ndarray = field(default_factory=lambda: np.eye(4))   # part design frame -> group frame
    shape_key: str | None = None                                # sub-shape of a multi-shape product


def _servo_parts(prefix, product):
    return {
        f"{prefix}-C": Part(f"{prefix}-C", "servo case", "buy", "purchased", "abs_black", "servo_black", product=product),
        f"{prefix}-L": Part(f"{prefix}-L", "servo label", "buy", "purchased", "label_vinyl", "servo_label", product=product),
        f"{prefix}-S": Part(f"{prefix}-S", "servo output spline", "buy", "purchased", "brass", "brass", product=product),
        f"{prefix}-H": Part(f"{prefix}-H", "servo double-arm horn", "buy", "purchased", "pom_white", "horn_white", product=product),
    }


PARTS: dict[str, Part] = {
    "PS-100": Part("PS-100", "base: steel-shot ballast pocket, 2020 socket, tongue under the 3018 front plate", "make",
                   "fdm_print", "PETG", "petg_graphite", notes="flat; 3 walls, 10% infill (the steel shot gives the weight)"),
    "PS-101": Part("PS-101", "base lid (lead groove, engraved name)", "make", "fdm_print", "PETG", "petg_graphite",
                   notes="flat"),
    "PS-110": Part("PS-110", "pan housing: pan servo pocket, thrust face, rim, 2020 socket", "make", "fdm_print", "PETG",
                   "petg_graphite", notes="upright, socket down; 5 walls"),
    "PS-111": Part("PS-111", "turntable retainer ring", "make", "fdm_print", "PETG", "petg_graphite", notes="flat"),
    "PS-120": Part("PS-120", "yoke: turntable disc, neck, U arms (tilt servo, 623ZZ)", "make", "fdm_print", "PETG",
                   "petg_white", notes="upright, disc down; 5 walls, 25% infill"),
    "PS-130": Part("PS-130", "cradle: phone rest bars, dovetail jaw channel, spring lanes, tilt ears", "make", "fdm_print",
                   "PETG", "petg_white", notes="on its back"),
    "PS-131": Part("PS-131", "clamp jaw (rack, V lip, spring posts) - same part left and right", "make", "fdm_print",
                   "PETG", "petg_orange", notes="on its back; 100% infill"),
    "PS-132": Part("PS-132", "pinion m1 14T (centres the jaws)", "make", "fdm_print", "PETG", "petg_orange",
                   notes="flat; 100% infill"),
    "PS-133": Part("PS-133", "idler spacer 5.5/3.2", "make", "fdm_print", "PETG", "petg_white", notes="on end"),
    "PS-134": Part("PS-134", "pinion sleeve, cut 4.0 from 4 x 0.45 brass tube", "make", "cut", "brass_c360", "brass",
                   vendor="K&S Precision Metals", vendor_pn="9220 (4 mm x 0.45 brass tube)", cost=0.2, density=8.5),
    # ---- purchased
    "PS-200": Part("PS-200", "MG90S metal-gear micro servo (SG90 case; SG90 also fits), with horns + screws", "buy",
                   "purchased", "", "", vendor="TowerPro / generic", vendor_pn="MG90S", cost=3.5, mass_g=13.4),
    "PS-201": Part("PS-201", f"2020 B-type aluminium extrusion (6 mm slot), cut {SP.POST_L:g}, tap M5 both ends", "buy",
                   "purchased", "aluminum_6063_t5", "alu", vendor="Misumi / generic", vendor_pn=f"HFS5-2020-{SP.POST_L:g}", cost=2.5,
                   density=2.7),
    "PS-202": Part("PS-202", "623ZZ ball bearing 3 x 10 x 4", "buy", "purchased", "steel", "steel", vendor="generic",
                   vendor_pn="623ZZ", cost=0.4, density=7.85),
    "PS-203": Part("PS-203", "extension spring OD 5, wire 0.6, 60 mm inside hooks, ~0.15 N/mm, ~2 N initial tension",
                   "buy", "purchased", "music_wire", "spring_steel", vendor="assortment / Lee Spring",
                   vendor_pn="OD 5 x 0.6 wire x 60 (assortment box)", cost=0.3, density=7.85),
    "PS-204": Part("PS-204", "steel shot / 4.5 mm steel BBs, ballast (fills the base pocket)", "buy",
                   "purchased", "steel", "", vendor="generic", vendor_pn="4.5 mm steel BB", cost=4.0, unit="kg",
                   qty_extra=1.5),
    "PS-205": Part("PS-205", "silicone anti-slip pad, 16 x 1 mm (cut from 1 mm sheet)", "buy", "purchased", "silicone",
                   "rubber", vendor="generic", vendor_pn="1 mm adhesive silicone sheet", cost=0.05, density=1.2),
    "PS-206": Part("PS-206", f"2020 slot cover strip (holds the servo leads), cut {SP.POST_Z1 - SP.BOSS['top'] - 4.0:.0f}", "buy", "purchased", "pvc",
                   "petg_graphite", vendor="generic", vendor_pn="6 mm slot cover", cost=0.3, density=1.4),
    "PS-207": Part("PS-207", "servo extension lead 3-way 2.54, 300 mm (stand -> hub)", "buy", "purchased", "", "",
                   vendor="generic", vendor_pn="JR/Futaba servo extension 30 cm", cost=0.5, qty_extra=2),
    "PS-208": Part("PS-208", "1 mm silicone pad tape for the jaw faces (2x 22 x 6)", "buy", "purchased", "silicone", "",
                   vendor="generic", vendor_pn="1 mm adhesive silicone sheet", cost=0.05, qty_extra=2),
    "PS-209": Part("PS-209", "cable tie 2.5 x 100", "buy", "purchased", "pa66", "", vendor="generic", cost=0.02, qty_extra=4),
    # ---- fasteners
    "LIB-M5x12-CSK": Part("LIB-M5x12-CSK", "ISO 10642 M5x12 countersunk, base -> post end", "buy", "purchased", "steel",
                          "black_oxide", vendor="McMaster-Carr / Bossard", vendor_pn="ISO 10642 M5x12", cost=0.1, density=7.85),
    "LIB-M5x20-BH": Part("LIB-M5x20-BH", "ISO 7380 M5x20 button head, housing -> post end", "buy", "purchased", "steel",
                         "black_oxide", vendor="McMaster-Carr / Bossard", vendor_pn="ISO 7380 M5x20", cost=0.12, density=7.85),
    "LIB-M3x8-BH": Part("LIB-M3x8-BH", "ISO 7380 M3x8 button head (retainer, lid, pinion)", "buy", "purchased", "steel",
                        "black_oxide", vendor="McMaster-Carr / Bossard", vendor_pn="ISO 7380 M3x8", cost=0.06, density=7.85),
    "LIB-M3x16-SHCS": Part("LIB-M3x16-SHCS", "ISO 4762 M3x16 (tilt idler axle)", "buy", "purchased", "steel", "black_oxide",
                           vendor="McMaster-Carr / Bossard", vendor_pn="ISO 4762 M3x16", cost=0.08, density=7.85),
    "LIB-M3-W433": Part("LIB-M3-W433", "DIN 433 M3 washer (6 OD, bears on the 623 inner ring)", "buy", "purchased", "steel",
                        "steel", vendor="McMaster-Carr / Bossard", vendor_pn="DIN 433 M3", cost=0.02, density=7.85),
    "LIB-INS-M3": Part("LIB-INS-M3", "M3 heat-set insert, 4.0 OD x 5.7", "buy", "purchased", "brass", "brass",
                       vendor="Ruthex / CNC Kitchen", vendor_pn="M3 x 5.7", cost=0.05, density=8.5),
    "LIB-M2.5x12-PAN": Part("LIB-M2.5x12-PAN", "ISO 7045 M2.5x12 pan head (spring posts, self-tap into the jaw)", "buy",
                            "purchased", "steel", "steel", vendor="McMaster-Carr / Bossard", vendor_pn="ISO 7045 M2.5x12",
                            cost=0.05, density=7.85),
    "LIB-PHONE": Part("LIB-PHONE", "phone placeholder (not supplied): 71.6 x 147.6 x 7.8, 230 g", "buy", "purchased",
                      "", "", mass_g=230.0, notes="any phone 60-85 wide, <= 9.5 thick (camera bump free), ~230 g"),
}
PARTS.update(_servo_parts("PS-200A", "PS-200"))
PARTS.update(_servo_parts("PS-200B", "PS-200"))
for k in ("phone_body", "phone_glass", "phone_bump", "phone_lens"):
    PARTS[f"LIB-PHONE-{k}"] = Part(f"LIB-PHONE-{k}", "phone placeholder", "buy", "purchased", "", k, product="LIB-PHONE")


@functools.lru_cache(maxsize=None)
def shape(pn: str, key: str | None = None):
    """design-frame solid for a part number."""
    if pn in P.MAKERS:
        return P.MAKERS[pn]()
    if pn.startswith("PS-200"):
        sub = pn[-1]
        return {"C": V.servo_case, "L": V.servo_label, "S": V.servo_spline, "H": V.servo_horn}[sub]()
    if pn == "PS-134":
        return G.cyl_y(2.0, 6.0, 10.0) - G.cyl_y(1.55, 5.9, 10.1)
    if pn == "PS-201":
        return V.extrusion_2020(SP.POST_L)
    if pn == "PS-202":
        r, sh = V.bearing_623()
        return r + sh
    if pn == "PS-203":
        return V.extension_spring(1.0)   # placeholder: springs are built per width (spring_shape)
    if pn == "PS-205":
        return V.silicone_pad(SP.PAD_D, SP.PAD_T)
    if pn == "PS-206":
        return V.slot_cover(SP.POST_Z1 - SP.BOSS["top"] - 4.0)
    if pn == "LIB-M5x12-CSK":
        return V.screw(5, 12, "csk")
    if pn == "LIB-M5x20-BH":
        return V.screw(5, 20, "button")
    if pn == "LIB-M3x8-BH":
        return V.screw(3, 8, "button")
    if pn == "LIB-M3x16-SHCS":
        return V.screw(3, 16, "socket")
    if pn == "LIB-M3-W433":
        return V.washer(3.2, 6.0, 0.5)
    if pn == "LIB-INS-M3":
        return V.heat_insert_m3()
    if pn == "LIB-M2.5x12-PAN":
        return V.screw(2.5, 12, "pan")
    if pn.startswith("LIB-PHONE-"):
        return V.phone_parts()[pn[len("LIB-PHONE-"):]]
    raise KeyError(pn)


@functools.lru_cache(maxsize=None)
def spring_shape(length):
    return V.extension_spring(round(length, 2), SP.SPRING["od"], SP.SPRING["wire"])


def spring_length(w):
    return w + 2 * SP.SPRING_SCREW["dx"]


SPRING_Y = 15.2   # spring axis in the back lanes (T frame y)


def instances(w=None) -> list[Inst]:
    """all instances, the clamp set for a phone of width w (nominal by default)."""
    w = SP.PHONE_NOM["w"] if w is None else w
    I = []
    ZP = SP.Z_TH
    tp = G.trans(0, 0, SP.H_T - ZP)          # T -> P
    # ---------------- base (S)
    I += [Inst("base", "PS-100", "base"), Inst("lid", "PS-101", "base"), Inst("pan_housing", "PS-110", "base"),
          Inst("retainer", "PS-111", "base"), Inst("post", "PS-201", "base", G.trans(0, 0, SP.POST_Z0)),
          Inst("slot_cover", "PS-206", "base", G.trans(0, 0, SP.BOSS["top"] + 2.0))]
    for k, (x, y) in enumerate(SP.PADS):
        I.append(Inst(f"pad_{k}", "PS-205", "base", G.trans(x, y, 0.0)))
    I.append(Inst("base_screw", "LIB-M5x12-CSK", "base", G.trans(0, 0, SP.BASE_Z0) @ G.rot_x(180)))
    I.append(Inst("housing_screw", "LIB-M5x20-BH", "base", G.trans(0, 0, ZP + SP.POCKET_FLOOR - SP.M5_CB["depth"])))
    for k, a in enumerate(SP.RET_INSERT_ANG):
        x, y = SP.RET_INSERT_R * math.cos(math.radians(a)), SP.RET_INSERT_R * math.sin(math.radians(a))
        I.append(Inst(f"ret_insert_{k}", "LIB-INS-M3", "base", G.trans(x, y, ZP + SP.H_RIM)))
        I.append(Inst(f"ret_screw_{k}", "LIB-M3x8-BH", "base", G.trans(x, y, ZP + SP.H_RIM + SP.RET_T)))
    for k, (x, y) in enumerate(SP.LID_INSERTS):
        I.append(Inst(f"lid_insert_{k}", "LIB-INS-M3", "base", G.trans(x, y, SP.BASE_TOP)))
        I.append(Inst(f"lid_screw_{k}", "LIB-M3x8-BH", "base", G.trans(x, y, SP.BASE_TOP + SP.LID_T)))
    Fp = P.pan_servo_frame()
    for sub in "CLS":
        I.append(Inst(f"pan_servo_{sub}", f"PS-200A-{sub}", "base", Fp))
    # ---------------- pan (P): yoke, tilt servo, 623ZZ, pan horn
    I.append(Inst("yoke", "PS-120", "pan", tp))
    I.append(Inst("pan_horn", "PS-200A-H", "pan", G.trans(0, 0, -ZP) @ Fp))
    Ft = P.tilt_servo_frame()
    for sub in "CLS":
        I.append(Inst(f"tilt_servo_{sub}", f"PS-200B-{sub}", "pan", tp @ Ft))
    I.append(Inst("bearing_623", "PS-202", "pan", tp @ G.trans(-SP.ARM_OUT, 0, 0) @ G.rot_y(90)))
    # ---------------- tilt (T): cradle, horn, idler screw, spacer, inserts
    I.append(Inst("cradle", "PS-130", "tilt"))
    Fh = G.frame((SP.ARM_OUT, 0, 0), (0, 0, 1), (0, 1, 0), (-1, 0, 0))     # horn arms along z
    I.append(Inst("tilt_horn", "PS-200B-H", "tilt", Fh))
    I.append(Inst("idler_spacer", "PS-133", "tilt"))
    I.append(Inst("idler_washer", "LIB-M3-W433", "tilt", G.trans(-SP.ARM_OUT, 0, 0) @ G.rot_y(-90)))
    I.append(Inst("idler_screw", "LIB-M3x16-SHCS", "tilt", G.trans(-SP.ARM_OUT - 0.5, 0, 0) @ G.rot_y(-90)))
    I.append(Inst("ear_insert", "LIB-INS-M3", "tilt", G.trans(-SP.EAR_X[1], 0, 0) @ G.rot_y(-90)))
    I.append(Inst("pinion_insert", "LIB-INS-M3", "tilt", G.trans(0, SP.FLOOR_Y[0], 0) @ G.rot_x(90)))
    # ---------------- clamp (T): jaws, pinion, sleeve, pinion screw, springs, posts
    I.append(Inst("jaw_l", "PS-131", "clamp", P.jaw_place(w, -1)))
    I.append(Inst("jaw_r", "PS-131", "clamp", P.jaw_place(w, +1)))
    I.append(Inst("pinion", "PS-132", "clamp"))
    I.append(Inst("pinion_sleeve", "PS-134", "clamp"))
    I.append(Inst("pinion_screw", "LIB-M3x8-BH", "clamp", G.trans(0, 6.0, 0) @ G.rot_x(90)))
    xs = w / 2 + SP.SPRING_SCREW["dx"]
    for sz in (-1, 1):
        z = sz * SP.SPRING_SCREW["z"]
        I.append(Inst(f"spring_{'u' if sz > 0 else 'd'}", "PS-203", "clamp", G.trans(-xs, SPRING_Y, z),
                      shape_key=f"spring:{spring_length(w):.2f}"))
        for sx in (-1, 1):
            I.append(Inst(f"spring_post_{'l' if sx < 0 else 'r'}{'u' if sz > 0 else 'd'}", "LIB-M2.5x12-PAN", "clamp",
                          G.trans(sx * xs, 15.5, z) @ G.rot_x(-90)))
    # ---------------- phone (T)
    for k in ("phone_body", "phone_glass", "phone_bump", "phone_lens"):
        I.append(Inst(k, f"LIB-PHONE-{k}", "phone"))
    return I


def inst_shape(i: Inst):
    if i.shape_key and i.shape_key.startswith("spring:"):
        return spring_shape(float(i.shape_key.split(":")[1]))
    return shape(i.pn)


def finish(pn):
    p = PARTS[pn]
    return SP.FIN.get(pn, p.finish)


# ---------------------------------------------------------------- poses
def group_to_S(group, pan=0.0, el=0.0):
    """4x4: group frame -> S at (pan, el) [deg]."""
    Rp = G.rot_z(pan)
    if group == "base":
        return np.eye(4)
    if group == "pan":
        return Rp @ G.trans(0, 0, SP.Z_TH)
    return Rp @ G.trans(0, 0, SP.H_T) @ G.rot_x(el)


S_TO_THREE = np.array([[-1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float)   # S -> stand root (three)
