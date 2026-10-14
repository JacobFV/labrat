"""The head as data: part definitions (PN, material, process, vendor), placed instances, motion groups and bolted
joints. Pure build123d; ``mf.py`` turns this into a microfab Assembly for the full checks and Cycles renders.

Kinematics (one DOF, the plunger position ``pos`` in mm, 0 = stopper bottomed, + = drawn up):
    carriage group translates +Z by pos
    gear C rotates  th_c = +pos / r5           about +Y through C
    gear B rotates  th_b = -th_c * z4 / z3     about +Y through B
    gear A (servo)  th_a = -th_b * z2 / z1     about +Y through A      (th_a = ratio * th_c)
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

from build123d import Location, Plane, Vector

from . import parts as P
from . import vendor as V
from .spec import LAY, S

GEOM_SOURCES = ("spec.py", "vendor.py", "parts.py", "assembly.py")   # everything a part shape depends on
SRC_HASH = hashlib.sha1(b"".join((Path(__file__).parent / f).read_bytes() for f in GEOM_SOURCES)).hexdigest()[:12]


@dataclass
class PartDef:
    pn: str
    name: str
    kind: str                      # make | buy
    material: str                  # microfab material key (checks, BOM)
    process: str                   # fdm_print | laser_cut | cnc_lathe | purchased
    make: Callable                 # () -> Shape in the part frame
    category: str = "motion"
    finish: str = ""               # render finish override
    vendor: str = ""
    vendor_pn: str = ""
    cost: float | None = None
    product: str = ""              # BOM roll-up: sub-part of this product PN
    holes: tuple = ()              # (purpose, local point, note)
    notes: str = ""
    library: tuple | None = None   # ('screw', kind, thread, length) | ('nut', thread): microfab library parts
    unit: str = "ea"               # 'mm' for stock cut to length
    min_wall: float | None = None  # per-part wall rule override (FDM gear teeth)
    qty_per: float = 1.0           # stock length per instance (unit == 'mm')


TOOTH_WALL = 0.3   # m0.8 / m1.0 PA 25 involute tooth tips are 0.3-0.5 mm wide; the FDM 1.2 mm wall rule cannot apply


@dataclass
class Inst:
    name: str
    pn: str
    loc: Location
    group: str = "static"          # static | carriage | gear_a | gear_b | gear_c
    tags: tuple = ()


@dataclass
class Bolt:
    name: str
    kind: str                      # shcs | set
    thread: str
    length: float
    head: tuple                    # bearing point under the head (set screw: its top)
    axis: tuple                    # from the head into the joint
    clamped: tuple                 # instance names
    receiver: str                  # instance name (insert, nut, tapped purchased part)
    group: str = "static"


@dataclass
class Head:
    parts: dict = field(default_factory=dict)
    insts: list = field(default_factory=list)
    bolts: list = field(default_factory=list)
    phases: dict = field(default_factory=dict)

    def add_part(self, d: PartDef):
        old = self.parts.get(d.pn)
        if old is not None and old is not d:
            raise ValueError(f"part number collision: {d.pn}")
        self.parts[d.pn] = d
        return d

    def add(self, pn, loc, name, group="static", tags=()):
        if any(i.name == name for i in self.insts):
            raise ValueError(f"duplicate instance name {name}")
        i = Inst(name, pn, loc, group, tuple(tags))
        self.insts.append(i)
        return i

    def inst(self, name) -> Inst:
        return next(i for i in self.insts if i.name == name)


# ---------------------------------------------------------------------------------------------------------------------
def frame(origin, z=(0, 0, 1), x=None) -> Location:
    zv = Vector(*z).normalized()
    if x is None:
        ref = Vector(1, 0, 0) if abs(zv.X) < 0.9 else Vector(0, 1, 0)
        x = ref - zv * ref.dot(zv)
    return Location(Plane(origin=tuple(origin), x_dir=tuple(x), z_dir=tuple(zv)))


def gear_loc(c, y_front, phase=0.0):
    """gear part frame (z = axis = +Y, z = 0 on the front face) at centre c = (x, z)."""
    return frame((c[0], y_front, c[1]), z=(0, 1, 0), x=(1, 0, 0))


def local_angle(src, dst):
    """angle in a gear's local XY (local y = machine -Z) of the direction src -> dst (machine x, z)."""
    return math.degrees(math.atan2(-(dst[1] - src[1]), dst[0] - src[0]))


def gear_phases():
    A, B, C = LAY.A, LAY.B, LAY.C
    ph = {
        "g5": 180.0,                                              # a tooth into the rack gap at zc (rack is at -X)
        "g4": local_angle(C, B),                                  # a G4 tooth toward B ...
        "g3": local_angle(B, C) + 180.0 / S.z3,                   # ... meets a G3 gap
        "g2": local_angle(B, A),                                  # a G2 tooth toward A ...
        "g1": local_angle(A, B) + 180.0 / S.z1,                   # ... meets a G1 gap
    }
    return ph


def kin(pos):
    r5 = S.z5 * S.m3 / 2
    th_c = math.degrees(pos / r5)
    th_b = -th_c * S.z4 / S.z3
    th_a = -th_b * S.z2 / S.z1
    return {"carriage": pos, "gear_c": th_c, "gear_b": th_b, "gear_a": th_a}


def group_motion(group, pos) -> Location:
    """motion of a group (machine frame) at plunger position pos."""
    k = kin(pos)
    if group == "carriage":
        return Location((0, 0, pos))
    if group in ("gear_a", "gear_b", "gear_c"):
        c = {"gear_a": LAY.A, "gear_b": LAY.B, "gear_c": LAY.C}[group]
        o = (c[0], 0.0, c[1])
        return Location(o) * Location((0, 0, 0), (0, 1, 0), k[group]) * Location((-o[0], -o[1], -o[2]))
    return Location()


# ---------------------------------------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def build() -> Head:
    H = Head()
    L = LAY
    sv = L.servo
    ph = gear_phases()
    H.phases = ph
    A, B, C = L.A, L.B, L.C

    # ================= make parts =================
    H.add_part(PartDef("SYR-100", "head body", "make", "petg_print", "fdm_print", P.body, "fixture",
                       holes=tuple(P.body_holes()),
                       notes="PETG, 0.2 mm, 6 walls, 40 % gyroid; upright, tree supports under lip + table"))
    H.add_part(PartDef("SYR-101", "gearbox cover", "make", "acrylic_clear", "laser_cut", P.cover, "panel",
                       holes=(("pin", (B[0], B[1], 1.5), "dowel B (slip)"), ("pin", (C[0], C[1], 1.5), "dowel C (slip)")),
                       notes="3 mm cast acrylic, laser cut; raster-engraved legend"))
    H.add_part(PartDef("SYR-102", "flange clamp", "make", "petg_print", "fdm_print", P.flange_clamp, "fixture",
                       holes=(("access", (0.0, 0.0, L.clampplate_bottom_z + 2.0), "plunger + thumb disc pass through"),)))
    H.add_part(PartDef("SYR-105", "thumb-disc cap", "make", "petg_print", "fdm_print", P.disc_cap, "motion",
                       finish="syr_petg_orange"))
    H.add_part(PartDef("SYR-103", "plunger carriage with m1 rack", "make", "petg_print", "fdm_print", P.carriage,
                       "motion", finish="syr_petg_orange", holes=tuple(P.carriage_holes()),
                       notes="printed on its back face (rack teeth in the XY plane)"))
    H.add_part(PartDef("SYR-104", "luer-to-tip adapter", "make", "pom_black", "cnc_lathe", P.adapter, "fluidics",
                       finish="syr_pom_white", holes=(("fluid", (0.0, 0.0, L.cone_top_z), "liquid bore"),),
                       notes="POM-C natural, turned; female ISO 80369-7 slip socket, 200 uL tip cone"))
    gtot_a, gtot_b = S.w12, S.w12 + (L.y_g4[0] - L.y_g2[1]) + S.w34
    gtot_c = S.w34 + (L.y_rack[0] - L.y_g4[1]) + S.w5
    H.add_part(PartDef("SYR-110", "G1 17T m0.8 servo gear", "make", "petg_print", "fdm_print",
                       lambda: P.gear_a(ph["g1"]), finish="syr_petg_white", min_wall=TOOTH_WALL,
                       holes=(("shaft", (0.0, 0.0, 1.0), "horn hub pocket"),)))
    H.add_part(PartDef("SYR-111", "G2/G3 50T m0.8 + 12T m1 compound gear", "make", "petg_print", "fdm_print",
                       lambda: P.gear_b(ph["g2"], ph["g3"]), finish="syr_petg_white", min_wall=TOOTH_WALL,
                       holes=tuple(P.gear_holes(gtot_b, P.lightening(12.0)))))
    H.add_part(PartDef("SYR-112", "G4/G5 49T m1 + 12T m1 output gear", "make", "petg_print", "fdm_print",
                       lambda: P.gear_c(ph["g4"], ph["g5"]), finish="syr_petg_white", min_wall=TOOTH_WALL,
                       holes=tuple(P.gear_holes(gtot_c, P.lightening(14.0, a0=math.pi / 6)))))
    spacers = {"b_front": (B, L.cover_back_y, L.y_g2[0]), "b_rear": (B, L.y_g4[1], L.back_plate_y[0]),
               "c_front": (C, L.cover_back_y, L.y_g4[0]), "c_rear": (C, L.y_rack[1], L.back_plate_y[0])}
    for k, (c, y0, y1) in spacers.items():
        ln = round(y1 - y0, 2)
        pn = f"SYR-130-{ln:05.2f}".replace(".", "p")
        if pn not in H.parts:
            H.add_part(PartDef(pn, f"bearing spacer, brass tube 4 x 0.45 cut to {ln:g}", "stock", "brass_c360",
                               "tube_cut", (lambda ln=ln: P.spacer(ln)), "motion", vendor="K&S Precision Metals",
                               vendor_pn="9220 (4 mm x 0.45 wall brass tube), cut + deburred", cost=0.004 * ln,
                               holes=(("shaft", (0, 0, ln / 2), "dowel"),), unit="mm", qty_per=ln,
                               notes="bears on the MR63ZZ inner ring only (OD 4.0 < shield bore)"))
        H.add(pn, frame((c[0], y0, c[1]), z=(0, 1, 0), x=(1, 0, 0)), f"spacer_{k}")

    # ================= purchased =================
    def buy(pn, name, make, material, vendor, vendor_pn, cost, product="", category="motion", finish="", holes=(), notes=""):
        return H.add_part(PartDef(pn, name, "buy", material, "purchased", make, category, finish, vendor, vendor_pn,
                                  cost, product, holes, notes))

    buy("SYR-200A", "FS90R servo case", V.servo_case, "abs_black", "Feetech", "FS90R", 6.50, category="electrical",
        notes="continuous rotation micro servo, 4.8-6 V, 110 rpm no-load @ 4.8 V")
    buy("SYR-200B", "FS90R output spline", V.servo_spline, "pom_black", "Feetech", "FS90R (incl.)", 0.0, "SYR-200A",
        holes=(("tool", (0, 0, 7.5), "M2 horn screw, tapped"),))
    buy("SYR-200C", "FS90R label", V.servo_label, "label_vinyl", "Feetech", "FS90R (incl.)", 0.0, "SYR-200A")
    buy("SYR-200D", "FS90R label print", V.servo_label_text, "print_black", "Feetech", "FS90R (incl.)", 0.0, "SYR-200A")
    buy("SYR-200E", "FS90R cross horn, arms trimmed to r4.6", V.servo_horn, "polypropylene_white", "Feetech",
        "FS90R (incl.)", 0.0, "SYR-200A", notes="trim the 4 arms to 4.6 mm from the centre")
    buy("SYR-201A", "12 mL luer-slip syringe barrel", V.syringe_barrel, "polypropylene_natural", "BD / Terumo / Monoject",
        "12 mL luer slip (barrel OD 20.2 measured)", 0.25, category="fluidics", finish="syr_pp_clear",
        holes=(("fluid", (0, 0, -85.0), "luer bore"),))
    buy("SYR-201B", "syringe graduations", V.syringe_ink, "print_black", "", "(printed on SYR-201A)", 0.0, "SYR-201A",
        category="labels")
    buy("SYR-201C", "syringe plunger rod", V.plunger_rod, "polypropylene_white", "", "(part of SYR-201A)", 0.0,
        "SYR-201A", category="fluidics")
    buy("SYR-201D", "syringe stopper", V.stopper, "epdm_rubber", "", "(part of SYR-201A)", 0.0, "SYR-201A",
        category="fluidics")
    buy("SYR-202", "200 uL universal pipette tip", V.pipette_tip, "polypropylene_natural", "Axygen / Sarstedt",
        "T-200-Y (representative)", 0.02, category="consumable", finish="syr_tip_yellow",
        holes=(("fluid", (0, 0, -40.0), "tip bore"),))
    buy("SYR-210", "MGN7 rail L70", V.mgn7_rail, "steel_hardened_ground", "HIWIN", "MGNR7R70HM", 9.0,
        holes=tuple(("tool", (2.4, 0.0, z), "counterbored M2 hole") for z in V.rail_hole_z()))
    buy("SYR-211A", "MGN7C carriage block", V.mgn7c_block, "steel_hardened_ground", "HIWIN", "MGN7C1R70HM (block)", 11.0)
    buy("SYR-211B", "MGN7C end caps", V.mgn7c_endcaps, "abs_black", "HIWIN", "(part of SYR-211A)", 0.0, "SYR-211A")
    buy("SYR-211C", "MGN7C end seals", V.mgn7c_seals, "silicone_rubber_red", "HIWIN", "(part of SYR-211A)", 0.0, "SYR-211A")
    buy("SYR-220", "MR63ZZ bearing 3x6x2.5", V.mr63zz, "steel_hardened_ground", "NMB / SKF", "MR63ZZ", 0.6,
        holes=(("shaft", (0, 0, 1.25), "dowel"),))
    buy("SYR-221", "dowel pin ISO 2338 3m6 x 40", V.dowel_pin, "steel_hardened_ground", "McMaster-Carr", "ISO 2338 3m6x40",
        0.3, category="fastener")
    buy("SYR-222", "heat-set insert M2 x 4", lambda: V.heatset("M2-0.4"), "brass_c360", "ruthex", "RX-M2x4", 0.10,
        category="fastener")
    buy("SYR-223", "heat-set insert M3 x 5.7", lambda: V.heatset("M3-0.5"), "brass_c360", "ruthex", "RX-M3x5.7", 0.12,
        category="fastener")
    buy("SYR-230A", "servo lead, brown (GND)", lambda: V.wire(cable_path(), 1.0, -1.0), "cable_pvc_black",
        "Feetech", "FS90R lead (incl.)", 0.0, "SYR-200A", "electrical", "syr_cable_brown")
    buy("SYR-230B", "servo lead, red (V+)", lambda: V.wire(cable_path(), 1.0, 0.0), "cable_pvc_black",
        "Feetech", "FS90R lead (incl.)", 0.0, "SYR-200A", "electrical", "syr_cable_red")
    buy("SYR-230C", "servo lead, orange (signal)", lambda: V.wire(cable_path(), 1.0, 1.0), "cable_pvc_black",
        "Feetech", "FS90R lead (incl.)", 0.0, "SYR-200A", "electrical", "syr_cable_orange")
    buy("SYR-231", "2.54 mm 3-way housing (servo lead end)", V.dupont_3pin, "abs_black", "Feetech",
        "FS90R lead (incl.)", 0.0, "SYR-200A", "electrical", notes="to Tappet tap_a PCA9685 ch 8")
    buy("SYR-232", "cable tie 2.5 x 100 nylon", lambda: V.cable_tie(*tie_args()), "polypropylene_natural", "HellermannTyton",
        "T18R", 0.03, category="electrical")

    # ================= static instances =================
    I = lambda pn, name, loc=Location(), g="static", t=(): H.add(pn, loc, name, g, t)
    I("SYR-100", "body")
    I("SYR-101", "cover", P.cover_loc())
    I("SYR-102", "flange_clamp")
    I("SYR-104", "adapter")
    I("SYR-201A", "barrel", Location((0, 0, L.flange_z)))
    I("SYR-201B", "graduations", Location((0, 0, L.flange_z)))
    I("SYR-202", "tip", Location((0, 0, L.tip_top_z)))
    rail_loc = frame((L.rail_face_x, 0, L.rail_z0), z=(0, 0, 1), x=(1, 0, 0))
    I("SYR-210", "rail", rail_loc)
    servo_loc = frame((A[0], L.cover_front_y, A[1]), z=(0, 1, 0), x=(-1, 0, 0))
    for pn, nm in (("SYR-200A", "servo"), ("SYR-200C", "servo_label"), ("SYR-200D", "servo_label_print")):
        I(pn, nm, servo_loc)
    for pn, nm in (("SYR-230A", "lead_gnd"), ("SYR-230B", "lead_vcc"), ("SYR-230C", "lead_sig")):
        I(pn, nm, t=("cable",))
    I("SYR-231", "lead_connector", connector_loc(), t=("cable",))
    I("SYR-232", "lead_tie", t=("cable",))
    for k, c in (("b", B), ("c", C)):
        I("SYR-221", f"pin_{k}", frame((c[0], L.cover_front_y + 0.2, c[1]), z=(0, 1, 0), x=(1, 0, 0)))

    # ================= moving: carriage =================
    I("SYR-103", "carriage", g="carriage")
    I("SYR-105", "disc_cap", g="carriage")
    I("SYR-201C", "plunger", Location((0, 0, L.flange_z)), "carriage")
    I("SYR-201D", "stopper", Location((0, 0, L.flange_z)), "carriage")
    blk = rail_loc * Location((0, 0, L.mgn0_z - L.rail_z0))
    for pn, nm in (("SYR-211A", "mgn_block"), ("SYR-211B", "mgn_endcaps"), ("SYR-211C", "mgn_seals")):
        I(pn, nm, blk, "carriage")

    # ================= moving: gears =================
    I("SYR-112", "gear_c", gear_loc(C, L.y_g4[0]), "gear_c")
    I("SYR-111", "gear_b", gear_loc(B, L.y_g2[0]), "gear_b")
    I("SYR-110", "gear_a", gear_loc(A, L.g1_back), "gear_a")
    for pn, nm in (("SYR-200B", "servo_spline"), ("SYR-200E", "servo_horn")):
        I(pn, nm, servo_loc, "gear_a")
    for k, (c, y0, y1, g) in {"b_front": (B, L.y_g2[0], None, "gear_b"), "b_rear": (B, L.y_g4[1] - S.brg_B, None, "gear_b"),
                              "c_front": (C, L.y_g4[0], None, "gear_c"), "c_rear": (C, L.y_rack[1] - S.brg_B, None, "gear_c")}.items():
        I("SYR-220", f"bearing_{k}", frame((c[0], y0, c[1]), z=(0, 1, 0), x=(1, 0, 0)), g)

    # ================= inserts + bolted joints =================
    def insert(pn_or_lib, name, top, out_normal, group="static"):
        return H.add(pn_or_lib, frame(top, z=out_normal), name, group)

    H.add_part(PartDef("MF-NUT-M2-ISO4032", "ISO 4032 hex nut M2", "buy", "stainless_a2_fastener", "purchased", None,
                       "fastener", library=("nut", "M2-0.4")))

    def screw_pn(kind, thread, length):
        size = thread.split("-")[0]
        pn = f"LIB-{kind.upper()}-{size}x{length:g}"
        if pn not in H.parts:
            H.add_part(PartDef(pn, f"ISO 4762 {size}x{length:g} socket head cap screw, A2", "buy", "stainless_a2_fastener",
                               "purchased", None, "fastener", vendor="McMaster-Carr / Bossard",
                               vendor_pn=f"ISO 4762 {size}x{length:g} A2", cost=round(0.06 * (1 + length / 30), 3),
                               library=("screw", kind, thread, float(length))))
        return pn

    by0 = L.back_plate_y[0]
    # cover -> standoffs: M3x8 into inserts
    for k, (x, z) in enumerate(P.STANDOFFS):
        ins = insert("SYR-223", f"ins_cover_{k}", (x, L.cover_back_y, z), (0, -1, 0))
        H.bolts.append(Bolt(f"scr_cover_{k}", "shcs", "M3-0.5", 8, (x, L.cover_front_y, z), (0, 1, 0), ("cover",), ins.name))
    # flange clamp: M3x10 into table inserts
    for k, (x, y) in enumerate(P.CLAMP_SCREWS):
        ins = insert("SYR-223", f"ins_clamp_{k}", (x, y, L.table_top_z), (0, 0, 1))
        H.bolts.append(Bolt(f"scr_clamp_{k}", "shcs", "M3-0.5", 10, (x, y, L.clampplate_top_z), (0, 0, -1),
                            ("flange_clamp",), ins.name))
    # rail: M2x6 through the counterbores into spine inserts
    for k, zr in enumerate(V.rail_hole_z()):
        z = L.rail_z0 + zr
        ins = insert("SYR-222", f"ins_rail_{k}", (L.rail_face_x, 0, z), (1, 0, 0))
        H.bolts.append(Bolt(f"scr_rail_{k}", "shcs", "M2-0.4", 6, (L.rail_face_x + L.rail_H - 2.3, 0, z), (-1, 0, 0),
                            ("rail",), ins.name))
    # carriage -> MGN7C: 4x M2x4 (counterbored 1.4 in the 3 mm plate)
    px = L.jaw_x[0] + P.PLATE_T - P.CBORE
    for sy in (-1, 1):
        for sz in (-1, 1):
            H.bolts.append(Bolt(f"scr_mgn_{'n' if sy < 0 else 'p'}{'d' if sz < 0 else 'u'}", "shcs", "M2-0.4", 4,
                                (px, sy * L.mgn_B / 2, L.mgn0_z + sz * L.mgn_C / 2), (-1, 0, 0), ("carriage",),
                                "mgn_block", "carriage"))
    # servo tabs -> cover: M2x8 with nuts on the cover's back face
    scx = A[0] - (sv["L"] / 2 - sv["shaft_from_end"])
    for k, dx in enumerate((-sv["tab_pitch"] / 2, sv["tab_pitch"] / 2)):
        nut = H.add("MF-NUT-M2-ISO4032", frame((scx + dx, L.cover_back_y, A[1]), z=(0, 1, 0)), f"nut_servo_{k}")
        H.bolts.append(Bolt(f"scr_servo_{k}", "shcs", "M2-0.4", 8, (scx + dx, L.cover_front_y - sv["tab_t"], A[1]),
                            (0, 1, 0), ("servo", "cover"), nut.name))
    # horn screw: M2x5 through G1 + horn into the servo spline
    H.bolts.append(Bolt("scr_horn", "shcs", "M2-0.4", 5, (A[0], L.g1_back + 1.8 + 1.2, A[1]), (0, -1, 0),
                        ("gear_a", "servo_horn"), "servo_spline", "gear_a"))
    # thumb-disc cap: 2x M2x8 into inserts in the jaw's rear block; the cap pocket bears on the disc top
    zc0 = L.disc0_z + L.jaw_top_dz
    for k, (x, y) in enumerate(L.cap_screws):
        ins = insert("SYR-222", f"ins_cap_{k}", (x, y, zc0), (0, 0, 1), "carriage")
        H.bolts.append(Bolt(f"scr_cap_{k}", "shcs", "M2-0.4", 8, (x, y, zc0 + L.cap_gap + L.cap_t), (0, 0, -1),
                            ("disc_cap",), ins.name, "carriage"))
    for b in H.bolts:
        screw_pn(b.kind, b.thread, b.length)
    return H


# ---------------------------------------------------------------------------------------------------------------------
# servo lead route (machine frame)
# ---------------------------------------------------------------------------------------------------------------------
def servo_exit():
    p, d = V.servo_cable_exit()
    loc = frame((LAY.A[0], LAY.cover_front_y, LAY.A[1]), z=(0, 1, 0), x=(-1, 0, 0))
    q = loc * Location(p)
    return (q.position.X, q.position.Y, q.position.Z)


def cable_path():
    x0, y0, z0 = servo_exit()
    yc = TIE_YC                                       # lead runs up the cover's front face through the tie
    return [(x0, y0, z0), (x0 - 2.5, y0, z0 + 0.3), (x0 - 4.9, y0 + 0.7, z0 + 3.2), (x0 - 5.4, y0 + 3.2, z0 + 7.7),
            (-7.0, yc - 4.0, 124.5), (-3.0, yc, 128.0), (-3.0, yc, 134.0), (-3.5, yc - 3.0, 148.0),
            (-6.0, yc - 9.0, 166.0), (-7.0, yc - 11.0, 178.0)]


TIE_YC = LAY.cover_front_y - 2.2


def connector_loc():
    pts = cable_path()
    a, b = Vector(*pts[-2]), Vector(*pts[-1])
    return frame(pts[-1], z=tuple((b - a).normalized()), x=(1, 0, 0))


def tie_args():
    yc = TIE_YC
    inner = (7.4, (LAY.cover_back_y - (yc - 1.7)))
    return ((-3.0, (LAY.cover_back_y + yc - 1.7) / 2, P.TIE_Z), (0, 0, 1), inner)
