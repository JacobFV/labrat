#!/usr/bin/env python3
"""LabRat low-voltage electronics package generator (stdlib only).

One design description below drives:
  * pin_map.csv       hub GPIO usage for both supported boards
  * bom.csv           electronics BOM (cheap generic parts)
  * power_budget.md   5 V servo-rail budget computed from the load table
  * wiring.svg        bench wiring diagram, centred on the servo hub

Cross-checks (exits non-zero on failure):
  * every GPIO in PINS matches the #defines in ../../firmware/servo-hub/include/board.h
  * every servo channel matches the firmware defaults (ch0 continuous, the rest positional)
  * the base build uses zero resistors (the user has only a few)

Usage:  python3 gen_wiring.py
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BOARD_H = ROOT / "firmware" / "servo-hub" / "include" / "board.h"
HUB_CPP = ROOT / "firmware" / "servo-hub" / "lib" / "hub_core" / "src" / "hub.cpp"

ERRORS: list[str] = []


def fail(msg: str) -> None:
    ERRORS.append(msg)


# =====================================================================================================================
# 1. DESIGN
# =====================================================================================================================

# signal -> (board.h define, esp32s3 gpio, esp32 gpio, direction, connects to, notes)
PINS = [
    ("I2C_SDA", "PIN_SDA", 8, 21, "I/O open-drain", "PCA9685 SDA",
     "pull-up is on the PCA9685 breakout (10k to its VCC = 3V3); 400 kHz"),
    ("I2C_SCL", "PIN_SCL", 9, 22, "OUT open-drain", "PCA9685 SCL", "pull-up on the breakout"),
    ("PCA_OE", "PIN_OE", 10, 23, "OUT", "PCA9685 OE",
     "high = every servo output off (estop, boot). Optional: the breakout pulls OE low by itself"),
    ("SYR_HOME", "PIN_SYR_HOME", 4, 26, "IN pull-up", "syr_home microswitch (NO) -> GND",
     "closed = 1; ends `pulse ... until:\"syr_home\"`"),
    ("ESTOP", "PIN_ESTOP", 5, 27, "IN pull-up", "E-stop mushroom button (NC) -> GND",
     "open = engaged = every servo off. No button fitted: put a jumper wire from this pin to GND"),
    ("IN1", "PIN_IN1", 6, 25, "IN pull-up", "spare switch -> GND", "event `in1`"),
    ("IN2", "PIN_IN2", 7, 33, "IN pull-up", "spare switch -> GND", "event `in2`"),
    ("A0", "PIN_A0", 1, 34, "ADC1 in", "spare analog (0..3.1 V)", "`read` -> adc.a0 in mV"),
    ("A1", "PIN_A1", 2, 35, "ADC1 in", "spare analog (0..3.1 V)", "`read` -> adc.a1 in mV"),
]
POWER_PINS = [
    ("3V3", "3V3", "3V3", "PWR out", "PCA9685 VCC (logic)", "the breakout's I2C pull-ups go to VCC: keep VCC at 3V3"),
    ("GND", "GND", "GND", "GND", "PCA9685 GND, switches, supply -", "single common ground"),
    ("5V", "5V", "5V", "PWR", "nothing", "do NOT feed the servo supply into this pin: the board runs from USB"),
]

# Servo channels: ch, role, part, kind (firmware default), stall_a, moving_a, idle_a, where
SERVOS = [
    (0, "syringe plunger drive", "FS90R continuous", "cont", 0.80, 0.20, 0.01, "tool head (hardware/tool-head)"),
    (1, "capacitive-probe arm", "SG90 positional", "pos", 0.75, 0.25, 0.01, "tool rail, on the head"),
    (2, "phone stand pan", "MG90S positional", "pos", 0.90, 0.30, 0.01, "phone stand"),
    (3, "phone stand tilt", "MG90S positional", "pos", 0.90, 0.30, 0.01, "phone stand"),
    (4, "spare / second syringe", "FS90R or SG90", "pos", 0.80, 0.25, 0.01, "-"),
    (5, "spare", "SG90", "pos", 0.75, 0.25, 0.01, "-"),
]

# Base build resistors: none. Optional sensors and what each needs.
OPTIONAL = [
    ("extra limit / reed / door switch", "in1 or in2 to GND", 0, "internal pull-up"),
    ("10k potentiometer (manual knob)", "ends 3V3 / GND, wiper -> a0", 0, "the pot is its own divider"),
    ("TTP223 touch pad (second one, for the hub)", "VCC 3V3, OUT -> in1, bridge pad A (active-low out)", 0,
     "3.3 V supply only, so OUT never exceeds 3.3 V"),
    ("I2C module with on-board pull-ups (INA219, VL53L0X, SHT31)", "SDA/SCL/3V3/GND in parallel", 0,
     "firmware does not read these yet"),
    ("LDR light sensor", "LDR 3V3 -> a0, 10k a0 -> GND", 1, "divider"),
    ("10k NTC thermistor", "NTC 3V3 -> a1, 10k a1 -> GND", 1, "divider"),
    ("servo-rail voltage monitor", "5V -> 10k -> a1 -> 10k -> GND", 2, "halves 5 V to 2.5 V for the ADC"),
    ("any 5 V-output sensor (e.g. HC-SR04 echo)", "OUT -> 10k -> pin -> 20k -> GND", 2,
     "or pick a 3.3 V part instead and use 0"),
]
BASE_RESISTORS = 0

BOM = [
    # ref, qty, item, spec / generic choice, approx USD each, note
    ("U1", 1, "ESP32-S3-DevKitC-1", "N8R8 or N16R8; or a classic ESP32 DevKitC (WROOM-32), ~6 USD", 9.0,
     "an S3 board is already on hand"),
    ("U2", 1, "PCA9685 16-ch 12-bit servo driver breakout", "generic 'PCA9685 servo driver' (Adafruit 815 clone)",
     3.5, "has I2C pull-ups, 220 R output resistors, a V+ screw terminal and a footprint for C1"),
    ("PS1", 1, "5 V 4 A DC power supply", "wall adapter, 5.5x2.1 mm barrel (20 W)", 8.0,
     "or an XL4015 5 A buck module from the 3018's 24 V supply, set to 5.2 V"),
    ("J1", 1, "DC barrel jack to screw terminal adapter", "5.5x2.1 mm female", 0.5, "to the PCA9685 V+ terminal"),
    ("C1", 1, "1000 uF 16 V electrolytic capacitor", "radial 10x16 mm, low-ESR", 0.3,
     "across V+/GND at the PCA9685 terminal; mind the polarity"),
    ("M1", 1, "FS90R continuous-rotation 9 g servo", "Feetech FS90R", 4.0, "listed in hardware/tool-head too"),
    ("M2", 1, "SG90 9 g servo", "TowerPro SG90 or clone", 1.5, "probe arm"),
    ("M3,M4", 2, "MG90S 9 g metal-gear servo", "TowerPro MG90S or clone", 3.0, "stand pan/tilt (SG90 works)"),
    ("SW1", 1, "lever microswitch", "KW11-3Z / SS-5GL type, use C + NO", 0.3, "syr_home"),
    ("SW2", 1, "22 mm mushroom emergency-stop button, NC contact", "LAY37 / XB2-ES542 style", 3.0, "estop"),
    ("W1", 6, "servo extension lead, 3-pin JR/Futaba", "4x 50 cm + 2x 100 cm (head runs)", 0.5,
     "22-26 AWG; the longest run is the head (ch0, ch1)"),
    ("W2", 1, "Dupont jumper wires F-F 20 cm", "pack of 40", 2.0, "I2C, 3V3/GND, switches"),
    ("H1", 1, "powered USB 2.0 hub, 7 ports", "with its own 5 V supply", 18.0,
     "laptop -> 3018, 2x SO-101 adapter, servo hub, microscope"),
    ("CBL1", 1, "USB cable for the hub board", "USB-C (S3) or micro-USB (classic), data-capable", 2.0, ""),
]

# Power model (5 V servo rail). Scenarios: (name, {ch: state}) with state in stall|moving|idle|off.
SCENARIOS = [
    ("idle, everything holding", {0: "off", 1: "idle", 2: "idle", 3: "idle"}),
    ("typical step: plunger moving, stand aiming, probe arm swinging",
     {0: "moving", 1: "moving", 2: "moving", 3: "moving"}),
    ("homing: plunger driven into its stop (until syr_home), stand aiming",
     {0: "stall", 1: "idle", 2: "moving", 3: "moving"}),
    ("bad day: all four installed servos stalled", {0: "stall", 1: "stall", 2: "stall", 3: "stall"}),
    ("worst case, six servos (two spares) stalled",
     {0: "stall", 1: "stall", 2: "stall", 3: "stall", 4: "stall", 5: "stall"}),
]
SUPPLY_A = 4.0


# =====================================================================================================================
# 2. CROSS-CHECKS
# =====================================================================================================================

def check_board_h() -> None:
    try:
        text = BOARD_H.read_text()
    except FileNotFoundError:
        return fail(f"{BOARD_H} missing")
    blocks = {"esp32s3": text.split("#if defined(HUB_BOARD_ESP32S3)")[1].split("#elif")[0],
              "esp32": text.split("#elif defined(HUB_BOARD_ESP32)")[1].split("#else")[0]}
    for board, col in (("esp32s3", 2), ("esp32", 3)):
        defs = {m.group(1): int(m.group(2)) for m in re.finditer(r"#define\s+(PIN_\w+)\s+(-?\d+)", blocks[board])}
        for row in PINS:
            if defs.get(row[1]) != row[col]:
                fail(f"{board}: {row[1]} is {defs.get(row[1])} in board.h, {row[col]} here")
        extra = set(defs) - {r[1] for r in PINS}
        if extra:
            fail(f"{board}: board.h pins not documented here: {sorted(extra)}")


def check_channel_defaults() -> None:
    text = HUB_CPP.read_text() if HUB_CPP.exists() else ""
    if "if (ch == 0) return ChannelCfg{700, 2300, 1500, Kind::Cont};" not in text:
        fail("hub.cpp defaultCfg no longer makes ch0 the continuous syringe servo: update SERVOS")
    for ch, *_rest in SERVOS:
        kind = _rest[2]
        if ch != 0 and kind == "cont":
            fail(f"ch{ch}: firmware default is pos")


def check_resistors() -> None:
    if BASE_RESISTORS != 0:
        fail("the base build must not need resistors")


# =====================================================================================================================
# 3. TABLES
# =====================================================================================================================

def write_pin_map() -> None:
    with open(HERE / "pin_map.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["signal", "esp32s3_gpio", "esp32_gpio", "direction", "connects_to", "notes"])
        for sig, _d, s3, c, dirn, to, note in PINS:
            w.writerow([sig, s3, c, dirn, to, note])
        for sig, s3, c, dirn, to, note in POWER_PINS:
            w.writerow([sig, s3, c, dirn, to, note])
        for ch, role, part, kind, *_r, where in SERVOS:
            w.writerow([f"PCA9685 ch{ch}", "-", "-", "PWM 50 Hz", f"{part} ({role})",
                        f"firmware default kind={kind}; {where}"])


def write_bom() -> None:
    with open(HERE / "bom.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ref", "qty", "item", "generic choice", "approx_usd_each", "note"])
        for row in BOM:
            w.writerow(row)
        w.writerow(["R*", 0, "resistors", "none in the base build (see README: optional sensors)", 0, ""])


def amps(ch: int, state: str) -> float:
    s = next(x for x in SERVOS if x[0] == ch)
    return {"stall": s[4], "moving": s[5], "idle": s[6], "off": 0.0}[state]


def write_power_budget() -> None:
    lines = [
        "# 5 V servo rail: power budget",
        "",
        "Generated by `gen_wiring.py` from its load table; edit the table there, not this file.",
        "",
        "The servos run from their own 5 V supply into the PCA9685 breakout's V+ screw terminal. The ESP32 and the",
        "PCA9685 logic (VCC, ~10 mA) run from USB 5 V through the DevKit's 3.3 V regulator. The two meet only at GND.",
        "",
        "## Per-servo currents at 5 V (conservative, datasheet and bench numbers rounded up)",
        "",
        "| ch | role | servo | idle (A) | moving (A) | stall (A) |",
        "|---|---|---|---|---|---|",
    ]
    for ch, role, part, kind, st, mv, idle, where in SERVOS:
        lines.append(f"| {ch} | {role} | {part} | {idle:.2f} | {mv:.2f} | {st:.2f} |")
    lines += ["", f"## Scenarios (supply: {SUPPLY_A:.0f} A)", "", "| scenario | rail current (A) | margin |",
              "|---|---|---|"]
    for name, states in SCENARIOS:
        a = sum(amps(ch, st) for ch, st in states.items())
        margin = "ok" if a <= SUPPLY_A * 0.8 else ("tight" if a <= SUPPLY_A else "exceeds supply: brief only")
        lines.append(f"| {name} | {a:.2f} | {margin} |")
    lines += [
        "",
        "## Choices",
        "",
        f"- **Supply: 5 V, {SUPPLY_A:.0f} A** (20 W). Normal work stays near 1-1.5 A; four installed servos all",
        "  stalled is still inside the rating. Six stalled servos exceed it, but only for the moment before the",
        "  firmware's timed pulses or the stall clears. A 3 A brick works for the four installed servos alone.",
        "  Cheapest alternative: an XL4015 5 A buck module running off the 3018's 24 V supply, set to 5.2 V.",
        "- **Bulk capacitor: 1000 uF / 16 V electrolytic across V+ and GND at the PCA9685 terminal** (the breakout",
        "  has a footprint for it). 9 g servos draw 0.5-0.9 A spikes at every start; without it the rail sags and",
        "  servos twitch. Rule of thumb ~150-250 uF per servo, so 1000 uF covers 4-6.",
        "- **Wiring:** supply to terminal with 20-22 AWG. Servo leads are 26-28 AWG; for the 1 m runs to the head,",
        "  1 m of 26 AWG round trip at 0.8 A stall drops ~0.2 V, which is acceptable.",
        "- **Common ground:** supply -, PCA9685 GND and ESP32 GND are one net (the breakout's header carries it).",
        "  The laptop USB ground joins it through the ESP32. Never connect the servo supply + to the ESP32's 5V pin.",
        "- **ESP32 power:** USB only (laptop, powered hub, phone OTG, or a USB charger when the phone drives it",
        "  over BLE). Peak ~0.35 A with BLE active.",
        "",
    ]
    (HERE / "power_budget.md").write_text("\n".join(lines))


# =====================================================================================================================
# 4. WIRING DIAGRAM (SVG)
# =====================================================================================================================

W, H = 1500, 1000
COL = {
    "5v": "#d62728", "3v3": "#ff7f0e", "gnd": "#222222", "sda": "#1f77b4", "scl": "#9467bd", "sig": "#2ca02c",
    "pwm": "#8c564b", "usb": "#7f7f7f", "ble": "#17becf", "probe": "#e377c2",
}
svg: list[str] = []
LABELS: list[tuple[float, float, float, float]] = []


def text(x, y, s, size=12, anchor="start", weight="normal", color="#111", italic=False):
    style = ' font-style="italic"' if italic else ""
    svg.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" '
               f'fill="{color}"{style}>{escape(s)}</text>')
    wpx = len(s) * size * (0.64 if weight == "bold" else 0.58)
    x0 = x - (wpx if anchor == "end" else wpx / 2 if anchor == "middle" else 0)
    LABELS.append((x0, y - size * 0.8, wpx, size))


def box(x, y, w, h, title, sub="", fill="#f6f6f6"):
    svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="#333" stroke-width="1.5"/>')
    text(x + w / 2, y + 20, title, 14, "middle", "bold")
    if sub:
        text(x + w / 2, y + 36, sub, 11, "middle", color="#555")


def pin(x, y, name, side):
    svg.append(f'<circle cx="{x}" cy="{y}" r="3.5" fill="#fff" stroke="#333"/>')
    if side == "right":
        text(x - 8, y + 4, name, 11, "end")
    elif side == "left":
        text(x + 8, y + 4, name, 11, "start")
    elif side == "top":
        text(x, y + 16, name, 11, "middle")
    else:
        text(x, y - 8, name, 11, "middle")


def wire(points, net, dashed=False, width=2.2):
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    dash = ' stroke-dasharray="7,5"' if dashed else ""
    svg.append(f'<polyline points="{d}" fill="none" stroke="{COL[net]}" stroke-width="{width}"{dash} '
               f'stroke-linejoin="round"/>')


def dot(x, y, net):
    svg.append(f'<circle cx="{x}" cy="{y}" r="4" fill="{COL[net]}"/>')


def note(x, y, lines, size=11, color="#333"):
    for i, s in enumerate(lines):
        text(x, y + i * (size + 4), s, size, color=color)


def draw() -> str:
    svg.clear()
    LABELS.clear()
    svg.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')
    text(30, 36, "LabRat bench: low-voltage wiring (servo hub = ESP32 + PCA9685)", 20, weight="bold")
    text(30, 58, "No resistors in the base build: inputs use the ESP32's internal pull-ups, I2C pull-ups are on the "
                 "PCA9685 breakout. Pin numbers: ESP32-S3-DevKitC-1 (classic ESP32 DevKitC in brackets).", 12,
         color="#444")

    # ---------------- laptop + USB hub
    box(30, 90, 250, 90, "Laptop", "bridge/ (python -m labrat_bridge)")
    box(30, 220, 250, 270, "Powered USB 2.0 hub", "own 5 V supply")
    usb_ports = [("3018 GRBL (CH340) 115200", 280), ("SO-101 so_a adapter 1 Mbps", 320),
                 ("SO-101 so_b adapter 1 Mbps", 360), ("servo hub (UART port) 115200", 400),
                 ("USB microscope (UVC)", 440)]
    wire([(155, 180), (155, 220)], "usb", width=4)
    for label, y in usb_ports:
        pin(280, y, label, "right")

    # ---------------- 3018 board + TTP223
    box(30, 560, 300, 170, "3018 controller (GRBL 1.1)", "Arduino-Nano class, 5 V logic")
    pin(330, 610, "probe A5 ($6=1)", "right")
    pin(330, 645, "5 V", "right")
    pin(330, 680, "GND", "right")
    wire([(280, 280), (300, 280), (300, 540), (180, 540), (180, 560)], "usb", width=4)
    box(420, 745, 220, 115, "TTP223 capacitive probe", "tool rail, on the head", fill="#fbeef7")
    pin(420, 800, "OUT (5 V high)", "left")
    pin(420, 822, "VCC", "left")
    pin(420, 844, "GND", "left")
    wire([(330, 610), (370, 610), (370, 800), (420, 800)], "probe")
    wire([(330, 645), (385, 645), (385, 822), (420, 822)], "5v")
    wire([(330, 680), (400, 680), (400, 844), (420, 844)], "gnd")
    note(40, 752, ["G38.3 needs the probe inside GRBL's step loop:", "it latches Z at the exact step.",
                   "TTP223 is powered from the 3018's 5 V, so its", "OUT is a 5 V signal: never wire it to an",
                   "ESP32 pin (3.3 V only, not 5 V tolerant)."], 11, "#7a2a5f")

    # ---------------- SO-101 adapters
    box(30, 860, 300, 110, "2x SO-101 bus servo adapter", "Waveshare / Seeed, one per arm")
    note(45, 915, ["own 5 V (7.4 V STS3215) or 12 V supply each,", "see hardware/so101/README.md;",
                   "USB to the hub above"], 11)

    # ---------------- ESP32 DevKit
    ex, ey, ew, eh = 560, 200, 230, 430
    box(ex, ey, ew, eh, "ESP32-S3-DevKitC-1", "fw: firmware/servo-hub", fill="#eef5fb")
    wire([(280, 400), (520, 400), (520, 260), (ex, 260)], "usb", width=4)
    pin(ex, 260, "USB (UART port)", "left")
    pin(ex, 300, "USB (native port)", "left")
    text(ex + 12, 325, "either port: same protocol", 10, color="#666")
    epins = [("3V3", 270, "3v3"), ("GND", 300, "gnd"), ("GPIO8 (21) SDA", 340, "sda"), ("GPIO9 (22) SCL", 370, "scl"),
             ("GPIO10 (23) OE", 400, "sig"), ("GPIO4 (26) syr_home", 450, "sig"), ("GPIO5 (27) estop", 480, "sig"),
             ("GPIO6 (25) in1", 510, "sig"), ("GPIO7 (33) in2", 540, "sig"), ("GPIO1 (34) a0", 570, "sig"),
             ("GPIO2 (35) a1", 600, "sig")]
    for name, y, _n in epins:
        pin(ex + ew, y, name, "right")
    # phone over BLE / OTG
    box(560, 90, 230, 80, "Android phone", "apps/phone, Web Bluetooth", fill="#eafafa")
    wire([(675, 170), (675, 200)], "ble", dashed=True)
    text(685, 192, "BLE Nordic UART 'labrat-hub'", 11, color=COL["ble"])
    wire([(560, 140), (500, 140), (500, 300), (ex, 300)], "usb", dashed=True, width=3)
    text(440, 132, "or USB-OTG", 11, "start", color="#666")

    # ---------------- PCA9685
    px, py, pw, ph = 950, 200, 230, 380
    box(px, py, pw, ph, "PCA9685 breakout (0x40)", "16-ch 12-bit PWM, 50 Hz", fill="#fbf3ec")
    ppins = [("VCC", 270, "3v3"), ("GND", 300, "gnd"), ("SDA", 340, "sda"), ("SCL", 370, "scl"), ("OE", 400, "sig")]
    for name, y, _n in ppins:
        pin(px, y, name, "left")
    for (name, y, net), (_p, py2, _n2) in zip(epins[:5], ppins):
        wire([(ex + ew, y), (px, py2)], net)
    # V+ terminal at the top
    for xx, lab in ((px + 60, "V+"), (px + 150, "GND")):
        svg.append(f'<circle cx="{xx}" cy="{py}" r="3.5" fill="#fff" stroke="#333"/>')
        text(xx + 7, py - 6, lab, 11)
    # supply
    box(950, 76, 230, 70, "5 V 4 A supply", "servo rail only", fill="#fdecec")
    pin(px + 60, 146, "+5 V", "bottom")
    pin(px + 150, 146, "-", "bottom")
    wire([(px + 60, 146), (px + 60, py)], "5v", width=3.5)
    wire([(px + 150, 146), (px + 150, py)], "gnd", width=3.5)
    # bulk cap between the two rails
    cx, cy = px + 105, 172
    wire([(px + 60, cy), (cx - 8, cy)], "5v")
    wire([(cx + 8, cy), (px + 150, cy)], "gnd")
    svg.append(f'<line x1="{cx - 6}" y1="{cy - 12}" x2="{cx - 6}" y2="{cy + 12}" stroke="#333" stroke-width="3"/>')
    svg.append(f'<path d="M {cx + 8} {cy - 12} Q {cx + 2} {cy} {cx + 8} {cy + 12}" fill="none" stroke="#333" '
               f'stroke-width="3"/>')
    dot(px + 60, cy, "5v")
    dot(px + 150, cy, "gnd")
    text(px + 160, cy - 4, "C1 1000 uF 16 V", 10)
    text(px + 160, cy + 10, "electrolytic, + to V+", 10)

    # ---------------- servos
    sx = 1290
    servo_rows = [(0, "FS90R syringe (cont)", 300), (1, "SG90 probe arm", 360), (2, "MG90S stand pan", 420),
                  (3, "MG90S stand tilt", 480), (4, "ch4-15 spare / syringe 2", 540)]
    for ch, label, y in servo_rows:
        pin(px + pw, y, f"ch{ch}" if ch < 4 else "ch4..15", "right")
        wire([(px + pw, y), (sx, y)], "pwm", width=3)
        svg.append(f'<rect x="{sx}" y="{y - 16}" width="180" height="32" rx="5" fill="#fff" stroke="#8c564b"/>')
        text(sx + 90, y + 4, label, 11, "middle")
    note(sx - 50, 590, ["3-pin servo plugs: brown/black GND,", "red V+ (5 V), orange/yellow signal.",
                        "Head runs (ch0, ch1): 1 m extensions."], 11)

    # ---------------- switches + analog
    swx = 870
    def to_gnd_switch(y, label, nc):
        wire([(ex + ew, y), (swx, y)], "sig")
        # contact symbol
        svg.append(f'<circle cx="{swx}" cy="{y}" r="3" fill="#2ca02c"/><circle cx="{swx + 30}" cy="{y}" r="3" '
                   f'fill="#222"/>')
        if nc:
            svg.append(f'<line x1="{swx}" y1="{y + 4}" x2="{swx + 30}" y2="{y + 4}" stroke="#333" stroke-width="2"/>')
        else:
            svg.append(f'<line x1="{swx}" y1="{y}" x2="{swx + 26}" y2="{y - 10}" stroke="#333" stroke-width="2"/>')
        wire([(swx + 30, y), (swx + 50, y), (swx + 50, 700)], "gnd")
        text(swx + 58, y + 4, label, 11)

    wire([(swx + 50, 700), (swx + 50, 720), (ex + 100, 720), (ex + 100, eh + ey)], "gnd")
    text(ex + 108, 712, "GND", 11, color=COL["gnd"])
    to_gnd_switch(450, "", False)
    to_gnd_switch(480, "", True)
    note(swx + 58, 655, ["syr_home: lever microswitch, C + NO (closed at the bottom stop)",
                         "estop: mushroom button, NC contact (open = stop)",
                         "in1 / in2: any switch to GND",
                         "a0 / a1: 0-3.1 V analog (see README for optional sensors)"], 11)
    for name, y, _n in epins[7:]:
        wire([(ex + ew, y), (ex + ew + 40, y)], "sig")
        svg.append(f'<line x1="{ex + ew + 40}" y1="{y - 5}" x2="{ex + ew + 40}" y2="{y + 5}" stroke="#2ca02c" '
                   f'stroke-width="2"/>')

    # ---------------- legend
    lx, ly = 1100, 820
    svg.append(f'<rect x="{lx - 10}" y="{ly - 20}" width="385" height="150" rx="6" fill="#fafafa" stroke="#bbb"/>')
    for i, (k, lab) in enumerate([("5v", "servo 5 V (V+)"), ("3v3", "3.3 V logic"), ("gnd", "GND (one common net)"),
                                  ("sda", "I2C SDA"), ("scl", "I2C SCL"), ("sig", "GPIO signal"),
                                  ("pwm", "servo PWM lead"), ("usb", "USB (dashed: optional)"),
                                  ("ble", "BLE (wireless)")]):
        yy = ly + (i % 5) * 26
        xx = lx + (i // 5) * 175
        wire([(xx, yy), (xx + 30, yy)], k, dashed=k == "ble")
        text(xx + 38, yy + 4, lab, 11)

    body = "\n".join(svg)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'font-family="DejaVu Sans, Arial, Helvetica, sans-serif">\n{body}\n</svg>\n')


def check_svg(doc: str) -> None:
    import xml.dom.minidom

    try:
        xml.dom.minidom.parseString(doc)
    except Exception as e:
        fail(f"wiring.svg is not well-formed XML: {e}")
    for x, y, w, h in LABELS:
        if x < 0 or y < 0 or x + w > W + 5 or y + h > H:
            fail(f"label at ({x:.0f},{y:.0f}) runs off the sheet")
    for i, a in enumerate(LABELS):
        for b in LABELS[i + 1:]:
            if a[0] < b[0] + b[2] - 2 and b[0] < a[0] + a[2] - 2 and a[1] < b[1] + b[3] - 2 and b[1] < a[1] + a[3] - 2:
                fail(f"labels overlap near ({a[0]:.0f},{a[1]:.0f}) and ({b[0]:.0f},{b[1]:.0f})")


# =====================================================================================================================

def main() -> int:
    check_board_h()
    check_channel_defaults()
    check_resistors()
    write_pin_map()
    write_bom()
    write_power_budget()
    doc = draw()
    check_svg(doc)
    (HERE / "wiring.svg").write_text(doc)
    if ERRORS:
        for e in ERRORS:
            print("FAIL:", e, file=sys.stderr)
        return 1
    print("ok: pin_map.csv bom.csv power_budget.md wiring.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
