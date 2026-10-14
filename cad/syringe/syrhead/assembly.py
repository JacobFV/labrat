"""The head as data: part definitions (PN, material, process, vendor), placed instances, motion groups and bolted
joints. Pure build123d; ``mf.py`` turns this into a microfab Assembly for the full checks and Cycles renders.

Kinematics (one DOF, the plunger position ``pos`` in mm, 0 = stopper bottomed, + = drawn up). Gear axes run along
machine +X; the rack is on the +Y side of the output pinion.
    carriage group translates +Z by pos
    gear C rotates  th_c = +pos / r5           about +X through C
    gear B rotates  th_b = -th_c * z4 / z3     about +X through B
    gear A (servo)  th_a = -th_b * z2 / z1     about +X through A      (th_a = ratio * th_c)
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
from . import tools as TL
from . import vendor as V
from .spec import LAY, S

GEOM_SOURCES = ("spec.py", "vendor.py", "parts.py", "tools.py", "assembly.py")   # everything a part shape depends on
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


AXIS = (1.0, 0.0, 0.0)            # every gear axis (machine +X)
# gear part frames: (machine x of the part's z = 0 face, part z direction). Local x = machine +Y in all of them.
GEAR_FRAMES = {"gear_c": (LAY.x_g4[1], -1), "gear_b": (LAY.x_g2[1], -1), "gear_a": (LAY.x_g1[0], +1)}
CENTRES = {"gear_a": LAY.A, "gear_b": LAY.B, "gear_c": LAY.C}


def gear_loc(group):
    x0, d = GEAR_FRAMES[group]
    c = CENTRES[group]
    return frame((x0, c[0], c[1]), z=(d, 0, 0), x=(0, 1, 0))


def local_angle(group, src, dst):
    """angle, in the gear part frame of ``group``, of the direction src -> dst (machine (y, z)). The local x axis is
    machine +Y; local y is machine -Z for a part whose z runs along -X and +Z for one whose z runs along +X."""
    d = GEAR_FRAMES[group][1]
    dy, dz = dst[0] - src[0], dst[1] - src[1]
    return math.degrees(math.atan2(-dz if d < 0 else dz, dy))


def gear_phases():
    A, B, C = LAY.A, LAY.B, LAY.C
    rack = (C[0] + 10.0, C[1])                                   # the rack is on the +Y side of the pinion
    return {
        "g5": local_angle("gear_c", C, rack),                    # a tooth into the rack gap at zc
        "g4": local_angle("gear_c", C, B),                       # a G4 tooth toward B ...
        "g3": local_angle("gear_b", B, C) + 180.0 / S.z3,        # ... meets a G3 gap
        "g2": local_angle("gear_b", B, A),                       # a G2 tooth toward A ...
        "g1": local_angle("gear_a", A, B) + 180.0 / S.z1,        # ... meets a G1 gap
    }


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
    if group in CENTRES:
        c = CENTRES[group]
        o = (0.0, c[0], c[1])
        return Location(o) * Location((0, 0, 0), AXIS, k[group]) * Location((-o[0], -o[1], -o[2]))
    return Location()


def servo_loc():
    """FS90R frame: z = output axis (machine -X), z = 0 on the tab top plane (gear plate 2's outer face), x along the
    case from the shaft toward the far end (machine -Z: the case hangs down)."""
    return frame((LAY.servo_tab_x, LAY.A[0], LAY.A[1]), z=(-1, 0, 0), x=(0, 0, -1))


# ---------------------------------------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def build() -> Head:
    H = Head()
    L = LAY
    sv = L.servo
    ph = gear_phases()
    H.phases = ph
    A, B, C = L.A, L.B, L.C
    x1 = L.plate1_x[1]
    xi, xo = L.plate2_x

    # ================= make parts =================
    H.add_part(PartDef("SYR-100", "head body", "make", "petg_print", "fdm_print", P.body, "fixture",
                       holes=tuple(P.body_holes()),
                       notes="PETG, 0.2 mm, 6 walls, 25 % gyroid; upright on the plug, no supports"))
    H.add_part(PartDef("SYR-101", "gear plate 2 with standoffs", "make", "petg_print", "fdm_print", P.plate2, "fixture",
                       holes=tuple(P.plate2_holes()),
                       notes="PETG, 0.2 mm, 6 walls; on its outer face (standoffs up), no supports"))
    H.add_part(PartDef("SYR-102", "flange clamp", "make", "petg_print", "fdm_print", P.flange_clamp, "fixture",
                       holes=(("access", (0.0, 0.0, L.clampplate_bottom_z + 2.0), "plunger + thumb disc pass through"),),
                       notes="upside down (relief pocket up), no supports"))
    H.add_part(PartDef("SYR-105", "thumb-disc cap", "make", "petg_print", "fdm_print", P.disc_cap, "motion",
                       finish="syr_petg_orange", notes="upside down (pocket up), no supports"))
    H.add_part(PartDef("SYR-103", "plunger carriage with m1 rack", "make", "petg_print", "fdm_print", P.carriage,
                       "motion", finish="syr_petg_orange", holes=tuple(P.carriage_holes()),
                       notes="on its back face (rack teeth stand up), no supports; slides on the body's square guide bar "
                             "(PLA on PLA, a smear of PTFE / silicone grease)"))
    H.add_part(PartDef("SYR-104", "luer-to-tip adapter", "make", "pom_black", "cnc_lathe", P.adapter, "fluidics",
                       finish="syr_pom_white", holes=(("fluid", (0.0, 0.0, L.cone_top_z), "liquid bore"),),
                       notes="POM-C natural, turned; female ISO 80369-7 slip socket, 200 uL tip cone"))
    gtot_b = (L.x_g2[1] - L.x_g3[0])
    gtot_c = (L.x_g4[1] - L.x_g5[0])
    H.add_part(PartDef("SYR-110", "G1 17T m0.8 servo gear", "make", "petg_print", "fdm_print",
                       lambda: P.gear_a(ph["g1"]), finish="syr_petg_white", min_wall=TOOTH_WALL,
                       holes=(("shaft", (0.0, 0.0, L.g1_w - 0.5), "horn hub pocket"),),
                       notes="horn pocket up, no supports"))
    H.add_part(PartDef("SYR-111", "G2/G3 50T m0.8 + 12T m1 compound gear", "make", "petg_print", "fdm_print",
                       lambda: P.gear_b(ph["g2"], ph["g3"]), finish="syr_petg_white", min_wall=TOOTH_WALL,
                       holes=tuple(P.gear_holes(gtot_b, P.lightening(12.0))), notes="G2 on the bed, no supports"))
    H.add_part(PartDef("SYR-112", "G4/G5 49T m1 + 12T m1 output gear", "make", "petg_print", "fdm_print",
                       lambda: P.gear_c(ph["g4"], ph["g5"]), finish="syr_petg_white", min_wall=TOOTH_WALL,
                       holes=tuple(P.gear_holes(gtot_c, P.lightening(15.0, a0=math.pi / 6))),
                       notes="G4 on the bed, no supports"))
    spacers = {"c_rear": (C, x1, L.x_g5[0]), "c_front": (C, L.x_g4[1], xi),
               "b_rear": (B, x1, L.x_g3[0]), "b_front": (B, L.x_g2[1], xi)}
    for k, (c, xa, xb) in spacers.items():
        ln = round(xb - xa, 2)
        pn = f"SYR-130-{ln:05.2f}".replace(".", "p")
        if pn not in H.parts:
            H.add_part(PartDef(pn, f"bearing spacer, brass tube 4 x 0.45 cut to {ln:g}", "stock", "brass_c360",
                               "tube_cut", (lambda ln=ln: P.spacer(ln)), "motion", vendor="K&S Precision Metals",
                               vendor_pn="9220 (4 mm x 0.45 wall brass tube), cut + deburred", cost=round(0.004 * ln, 4),
                               holes=(("shaft", (0, 0, ln / 2), "dowel"),), unit="mm", qty_per=ln,
                               notes="bears on the MR63ZZ inner ring only (OD 4.0 < shield bore)"))
        H.add(pn, frame((xa, c[0], c[1]), z=(1, 0, 0), x=(0, 1, 0)), f"spacer_{k}")

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
    buy("SYR-202", "200 uL universal pipette tip", V.pipette_tip, "polypropylene_natural", "(FT-200L-RS rack)",
        "200 uL, 96 / rack (representative tip geometry)", 0.02, category="consumable", finish="syr_tip_yellow",
        holes=(("fluid", (0, 0, -40.0), "tip bore"),))
    buy("SYR-220", "MR63ZZ bearing 3x6x2.5", V.mr63zz, "steel_hardened_ground", "NMB / SKF", "MR63ZZ", 0.6,
        holes=(("shaft", (0, 0, 1.25), "dowel"),))
    buy("SYR-221", f"dowel pin ISO 2338 3m6 x {S.pin_len:g}", V.dowel_pin, "steel_hardened_ground", "McMaster-Carr",
        f"ISO 2338 3m6x{S.pin_len:g}", 0.3, category="fastener")
    buy("SYR-222", "heat-set insert M2 x 4", lambda: V.heatset("M2-0.4"), "brass_c360", "ruthex", "RX-M2x4", 0.10,
        category="fastener")
    buy("SYR-223", "heat-set insert M3 x 5.7", lambda: V.heatset("M3-0.5"), "brass_c360", "ruthex", "RX-M3x5.7", 0.12,
        category="fastener")
    buy("SYR-230A", "servo lead, brown (GND)", lambda: V.wire(cable_path(), 1.0, -1.0, N0), "cable_pvc_black",
        "Feetech", "FS90R lead (incl.)", 0.0, "SYR-200A", "electrical", "syr_cable_brown")
    buy("SYR-230B", "servo lead, red (V+)", lambda: V.wire(cable_path(), 1.0, 0.0, N0), "cable_pvc_black",
        "Feetech", "FS90R lead (incl.)", 0.0, "SYR-200A", "electrical", "syr_cable_red")
    buy("SYR-230C", "servo lead, orange (signal)", lambda: V.wire(cable_path(), 1.0, 1.0, N0), "cable_pvc_black",
        "Feetech", "FS90R lead (incl.)", 0.0, "SYR-200A", "electrical", "syr_cable_orange")
    buy("SYR-231", "2.54 mm 3-way housing (servo lead end)", V.dupont_3pin, "abs_black", "Feetech",
        "FS90R lead (incl.)", 0.0, "SYR-200A", "electrical", notes="to Tappet tap_a PCA9685 ch 8")
    buy("SYR-232", "cable tie 2.5 x 100 nylon", lambda: V.cable_tie(*tie_args()), "polypropylene_natural", "HellermannTyton",
        "T18R", 0.03, category="electrical")

    # ================= static instances =================
    I = lambda pn, name, loc=Location(), g="static", t=(): H.add(pn, loc, name, g, t)
    I("SYR-100", "body")
    I("SYR-101", "plate2")
    I("SYR-102", "flange_clamp")
    I("SYR-104", "adapter")
    syr = Location((0, 0, L.flange_z), (0, 0, 1), 90)             # flange long axis along Y
    I("SYR-201A", "barrel", syr)
    I("SYR-201B", "graduations", Location((0, 0, L.flange_z)))    # print faces the front (-Y): the camera side
    I("SYR-202", "tip", Location((0, 0, L.tip_top_z)))
    for pn, nm in (("SYR-200A", "servo"), ("SYR-200C", "servo_label"), ("SYR-200D", "servo_label_print")):
        I(pn, nm, servo_loc())
    for pn, nm in (("SYR-230A", "lead_gnd"), ("SYR-230B", "lead_vcc"), ("SYR-230C", "lead_sig")):
        I(pn, nm, t=("cable",))
    I("SYR-231", "lead_connector", connector_loc(), t=("cable",))
    I("SYR-232", "lead_tie", t=("cable",))
    for k, c in (("b", B), ("c", C)):                               # pressed flush into plate 2's outer face
        I("SYR-221", f"pin_{k}", frame((xo, c[0], c[1]), z=(-1, 0, 0), x=(0, 1, 0)))

    # ================= moving: carriage =================
    I("SYR-103", "carriage", g="carriage")
    I("SYR-105", "disc_cap", g="carriage")
    I("SYR-201C", "plunger", syr, "carriage")
    I("SYR-201D", "stopper", Location((0, 0, L.flange_z)), "carriage")

    # ================= moving: gears =================
    I("SYR-112", "gear_c", gear_loc("gear_c"), "gear_c")
    I("SYR-111", "gear_b", gear_loc("gear_b"), "gear_b")
    I("SYR-110", "gear_a", gear_loc("gear_a"), "gear_a")
    for pn, nm in (("SYR-200B", "servo_spline"), ("SYR-200E", "servo_horn")):
        I(pn, nm, servo_loc(), "gear_a")
    for k, (c, xa, g) in {"c_rear": (C, L.x_g5[0], "gear_c"), "c_front": (C, L.x_g4[1] - S.brg_B, "gear_c"),
                          "b_rear": (B, L.x_g3[0], "gear_b"), "b_front": (B, L.x_g2[1] - S.brg_B, "gear_b")}.items():
        I("SYR-220", f"bearing_{k}", frame((xa, c[0], c[1]), z=(1, 0, 0), x=(0, 1, 0)), g)

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

    # gear plate 2 -> gear plate 1: M3x30 through each standoff into an insert in plate 1
    for k, (y, z) in enumerate(L.standoffs):
        ins = insert("SYR-223", f"ins_plate2_{k}", (x1, y, z), (1, 0, 0))
        H.bolts.append(Bolt(f"scr_plate2_{k}", "shcs", "M3-0.5", 30, (xo, y, z), (-1, 0, 0), ("plate2",), ins.name))
    # flange clamp: M2x8 into table inserts
    for k, (x, y) in enumerate(P.CLAMP_SCREWS):
        ins = insert("SYR-222", f"ins_clamp_{k}", (x, y, L.table_top_z), (0, 0, 1))
        H.bolts.append(Bolt(f"scr_clamp_{k}", "shcs", "M2-0.4", 8, (x, y, L.clampplate_top_z), (0, 0, -1),
                            ("flange_clamp",), ins.name))
    # servo tabs -> gear plate 2: M2x8, nuts on the plate's inner face
    sc = L.servo_case_c
    for k, dz in enumerate((-sv["tab_pitch"] / 2, sv["tab_pitch"] / 2)):
        nut = H.add("MF-NUT-M2-ISO4032", frame((xi, sc[0], sc[1] + dz), z=(-1, 0, 0)), f"nut_servo_{k}")
        H.bolts.append(Bolt(f"scr_servo_{k}", "shcs", "M2-0.4", 8, (xo + sv["tab_t"], sc[0], sc[1] + dz), (-1, 0, 0),
                            ("servo", "plate2"), nut.name))
    # horn screw: M2x6 through G1 + horn into the servo spline; the head bears on G1's gear-side face
    H.bolts.append(Bolt("scr_horn", "shcs", "M2-0.4", 6, (L.x_g1[0], A[0], A[1]), (1, 0, 0),
                        ("gear_a", "servo_horn"), "servo_spline", "gear_a"))
    # thumb-disc cap: 2x M2x8 into inserts in the jaw's rear block; the cap pocket bears on the disc top
    zc0 = L.disc0_z + L.jaw_top_dz
    for k, (x, y) in enumerate(L.cap_screws):
        ins = insert("SYR-222", f"ins_cap_{k}", (x, y, zc0), (0, 0, 1), "carriage")
        H.bolts.append(Bolt(f"scr_cap_{k}", "shcs", "M2-0.4", 8, (x, y, zc0 + L.cap_gap + L.cap_t), (0, 0, -1),
                            ("disc_cap",), ins.name, "carriage"))
    tool_modules(H, buy, insert)
    for b in H.bolts:
        screw_pn(b.kind, b.thread, b.length)
    return H


def tool_modules(H, buy, insert):
    """tool rail modules (syrhead.tools): TM-110 USB microscope, TM-120 capacitive probe, at their rail index."""
    def mpt(x, p):
        q = TL.station_loc(x) * Location(p)
        return (q.position.X, q.position.Y, q.position.Z)

    H.add_part(PartDef("TM-110", "tool module: USB microscope cradle", "make", "petg_print", "fdm_print",
                       TL.microscope_module, "fixture", holes=tuple(TL.microscope_module_holes()),
                       notes="on its side (module +X up), no supports; slides on the tool rail, 1x M3x10 at an index"))
    H.add_part(PartDef("TM-120", "tool module: capacitive probe", "make", "petg_print", "fdm_print",
                       TL.probe_module, "fixture", holes=tuple(TL.probe_module_holes()),
                       notes="on its side (module +X up), no supports; electrode pinched by 1x M2x8"))
    H.add_part(PartDef("TM-121", "probe module lid (TTP223 cover)", "make", "petg_print", "fdm_print", TL.probe_lid,
                       "fixture", notes="flat, no supports"))
    buy("SYR-300A", "USB digital microscope housing", TL.mic_body, "abs_black", "generic (Jiusion / DEPSTECH / Mustool)",
        "1000x 8-LED USB digital microscope", 18.0, category="electrical",
        notes="Ø33 x ~118 body (ASSUMED: measure); UVC camera on the laptop's USB")
    buy("SYR-300B", "microscope front cap + window", TL.mic_cap, "polycarbonate_clear", "", "(part of SYR-300A)", 0.0,
        "SYR-300A", "electrical")
    buy("SYR-300C", "microscope focus wheel", TL.mic_wheel, "aluminum_6063_t5_clear_anodized", "", "(part of SYR-300A)",
        0.0, "SYR-300A", "electrical")
    buy("SYR-300D", "microscope LED ring (8x white)", TL.mic_leds, "pilot_white", "", "(part of SYR-300A)", 0.0,
        "SYR-300A", "electrical")
    buy("SYR-300E", "microscope '1000X' print", TL.mic_label, "print_white", "", "(part of SYR-300A)", 0.0, "SYR-300A",
        "labels")
    buy("SYR-300F", "microscope USB lead", TL.usb_cable, "cable_pvc_black", "", "(part of SYR-300A)", 0.0, "SYR-300A",
        "electrical")
    buy("SYR-310A", "TTP223 capacitive touch module (mini)", TL.ttp223_pcb, "pcb_green", "generic", "TTP223 15 x 11 module",
        0.4, category="electrical", finish="syr_pcb_red", notes="VCC 5 V, OUT -> GRBL probe pin (A5), $6=1")
    buy("SYR-310B", "TTP223 IC + LED", TL.ttp223_parts, "ic_epoxy_black", "", "(part of SYR-310A)", 0.0, "SYR-310A",
        "electrical")
    H.add_part(PartDef("SYR-311", f"electrode, stainless rod Ø{TL.ROD_D:g} cut to {TL.ROD_L:g}", "stock",
                       "stainless_304_brushed", "tube_cut", TL.electrode_rod, "electrical", vendor="McMaster-Carr",
                       vendor_pn=f"304 stainless rod Ø{TL.ROD_D:g}", cost=round(0.01 * TL.ROD_L, 3), unit="mm",
                       qty_per=TL.ROD_L, notes="tip squared and deburred"))
    buy("SYR-312", "electrode lead (PVC, 26 AWG)", lambda: V.wire(TL.electrode_wire_pts(), 0.8, 0.0, (1, 0, 0)),
        "cable_pvc_black", "generic", "26 AWG hook-up wire", 0.05, category="electrical", finish="syr_cable_red")
    for k, fin in ((-1.0, "syr_cable_brown"), (0.0, "syr_cable_red"), (1.0, "syr_cable_orange")):
        pn = {-1.0: "SYR-313A", 0.0: "SYR-313B", 1.0: "SYR-313C"}[k]
        buy(pn, f"probe lead {['GND', 'VCC', 'OUT'][int(k) + 1]} (to the 3018 board: GND, 5V, probe pin A5)",
            (lambda k=k: V.wire(probe_lead_pts(), 0.8, k, (1, 0, 0))), "cable_pvc_black", "generic",
            "3-way 26 AWG + 2.54 housing", 0.3 if k == 0 else 0.0, "" if k == 0 else "SYR-313B", "electrical", fin)
    H.add_part(PartDef("SYR-233", "cable tie 2.5 x 200 nylon (microscope)", "buy", "polypropylene_natural", "purchased",
                       lambda: V.cable_tie((0, 0, 0), (0, 0, 1), MIC_TIE_INNER, x_dir=(1, 0, 0)), "electrical",
                       vendor="HellermannTyton", vendor_pn="T30R", cost=0.04))

    # ---- instances ----
    mx, px = TL.MIC_X, TL.PROBE_X
    H.add("TM-110", TL.station_loc(mx), "mod_microscope")
    H.add("TM-120", TL.station_loc(px), "mod_probe")
    H.add("TM-121", TL.station_loc(px), "mod_probe_lid")
    ax, ay = TL.mic_axis()
    mic = frame((ax, ay, TL.MIC_FRONT_Z), z=(0, 0, 1), x=(1, 0, 0))
    for pn, nm in (("SYR-300A", "microscope"), ("SYR-300B", "microscope_cap"), ("SYR-300C", "microscope_wheel"),
                   ("SYR-300D", "microscope_leds"), ("SYR-300E", "microscope_label")):
        H.add(pn, mic, nm)
    H.add("SYR-300F", Location(), "microscope_usb", tags=("cable",))
    for k, (a, b) in enumerate(TL.CRADLES):
        zc = TL.MIC_FRONT_Z + (a + b) / 2
        H.add("SYR-233", frame((ax, TL.FACE_Y - MIC_TIE_ZC, zc), z=(0, 0, 1), x=(-1, 0, 0)), f"microscope_tie_{k}",
              tags=("cable",))
    tx, ty, tz = TL.probe_tip()
    H.add("SYR-311", frame((tx, ty, tz), z=(0, 0, 1), x=(1, 0, 0)), "electrode")
    ttp_o = mpt(px, (0, (TL.TTP_Y[0] + TL.TTP_Y[1]) / 2, TL.TTP_BLOCK[1] - TL.TTP_DEPTH))
    H.add("SYR-310A", frame(ttp_o, z=(0, -1, 0), x=(0, 0, 1)), "ttp223")
    H.add("SYR-310B", frame(ttp_o, z=(0, -1, 0), x=(0, 0, 1)), "ttp223_ic")
    H.add("SYR-312", Location(), "electrode_lead", tags=("cable",))
    for pn, nm in (("SYR-313A", "probe_lead_gnd"), ("SYR-313B", "probe_lead_vcc"), ("SYR-313C", "probe_lead_out")):
        H.add(pn, Location(), nm, tags=("cable",))

    # ---- bolted joints ----
    for name, x in (("microscope", mx), ("probe", px)):
        ins = insert("SYR-223", f"ins_rail_{name}", (x, TL.FACE_Y - TL.RAIL_DEPTH, TL.RAIL_Z), (0, -1, 0))
        H.bolts.append(Bolt(f"scr_index_{name}", "shcs", "M3-0.5", 10, mpt(x, (0, 0, TL.SADDLE_Z[1])), (0, 1, 0),
                            (f"mod_{name}",), ins.name))
    ymid = (TL.PINCH[0] + TL.PINCH[1]) / 2
    ins = insert("SYR-222", "ins_pinch", mpt(px, (TL.PINCH_SCREW_X, ymid, TL.PROBE_ZC - 0.4)), (0, -1, 0))
    H.bolts.append(Bolt("scr_pinch", "shcs", "M2-0.4", 8, mpt(px, (TL.PINCH_SCREW_X, ymid, TL.PROBE_ZC + 4.0)),
                        (0, 1, 0), ("mod_probe",), ins.name))
    ins = insert("SYR-222", "ins_lid", mpt(px, (0, TL.TTP_Y[0] - 4.0, TL.TTP_BLOCK[1])), (0, -1, 0))
    H.bolts.append(Bolt("scr_lid", "shcs", "M2-0.4", 6, mpt(px, (0, TL.TTP_Y[0] - 4.0, TL.TTP_BLOCK[1] + 2.05)),
                        (0, 1, 0), ("mod_probe_lid",), ins.name))


# the tie's back run lies in the arm's tunnel (local Z 11.1 .. 12.9), its front run 0.6 in front of the microscope
MIC_TIE_ZC = (12.4 + TL.MIC_ZC + TL.MIC["d"] / 2 + 0.6) / 2                    # module local Z of the loop centre
MIC_TIE_INNER = (TL.MIC["d"] + 1.0, TL.MIC_ZC + TL.MIC["d"] / 2 + 0.6 - 12.4)  # (along X, along Y)


def probe_lead_pts():
    """the probe's 3-way lead: TTP223 pads -> exit slot -> up the arm front -> to the 3018 controller (machine)."""
    px = TL.PROBE_X

    def m(p):
        q = TL.station_loc(px) * Location(p)
        return (q.position.X, q.position.Y, q.position.Z)
    zt = TL.TTP_BLOCK[1]
    y = TL.TTP_Y[1]
    return [m((0, y - 1.5, zt - TL.TTP_DEPTH + 2.0)), m((0, y + 1.5, zt - 1.0)), m((0, y + 6.0, zt + 0.8)),
            m((0, -8.0, TL.SADDLE_Z[1] + 6.0)), (px + 2.0, TL.FACE_Y - 20.0, 60.0), (8.0, -40.0, 120.0),
            (8.0, -20.0, 150.0), (10.0, 10.0, 185.0)]


# ---------------------------------------------------------------------------------------------------------------------
# servo lead route (machine frame): out of the case's lower end, back to gear plate 2's outer face, through the tie,
# up the back of the plate and off the top toward the Z carriage (y < 25 above the Z-stepper line)
# ---------------------------------------------------------------------------------------------------------------------
N0 = (0.0, 1.0, 0.0)              # ribbon reference normal at the start (the lead leaves the case pointing down)


def servo_exit():
    p, d = V.servo_cable_exit()
    q = servo_loc() * Location(p)
    return (q.position.X, q.position.Y, q.position.Z)


TIE_XC = LAY.plate2_x[1] + 2.2    # lead centre line at the tie, 2.2 off gear plate 2's outer face


def cable_path():
    x0, y0, z0 = servo_exit()
    ty, tz = P.TIE
    xc = TIE_XC
    return [(x0, y0, z0), (x0, y0, z0 - 3.0), (x0 - 3.0, y0 + 2.0, z0 - 7.5), (x0 - 9.0, y0 + 7.0, tz + 0.8),
            (xc + 0.6, ty - 9.0, tz), (xc, ty, tz), (xc, ty + 9.0, tz + 1.5), (xc, 13.0, tz + 14.0),
            (xc, 15.0, 110.0), (xc - 1.5, 16.0, 140.0), (xc - 6.0, 17.0, 165.0), (xc - 9.0, 17.5, 180.0)]


def connector_loc():
    pts = cable_path()
    a, b = Vector(*pts[-2]), Vector(*pts[-1])
    return frame(pts[-1], z=tuple((b - a).normalized()), x=(1, 0, 0))


def tie_args():
    """(centre, bundle direction, loop inner (along the plate normal, along the slot pair))."""
    ty, tz = P.TIE
    xi = LAY.plate2_x[0]
    outer = TIE_XC + 1.7
    return ((xi + outer) / 2, ty, tz), (0, 1, 0), (outer - xi, 2 * P.TIE_SLOT_DZ - 1.6)
