#!/usr/bin/env python3
"""Tappet electronics design package generator.

One in-code design description (DESIGN below) drives everything:

  * drawings/*.svg (+ .png when rsvg-convert / ImageMagick / cairosvg is available)
  * netlist.csv      (net, ref, pin)
  * pin_map.csv      (ESP32-S3 DevKitC-1 pin usage)
  * bom_electronics.csv
  * power_budget.md  (numbers computed from the load model in this file)

Cross-checks (the script exits non-zero on any failure):
  * every GPIO assignment matches ../../config/robot.json electronics.pins
  * sense-divider resistor values match electronics.dividers
  * servo channel -> joint mapping matches config joints.*.ch
  * every component in DESIGN is drawn on some sheet, and every drawn ref exists in DESIGN
  * no two labels overlap and no label is crossed by a wire on any drawing

Usage:  python3 gen_drawings.py            (stdlib only)
"""
from __future__ import annotations

import csv
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONFIG_PATH = ROOT / "config" / "robot.json"
DRAW = HERE / "drawings"

PROJECT = "TAPPET desktop robot"
REV = "0.1"
DATE = "2026-10-11"

ERRORS: list[str] = []


def fail(msg: str) -> None:
    ERRORS.append(msg)


# =============================================================================
# 1. DESIGN DESCRIPTION (single source of truth for drawings + netlists)
# =============================================================================

# GPIO assignment as designed. Cross-checked against config/robot.json.
GPIO = {
    "I2C_SDA": 8,
    "I2C_SCL": 9,
    "PCA_OE": 10,
    "USB_DN": 19,
    "USB_DP": 20,
    "PHONE_VBUS_EN": 7,
    "PHONE_VBUS_FLT": 15,
    "VSERVO_SENSE": 4,
    "VIN_SENSE": 5,
    "STATUS_LED": 48,
    "USER_BUTTON": 0,
}

GPIO_INFO = {  # signal -> (direction, function, notes)
    "I2C_SDA": ("I/O (OD)", "I2C SDA to PCA9685 (0x40)", "4.7-10k pull-ups on PCA9685 breakout; 400 kHz"),
    "I2C_SCL": ("OUT (OD)", "I2C SCL to PCA9685", "pull-up on breakout"),
    "PCA_OE": ("OUT", "PCA9685 /OE (low = servos energised)", "R11 2.2k pull-up -> servos OFF while ESP32 boots/resets"),
    "USB_DN": ("I/O", "USB OTG D- (host) to HEAD port", "also wired to DevKit 'USB' connector - leave that port unplugged"),
    "USB_DP": ("I/O", "USB OTG D+ (host) to HEAD port", "ESD: U5 USBLC6-2SC6"),
    "PHONE_VBUS_EN": ("OUT", "U4 TPS2553 EN (high = phone VBUS on)", "R5 100k pull-down -> phone power off at boot"),
    "PHONE_VBUS_FLT": ("IN", "U4 TPS2553 /FAULT (low = over-current/thermal)", "R6 10k pull-up to 3V3, open-drain"),
    "VSERVO_SENSE": ("ADC1_CH3", "Servo rail monitor, 10k/10k -> 0.5x", "C10 100nF; brownout fault < 4.3 V"),
    "VIN_SENSE": ("ADC1_CH4", "PD input monitor, 100k/22k -> 0.18x", "C11 100nF; <7.0 V = PD not negotiated"),
    "STATUS_LED": ("OUT", "On-board WS2812 RGB LED", "DevKitC-1 v1.0 = GPIO48; v1.1 boards moved it to GPIO38"),
    "USER_BUTTON": ("IN", "BOOT button (+ optional panel SW1)", "strapping pin: do not hold at power-up"),
}

# ESP32-S3-DevKitC-1 headers (pin 1 at the antenna end)
DEVKIT_J1 = ["3V3", "3V3", "RST", "GPIO4", "GPIO5", "GPIO6", "GPIO7", "GPIO15", "GPIO16", "GPIO17",
             "GPIO18", "GPIO8", "GPIO3", "GPIO46", "GPIO9", "GPIO10", "GPIO11", "GPIO12", "GPIO13",
             "GPIO14", "5V", "GND"]
DEVKIT_J3 = ["GND", "GPIO43", "GPIO44", "GPIO1", "GPIO2", "GPIO42", "GPIO41", "GPIO40", "GPIO39",
             "GPIO38", "GPIO37", "GPIO36", "GPIO35", "GPIO0", "GPIO45", "GPIO48", "GPIO47", "GPIO21",
             "GPIO20", "GPIO19", "GND", "GND"]
DEVKIT_ALT = {"GPIO43": "U0TXD", "GPIO44": "U0RXD", "GPIO0": "BOOT", "GPIO3": "strap", "GPIO45": "strap",
              "GPIO46": "strap", "GPIO19": "USB D-", "GPIO20": "USB D+", "GPIO35": "PSRAM*",
              "GPIO36": "PSRAM*", "GPIO37": "PSRAM*", "RST": "EN"}


def devkit_net(name: str) -> str | None:
    if name == "3V3":
        return "+3V3"
    if name == "5V":
        return "5V_DEVKIT"
    if name == "GND":
        return "GND"
    if name.startswith("GPIO"):
        n = int(name[4:])
        for sig, g in GPIO.items():
            if g == n:
                return sig
    return None


# Servos: ref, channel, joint, type, location
SERVOS = [
    ("M1", 0, "L_YAW", "SG90", "base, under L turret (20T->30T, gear 1.5)"),
    ("M2", 1, "L_SHOULDER", "MG90S", "on L turret (rotates with yaw)"),
    ("M3", 2, "L_ELBOW", "MG90S", "on L turret, drives parallel link"),
    ("M4", 3, "R_YAW", "SG90", "base, under R turret (20T->30T, gear 1.5)"),
    ("M5", 4, "R_SHOULDER", "MG90S", "on R turret (mirrored, dir -1)"),
    ("M6", 5, "R_ELBOW", "MG90S", "on R turret (mirrored, dir -1)"),
    ("M7", 6, "NECK_PAN", "MG90S", "top of column"),
    ("M8", 7, "NECK_TILT", "MG90S", "neck yoke, carries the phone"),
]

COMPONENTS: dict[str, dict] = {}


def comp(ref, part, value, sheet, pins, ohms=None, note=""):
    COMPONENTS[ref] = dict(ref=ref, part=part, value=value, sheet=sheet, pins=pins, ohms=ohms, note=note)


# ---- Sheet 1: power -----------------------------------------------------------
comp("J1", "USB-C receptacle (16P, sink)", "PWR IN", 1,
     {"VBUS": "VBUS_IN", "CC1": "CC1_IN", "CC2": "CC2_IN", "D+": "DP_IN", "D-": "DN_IN",
      "GND": "GND", "SHIELD": "GND"})
comp("U1", "CH224K USB-PD sink controller", "CH224K", 1,
     {"VDD": "CH224_VDD", "CC1": "CC1_IN", "CC2": "CC2_IN", "DP": "DP_IN", "DM": "DN_IN",
      "CFG1": "CH224_CFG1", "CFG2": "NC", "CFG3": "NC", "PG": "PD_PG", "GND": "GND"})
comp("R1", "Resistor 0603", "1k", 1, {"1": "VBUS_IN", "2": "CH224_VDD"}, 1000)
comp("C1", "Capacitor 0603 X7R", "1uF 25V", 1, {"1": "CH224_VDD", "2": "GND"})
comp("R2", "Resistor 0603 1%", "6.8k", 1, {"1": "CH224_CFG1", "2": "GND"}, 6800,
     "CFG1 = 6.8k -> request 9 V (CH224K single-resistor mode)")
comp("R3", "Resistor 0603", "4.7k", 1, {"1": "VBUS_IN", "2": "PG_LED"}, 4700)
comp("D1", "LED 0603 green", "PD OK", 1, {"A": "PG_LED", "K": "PD_PG"})
comp("F1", "PTC resettable fuse 1812", "3A hold", 1, {"1": "VBUS_IN", "2": "VIN_9V"})
comp("D2", "TVS diode SMB", "SMBJ12A", 1, {"K": "VIN_9V", "A": "GND"})
comp("C2", "Al-electrolytic", "470uF 25V", 1, {"+": "VIN_9V", "-": "GND"}, note="star ground point")
comp("C3", "Capacitor 1206 X7R", "10uF 25V", 1, {"1": "VIN_9V", "2": "GND"})
comp("U2", "Buck module 5A CC/CV", "BUCK A", 1,
     {"IN+": "VIN_9V", "IN-": "GND", "OUT+": "V_SERVO", "OUT-": "GND"})
comp("C4", "Al-electrolytic low-ESR", "1000uF 10V", 1, {"+": "V_SERVO", "-": "GND"})
comp("C5", "Capacitor 0805 X7R", "100nF", 1, {"1": "V_SERVO", "2": "GND"})
comp("U3", "Buck module 5V fixed", "BUCK B", 1,
     {"IN+": "VIN_9V", "IN-": "GND", "OUT+": "5V_LOGIC", "OUT-": "GND"})
comp("C6", "Al-electrolytic", "100uF 10V", 1, {"+": "5V_LOGIC", "-": "GND"})
comp("D3", "Schottky 3A 40V SMA", "SS34", 1, {"A": "5V_LOGIC", "K": "5V_DEVKIT"})
comp("U4", "USB power switch, adj. limit", "TPS2553DBV", 1,
     {"1 IN": "5V_LOGIC", "2 GND": "GND", "3 EN": "PHONE_VBUS_EN", "4 /FLT": "PHONE_VBUS_FLT",
      "5 ILIM": "U4_ILIM", "6 OUT": "VBUS_HEAD"})
comp("C9", "Capacitor 0603 X7R", "1uF", 1, {"1": "5V_LOGIC", "2": "GND"})
comp("R4", "Resistor 0603 1%", "15k", 1, {"1": "U4_ILIM", "2": "GND"}, 15000, "I_limit ~1.7 A typ")
comp("R5", "Resistor 0603", "100k", 1, {"1": "PHONE_VBUS_EN", "2": "GND"}, 100000)
comp("R6", "Resistor 0603", "10k", 1, {"1": "+3V3", "2": "PHONE_VBUS_FLT"}, 10000)
comp("C7", "Al-polymer / electrolytic", "150uF 10V", 1, {"+": "VBUS_HEAD", "-": "GND"},
     note=">=120uF per USB 2.0 downstream-port rule")
comp("C8", "Capacitor 0603 X7R", "100nF", 1, {"1": "VBUS_HEAD", "2": "GND"})
for tp, net in [("TP1", "VIN_9V"), ("TP2", "V_SERVO"), ("TP3", "5V_LOGIC"), ("TP4", "VBUS_HEAD"),
                ("TP5", "5V_DEVKIT"), ("TP6", "+3V3"), ("TP7", "VSERVO_SENSE"), ("TP8", "VIN_SENSE"),
                ("TP9", "GND")]:
    comp(tp, "Test point (turret/pin)", net, 1 if tp in ("TP1", "TP2", "TP3", "TP4") else 2, {"1": net})

# ---- Sheet 2: logic -------------------------------------------------------------
_a1 = {}
for i, n in enumerate(DEVKIT_J1):
    _a1[f"J1-{i + 1}"] = devkit_net(n) or "NC"
for i, n in enumerate(DEVKIT_J3):
    _a1[f"J3-{i + 1}"] = devkit_net(n) or "NC"
comp("A1", "ESP32-S3-DevKitC-1 (N8R2 / N16R8)", "ESP32-S3", 2, _a1)
_a2 = {"VCC": "+3V3", "GND": "GND", "SDA": "I2C_SDA", "SCL": "I2C_SCL", "OE": "PCA_OE",
       "V+ (term)": "V_SERVO", "GND (term)": "GND"}
for ch in range(16):
    _a2[f"PWM{ch}"] = f"PWM{ch}" if ch < 8 else "NC"
comp("A2", "PCA9685 16-ch PWM breakout (addr 0x40)", "PCA9685", 2, _a2)
comp("R7", "Resistor 0603 1%", "10k", 2, {"1": "V_SERVO", "2": "VSERVO_SENSE"}, 10000)
comp("R8", "Resistor 0603 1%", "10k", 2, {"1": "VSERVO_SENSE", "2": "GND"}, 10000)
comp("C10", "Capacitor 0603", "100nF", 2, {"1": "VSERVO_SENSE", "2": "GND"})
comp("R9", "Resistor 0603 1%", "100k", 2, {"1": "VIN_9V", "2": "VIN_SENSE"}, 100000)
comp("R10", "Resistor 0603 1%", "22k", 2, {"1": "VIN_SENSE", "2": "GND"}, 22000)
comp("C11", "Capacitor 0603", "100nF", 2, {"1": "VIN_SENSE", "2": "GND"})
comp("R11", "Resistor 0603", "2.2k", 2, {"1": "+3V3", "2": "PCA_OE"}, 2200,
     "stiff pull-up: overrides the 10k OE pull-down fitted on Adafruit-style breakouts")
comp("J2", "USB-C receptacle (16P, source/DFP)", "HEAD", 2,
     {"VBUS": "VBUS_HEAD", "CC1": "CC1_HEAD", "CC2": "CC2_HEAD", "D+": "USB_DP", "D-": "USB_DN",
      "GND": "GND", "SHIELD": "GND"})
comp("R12", "Resistor 0603 1%", "22k", 2, {"1": "VBUS_HEAD", "2": "CC1_HEAD"}, 22000, "Rp: advertise 1.5 A")
comp("R13", "Resistor 0603 1%", "22k", 2, {"1": "VBUS_HEAD", "2": "CC2_HEAD"}, 22000, "Rp: advertise 1.5 A")
comp("U5", "USB ESD array SOT-23-6", "USBLC6-2SC6", 2,
     {"1 I/O1": "USB_DP", "6 I/O1": "USB_DP", "3 I/O2": "USB_DN", "4 I/O2": "USB_DN", "2 GND": "GND",
      "5 VBUS": "VBUS_HEAD"})
comp("SW1", "Tactile switch 6x6 (panel)", "USER", 2, {"1": "USER_BUTTON", "2": "GND"},
     note="optional, parallel to on-board BOOT")

# ---- Sheet 3: servos --------------------------------------------------------------
for ref, ch, joint, typ, loc in SERVOS:
    comp(ref, f"Servo {typ}", joint, 3, {"SIG": f"PWM{ch}", "V+": "V_SERVO", "GND": "GND"},
         note=f"PCA9685 ch{ch}; {loc}")
comp("E1", "Stylus tip, conductive (L)", "TIP L", 3, {"TIP": "GND"}, note="via 30AWG flex lead")
comp("E2", "Stylus tip, conductive (R)", "TIP R", 3, {"TIP": "GND"}, note="via 30AWG flex lead")


# =============================================================================
# 2. CROSS-CHECKS AGAINST config/robot.json
# =============================================================================

def cross_check(cfg: dict) -> None:
    pins = cfg["electronics"]["pins"]
    for k, v in pins.items():
        if k not in GPIO:
            fail(f"config pin {k} not assigned in design")
        elif GPIO[k] != v:
            fail(f"pin mismatch {k}: design GPIO{GPIO[k]} vs config GPIO{v}")
    for k in GPIO:
        if k not in pins:
            fail(f"design signal {k} missing from config electronics.pins")
    # every signal must actually land on a DevKit header pin
    header_nets = set(COMPONENTS["A1"]["pins"].values())
    for k in GPIO:
        if k not in header_nets:
            fail(f"signal {k} (GPIO{GPIO[k]}) not found on DevKit headers")
    dv = cfg["electronics"]["dividers"]
    chk = {"VSERVO_SENSE": ("R7", "R8"), "VIN_SENSE": ("R9", "R10")}
    for k, (rt, rb) in chk.items():
        if COMPONENTS[rt]["ohms"] != dv[k]["r_top"] or COMPONENTS[rb]["ohms"] != dv[k]["r_bot"]:
            fail(f"divider {k} mismatch: design {COMPONENTS[rt]['ohms']}/{COMPONENTS[rb]['ohms']} "
                 f"vs config {dv[k]['r_top']}/{dv[k]['r_bot']}")
        # check the divider is wired to the right net
        if COMPONENTS[rt]["pins"]["2"] != k or COMPONENTS[rb]["pins"]["1"] != k:
            fail(f"divider {k} not wired to its sense net")
    for ref, ch, joint, _t, _l in SERVOS:
        j = cfg["joints"].get(joint)
        if j is None:
            fail(f"servo joint {joint} missing in config")
        elif j["ch"] != ch:
            fail(f"servo {joint}: design ch{ch} vs config ch{j['ch']}")
    if len(cfg["joints"]) != len(SERVOS):
        fail("servo count differs from config joints")
    if "0x40" not in cfg["electronics"]["pwm"]:
        fail("PCA9685 address in config is not 0x40")


# =============================================================================
# 3. SVG DRAWING LIBRARY
# =============================================================================
FONT = "Helvetica, Arial, sans-serif"
INK = "#111"
WIRE = "#1a1a1a"
BLUE = "#1f4e9c"
GREY = "#666"
RED = "#c62828"
LIGHT = "#f4f6fa"
DRAWN_REFS: dict[str, str] = {}


def text_w(s: str, size: float, bold=False) -> float:
    w = 0.0
    for ch in s:
        if ch in "il.,:;|!'I1() ":
            w += 0.32
        elif ch in "mwMW@%":
            w += 0.86
        elif ch.isupper() or ch.isdigit():
            w += 0.64
        else:
            w += 0.54
    return w * size * (1.07 if bold else 1.0) * 1.05


class Sheet:
    def __init__(self, name: str, title: str, sheet_no: str, w=1800, h=1150, frame=True):
        self.name, self.title, self.no, self.w, self.h = name, title, sheet_no, w, h
        self.el: list[str] = []
        self.texts: list[tuple] = []   # (x0,y0,x1,y1,s)
        self.segs: list[tuple] = []    # (x1,y1,x2,y2)
        self.refs: list[str] = []
        self.frame = frame
        self.el.append(f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>')
        if frame:
            self.el.append(f'<rect x="12" y="12" width="{w - 24}" height="{h - 24}" fill="none" '
                           f'stroke="{INK}" stroke-width="2"/>')

    # ---------- primitives
    def line(self, x1, y1, x2, y2, w=1.6, color=WIRE, dash=None, track=True, cap="round"):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.el.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" '
                       f'stroke-width="{w}" stroke-linecap="{cap}"{d}/>')
        if track:
            self.segs.append((x1, y1, x2, y2))

    def wire(self, *pts, w=1.6, color=WIRE, dash=None, track=True):
        for (a, b) in zip(pts, pts[1:]):
            self.line(a[0], a[1], b[0], b[1], w, color, dash, track)

    def path(self, d, w=1.6, color=INK, fill="none", dash=None):
        dd = f' stroke-dasharray="{dash}"' if dash else ""
        self.el.append(f'<path d="{d}" stroke="{color}" stroke-width="{w}" fill="{fill}" '
                       f'stroke-linejoin="round" stroke-linecap="round"{dd}/>')

    def rect(self, x, y, w, h, stroke=INK, fill="none", sw=1.8, rx=0, dash=None, track=True):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.el.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
                       f'stroke="{stroke}" fill="{fill}" stroke-width="{sw}"{d}/>')
        if track:
            self.segs += [(x, y, x + w, y), (x + w, y, x + w, y + h), (x, y + h, x + w, y + h), (x, y, x, y + h)]

    def circle(self, x, y, r, stroke=INK, fill="none", sw=1.6):
        self.el.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" stroke="{stroke}" fill="{fill}" '
                       f'stroke-width="{sw}"/>')

    def text(self, x, y, s, size=13, anchor="start", bold=False, color=INK, italic=False, track=True,
             rot=0, family=None):
        s = str(s)
        w = text_w(s, size, bold)
        x0 = x if anchor == "start" else (x - w / 2 if anchor == "middle" else x - w)
        attrs = (f'x="{x:.1f}" y="{y:.1f}" font-family="{family or FONT}" font-size="{size}" '
                 f'text-anchor="{anchor}" fill="{color}"')
        if bold:
            attrs += ' font-weight="bold"'
        if italic:
            attrs += ' font-style="italic"'
        if rot:
            attrs += f' transform="rotate({rot} {x:.1f} {y:.1f})"'
        self.el.append(f"<text {attrs}>{escape(s)}</text>")
        if track and not rot:
            self.texts.append((x0, y - 0.78 * size, x0 + w, y + 0.22 * size, s))

    def dot(self, x, y, r=3.6):
        self.el.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{WIRE}"/>')

    def nc(self, x, y):
        self.line(x - 5, y - 5, x + 5, y + 5, 1.4, color="#1565c0", track=False)
        self.line(x - 5, y + 5, x + 5, y - 5, 1.4, color="#1565c0", track=False)

    def reg(self, ref):
        if ref not in COMPONENTS:
            fail(f"{self.name}: drawn ref {ref} not in design")
        DRAWN_REFS[ref] = self.name
        return COMPONENTS.get(ref, {"value": "?", "part": "?"})

    # ---------- title block
    def title_block(self):
        x, y, w, h = self.w - 560, self.h - 120, 540, 102
        self.rect(x, y, w, h, sw=2, fill="#fff")
        self.line(x, y + 34, x + w, y + 34, 1.2, INK)
        self.line(x, y + 68, x + w, y + 68, 1.2, INK)
        self.line(x + 360, y + 34, x + 360, y + h, 1.2, INK)
        self.line(x + 450, y + 68, x + 450, y + h, 1.2, INK)
        self.text(x + 10, y + 23, PROJECT, 16, bold=True)
        self.text(x + w - 10, y + 23, "hardware/electronics", 11, anchor="end", color=GREY)
        self.text(x + 10, y + 57, self.title, 15, bold=True, color=BLUE)
        self.text(x + 370, y + 57, f"Sheet {self.no}", 13)
        self.text(x + 10, y + 91, f"File: drawings/{self.name}.svg", 11, color=GREY)
        self.text(x + 370, y + 91, f"Rev {REV}", 13, bold=True)
        self.text(x + 460, y + 91, DATE, 12)

    # ---------- output + layout check
    def check(self):
        bad = []
        T = self.texts
        for i in range(len(T)):
            a = T[i]
            for j in range(i + 1, len(T)):
                b = T[j]
                if a[0] < b[2] - 1 and b[0] < a[2] - 1 and a[1] < b[3] - 1 and b[1] < a[3] - 1:
                    bad.append(f"text overlap: '{a[4]}' / '{b[4]}'")
            for s in self.segs:
                if seg_hits_box(s, (a[0] + 1.5, a[1] + 1.5, a[2] - 1.5, a[3] - 1.5)):
                    bad.append(f"wire crosses text '{a[4]}' seg={tuple(round(v) for v in s)}")
                    break
            if a[0] < 14 or a[2] > self.w - 14 or a[1] < 14 or a[3] > self.h - 14:
                bad.append(f"text outside frame: '{a[4]}'")
        return bad

    def save(self):
        if self.frame:
            self.title_block()
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
               f'viewBox="0 0 {self.w} {self.h}">\n' + "\n".join(self.el) + "\n</svg>\n")
        p = DRAW / f"{self.name}.svg"
        p.write_text(svg)
        problems = self.check()
        for pr in problems:
            fail(f"{self.name}: {pr}")
        return p


def seg_hits_box(s, b):
    x1, y1, x2, y2 = s
    bx0, by0, bx1, by1 = b
    if bx1 <= bx0 or by1 <= by0:
        return False
    # Liang-Barsky clip
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - bx0), (dx, bx1 - x1), (-dy, y1 - by0), (dy, by1 - y1)):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                if t > t1:
                    return False
                t0 = max(t0, t)
            else:
                if t < t0:
                    return False
                t1 = min(t1, t)
    return t0 <= t1


# ---------- schematic symbols ------------------------------------------------------------
def _axis(a, b):
    (x1, y1), (x2, y2) = a, b
    L = math.hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    return x1, y1, x2, y2, L, ux, uy


def _labels(s: Sheet, a, b, ref, value, side, off=14, extra=None):
    x1, y1, x2, y2, L, ux, uy = _axis(a, b)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    vertical = abs(uy) > 0.5
    vals = value.split(" ") if (vertical and " " in value and len(value) > 6) else [value]
    lines = [(ref, True)] + [(v, False) for v in vals] + ([(extra, False)] if extra else [])
    if vertical:
        if side in ("right", None):
            for i, (t, bo) in enumerate(lines):
                s.text(mx + off, my - 4 + i * 15 - (len(lines) - 2) * 7, t, 12.5 if bo else 11.5, bold=bo,
                       color=INK if bo else BLUE)
        else:
            for i, (t, bo) in enumerate(lines):
                s.text(mx - off, my - 4 + i * 15 - (len(lines) - 2) * 7, t, 12.5 if bo else 11.5,
                       anchor="end", bold=bo, color=INK if bo else BLUE)
    else:
        if side in ("top", None):
            s.text(mx, my - off - 16, ref, 12.5, anchor="middle", bold=True)
            s.text(mx, my - off - 2, value, 11.5, anchor="middle", color=BLUE)
            if extra:
                s.text(mx, my + off + 12, extra, 11, anchor="middle", color=GREY)
        else:
            s.text(mx, my + off + 10, ref, 12.5, anchor="middle", bold=True)
            s.text(mx, my + off + 24, value, 11.5, anchor="middle", color=BLUE)


def resistor(s: Sheet, ref, a, b, side=None, value=None, extra=None):
    c = s.reg(ref)
    x1, y1, x2, y2, L, ux, uy = _axis(a, b)
    body = 44
    p0 = (x1 + ux * (L - body) / 2, y1 + uy * (L - body) / 2)
    p1 = (x1 + ux * (L + body) / 2, y1 + uy * (L + body) / 2)
    s.line(x1, y1, *p0)
    s.line(*p1, x2, y2)
    nx, ny = -uy, ux
    pts = [p0]
    n = 6
    for i in range(n):
        t = (i + 0.5) / n * body
        sg = 7 if i % 2 == 0 else -7
        pts.append((p0[0] + ux * t + nx * sg, p0[1] + uy * t + ny * sg))
    pts.append(p1)
    s.path("M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in pts), 1.8)
    _labels(s, a, b, ref, value or c["value"], side, extra=extra)


def capacitor(s: Sheet, ref, a, b, polarized=False, side=None, value=None, extra=None):
    c = s.reg(ref)
    x1, y1, x2, y2, L, ux, uy = _axis(a, b)
    gap = 8
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    pa = (mx - ux * gap / 2, my - uy * gap / 2)
    pb = (mx + ux * gap / 2, my + uy * gap / 2)
    s.line(x1, y1, *pa)
    s.line(*pb, x2, y2)
    nx, ny = -uy, ux
    hw = 14
    s.line(pa[0] - nx * hw, pa[1] - ny * hw, pa[0] + nx * hw, pa[1] + ny * hw, 2.4, INK, track=False)
    if polarized:
        # curved negative plate
        q0 = (pb[0] - nx * hw + ux * 4, pb[1] - ny * hw + uy * 4)
        q1 = (pb[0] + nx * hw + ux * 4, pb[1] + ny * hw + uy * 4)
        s.path(f"M {q0[0]:.1f},{q0[1]:.1f} Q {pb[0]:.1f},{pb[1]:.1f} {q1[0]:.1f},{q1[1]:.1f}", 2.4)
        px, py = pa[0] - ux * 9 - nx * 12, pa[1] - uy * 9 - ny * 12
        s.text(px, py + 4, "+", 12, anchor="middle", bold=True, track=False)
    else:
        s.line(pb[0] - nx * hw, pb[1] - ny * hw, pb[0] + nx * hw, pb[1] + ny * hw, 2.4, INK, track=False)
    _labels(s, a, b, ref, value or c["value"], side, off=20, extra=extra)


def diode(s: Sheet, ref, a, b, kind="std", side=None, value=None, extra=None):
    """a = anode end, b = cathode end."""
    c = s.reg(ref)
    x1, y1, x2, y2, L, ux, uy = _axis(a, b)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    h = 9
    ta = (mx - ux * h, my - uy * h)
    tb = (mx + ux * h, my + uy * h)
    nx, ny = -uy, ux
    w = 10
    s.line(x1, y1, *ta)
    s.line(*tb, x2, y2)
    fill = "#fff"
    s.path(f"M {ta[0] + nx * w:.1f},{ta[1] + ny * w:.1f} L {ta[0] - nx * w:.1f},{ta[1] - ny * w:.1f} "
           f"L {tb[0]:.1f},{tb[1]:.1f} Z", 1.8, fill=fill)
    b0 = (tb[0] + nx * w, tb[1] + ny * w)
    b1 = (tb[0] - nx * w, tb[1] - ny * w)
    if kind == "schottky":
        s.path(f"M {b0[0] + ux * 4 - nx * 0:.1f},{b0[1] + uy * 4:.1f} L {b0[0]:.1f},{b0[1]:.1f} "
               f"L {b1[0]:.1f},{b1[1]:.1f} L {b1[0] - ux * 4:.1f},{b1[1] - uy * 4:.1f}", 2.2)
        s.path(f"M {b0[0]:.1f},{b0[1]:.1f} L {b0[0] + ux * 4 + nx * 4:.1f},{b0[1] + uy * 4 + ny * 4:.1f}",
               2.2)
        s.path(f"M {b1[0]:.1f},{b1[1]:.1f} L {b1[0] - ux * 4 - nx * 4:.1f},{b1[1] - uy * 4 - ny * 4:.1f}",
               2.2)
    elif kind == "tvs":
        s.path(f"M {b0[0] - ux * 5:.1f},{b0[1] - uy * 5:.1f} L {b0[0]:.1f},{b0[1]:.1f} "
               f"L {b1[0]:.1f},{b1[1]:.1f} L {b1[0] + ux * 5:.1f},{b1[1] + uy * 5:.1f}", 2.2)
    else:
        s.line(*b0, *b1, 2.2, INK, track=False)
    if kind == "led":
        for k in (0, 1):
            ox, oy = mx + nx * (14 + k * 0) + ux * (-4 + k * 8), my + ny * (14) + uy * (-4 + k * 8)
            ex, ey = ox + nx * 10 + ux * 4, oy + ny * 10 + uy * 4
            s.line(ox, oy, ex, ey, 1.3, INK, track=False)
            s.path(f"M {ex - nx * 4 + ux * 1:.1f},{ey - ny * 4 + uy * 1:.1f} L {ex:.1f},{ey:.1f} "
                   f"L {ex - ux * 4 - nx * 1:.1f},{ey - uy * 4 - ny * 1:.1f}", 1.3)
    _labels(s, a, b, ref, value or c["value"], side, off=22 if kind == "led" else 16, extra=extra)


def ptc(s: Sheet, ref, a, b, side="top", extra=None):
    c = s.reg(ref)
    x1, y1, x2, y2, L, ux, uy = _axis(a, b)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    s.line(x1, y1, mx - 22, my)
    s.line(mx + 22, my, x2, y2)
    s.rect(mx - 22, my - 8, 44, 16, sw=1.8, track=False, fill="#fff")
    s.path(f"M {mx - 26},{my + 14} L {mx + 20},{my - 14} L {mx + 28},{my - 14}", 1.4)
    s.text(mx + 30, my + 18, "t°", 10, track=False)
    _labels(s, a, b, ref, c["value"], side, off=18, extra=extra)


def gnd(s: Sheet, x, y, label=None):
    s.line(x, y, x, y + 10)
    s.line(x - 11, y + 10, x + 11, y + 10, 2, INK, track=False)
    s.line(x - 7, y + 14, x + 7, y + 14, 2, INK, track=False)
    s.line(x - 3, y + 18, x + 3, y + 18, 2, INK, track=False)
    if label:
        s.text(x, y + 34, label, 10.5, anchor="middle", bold=True, color=RED)


def rail(s: Sheet, x, y, name, up=True):
    d = -1 if up else 1
    s.line(x, y, x, y + d * 12)
    s.line(x - 10, y + d * 12, x + 10, y + d * 12, 2.2, INK, track=False)
    s.text(x, y + d * 18 + (0 if up else 10), name, 11.5, anchor="middle", bold=True, color=RED)


def netlabel(s: Sheet, x, y, name, direction="right", color=BLUE, size=11.5):
    w = text_w(name, size, True) + 18
    h = 18
    if direction == "right":
        pts = [(x, y), (x + 8, y - h / 2), (x + w, y - h / 2), (x + w, y + h / 2), (x + 8, y + h / 2)]
        tx = x + 11
        anc = "start"
    else:
        pts = [(x, y), (x - 8, y - h / 2), (x - w, y - h / 2), (x - w, y + h / 2), (x - 8, y + h / 2)]
        tx = x - 11
        anc = "end"
    s.path("M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in pts) + " Z", 1.3, color=color, fill="#eef3fb")
    s.text(tx, y + 4, name, size, anchor=anc, bold=True, color=color)
    return w


def netname(s: Sheet, x, y, name, anchor="start"):
    s.text(x, y, name, 11, anchor=anchor, bold=True, color="#2e7d32")


def testpoint(s: Sheet, ref, x, y, up=True):
    s.reg(ref)
    d = -1 if up else 1
    s.line(x, y, x, y + d * 16)
    s.circle(x, y + d * 22, 6, stroke=INK, fill="#fff6d5")
    s.text(x + 10, y + d * 22 + 4 + (0 if up else 0), ref, 10.5, bold=True, color="#8d6e00")


def ic(s: Sheet, ref, x, y, w, h, title_lines, left=(), right=(), top=(), bottom=(), stub=30,
       ref_pos="above", fill="#fbfbf3"):
    """pins: list of (name, offset). Returns dict name -> pin end (x,y)."""
    s.reg(ref)
    s.rect(x, y, w, h, sw=2, fill=fill)
    ends = {}
    for name, oy in left:
        yy = y + oy
        s.line(x - stub, yy, x, yy)
        s.text(x + 6, yy + 4, name, 11)
        ends[name] = (x - stub, yy)
    for name, oy in right:
        yy = y + oy
        s.line(x + w, yy, x + w + stub, yy)
        s.text(x + w - 6, yy + 4, name, 11, anchor="end")
        ends[name] = (x + w + stub, yy)
    for name, ox in top:
        xx = x + ox
        s.line(xx, y - stub, xx, y)
        s.text(xx, y + 16, name, 11, anchor="middle")
        ends[name] = (xx, y - stub)
    for name, ox in bottom:
        xx = x + ox
        s.line(xx, y + h, xx, y + h + stub)
        s.text(xx, y + h - 7, name, 11, anchor="middle")
        ends[name] = (xx, y + h + stub)
    if ref_pos == "above":
        s.text(x, y - 8, ref, 13, bold=True)
    elif ref_pos == "inside":
        pass
    cy = y + h / 2 - (len(title_lines) - 1) * 8
    for i, t in enumerate(title_lines):
        s.text(x + w / 2, cy + i * 16 + 4, t, 12 if i else 13, anchor="middle", bold=(i == 0),
               color=INK if i == 0 else BLUE)
    return ends


def note_box(s: Sheet, x, y, w, title, lines, size=11.5, lh=16):
    h = 30 + lh * len(lines) + 6
    s.rect(x, y, w, h, stroke="#9aa5b8", fill="#f7f9fc", sw=1.2, rx=4, track=False)
    s.text(x + 10, y + 20, title, 13, bold=True, color=BLUE)
    for i, ln in enumerate(lines):
        bold = ln.startswith("!")
        s.text(x + 12, y + 40 + i * lh, ln.lstrip("!"), size, bold=bold, color=INK)
        if text_w(ln, size) > w - 20:
            fail(f"{s.name}: note line too long for box: {ln[:40]}")
    return h


# =============================================================================
# 4. SHEETS
# =============================================================================

def sheet_power() -> Sheet:
    s = Sheet("schematic_power", "Power tree", "1 / 3")
    s.text(30, 44, "SHEET 1 - POWER TREE: USB-C PD 9 V in -> protection -> Buck A (servo rail) / Buck B (logic, phone)",
           15, bold=True)
    Y = 150
    # ---- J1 PWR IN
    j1 = ic(s, "J1", 60, 120, 110, 360, ["USB-C", "PWR IN", "(sink)"],
            right=[("VBUS", 30), ("GND", 70), ("CC1", 220), ("CC2", 250), ("D+", 280), ("D-", 310),
                   ("SHIELD", 340)], fill="#eef3fb")
    s.wire(j1["GND"], (220, 190))
    gnd(s, 220, 190)
    s.wire(j1["SHIELD"], (220, 460))
    gnd(s, 220, 460)
    # ---- U1 CH224K
    u1 = ic(s, "U1", 340, 300, 180, 180, ["CH224K", "USB-PD sink", "trigger"],
            left=[("CC1", 40), ("CC2", 70), ("DP", 100), ("DM", 130)],
            right=[("PG", 40), ("CFG1", 70), ("CFG2", 100), ("CFG3", 130)],
            top=[("VDD", 90)], bottom=[("GND", 90)])
    for p in ("CC1", "CC2"):
        s.wire(j1[p], u1[p])
    s.wire(j1["D+"], u1["DP"])
    s.wire(j1["D-"], u1["DM"])
    gnd(s, *u1["GND"])
    # VDD feed
    s.wire(j1["VBUS"], (760, Y))
    s.dot(260, Y)
    resistor(s, "R1", (260, Y), (260, 240), side="left")
    s.wire((260, 240), (430, 240), u1["VDD"])
    s.dot(300, 240)
    capacitor(s, "C1", (300, 240), (300, 296), side="left")
    gnd(s, 300, 296)
    # CFG / PG
    s.wire(u1["CFG1"], (620, 370))
    resistor(s, "R2", (620, 370), (620, 450), side="right", extra="-> 9 V")
    gnd(s, 620, 450)
    s.nc(*u1["CFG2"])
    s.nc(*u1["CFG3"])
    s.dot(720, Y)
    resistor(s, "R3", (720, Y), (720, 235), side="right")
    diode(s, "D1", (720, 235), (720, 305), kind="led", side="right")
    s.wire((720, 305), (720, 340), u1["PG"])
    netname(s, 180, Y - 8, "VBUS_IN (5-9 V)")
    # ---- protection
    ptc(s, "F1", (760, Y), (860, Y), side="top")
    s.text(810, Y + 40, "PTC 1812, 16 V", 11, anchor="middle", color=GREY)
    s.wire((860, Y), (1230, Y))
    netname(s, 870, Y - 8, "VIN_9V")
    for x in (930, 1030, 1110):
        s.dot(x, Y)
    diode(s, "D2", (930, 245), (930, Y), kind="tvs", side="right")
    capacitor(s, "C2", (1030, Y), (1030, 240), polarized=True, side="right")
    capacitor(s, "C3", (1110, Y), (1110, 240), side="right")
    s.wire((930, 245), (930, 275), (1110, 275), (1110, 240))
    s.wire((1030, 240), (1030, 275))
    s.dot(1030, 275)
    gnd(s, 1030, 275, label="STAR GND")
    testpoint(s, "TP1", 1170, Y)
    s.dot(1170, Y)
    # ---- Buck A
    ua = ic(s, "U2", 1260, 110, 160, 140, ["BUCK A  9->5 V", "XL4015 5A CC/CV", "set 5.10 V, CC 4.5 A"],
            left=[("IN+", 40), ("IN-", 100)], right=[("OUT+", 40), ("OUT-", 100)])
    s.wire(ua["IN-"], (1230, 210))
    gnd(s, 1230, 210)
    gnd(s, *ua["OUT-"])
    s.wire(ua["OUT+"], (1700, Y))
    for x in (1520, 1600, 1650):
        s.dot(x, Y)
    capacitor(s, "C4", (1520, Y), (1520, 240), polarized=True, side="right")
    gnd(s, 1520, 240)
    capacitor(s, "C5", (1600, Y), (1600, 240), side="right")
    gnd(s, 1600, 240)
    testpoint(s, "TP2", 1650, Y)
    netlabel(s, 1700, Y, "V_SERVO", "right")
    s.text(1770, 292, "-> A2 V+ terminal (sheet 3)", 11, anchor="end", color=GREY)
    s.text(1770, 307, "C4/C5 mounted AT the terminal", 11, anchor="end", color=GREY)
    # ---- Buck B
    s.dot(1200, Y)
    ub = ic(s, "U3", 1260, 450, 160, 120, ["BUCK B  9->5 V", "Mini560 5V fixed", "(5 A rated, <=2.2 A)"],
            left=[("IN+", 30), ("IN-", 90)], right=[("OUT+", 30), ("OUT-", 90)])
    s.wire((1200, Y), (1200, 480), ub["IN+"])
    s.wire(ub["IN-"], (1220, 540))
    gnd(s, 1220, 540)
    gnd(s, *ub["OUT-"])
    s.wire(ub["OUT+"], (1590, 480))
    capacitor(s, "C6", (1500, 480), (1500, 560), polarized=True, side="right")
    gnd(s, 1500, 560)
    s.dot(1500, 480)
    testpoint(s, "TP3", 1460, 480)
    s.dot(1460, 480)
    netname(s, 1568, 600, "5V_LOGIC", anchor="start")
    diode(s, "D3", (1590, 480), (1670, 480), kind="schottky", side="top")
    netlabel(s, 1670, 480, "5V_DEVKIT", "right")
    s.text(1730, 512, "-> A1 5V pin", 11, anchor="middle", color=GREY)
    s.text(1730, 527, "(sheet 2)", 11, anchor="middle", color=GREY)
    # ---- U4 power switch
    s.dot(1560, 480)
    u4 = ic(s, "U4", 1240, 700, 180, 160, ["TPS2553", "USB power switch", "I_LIM ~1.7 A"],
            left=[("/FAULT", 40), ("EN", 120)], right=[("OUT", 40)], top=[("IN", 90)],
            bottom=[("GND", 60), ("ILIM", 130)])
    s.wire((1560, 480), (1560, 640), (1330, 640), u4["IN"])
    s.dot(1560, 640)
    s.wire((1560, 640), (1640, 640))
    capacitor(s, "C9", (1640, 640), (1640, 690), side="right")
    gnd(s, 1640, 690)
    gnd(s, *u4["GND"])
    resistor(s, "R4", u4["ILIM"], (1370, 970), side="right")
    gnd(s, 1370, 970)
    s.wire(u4["/FAULT"], (1050, 740))
    s.dot(1130, 740)
    resistor(s, "R6", (1130, 740), (1130, 650), side="right")
    rail(s, 1130, 650, "+3V3")
    netlabel(s, 1050, 740, "PHONE_VBUS_FLT", "left")
    s.text(925, 768, "-> GPIO15 (sheet 2)", 11, anchor="middle", color=GREY)
    s.wire(u4["EN"], (1050, 820))
    s.dot(1130, 820)
    resistor(s, "R5", (1130, 820), (1130, 900), side="right")
    gnd(s, 1130, 900)
    netlabel(s, 1050, 820, "PHONE_VBUS_EN", "left")
    s.text(925, 848, "<- GPIO7 (sheet 2)", 11, anchor="middle", color=GREY)
    s.wire(u4["OUT"], (1690, 740))
    for x in (1500, 1570, 1630):
        s.dot(x, 740)
    capacitor(s, "C7", (1500, 740), (1500, 820), polarized=True, side="right")
    gnd(s, 1500, 820)
    capacitor(s, "C8", (1570, 740), (1570, 820), side="right")
    gnd(s, 1570, 820)
    testpoint(s, "TP4", 1630, 740)
    netlabel(s, 1690, 740, "VBUS_HEAD", "right", size=11)
    s.text(1730, 772, "-> J2 (sheet 2)", 11, anchor="middle", color=GREY)

    note_box(s, 30, 560, 830, "Design notes - power", [
        "!PD contract: U1 CH224K requests 9 V (CFG1 = 6.8k). Single-resistor mode: 6.8k=9 V, 24k=12 V,",
        "  56k=15 V, OPEN = 20 V(!) - an unfitted R2 asks for 20 V: D2 + F1 then protect the bucks.",
        "  Charger without 9 V -> CH224K stays at 5 V; firmware sees VIN_SENSE < 7 V and refuses 'enable'.",
        "  D1 'PD OK' lights when PG pulls low (contract accepted).",
        "!Protection: F1 PTC (3 A hold / ~5 A trip) ahead of D2 SMBJ12A (13.3 V min breakdown) so a",
        "  sustained over-voltage or reversed module wiring trips F1 instead of burning D2.",
        "  C2 470 uF holds VIN through servo current steps; its (-) lead is the STAR GROUND.",
        "!Buck A (servo rail): CC/CV module, trimmed to 5.10 V and CC 4.5 A -> stall current limited at",
        "  the source; C4 1000 uF low-ESR sits at the PCA9685 V+ terminal, not at the module.",
        "!Buck B (logic): fixed 5 V (no trimmer to mis-set into the phone). D3 SS34 feeds the DevKit",
        "  5V pin: blocks back-feed and lets a PC on the DevKit UART port win (Vf ~0.35 V).",
        "!Phone VBUS: U4 TPS2553, R4 15k -> I_OS ~1.7 A typ (>= 1.5 A Rp advert, no nuisance trips).",
        "  EN pulled low (R5) = phone unpowered at reset; /FAULT open-drain to GPIO15 (R6 pull-up).",
        "  C7 >= 120 uF on the downstream port (USB 2.0), soft-start in U4 limits inrush.",
        "!Grounds: STAR = C2 (-) on the PB power board. Servo current returns A2 -> U2 OUT- -> U2 IN- -> star.",
        "Test points: TP1 VIN 8.6-9.4 V | TP2 V_SERVO 5.0-5.2 V | TP3 5V_LOGIC 4.9-5.1 V | TP4 VBUS_HEAD",
        "  0 V (EN low) / 4.8-5.1 V (EN high). TP5..TP9 on sheet 2.",
    ], size=11.5, lh=17)
    return s


def sheet_logic() -> Sheet:
    s = Sheet("schematic_logic", "MCU, PWM, USB head port", "2 / 3")
    s.text(30, 44, "SHEET 2 - LOGIC: ESP32-S3-DevKitC-1, PCA9685, USB-C HEAD port (OTG host), sense dividers, UI",
           15, bold=True)
    # DevKit A1
    X0, X1, Y0, P = 790, 1030, 130, 30
    s.reg("A1")
    s.rect(X0, Y0, X1 - X0, 22 * P + 40, sw=2.2, fill="#f3f7f0")
    s.text(X0 + (X1 - X0) / 2, Y0 + 330, "A1", 14, anchor="middle", bold=True)
    s.text(X0 + (X1 - X0) / 2, Y0 + 350, "ESP32-S3-DevKitC-1", 13, anchor="middle", bold=True)
    s.text(X0 + (X1 - X0) / 2, Y0 + 368, "N8R2 / N16R8", 11.5, anchor="middle", color=BLUE)
    s.text(X0 + (X1 - X0) / 2, Y0 + 386, "J1 (left)   J3 (right)", 11, anchor="middle", color=GREY)
    # on-board parts
    s.rect(X0 + 50, Y0 + 430, 140, 54, stroke=GREY, dash="4 3", sw=1.2, fill="#fff")
    s.text(X0 + 120, Y0 + 452, "on-board: BOOT sw", 10.5, anchor="middle", color=GREY)
    s.text(X0 + 120, Y0 + 470, "WS2812 RGB LED", 10.5, anchor="middle", color=GREY)
    s.rect(X0 + 50, Y0 + 500, 140, 54, stroke=GREY, dash="4 3", sw=1.2, fill="#fff")
    s.text(X0 + 120, Y0 + 522, "USB-UART port", 10.5, anchor="middle", color=GREY)
    s.text(X0 + 120, Y0 + 540, "(PC, 115200 baud)", 10.5, anchor="middle", color=GREY)
    lp, rp = {}, {}
    for i, n in enumerate(DEVKIT_J1):
        y = Y0 + 30 + i * P
        s.line(X0 - 30, y, X0, y)
        s.text(X0 + 6, y + 4, n, 11)
        s.text(X0 - 26, y - 4, str(i + 1), 9, color=GREY)
        lp[n] = lp.get(n, []) + [(X0 - 30, y)]
    for i, n in enumerate(DEVKIT_J3):
        y = Y0 + 30 + i * P
        s.line(X1, y, X1 + 30, y)
        s.text(X1 - 6, y + 4, n, 11, anchor="end")
        s.text(X1 + 26, y - 4, str(i + 1), 9, anchor="end", color=GREY)
        rp[n] = rp.get(n, []) + [(X1 + 30, y)]
    used_l = {"3V3", "GPIO4", "GPIO5", "GPIO7", "GPIO15", "GPIO8", "GPIO9", "GPIO10", "5V", "GND"}
    used_r = {"GND", "GPIO0", "GPIO48", "GPIO19", "GPIO20"}
    for n, pts in lp.items():
        if n not in used_l:
            for p in pts:
                s.nc(*p)
    for n, pts in rp.items():
        if n not in used_r:
            for p in pts:
                s.nc(*p)
    # left side labels
    (a, b) = lp["3V3"]
    s.wire(a, (740, a[1]), (740, b[1]), b)
    s.dot(740, a[1])
    rail(s, 740, a[1], "+3V3")
    s.wire((740, a[1]), (700, a[1]))
    testpoint(s, "TP6", 700, a[1], up=False)
    for n in ("GPIO4", "GPIO5", "GPIO7", "GPIO15", "GPIO8", "GPIO9", "GPIO10", "5V"):
        p = lp[n][0]
        net = devkit_net(n)
        s.wire(p, (680, p[1]))
        netlabel(s, 680, p[1], net, "left")
    p = lp["5V"][0]
    s.dot(710, p[1])
    testpoint(s, "TP5", 710, p[1], up=True)
    p = lp["GND"][0]
    s.wire(p, (700, p[1]))
    s.dot(740, p[1])
    gnd(s, 740, p[1])
    testpoint(s, "TP9", 700, p[1], up=False)
    # right side
    g = rp["GND"]
    s.wire(g[0], (1080, g[0][1]))
    gnd(s, 1080, g[0][1])
    s.wire(g[1], (1075, g[1][1]), (1075, g[2][1]), g[2])
    s.dot(1075, g[2][1])
    s.wire((1075, g[2][1]), (1075, g[2][1] + 20))
    gnd(s, 1075, g[2][1] + 20)
    for n in ("GPIO0", "GPIO48"):
        p = rp[n][0]
        s.wire(p, (1100, p[1]))
        netlabel(s, 1100, p[1], devkit_net(n), "right")
    # USB D+/D- to ESD + J2
    dp = rp["GPIO20"][0]
    dn = rp["GPIO19"][0]
    u5 = ic(s, "U5", 1200, 830, 130, 110, ["USBLC6-2SC6", "ESD"],
            left=[("1 I/O1", 30), ("3 I/O2", 60)], right=[("6 I/O1", 30), ("4 I/O2", 60)],
            top=[("5 VBUS", 65)], bottom=[("2 GND", 65)], ref_pos="above")
    s.wire(dp, (1110, dp[1]), (1110, u5["1 I/O1"][1]), u5["1 I/O1"])
    s.wire(dn, (1095, dn[1]), (1095, u5["3 I/O2"][1]), u5["3 I/O2"])
    s.text(1116, u5["1 I/O1"][1] - 6, "USB_DP", 10.5, bold=True, color="#2e7d32")
    s.text(1101, u5["3 I/O2"][1] + 16, "USB_DN", 10.5, bold=True, color="#2e7d32")
    gnd(s, *u5["2 GND"])
    j2 = ic(s, "J2", 1600, 560, 130, 400, ["USB-C", "HEAD", "(source /", "DFP)"],
            left=[("VBUS", 40), ("CC1", 130), ("CC2", 170), ("D+", 300), ("D-", 330), ("GND", 360),
                  ("SHIELD", 385)], fill="#eef3fb")
    s.wire(u5["6 I/O1"], (1460, u5["6 I/O1"][1]), (1460, j2["D+"][1]), j2["D+"])
    s.wire(u5["4 I/O2"], (1440, u5["4 I/O2"][1]), (1440, j2["D-"][1]), j2["D-"])
    yv = j2["VBUS"][1]
    s.wire((1265, u5["5 VBUS"][1]), (1265, yv), j2["VBUS"])
    s.wire((1265, 650), (1245, 650))
    s.dot(1265, 650)
    netlabel(s, 1245, 650, "VBUS_HEAD", "left")
    s.dot(1420, yv)
    s.dot(1500, yv)
    resistor(s, "R13", (1420, yv), (1420, j2["CC2"][1]), side="left", extra="Rp 1.5 A")
    s.wire((1420, j2["CC2"][1]), j2["CC2"])
    resistor(s, "R12", (1500, yv), (1500, j2["CC1"][1]), side="right")
    s.wire((1500, j2["CC1"][1]), j2["CC1"])
    s.wire(j2["GND"], (1545, j2["GND"][1]), (1545, j2["SHIELD"][1]), j2["SHIELD"])
    s.dot(1545, j2["SHIELD"][1])
    s.wire((1545, j2["SHIELD"][1]), (1545, j2["SHIELD"][1] + 20))
    gnd(s, 1545, j2["SHIELD"][1] + 20)
    s.text(1665, 990, "short USB 2.0 C-C cable,", 11, anchor="middle", color=GREY)
    s.text(1665, 1005, "up the column to the phone", 11, anchor="middle", color=GREY)

    # ---- user button / LED block (top right)
    bx, by = 1180, 120
    s.text(bx, by + 10, "User I/O", 13, bold=True, color=BLUE)
    netlabel(s, bx + 20, by + 60, "USER_BUTTON", "right")
    s.wire((bx + 20, by + 60), (bx, by + 60), (bx, by + 90))
    # SW1 pushbutton (vertical)
    s.reg("SW1")
    sx, sy = bx, by + 90
    s.line(sx, sy, sx, sy + 12)
    s.circle(sx, sy + 15, 3)
    s.circle(sx, sy + 45, 3)
    s.line(sx, sy + 48, sx, sy + 60)
    s.line(sx - 12, sy + 12, sx - 12, sy + 48, 2, INK, track=False)
    s.line(sx - 12, sy + 30, sx - 22, sy + 30, 2, INK, track=False)
    s.line(sx - 22, sy + 24, sx - 22, sy + 36, 2, INK, track=False)
    s.text(sx + 12, sy + 28, "SW1", 12.5, bold=True)
    s.text(sx + 12, sy + 43, "panel button", 11.5, color=BLUE)
    s.text(sx + 12, sy + 57, "(optional, || BOOT)", 11, color=GREY)
    gnd(s, sx, sy + 60)
    s.text(bx + 170, by + 105, "GPIO0 has the DevKit's 10k pull-up + BOOT sw.", 11, color=GREY)
    s.text(bx + 170, by + 121, "Short = enable/relax, long >1.5 s = home.", 11, color=GREY)
    s.text(bx + 170, by + 137, "Strapping pin: released at power-up!", 11, color=RED)
    netlabel(s, bx + 20, by + 210, "STATUS_LED", "right")
    s.text(bx + 170, by + 214, "-> on-board WS2812 (GPIO48 on DevKitC-1 v1.0;", 11, color=GREY)
    s.text(bx + 170, by + 230, "v1.1 boards use GPIO38 - set in firmware)", 11, color=GREY)

    # ---- left: sense dividers + PCA9685
    def divider(x, top_net, rt, rb, cref, sense, tpy):
        rail(s, x, 160, top_net)
        resistor(s, rt, (x, 160), (x, 250), side="left")
        s.dot(x, 250)
        resistor(s, rb, (x, 250), (x, 340), side="left")
        gnd(s, x, 340)
        s.wire((x, 250), (x + 60, 250), (x + 60, 270))
        s.dot(x + 60, 250)
        capacitor(s, cref, (x + 60, 270), (x + 60, 320), side="right")
        gnd(s, x + 60, 320)
        s.wire((x + 60, 250), (x + 120, 250))
        s.dot(x + 100, 250)
        testpoint(s, "TP7", x + 100, 250, up=True)
        netlabel(s, x + 120, 250, sense, "right")

    s.text(40, 110, "Sense dividers (ADC1, 11 dB atten.)", 13, bold=True, color=BLUE)
    divider(100, "V_SERVO", "R7", "R8", "C10", "VSERVO_SENSE", 0)
    s.text(40, 395, "5.1 V -> 2.55 V (4.3 V fault = 2.15 V)", 11, color=GREY)
    s.text(40, 411, "fitted at the A2 V+ terminal (senses the load)", 11, color=GREY)

    # second divider lower
    def divider2(x, y0, top_net, rt, rb, cref, sense):
        rail(s, x, y0, top_net)
        resistor(s, rt, (x, y0), (x, y0 + 90), side="left")
        s.dot(x, y0 + 90)
        resistor(s, rb, (x, y0 + 90), (x, y0 + 180), side="left")
        gnd(s, x, y0 + 180)
        s.wire((x, y0 + 90), (x + 60, y0 + 90), (x + 60, y0 + 110))
        s.dot(x + 60, y0 + 90)
        capacitor(s, cref, (x + 60, y0 + 110), (x + 60, y0 + 160), side="right")
        gnd(s, x + 60, y0 + 160)
        s.wire((x + 60, y0 + 90), (x + 120, y0 + 90))
        s.dot(x + 100, y0 + 90)
        testpoint(s, "TP8", x + 100, y0 + 90, up=True)
        netlabel(s, x + 120, y0 + 90, sense, "right")

    divider2(100, 460, "VIN_9V", "R9", "R10", "C11", "VIN_SENSE")
    s.text(40, 695, "9.0 V -> 1.62 V; full scale ~17 V; on PB", 11, color=GREY)

    # PCA9685 A2
    a2 = ic(s, "A2", 140, 790, 200, 210, ["PCA9685", "16-ch PWM", "addr 0x40", "(A0-A5 open)"],
            right=[("OE", 30), ("VCC", 60), ("SDA", 90), ("SCL", 120), ("GND", 150), ("V+ hdr", 180)])
    s.wire(a2["OE"], (480, a2["OE"][1]))
    s.dot(420, a2["OE"][1])
    resistor(s, "R11", (420, a2["OE"][1]), (420, a2["OE"][1] - 90), side="right",
             extra="at A2 header")
    rail(s, 420, a2["OE"][1] - 90, "+3V3")
    netlabel(s, 480, a2["OE"][1], "PCA_OE", "right")
    s.wire(a2["VCC"], (380, a2["VCC"][1]))
    netlabel(s, 380, a2["VCC"][1], "+3V3", "right", color=RED)
    s.wire(a2["SDA"], (380, a2["SDA"][1]))
    netlabel(s, 380, a2["SDA"][1], "I2C_SDA", "right")
    s.wire(a2["SCL"], (380, a2["SCL"][1]))
    netlabel(s, 380, a2["SCL"][1], "I2C_SCL", "right")
    s.wire(a2["GND"], (395, a2["GND"][1]))
    gnd(s, 395, a2["GND"][1])
    s.nc(*a2["V+ hdr"])
    s.text(380, a2["V+ hdr"][1] + 4, "NC - V+ via screw terminal (sheet 3)", 10.5, color=GREY)
    s.text(140, 1030, "Breakout I2C pull-ups (10k) to VCC=3V3 are sufficient", 11, color=GREY)
    s.text(140, 1046, "at 400 kHz with a < 20 cm harness.", 11, color=GREY)

    s.text(1180, 420, "USB host port (native OTG, full-speed 12 Mb/s)", 13, bold=True, color=BLUE)
    for i, ln in enumerate([
        "ESP32-S3 is USB HOST; phone = device -> AOA accessory.",
        "Rp 22k on CC1/CC2 to VBUS_HEAD advertises 1.5 A and",
        "disappears with VBUS, so phone_power off = clean detach.",
        "GPIO19/20 are also on the DevKit 'USB' connector:",
        "never plug anything into that port while J2 is in use.",
        "Keep D+/D- a twisted pair, < 15 cm to J2, 90 ohm diff.",
    ]):
        s.text(1180, 444 + i * 16, ln, 11, color=INK if i else INK)
    return s


SERVO_WIRE = {"SIG": "#f28c00", "V+": "#d32f2f", "GND": "#6d4c41"}


def sheet_servos() -> Sheet:
    s = Sheet("schematic_servos", "Servo outputs", "3 / 3")
    s.text(30, 44, "SHEET 3 - SERVOS: PCA9685 (A2) outputs ch0-7 -> 8 servos; ch8-15 spare; stylus ground leads",
           15, bold=True)
    s.reg("A2")
    X, Y, W, H = 230, 100, 230, 870
    s.rect(X, Y, W, H, sw=2.2, fill="#fbfbf3")
    s.text(X, Y - 8, "A2", 13, bold=True)
    s.text(X + 70, Y + 300, "PCA9685", 14, anchor="middle", bold=True)
    s.text(X + 70, Y + 318, "breakout", 12, anchor="middle", color=BLUE)
    s.text(X + 70, Y + 334, "50 Hz, 12-bit", 11, anchor="middle", color=GREY)
    s.text(X + 70, Y + 350, "500-2500 us", 11, anchor="middle", color=GREY)
    left = [("V+ term", 60), ("GND term", 160), ("VCC", 420), ("SDA", 450), ("SCL", 480), ("OE", 510),
            ("GND", 540)]
    for name, oy in left:
        yy = Y + oy
        s.line(X - 30, yy, X, yy)
        s.text(X + 6, yy + 4, name, 11)
    yv, yg = Y + 60, Y + 160
    s.wire((X - 30, yv), (110, yv))
    netlabel(s, 110, yv, "V_SERVO", "left")
    s.dot(150, yv)
    capacitor(s, "C4", (150, yv), (150, yv + 70), polarized=True, side="left")
    gnd(s, 150, yv + 70)
    s.wire((X - 30, yg), (190, yg))
    gnd(s, 190, yg)
    s.text(214, yg + 40, "via W4 to U2 OUT-", 10.5, anchor="end", color=RED)
    s.text(40, Y + 240, "W4: AWG20 red/black", 11, color=GREY)
    s.text(40, Y + 256, "from U2, ferrules in", 11, color=GREY)
    s.text(40, Y + 272, "the screw terminal", 11, color=GREY)
    for name, oy, net in [("VCC", 420, "+3V3"), ("SDA", 450, "I2C_SDA"), ("SCL", 480, "I2C_SCL"),
                          ("OE", 510, "PCA_OE")]:
        netlabel(s, X - 30, Y + oy, net, "left", size=10.5)
    s.wire((X - 30, Y + 540), (X - 50, Y + 540))
    gnd(s, X - 50, Y + 540)
    s.text(X + 10, Y + H - 40, "PWM8-15 / V+ / GND: spare", 11, color=GREY)
    s.text(X + 10, Y + H - 24, "headers populated, NC", 11, color=GREY)

    lens = servo_lengths()
    routes = {}
    for _r, _c, joint, _t, _l in SERVOS:
        need, ext = lens[joint]
        where = ("in base" if "YAW" in joint else
                 f"up {joint[0]} turret bore" if joint[0] in "LR" else "up the column")
        routes[joint] = (f"{where}, {LOOP[joint]} mm loop, needs {need} mm: "
                         + (f"+{ext} mm extension" if ext else "stock lead"))
    for i, (ref, ch, joint, typ, loc) in enumerate(SERVOS):
        y0 = Y + 50 + i * 100
        s.reg(ref)
        for k, (pn, dy) in enumerate((("SIG", 0), ("V+", 22), ("GND", 44))):
            yy = y0 + dy
            nm = f"PWM{ch}" if pn == "SIG" else pn
            s.line(X + W, yy, X + W + 20, yy)
            s.text(X + W - 6, yy + 4, nm, 11, anchor="end")
            col = SERVO_WIRE[pn]
            s.line(X + W + 20, yy, 700, yy, 3.2, col)
            s.line(760, yy, 1100, yy, 3.2, col)
            s.line(1100, yy, 1150, y0 + 22, 3.2, col)
        # header/plug mating mark
        s.rect(700, y0 - 8, 60, 60, stroke=GREY, fill="#eceff1", sw=1.2, track=False)
        s.text(730, y0 + 26, "3P", 10, anchor="middle", color=GREY, track=False)
        s.text(560, y0 - 8, f"ch{ch}", 12, anchor="middle", bold=True, color=BLUE)
        s.text(930, y0 - 8, routes[joint], 11, anchor="middle", color=GREY)
        # servo symbol
        s.circle(1180, y0 + 22, 28, stroke=INK, fill="#fff", sw=2)
        s.text(1180, y0 + 28, "M", 17, anchor="middle", bold=True)
        s.text(1225, y0 + 14, f"{ref}  {joint}", 13, bold=True)
        s.text(1225, y0 + 31, f"{typ}  -  ch{ch}", 11.5, color=BLUE)
        s.text(1225, y0 + 47, loc, 11, color=GREY)
    # wire colour legend
    lx, ly = 40, 1010
    s.text(lx, ly, "Servo lead colours (JR/Futaba 3-pin, 2.54 mm):", 12, bold=True)
    for k, (pn, lab) in enumerate((("SIG", "orange = signal (PWMn)"), ("V+", "red = V+ (V_SERVO 5.1 V)"),
                                    ("GND", "brown = GND"))):
        xx = lx + k * 260
        s.line(xx, ly + 24, xx + 40, ly + 24, 4, SERVO_WIRE[pn])
        s.text(xx + 48, ly + 28, lab, 11.5)
    s.text(lx, ly + 58, "Extensions: 26AWG 3-wire JR, female-male. Plug brown to the GND row (board edge) - follow the A2 silkscreen.",
           11, color=GREY)

    # stylus grounding block
    bx, by = 1560, 100
    s.text(bx, by + 10, "Stylus ground reference", 13, bold=True, color=BLUE)
    for k, ref in enumerate(("E1", "E2")):
        s.reg(ref)
        x = bx + 40 + k * 110
        # stylus tip symbol: rounded tip
        s.path(f"M {x - 9},{by + 60} L {x - 9},{by + 100} Q {x},{by + 116} {x + 9},{by + 100} "
               f"L {x + 9},{by + 60} Z", 1.8, fill="#e0e0e0")
        s.text(x, by + 48, ref, 12.5, anchor="middle", bold=True)
        s.text(x + 14, by + 160, "TIP " + "LR"[k], 11, color=BLUE)
        s.wire((x, by + 116), (x, by + 145))
        s.wire((x, by + 145), (x, by + 175), dash="3 3")
        s.wire((x, by + 175), (x, by + 200))
        gnd(s, x, by + 200)
    s.text(bx, by + 250, "30AWG silicone / tinsel lead", 11, color=GREY)
    s.text(bx, by + 266, "along each arm, 5 mm loops at", 11, color=GREY)
    s.text(bx, by + 282, "every joint, to GND at A2.", 11, color=GREY)
    s.text(bx, by + 306, "Tray floor: copper-foil pad", 11, color=GREY)
    s.text(bx, by + 322, "under the phone, also to GND,", 11, color=GREY)
    s.text(bx, by + 338, "closes the touch return path.", 11, color=GREY)
    s.text(bx, by + 362, "See README section 6.", 11, color=GREY)
    return s


# ---- block diagram ---------------------------------------------------------------------
def box(s, x, y, w, h, title, lines=(), fill="#eef3fb", stroke=BLUE, size=13):
    s.rect(x, y, w, h, stroke=stroke, fill=fill, sw=2, rx=8)
    s.text(x + w / 2, y + 24, title, size, anchor="middle", bold=True)
    for i, ln in enumerate(lines):
        s.text(x + w / 2, y + 44 + i * 16, ln, 11, anchor="middle", color="#333")
        if text_w(ln, 11) > w - 10:
            fail(f"{s.name}: box text too wide: {ln}")


def arrow(s, pts, color=RED, w=2.6, dash=None, both=False, label=None, lpos=None, lanchor="middle"):
    s.wire(*pts, w=w, color=color, dash=dash, track=False)

    def head(p, q):
        (x1, y1), (x2, y2) = p, q
        L = math.hypot(x2 - x1, y2 - y1)
        ux, uy = (x2 - x1) / L, (y2 - y1) / L
        a = (x2 - ux * 12 - uy * 6, y2 - uy * 12 + ux * 6)
        b = (x2 - ux * 12 + uy * 6, y2 - uy * 12 - ux * 6)
        s.path(f"M {x2:.1f},{y2:.1f} L {a[0]:.1f},{a[1]:.1f} L {b[0]:.1f},{b[1]:.1f} Z", 1, color, fill=color)

    head(pts[-2], pts[-1])
    if both:
        head(pts[1], pts[0])
    if label:
        x, y = lpos
        for i, ln in enumerate(label.split("\n")):
            s.text(x, y + i * 14, ln, 11, anchor=lanchor, color=color, bold=(i == 0))


def block_diagram() -> Sheet:
    s = Sheet("block_diagram", "System block diagram", "BD")
    s.text(30, 44, "SYSTEM BLOCK DIAGRAM - power (red), wired data (blue), wireless (green, dashed), mechanical (grey)",
           15, bold=True)
    s.rect(336, 90, 974, 680, stroke=INK, fill="#fcfcfc", sw=2, rx=10, dash="8 5", track=False)
    s.text(352, 118, "TAPPET BODY (base bay)", 14, bold=True)
    A, B, C = 360, 660, 960            # column x
    PW = "#fdecea"
    # external, left
    box(s, 40, 150, 210, 90, "USB-C PD charger", [">= 20 W (30 W rec.)", "9 V / 2.22-3 A PDO"], fill=PW, stroke=RED)
    box(s, 40, 420, 210, 100, "PC / laptop", ["Python tools, flashing", "same JSON protocol", "DevKit UART USB port"],
        fill="#eceff1", stroke=GREY)
    box(s, 40, 660, 210, 90, "Browser (any)", ["remote control UI", "6-digit PIN pairing"], fill="#eceff1",
        stroke=GREY)
    # body
    box(s, A, 150, 220, 90, "PD trigger + protection", ["CH224K -> 9 V", "PTC 3 A, TVS 12 V, 470 uF"], fill=PW,
        stroke=RED)
    box(s, B, 150, 220, 90, "Buck A  5.1 V / 5 A", ["XL4015 CC/CV module", "CC limit 4.5 A"], fill=PW, stroke=RED)
    box(s, C, 150, 220, 90, "PCA9685 PWM", ["I2C 0x40, OE gated", "16 ch, 50 Hz", ], )
    box(s, A, 320, 220, 90, "Buck B  5 V / 3 A", ["Mini560, fixed 5 V", "logic + phone"], fill=PW, stroke=RED)
    box(s, B, 300, 220, 200, "ESP32-S3 DevKitC-1", ["motion planner, IK, watchdog", "USB OTG host (AOA)",
                                                     "UART bridge 115200", "ADC: VIN, V_SERVO",
                                                     "fault + power governor"])
    box(s, C, 330, 300, 150, "8 servos", ["L/R arm: YAW (SG90),", "SHOULDER, ELBOW (MG90S)",
                                          "neck: PAN, TILT (MG90S)", "ch0-7 (spares ch8-15)",
                                          "stylus tips -> GND leads"], fill="#f1f8e9", stroke="#558b2f")
    box(s, A, 580, 220, 90, "USB power switch", ["TPS2553, 1.7 A limit", "EN GPIO7, /FLT GPIO15"], fill=PW,
        stroke=RED)
    box(s, B, 580, 220, 90, "USB-C HEAD port", ["Rp 22k = 1.5 A advert.", "ESD USBLC6, D+/D- GPIO20/19"])
    # external, right
    box(s, 1450, 260, 300, 110, "Target iPhone in tray", ["screen-up, portrait", "tapped by the stylus tips",
                                                          "browser: web UI / touchpad"], fill="#fff8e1",
        stroke="#f9a825")
    box(s, 1450, 440, 300, 150, "Android phone = HEAD", ["display, camera, mic, speaker", "Tappet app (AOA accessory)",
                                                         "web UI :8080 + Claude agent", "on 2-axis neck, charging",
                                                         "camera checks taps on tray"], fill="#ede7f6",
        stroke="#5e35b1")
    box(s, 1450, 680, 300, 90, "Claude API (cloud)", ["Messages API + tools", "called from the head app"],
        fill="#ede7f6", stroke="#5e35b1")

    def arr(pts, color=RED, label=None, lpos=None, anchor="middle", both=False, dash=None, w=2.6):
        arrow(s, pts, color=color, label=label, lpos=lpos, lanchor=anchor, both=both, dash=dash, w=w)
        for a, b in zip(pts, pts[1:]):
            s.segs.append((a[0], a[1], b[0], b[1]))

    # power
    arr([(250, 195), (A, 195)], label="USB-C PD", lpos=(292, 185))
    arr([(A + 220, 195), (B, 195)], label="9 V", lpos=(620, 185))
    arr([(470, 240), (470, 320)], label="9 V", lpos=(480, 285), anchor="start")
    arr([(B + 220, 195), (C, 195)], label="V+ 5.1 V", lpos=(920, 185))
    arr([(1150, 240), (1150, 330)], color=BLUE, label="PWM x8", lpos=(1160, 280), anchor="start")
    arr([(1050, 240), (1050, 330)], label="V+ (1000 uF)", lpos=(1040, 280), anchor="end")
    arr([(A + 220, 365), (B, 365)], label="5 V (SS34)", lpos=(620, 355))
    arr([(470, 410), (470, 580)], label="5 V", lpos=(480, 500), anchor="start")
    arr([(A + 220, 625), (B, 625)], label="VBUS", lpos=(620, 615))
    # data
    arr([(820, 300), (820, 270), (1000, 270), (1000, 240)], color=BLUE, label="I2C + OE", lpos=(910, 262))
    arr([(770, 500), (770, 580)], color=BLUE, both=True, label="USB FS host", lpos=(780, 545), anchor="start")
    # UART crosses the 5 V feed: draw a hop
    s.path("M 250,470 L 460,470 A 10,10 0 0 1 480,470 L 660,470", 2.6, color=BLUE)
    arrow(s, [(600, 470), (B, 470)], color=BLUE, both=False)
    arrow(s, [(300, 470), (250, 470)], color=BLUE, both=False)
    s.segs.append((250, 470, 460, 470))
    s.segs.append((480, 470, 660, 470))
    s.text(292, 460, "UART 115200", 11, anchor="middle", color=BLUE, bold=True)
    arr([(B + 220, 610), (1360, 610), (1360, 530), (1450, 530)], label="USB-C cable: 5 V, 1.5 A charge",
        lpos=(1100, 600))
    arr([(B + 220, 650), (1380, 650), (1380, 560), (1450, 560)], color=BLUE, both=True,
        label="AOA bulk endpoints: JSON lines", lpos=(1100, 670))
    # wireless + mechanical
    arr([(1600, 370), (1600, 440)], color="#2e7d32", dash="7 5", both=True, label="Wi-Fi :8080",
        lpos=(1610, 410), anchor="start")
    arr([(1600, 590), (1600, 680)], color="#2e7d32", dash="7 5", both=True, label="HTTPS",
        lpos=(1610, 640), anchor="start")
    arr([(250, 705), (290, 705), (290, 820), (1420, 820), (1420, 580), (1450, 580)], color="#2e7d32",
        dash="7 5", both=True, label="Wi-Fi LAN: http://<head>:8080", lpos=(850, 810))
    arr([(1260, 400), (1350, 400), (1350, 315), (1450, 315)], color=GREY, w=3, label="stylus taps",
        lpos=(1400, 305))
    note_box(s, 40, 870, 1150, "Key facts", [
        "Single input: one USB-C PD charger powers servos, MCU and charges the head phone. No batteries in the body.",
        "ESP32-S3 is USB host on its native OTG pins and switches the Android head into Android Open Accessory mode;",
        "the DevKit's UART port exposes the same newline-JSON protocol for a PC. Servo outputs are disabled (PCA OE high)",
        "until firmware 'enable'; the watchdog parks and relaxes the arms after 5 s without commands.",
    ])
    return s


# ---- pinout -------------------------------------------------------------------------------
FUNC_COL = {"power": "#c62828", "i2c": "#1565c0", "usb": "#6a1b9a", "adc": "#2e7d32", "ctrl": "#ef6c00",
            "ui": "#00838f", "gnd": "#424242"}


def sig_cat(sig):
    return {"I2C_SDA": "i2c", "I2C_SCL": "i2c", "PCA_OE": "ctrl", "USB_DN": "usb", "USB_DP": "usb",
            "PHONE_VBUS_EN": "ctrl", "PHONE_VBUS_FLT": "ctrl", "VSERVO_SENSE": "adc", "VIN_SENSE": "adc",
            "STATUS_LED": "ui", "USER_BUTTON": "ui"}[sig]


def pinout() -> Sheet:
    s = Sheet("pinout", "ESP32-S3 DevKitC-1 pin map", "PM")
    s.text(30, 44, "ESP32-S3-DevKitC-1 PIN USAGE (top view, USB connectors at the bottom)", 15, bold=True)
    bx0, bx1, by0 = 760, 1040, 100
    P = 38
    s.rect(bx0, by0, bx1 - bx0, 22 * P + 40, stroke=INK, fill="#1b5e20", sw=2, rx=8, track=False)
    s.rect(bx0 + 40, by0 + 20, bx1 - bx0 - 80, 140, stroke="#ccc", fill="#cfd8dc", sw=1.5, rx=4, track=False)
    s.text((bx0 + bx1) / 2, by0 + 80, "ESP32-S3-WROOM-1", 13, anchor="middle", bold=True, track=False)
    s.text((bx0 + bx1) / 2, by0 + 98, "(antenna end)", 11, anchor="middle", color="#333", track=False)
    s.rect(bx0 + 60, by0 + 22 * P - 10, 60, 50, stroke="#999", fill="#b0bec5", sw=1.5, track=False)
    s.rect(bx1 - 120, by0 + 22 * P - 10, 60, 50, stroke="#999", fill="#b0bec5", sw=1.5, track=False)
    s.text(bx0 + 90, by0 + 22 * P + 22, "UART", 11, anchor="middle", bold=True, color="#263238", track=False)
    s.text(bx1 - 90, by0 + 22 * P + 22, "USB", 11, anchor="middle", bold=True, color="#263238", track=False)
    s.text((bx0 + bx1) / 2, by0 + 420, "J1 = left header", 12, anchor="middle", color="#fff", bold=True,
           track=False)
    s.text((bx0 + bx1) / 2, by0 + 440, "J3 = right header", 12, anchor="middle", color="#fff", bold=True,
           track=False)
    s.circle(bx0 + 70, by0 + 22 * P - 60, 8, stroke="#fff", fill="#fff59d")
    s.text(bx0 + 84, by0 + 22 * P - 56, "RGB LED", 10, color="#fff", track=False)
    s.circle(bx1 - 70, by0 + 22 * P - 60, 8, stroke="#fff", fill="#b0bec5")
    s.text(bx1 - 84, by0 + 22 * P - 56, "BOOT", 10, anchor="end", color="#fff", track=False)

    def tag(x, y, label, col, anchor):
        w = text_w(label, 11, True) + 14
        x0 = x if anchor == "start" else x - w
        s.rect(x0, y - 11, w, 20, stroke=col, fill=col, sw=1, rx=4, track=False)
        s.text(x0 + w / 2, y + 4, label, 11, anchor="middle", bold=True, color="#fff")
        return w

    for side, pins in (("L", DEVKIT_J1), ("R", DEVKIT_J3)):
        for i, n in enumerate(pins):
            y = by0 + 30 + i * P
            px = bx0 + 18 if side == "L" else bx1 - 18
            s.circle(px, y, 7, stroke="#fdd835", fill="#000", sw=2.5)
            net = devkit_net(n)
            if side == "L":
                s.text(bx0 - 10, y + 4, f"{i + 1}", 10, anchor="end", color=GREY)
                x = bx0 - 34
                s.text(x, y + 4, n, 12, anchor="end", bold=net is not None)
                x -= text_w(n, 12, True) + 12
                if n in DEVKIT_ALT:
                    s.text(x, y + 4, f"({DEVKIT_ALT[n]})", 10.5, anchor="end", color=GREY)
                    x -= text_w(f"({DEVKIT_ALT[n]})", 10.5) + 10
                if net:
                    cat = "power" if net in ("+3V3", "5V_DEVKIT") else ("gnd" if net == "GND" else sig_cat(net))
                    w = tag(x, y, net, FUNC_COL[cat], "end")
                    x -= w + 8
                    if net in GPIO_INFO:
                        s.text(x, y + 4, GPIO_INFO[net][1], 10.5, anchor="end", color="#333")
            else:
                s.text(bx1 + 10, y + 4, f"{i + 1}", 10, color=GREY)
                x = bx1 + 34
                s.text(x, y + 4, n, 12, bold=net is not None)
                x += text_w(n, 12, True) + 12
                if n in DEVKIT_ALT:
                    s.text(x, y + 4, f"({DEVKIT_ALT[n]})", 10.5, color=GREY)
                    x += text_w(f"({DEVKIT_ALT[n]})", 10.5) + 10
                if net:
                    cat = "power" if net in ("+3V3", "5V_DEVKIT") else ("gnd" if net == "GND" else sig_cat(net))
                    w = tag(x, y, net, FUNC_COL[cat], "start")
                    x += w + 8
                    if net in GPIO_INFO:
                        s.text(x, y + 4, GPIO_INFO[net][1], 10.5, color="#333")
    # legend
    lx, ly = 30, 1000
    s.text(lx, ly, "Legend:", 12, bold=True)
    xx = lx + 60
    for k, lab in (("power", "power"), ("gnd", "ground"), ("i2c", "I2C"), ("usb", "USB OTG"), ("adc", "ADC sense"),
                   ("ctrl", "control"), ("ui", "user I/O")):
        s.rect(xx, ly - 12, 16, 16, stroke=FUNC_COL[k], fill=FUNC_COL[k], track=False, sw=1)
        s.text(xx + 22, ly + 1, lab, 11.5)
        xx += 34 + text_w(lab, 11.5)
    s.text(lx, ly + 26, "Free for expansion: GPIO1, 2, 6, 11-14, 16-18, 21, 38-42, 47 (ADC-capable: 1, 2, 6, 11-14, 16-18).",
           11.5)
    s.text(lx, ly + 44, "Avoid: GPIO3/45/46 (straps), 35-37 (octal PSRAM on N16R8), 43/44 (UART0 console), "
           "19/20 (taken by USB host).", 11.5)
    s.text(lx, ly + 62, "* PSRAM pins used only on octal-PSRAM (R8) modules.  RGB LED: GPIO48 on DevKitC-1 v1.0, "
           "GPIO38 on v1.1 - check the silkscreen.", 11, color=GREY)
    return s


# ---- wiring harness ------------------------------------------------------------------------
# The base-bay geometry and the CAD-owned module positions are read from hardware/cad (build123d sources,
# parsed statically - build123d is NOT imported). Built-in defaults mirror CAD rev of 2026-10-11.
CAD_DIR = ROOT / "hardware" / "cad" / "tappet_cad"
CAD_DEFAULTS = dict(
    BASE_X0=-105.0, BASE_X1=105.0, BASE_Y0=-50.0, BASE_Y1=70.0, BASE_R=14.0, WALL=2.6, DECK_Z=30.0,
    DEVKIT=dict(cx=-23.0, y0=-46.6, w=25.5, l=69.0), PCA=dict(cx=30.0, cy=3.0, w=25.4, l=62.2),
    BUCKS=[dict(cx=70.0, cy=-29.5), dict(cx=70.0, cy=-1.0)], PDTRIG=dict(cx=40.0, y0=-46.6, w=10.0, l=20.0),
    COLUMN_HOLES=[(-17.0, -32.0), (17.0, -32.0), (-17.0, -8.0), (17.0, -8.0)], COLUMN_CABLE=(0.0, -20.0),
    GEAR_CENTER=25.0, DOVETAIL_X=(-30.0, 30.0), SOCKET_BLOCK=dict(dx=10.0, y0=54.0),
)


def load_cad_layout():
    import ast
    vals: dict = {}
    found = []
    for f in (CAD_DIR / "params.py", CAD_DIR / "parts" / "base.py"):
        if not f.exists():
            continue
        found.append(f.name)
        tree = ast.parse(f.read_text())
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            try:
                v = eval(compile(ast.Expression(node.value), str(f), "eval"),
                         {"dict": dict, "__builtins__": {}}, dict(vals))
            except Exception:
                continue
            for t in node.targets:
                if isinstance(t, ast.Name):
                    vals[t.id] = v
                elif isinstance(t, ast.Tuple) and isinstance(v, tuple):
                    for n_, vv in zip(t.elts, v):
                        if isinstance(n_, ast.Name):
                            vals[n_.id] = vv
    out = {}
    for k, d in CAD_DEFAULTS.items():
        out[k] = vals.get(k, d)
    out["_source"] = ("hardware/cad (" + ", ".join(found) + ")") if found else "built-in defaults (CAD not found)"
    return out


CAD = load_cad_layout()
BX0, BX1, BY0, BY1 = CAD["BASE_X0"], CAD["BASE_X1"], CAD["BASE_Y0"], CAD["BASE_Y1"]
BASE_W, BASE_D = BX1 - BX0, BY1 - BY0
SC = 4.2
HX, HY = 60, 112   # page position of the base rectangle's top-left corner = world (BX0, BY1)


def W2P(x, y):
    return HX + (x - BX0) * SC, HY + (BY1 - y) * SC


def _rect_c(cx, cy, w, h):
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


_dk, _pc, _pd, _bk = CAD["DEVKIT"], CAD["PCA"], CAD["PDTRIG"], CAD["BUCKS"]
# name: (x0, y0, x1, y1, title, lines, fill, owner)
MODULES = {
    "A1": (_dk["cx"] - _dk["w"] / 2, _dk["y0"], _dk["cx"] + _dk["w"] / 2, _dk["y0"] + _dk["l"], "A1",
           ["ESP32-S3", "DevKitC-1"], "#c8e6c9", "CAD"),
    "A2": (*_rect_c(_pc["cx"], _pc["cy"], _pc["w"], _pc["l"]), "A2", ["PCA9685", "+ adaptor:", "C4 C5 R7 R8",
                                                                      "C10 R11 TP2"], "#bbdefb", "CAD"),
    "U1": (_pd["cx"] - _pd["w"] / 2, _pd["y0"], _pd["cx"] + _pd["w"] / 2, _pd["y0"] + _pd["l"], "U1", ["+J1"],
           "#ffcdd2", "CAD"),
    "U2": (*_rect_c(_bk[0]["cx"] + 1.0, _bk[0]["cy"], 51, 26), "U2 BUCK A", ["XL4015 51x26"], "#ffcdd2", "CAD*"),
    "U3": (*_rect_c(_bk[1]["cx"], _bk[1]["cy"], 22, 17), "U3", ["Mini560"], "#ffcdd2", "CAD*"),
    "PB": (-7.0, -47.0, 33.0, -30.0, "PB power board", ["F1 D2 C2 C3 STAR D3", "R9 R10 C11 TP1 TP8"], "#fff9c4",
           "new"),
    "HB": (-95.0, -47.0, -65.0, -27.0, "HEAD board", ["J2 U4 U5", "R4-6 R12 R13", "C7-C9 TP4"], "#e1bee7", "new"),
}
ARMB = {"L": (-85.0, 40.0), "R": (85.0, 40.0)}
YAW_SERVOS = {a: (ARMB[a][0] + (CAD["GEAR_CENTER"] if a == "L" else -CAD["GEAR_CENTER"]), ARMB[a][1] - 6.0)
              for a in "LR"}   # SG90 body centre (pinion inboard of the turret axis)
TURRET_SLOT = {a: (ARMB[a][0], ARMB[a][1] - 28.0) for a in "LR"}

HARNESS = [
    # id, from, to, route (world mm), gauge, wire, connector, tag point
    ("W1", "U1 OUT (9 V) + GND", "PB VIN", [(35, -40), (33, -40)], "AWG20", "red/black", "solder", (28, -40)),
    ("W2", "PB VIN_9V + GND", "U2 IN+/IN-", [(33, -44), (45.5, -44), (46, -36)], "AWG20", "red/black",
     "screw term.", (51, -44)),
    ("W3", "U2 IN (daisy)", "U3 IN", [(90, -16.5), (90, -12), (81, -3)], "AWG20", "red/black", "screw term.",
     (94, -10)),
    ("W4", "U2 OUT+/OUT-", "A2 V+ terminal", [(96.5, -29.5), (99, -29.5), (99, 11), (45, 11), (42.7, 3)],
     "AWG20", "red/black", "screw term.", (99, -4)),
    ("W5", "U3 OUT (5 V) + GND", "PB 5V_LOGIC", [(59, -1), (50, -1), (50, -13), (44, -13), (33, -33)], "AWG22",
     "red/black", "JST-XH 2P", (50, -7)),
    ("W6", "PB 5V_LOGIC + GND", "HEAD board U4", [(-7, -44), (-65, -44)], "AWG22", "red/black", "JST-XH 2P",
     (-52, -44)),
    ("W7", "PB 5V_DEVKIT + GND", "A1 J1-21/22", [(-7, -40), (-36, -40)], "AWG24", "red/black", "Dupont 1x2",
     (-48, -40)),
    ("W8", "A1 3V3 GND 8 9 10 4", "A2 I2C header", [(-34, -14), (-8, -14), (-8, -29), (25, -29), (25, -28)],
     "AWG26", "6-way ribbon", "Dupont 1x6", (8, -29)),
    ("W9", "A1 J3-19/20 + GND", "HEAD board U5", [(-10, -35), (-65, -35)], "AWG26", "twisted trio", "Dupont 1x3",
     (-52, -35)),
    ("W10", "A1 GPIO7/15 + 3V3", "HEAD board U4", [(-36, 2), (-80, 2), (-80, -27)], "AWG26", "3-way ribbon",
     "Dupont 1x3", (-60, 2)),
    ("W11", "A1 GPIO5 + GND", "PB VIN divider", [(-34, 10), (-22, 10), (-22, -31), (-7, -31)], "AWG26",
     "2-way ribbon", "Dupont 1x2", (-22, -10)),
]
W_COLORS = {"AWG20": "#d32f2f", "AWG22": "#e57373", "AWG24": "#ef6c00", "AWG26": "#1565c0"}

# servo bundles from the A2 header edge (x = A2 x0): trunk + branch per joint; deck pass-through points
_ax0 = MODULES["A2"][0]
SERVO_BUNDLES = {
    "L arm (ch0-2)": ([(_ax0, 20), (5, 26), (-45, 28)],
                      {"L_YAW": [(-45, 28), YAW_SERVOS["L"]], "L_SHOULDER": [(-45, 28), (-72, 18), TURRET_SLOT["L"]],
                       "L_ELBOW": [(-45, 28), (-72, 18), TURRET_SLOT["L"]]}),
    "R arm (ch3-5)": ([(_ax0, 26), (12, 30), (12, 40), (50, 40)],
                      {"R_YAW": [(50, 40), YAW_SERVOS["R"]], "R_SHOULDER": [(50, 40), TURRET_SLOT["R"]],
                       "R_ELBOW": [(50, 40), TURRET_SLOT["R"]]}),
    "neck (ch6-7)": ([(_ax0, 14), (8, 0), CAD["COLUMN_CABLE"]],
                     {"NECK_PAN": [CAD["COLUMN_CABLE"]], "NECK_TILT": [CAD["COLUMN_CABLE"]]}),
}
RISE = {"L_YAW": 15, "R_YAW": 15, "L_SHOULDER": 100, "L_ELBOW": 110, "R_SHOULDER": 100, "R_ELBOW": 110,
        "NECK_PAN": 200, "NECK_TILT": 235}
LOOP = {"L_YAW": 20, "R_YAW": 20, "L_SHOULDER": 60, "L_ELBOW": 60, "R_SHOULDER": 60, "R_ELBOW": 60,
        "NECK_PAN": 40, "NECK_TILT": 80}
STOCK_LEAD = 250
EXT_SIZES = (100, 150, 200, 300)


def check_placement():
    names = list(MODULES)
    for i, a in enumerate(names):
        ra = MODULES[a]
        if ra[0] < BX0 + CAD["WALL"] - 0.5 or ra[2] > BX1 - CAD["WALL"] + 0.5 or \
                ra[1] < BY0 + CAD["WALL"] - 0.5 or ra[3] > BY1 - CAD["WALL"] + 0.5:
            fail(f"harness: module {a} outside the base inner wall")
        for b in names[i + 1:]:
            rb = MODULES[b]
            if "new" not in (ra[7], rb[7]):
                continue   # CAD-vs-CAD overlaps (stacked boards) are the CAD's business
            if ra[0] < rb[2] and rb[0] < ra[2] and ra[1] < rb[3] and rb[1] < ra[3]:
                fail(f"harness: added board {a if ra[7] == 'new' else b} overlaps {b if ra[7] == 'new' else a}")


def route_len(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def servo_lengths():
    """joint -> (needed lead length mm, extension mm or 0)."""
    out = {}
    for name, (trunk, branches) in SERVO_BUNDLES.items():
        for joint, br in branches.items():
            L = route_len(trunk) + route_len(br) + RISE[joint] + LOOP[joint] + 40  # 40: header + strain relief
            need = int(math.ceil(L / 10) * 10)
            ext = 0
            if need > STOCK_LEAD - 20:
                ext = next(e for e in EXT_SIZES if STOCK_LEAD + e >= need + 20)
            out[joint] = (need, ext)
    return out


def harness_len(pts):
    return int(math.ceil((route_len(pts) * 1.25 + 40) / 10.0) * 10)


def wiring_harness() -> Sheet:
    check_placement()
    s = Sheet("wiring_harness", "Wiring harness (base bay)", "WH")
    s.text(30, 44, f"WIRING HARNESS - base bay top view {BASE_W:.0f} x {BASE_D:.0f} mm (scale {SC:.1f} px/mm), "
           "front = tray / user at the top, +x = robot's right", 15, bold=True)
    s.text(30, 64, f"Bay outline and CAD-owned module positions read from {CAD['_source']}; PB and HEAD board "
           "are electronics additions.", 11, color=GREY)
    x0, y0 = W2P(BX0, BY1)
    s.rect(x0, y0, BASE_W * SC, BASE_D * SC, stroke=INK, fill="#fafafa", sw=2.5, rx=CAD["BASE_R"] * SC,
           track=False)
    w_ = CAD["WALL"] * SC
    s.rect(x0 + w_, y0 + w_, (BASE_W - 2 * CAD["WALL"]) * SC, (BASE_D - 2 * CAD["WALL"]) * SC, stroke="#bbb",
           fill="none", sw=1, rx=(CAD["BASE_R"] - CAD["WALL"]) * SC, track=False)
    s.text(x0 + BASE_W * SC / 2, y0 - 8, "FRONT (tray / user, +y)", 12, anchor="middle", bold=True, color=GREY)
    s.text(x0 + BASE_W * SC / 2, y0 + BASE_D * SC + 20,
           "REAR PANEL:  J2 HEAD (x -80)  |  A1 UART (flash)  |  U1+J1 PWR IN (x 40)", 12, anchor="middle",
           bold=True, color=GREY)
    sx, sy = x0 + BASE_W * SC - 50 * SC, y0 + BASE_D * SC + 44
    s.line(sx, sy, sx + 50 * SC, sy, 2, INK, track=False)
    for k in range(6):
        s.line(sx + k * 10 * SC, sy - 5, sx + k * 10 * SC, sy + 5, 1.5, INK, track=False)
    s.text(sx - 8, sy + 4, "50 mm", 11, anchor="end")
    # keep-outs and deck pass-throughs
    for a, (cx, cy) in ARMB.items():
        px, py = W2P(cx, cy)
        s.circle(px, py, 14 * SC, stroke="#9e9e9e", fill="#eeeeee", sw=1.5)
        s.circle(px, py, 7 * SC, stroke="#9e9e9e", fill="#fff", sw=1.2)
        s.text(px, py + 14 * SC + 14, f"{a} 608 boss", 10, anchor="middle", color="#555")
        tx, ty = TURRET_SLOT[a]
        q0 = W2P(tx - 7, ty + 3.5)
        s.rect(*q0, 14 * SC, 7 * SC, stroke="#555", fill="#fff", sw=1.2, rx=6, track=False, dash="3 2")
        s.text(q0[0] + 7 * SC, q0[1] - 5, f"{a} arm slot", 9.5, anchor="middle", color="#555")
        yx, yy = YAW_SERVOS[a]
        q = W2P(yx - 6.25, yy + 11.5)
        s.rect(*q, 12.5 * SC, 23 * SC, stroke="#8d6e63", fill="none", sw=1.4, dash="4 3", track=False)
        s.text(q[0] + 6.25 * SC, q[1] - 6, ("M1" if a == "L" else "M4") + " yaw", 10, anchor="middle",
               bold=True, color="#6d4c41")
    cxh = [h[0] for h in CAD["COLUMN_HOLES"]]
    cyh = [h[1] for h in CAD["COLUMN_HOLES"]]
    q = W2P(min(cxh) - 4, max(cyh) + 4)
    s.rect(*q, (max(cxh) - min(cxh) + 8) * SC, (max(cyh) - min(cyh) + 8) * SC, stroke="#9e9e9e", fill="none",
           sw=1.2, dash="6 4", track=False)
    cc = CAD["COLUMN_CABLE"]
    q = W2P(cc[0] - 6, cc[1] + 7)
    s.rect(*q, 12 * SC, 14 * SC, stroke="#555", fill="#fff", sw=1.2, rx=4, track=False, dash="3 2")
    for xc in CAD["DOVETAIL_X"]:
        q = W2P(xc - CAD["SOCKET_BLOCK"]["dx"], BY1 - CAD["WALL"])
        s.rect(*q, 2 * CAD["SOCKET_BLOCK"]["dx"] * SC, (BY1 - CAD["WALL"] - CAD["SOCKET_BLOCK"]["y0"]) * SC,
               stroke="#9e9e9e", fill="#eeeeee", sw=1.2, track=False)
    s.text(W2P(0, 60)[0], W2P(0, 60)[1] + 4, "tray dovetail sockets", 10, anchor="middle", color="#777")
    # modules (draw low ones first)
    for name in ("U1", "PB", "HB", "U2", "U3", "A2", "A1"):
        ax, ay, bx, by, title, lines, fill, owner = MODULES[name]
        px, py = W2P(ax, by)
        w, h = (bx - ax) * SC, (by - ay) * SC
        s.rect(px, py, w, h, stroke=INK, fill=fill, sw=1.6, rx=3, track=False,
               dash="5 3" if name == "A1" else None)
        n = 1 + len(lines)
        cy = py + h / 2 - (n - 1) * 6.5 + 4
        if name == "U1":
            cy = py + 18
        if name == "A1":
            cy = py + 14
        s.text(px + w / 2, cy, title, 11, anchor="middle", bold=True)
        for i, ln in enumerate(lines):
            s.text(px + w / 2, cy + 13 * (i + 1), ln, 9.5, anchor="middle", color="#333")
    # harness wires
    for wid, a, b, pts, g, col, conn, tag in HARNESS:
        s.wire(*[W2P(*p) for p in pts], w=3.2 if g == "AWG20" else 2.2, color=W_COLORS[g], track=False)
    # servo bundles
    for name, (trunk, branches) in SERVO_BUNDLES.items():
        s.wire(*[W2P(*p) for p in trunk], w=4, color="#f28c00", track=False)
        for joint, br in branches.items():
            Q = [W2P(*p) for p in br]
            if len(Q) > 1:
                s.wire(*Q, w=2, color="#f28c00", track=False)
            s.circle(*Q[-1], 4, stroke="#f28c00", fill="#f28c00")
    for wid, a, b, pts, g, col, conn, tag in HARNESS:
        tx, ty = W2P(*tag)
        tw = text_w(wid, 9.5, True) + 8
        s.rect(tx - tw / 2, ty - 8, tw, 16, stroke=W_COLORS[g], fill="#fff", sw=1.4, rx=8, track=False)
        s.text(tx, ty + 3.5, wid, 9.5, anchor="middle", bold=True, color=W_COLORS[g])
    s.text(W2P(-18, 32)[0], W2P(-18, 32)[1], "L arm bundle (ch0-2 + stylus GND)", 10, anchor="middle",
           color="#e65100", bold=True)
    s.text(W2P(28, 43)[0], W2P(28, 43)[1], "R arm bundle (ch3-5 + stylus GND)", 10, anchor="middle",
           color="#e65100", bold=True)
    s.text(W2P(9, 5)[0] - 4, W2P(9, 5)[1], "neck ch6-7", 10, anchor="end", color="#e65100", bold=True)
    s.text(W2P(9, 5)[0] - 4, W2P(9, 5)[1] + 12, "to column slot", 10, anchor="end", color="#e65100")

    # ---- tables (right)
    tx, ty = 1000, 92
    s.text(tx, ty, "Base harness", 13, bold=True, color=BLUE)
    cols = [tx, tx + 40, tx + 200, tx + 345, tx + 410, tx + 515, tx + 610]
    ty += 22
    for c, h in zip(cols, ["ID", "from", "to", "gauge", "wire", "connector", "length"]):
        s.text(c, ty, h, 11, bold=True)
    s.line(tx, ty + 6, tx + 670, ty + 6, 1, GREY, track=False)
    for wid, a, b, pts, g, col, conn, tag in HARNESS:
        ty += 18
        s.text(cols[0], ty, wid, 11, bold=True, color=W_COLORS[g])
        s.text(cols[1], ty, a, 10.5)
        s.text(cols[2], ty, b, 10.5)
        s.text(cols[3], ty, g, 10.5)
        s.text(cols[4], ty, col, 10.5)
        s.text(cols[5], ty, conn, 10.5)
        s.text(cols[6], ty, f"{harness_len(pts)} mm", 10.5)
    ty += 18
    s.text(tx, ty, "W12 (external): J2 -> head phone, 0.3 m USB 2.0 C-C cable, right-angle plug at the phone.",
           10.5, color="#6a1b9a")
    ty += 30
    s.text(tx, ty, f"Servo leads (stock {STOCK_LEAD} mm, from A2 headers)", 13, bold=True, color=BLUE)
    ty += 22
    cols2 = [tx, tx + 105, tx + 145, tx + 240, tx + 310, tx + 370, tx + 430]
    for c, h in zip(cols2, ["joint", "ch", "servo", "in base", "rise", "loop", "needed -> extension"]):
        s.text(c, ty, h, 11, bold=True)
    s.line(tx, ty + 6, tx + 670, ty + 6, 1, GREY, track=False)
    lens = servo_lengths()
    for ref, ch, joint, typ, loc in SERVOS:
        trunk, branches = next(v for v in SERVO_BUNDLES.values() if joint in v[1])
        ty += 18
        need, ext = lens[joint]
        s.text(cols2[0], ty, joint, 10.5, bold=True)
        s.text(cols2[1], ty, f"ch{ch}", 10.5)
        s.text(cols2[2], ty, f"{ref} {typ}", 10.5)
        s.text(cols2[3], ty, f"{route_len(trunk) + route_len(branches[joint]):.0f} mm", 10.5)
        s.text(cols2[4], ty, f"{RISE[joint]} mm", 10.5)
        s.text(cols2[5], ty, f"{LOOP[joint]} mm", 10.5)
        s.text(cols2[6], ty, f"{need} mm -> " + (f"+{ext} mm ext." if ext else "stock lead OK"), 10.5,
               color=RED if ext else INK)
    ty += 26
    note_box(s, tx, ty, 770, "Routing rules", [
        "!Power (W1-W4): AWG20 silicone, red/black twisted, bootlace ferrules in screw terminals.",
        "Servo current returns via W4 to U2, U2 IN- to the STAR (C2 -) on PB through W2. No extra A2-GND-to-star",
        "  wire (it would close a loop with the I2C ground in W8).",
        "!Signals (W7-W11): AWG24-26. A1 sits ~11 mm up on its headers, so W6/W7/W9/W11 pass under it.",
        "  W9 (USB D+/D-) twisted, < 15 cm end to end; keep W8/W9 >= 15 mm from the U2/U3 inductors.",
        "!Arms: each bundle (3 servo leads + 30 AWG stylus GND) rises through its deck arm slot into the",
        "  hollow turret; 60 mm service loop cable-tied at the slot, so the yaw range (joint -93..+23 deg,",
        "  up to 174 deg at the servo after 1.5x gearing) only flexes the loop, never the pins.",
        "!Column: neck leads rise through the column cable slot; 40 mm loop at pan, 80 mm at the tilt yoke.",
        "  The USB-C head cable (W12) runs from J2 on the rear panel up the column's rear cable channel.",
    ], size=11, lh=16)

    # ---- front elevation (x-z) -----------------------------------------------------------
    ez = 0.95
    DZ = CAD["DECK_Z"]
    ox, oy = 60 + 120 * ez + 40, 1045

    def E(x, z):
        return ox + x * ez, oy - z * ez

    s.text(60, 718, "Front elevation (schematic, z up) - cable paths", 13, bold=True, color=BLUE)
    s.rect(*E(BX0, DZ), BASE_W * ez, DZ * ez, stroke=INK, fill="#fafafa", sw=2, track=False)
    for sgn in (-1, 1):
        cx = 85 * sgn
        s.rect(*E(cx - 20, DZ + 25), 40 * ez, 25 * ez, stroke=INK, fill="#e0e0e0", sw=1.6, track=False)
        s.rect(*E(cx - 12, DZ + 47), 24 * ez, 22 * ez, stroke=INK, fill="#bbdefb", sw=1.4, track=False)
        s.line(*E(cx, DZ + 47), *E(cx + 30 * sgn, DZ + 115), 4, "#9e9e9e", track=False)
        s.line(*E(cx + 30 * sgn, DZ + 115), *E(cx + 60 * sgn, DZ + 15), 4, "#9e9e9e", track=False)
        s.line(*E(cx + 60 * sgn, DZ + 15), *E(cx + 60 * sgn, DZ - 15), 3, "#424242", track=False)
        s.wire(E(cx - 4 * sgn, 6), E(cx - 4 * sgn, DZ + 15), w=2.2, color="#f28c00", track=False)
        s.circle(*E(cx - 4 * sgn, DZ + 10), 7, stroke="#f28c00", sw=2)
        s.wire(E(cx - 4 * sgn, DZ + 15), E(cx - 4 * sgn, DZ + 37), w=2.2, color="#f28c00", track=False)
        s.wire(E(cx, 6), E(cx + 3 * sgn, DZ + 47), E(cx + 32 * sgn, DZ + 113), E(cx + 58 * sgn, DZ + 17), w=1.2,
               color="#424242", dash="3 3", track=False)
    s.rect(*E(-8, 210), 16 * ez, (210 - DZ) * ez, stroke=INK, fill="#e0e0e0", sw=1.6, track=False)
    s.rect(*E(-12, 222), 24 * ez, 14 * ez, stroke=INK, fill="#bbdefb", sw=1.4, track=False)
    s.rect(*E(-38, 300), 76 * ez, 62 * ez, stroke="#5e35b1", fill="#ede7f6", sw=1.6, track=False)
    s.text(E(0, 268)[0], E(0, 268)[1] + 4, "head phone", 10, anchor="middle", color="#5e35b1", track=False)
    s.wire(E(-3, 6), E(-3, 205), w=2.2, color="#f28c00", track=False)
    s.circle(*E(-3, 214), 6, stroke="#f28c00", sw=2)
    s.wire(E(4, 20), E(4, 236), w=2.2, color="#8e24aa", dash="5 4", track=False)
    s.text(E(25, 12)[0], E(25, 12)[1] + 4, "base bay", 10.5, color=GREY)
    lx = E(BX1, 0)[0] + 40
    for i, (t, c) in enumerate([("orange: servo leads up the arm slots / column, with service loops", "#e65100"),
                                ("purple dashed: USB-C head cable (J2 -> phone) up the column", "#6a1b9a"),
                                ("black dashed: 30 AWG stylus ground lead along each arm", "#424242"),
                                ("grey: arm links (schematic); blue: servos on turret / neck", "#555")]):
        s.text(lx, 790 + i * 18, t, 10.5, color=c)
    return s


# ---- power budget -----------------------------------------------------------------------
LOADS = {
    # name: (rail, idle_mA, typical_mA, peak_mA)
    "SG90 (yaw) x2": ("servo", 2 * 10, 2 * 10, 2 * 650),
    "MG90S (shoulder/elbow) x4": ("servo", 4 * 70, 4 * 70, 4 * 70),
    "MG90S (neck) x2": ("servo", 10 + 100, 10 + 100, 10 + 100),
}
SERVO = {
    "SG90": dict(idle=10, hold=40, moving=200, peak_move=350, stall=650),
    "MG90S": dict(idle=10, hold=70, moving=250, peak_move=400, stall=750),
}
EFF_A = 0.85   # XL4015 async buck 9 -> 5.1 V, 1-3 A
EFF_B = 0.92   # Mini560 sync buck 9 -> 5 V, 0.3-2 A
PD_W = 9 * 2.22
V_SERVO = 5.1
V_LOGIC = 5.0
ESP = dict(idle=0.10, typ=0.20, peak=0.35)   # A at 5 V (LDO: same current)
PCA = 0.010
PHONE = 1.5
PHONE_PEAK = 1.7


def scenarios():
    hold = {"L_YAW": "SG90", "R_YAW": "SG90"}
    types = {j: ("SG90" if "YAW" in j else "MG90S") for _, _, j, _, _ in SERVOS}

    def servo_current(moving: list[str], mode: str):
        tot = 0.0
        for j, t in types.items():
            d = SERVO[t]
            if j in moving:
                tot += d[mode]
            else:
                tot += d["idle"] if "YAW" in j else d["hold"]
        return tot / 1000.0

    S = []
    # name, servo A, logic A, phone A
    S.append(("Relaxed (OE high), phone charging", 8 * 0.005, ESP["typ"] + PCA, PHONE))
    S.append(("Enabled, holding pose, phone charging", servo_current([], "moving"), ESP["typ"] + PCA, PHONE))
    S.append(("Typical: 3 servos moving, phone charging", servo_current(["L_SHOULDER", "L_ELBOW", "L_YAW"], "moving"),
              ESP["typ"] + PCA, PHONE))
    S.append(("Accel. peak: 3 moving, phone charging", servo_current(["L_SHOULDER", "L_ELBOW", "NECK_TILT"], "peak_move"),
              ESP["peak"] + PCA, PHONE_PEAK))
    S.append(("Fault: 3 servos stalled, phone charging", servo_current(["L_SHOULDER", "L_ELBOW", "NECK_TILT"], "stall"),
              ESP["peak"] + PCA, PHONE_PEAK))
    S.append(("Fault: 3 stalled, phone power off (governor)",
              servo_current(["L_SHOULDER", "L_ELBOW", "NECK_TILT"], "stall"), ESP["peak"] + PCA, 0.0))
    out = []
    for name, i_s, i_l, i_p in S:
        p_s = V_SERVO * i_s
        p_l = V_LOGIC * i_l
        p_p = V_LOGIC * i_p
        loss = p_s * (1 / EFF_A - 1) + (p_l + p_p) * (1 / EFF_B - 1) + 0.15 + 0.35 * (i_l + i_p) * 0.25
        pin = p_s + p_l + p_p + loss
        out.append(dict(name=name, i_servo=i_s, i_logic=i_l, i_phone=i_p, p_servo=p_s, p_logic=p_l,
                        p_phone=p_p, p_loss=loss, p_in=pin, i_in=pin / 9.0))
    return out


def power_budget_svg(sc) -> Sheet:
    s = Sheet("power_budget", "Power budget", "PB", w=1800, h=1000)
    s.text(30, 44, "POWER BUDGET - input power at the 9 V PD contract, by scenario (stacked: where the watts go)",
           15, bold=True)
    cats = [("p_servo", "Servo rail (5.1 V)", "#2a78d6"), ("p_logic", "ESP32-S3 + PCA9685", "#eb6834"),
            ("p_phone", "Head phone charging", "#1baf7a"), ("p_loss", "Conversion + protection loss", "#eda100")]
    x0, y0, wmax = 470, 120, 900
    pmax = 35.0
    scale = wmax / pmax
    # grid
    for wv in range(0, 36, 5):
        x = x0 + wv * scale
        s.line(x, y0 - 10, x, y0 + len(sc) * 70, 1, "#e0e0e0", track=False)
        s.text(x, y0 + len(sc) * 70 + 18, f"{wv} W", 11, anchor="middle", color=GREY)
    for k, (lim, lab, col) in enumerate(((PD_W, "20 W charger (9 V x 2.22 A)", RED),
                                         (27.0, "27-30 W charger (9 V x 3 A)", "#7b1fa2"))):
        x = x0 + lim * scale
        s.line(x, y0 - 30, x, y0 + len(sc) * 70, 2, col, dash="6 4", track=False)
        s.text(x + 6, y0 - 34 + k * 16 - 0, lab, 11, color=col, bold=True)
    for i, r in enumerate(sc):
        y = y0 + i * 70 + 10
        if i == 0:
            s.text(x0 + wmax + 20, y0 - 14, "input", 12, bold=True, color=GREY)
        s.text(x0 - 14, y + 18, r["name"], 12, anchor="end", bold=True)
        s.text(x0 - 14, y + 34, f"servo {r['i_servo']:.2f} A | logic {r['i_logic']:.2f} A | phone {r['i_phone']:.1f} A",
               10.5, anchor="end", color=GREY)
        x = x0
        for key, lab, col in cats:
            w = r[key] * scale
            if w > 0.5:
                s.rect(x, y, max(w - 2, 0.5), 36, stroke=col, fill=col, sw=0, track=False, rx=2)
            x += w
        s.text(x0 + wmax + 20, y + 23, f"{r['p_in']:.1f} W", 13, bold=True)
        s.text(x0 + wmax + 80, y + 23, f"{r['i_in']:.2f} A @ 9 V", 12, color=GREY)
    # legend
    ly = y0 + len(sc) * 70 + 50
    lx = x0
    for key, lab, col in cats:
        s.rect(lx, ly - 12, 16, 16, stroke=col, fill=col, track=False, sw=0)
        s.text(lx + 22, ly + 1, lab, 12)
        lx += 50 + text_w(lab, 12)
    # table
    ty = ly + 40
    s.text(30, ty, "Per-load assumptions (5 V rail currents):", 13, bold=True, color=BLUE)
    rows = [
        ("SG90 (yaw x2)", "idle 10 mA | moving 150-250 (200 avg) | accel. 350 | stall 650+ mA"),
        ("MG90S (shoulder, elbow x4, neck x2)", "hold 70 mA | moving 250 avg | accel. 400 | stall 750+ mA"),
        ("ESP32-S3 DevKit (Wi-Fi off; USB host)", "100 mA idle | 200 mA typ | 350 mA peak (LDO -> same current at 5 V)"),
        ("PCA9685", "10 mA logic (3V3)"),
        ("Head phone (Rp 22k = 1.5 A advert.)", "1.5 A, TPS2553 limit 1.7 A typ"),
        ("Efficiency", f"Buck A {EFF_A:.0%} (async), Buck B {EFF_B:.0%} (sync), PTC/Schottky/LDO ~0.2-0.4 W"),
    ]
    for i, (a, b) in enumerate(rows):
        s.text(30, ty + 22 + i * 17, a, 11.5, bold=True)
        s.text(330, ty + 22 + i * 17, b, 11.5)
    s.text(1000, ty, "Rule:", 13, bold=True, color=BLUE)
    for i, ln in enumerate(["Firmware motion planner allows at most 3 servos moving at once.",
                            "Above ~18 W the 20 W charger is out of headroom: firmware",
                            "governor drops phone VBUS (EN low) while VIN_SENSE < 8.3 V",
                            "or V_SERVO < 4.6 V, and restores it 2 s after recovery."]):
        s.text(1000, ty + 22 + i * 17, ln, 11.5)
    return s


# =============================================================================
# 5. CSV OUTPUTS
# =============================================================================

ONBOARD_NETS = {"STATUS_LED"}   # terminates on a part inside A1 (on-board WS2812)


def write_netlist():
    rows = []
    for ref, c in COMPONENTS.items():
        for pin, net in c["pins"].items():
            rows.append((net, ref, pin))
    rows.sort(key=lambda r: (r[0] == "NC", r[0], r[1], r[2]))
    with open(HERE / "netlist.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["net", "ref", "pin"])
        w.writerows(rows)
    # connectivity sanity: every non-NC net has >= 2 pins, except test points / labels-only nets
    nets: dict[str, list] = {}
    for net, ref, pin in rows:
        nets.setdefault(net, []).append(ref)
    for net, refs in nets.items():
        if net != "NC" and len(refs) < 2 and net not in ONBOARD_NETS:
            fail(f"net {net} has a single connection ({refs})")
    return nets


def write_pinmap():
    rows = []
    for hdr, pins in (("J1", DEVKIT_J1), ("J3", DEVKIT_J3)):
        for i, n in enumerate(pins):
            net = devkit_net(n)
            if not net:
                continue
            gpio = n[4:] if n.startswith("GPIO") else ""
            info = GPIO_INFO.get(net, ("PWR" if net != "GND" else "GND", "power rail", ""))
            rows.append((gpio, net, info[0], info[1], f"{hdr}-{i + 1}", info[2]))
    with open(HERE / "pin_map.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["gpio", "signal_net", "direction", "function", "devkit_header_pin", "notes"])
        w.writerows(rows)


BOM = [
    # ref(s), qty, part, value/spec, example MPN / module, marketplace description, 1k USD, single USD, notes
    ("J1, J2", 2, "USB-C receptacle 16P, mid-mount/THT shell", "USB 2.0 + PD, 5 A VBUS", "GCT USB4105-GF-A / Korean Hroparts TYPE-C-31-M-12",
     "USB Type-C female 16pin SMD+THT shell", 0.18, 0.60, "J1 PWR IN (sink), J2 HEAD (source); THT shell pegs for strength"),
    ("U1", 1, "USB-PD sink trigger", "CH224K, 9 V via CFG1", "WCH CH224K (ESSOP-10) / 'CH224K PD decoy module'",
     "CH224K PD trigger board 5/9/12/15/20V selectable", 0.25, 1.50, "v0: module with solder jumper set to 9 V; v1: IC on the carrier PCB"),
    ("R1", 1, "Resistor 0603", "1 kOhm 5%", "generic", "0603 resistor", 0.002, 0.05, "CH224K VDD feed"),
    ("R2", 1, "Resistor 0603", "6.8 kOhm 1%", "generic", "0603 1% resistor", 0.002, 0.05, "CFG1 -> 9 V request"),
    ("R3", 1, "Resistor 0603", "4.7 kOhm", "generic", "0603 resistor", 0.002, 0.05, "PD OK LED"),
    ("D1", 1, "LED 0603 green", "2 mA", "generic", "0603 green LED", 0.01, 0.10, "PD OK"),
    ("F1", 1, "PTC resettable fuse 1812", "I_hold 3.0 A, I_trip ~5 A, >= 16 V", "Littelfuse 1812L300/16 class (verify suffix) / Bourns MF-MSMF300",
     "1812 PPTC 3A 16V", 0.12, 0.50, "ahead of TVS"),
    ("D2", 1, "TVS diode SMB, unidirectional", "SMBJ12A, 600 W", "Littelfuse SMBJ12A", "SMBJ12A TVS DO-214AA", 0.05, 0.30,
     "VRWM 12 V, VBR >= 13.3 V, Vc 19.9 V"),
    ("C2", 1, "Aluminium electrolytic", "470 uF 25 V, 10x12", "Panasonic EEU-FR1E471 / generic low-ESR", "470uF 25V radial low ESR",
     0.06, 0.30, "VIN bulk + STAR ground"),
    ("C3", 1, "MLCC 1206 X7R", "10 uF 25 V", "Samsung CL31B106KAHNNNE", "1206 10uF 25V X7R", 0.03, 0.15, ""),
    ("U2", 1, "Buck A module", "8-36 V in, 5.1 V out, CC 4.5 A (5 A rated)", "XL4015 CC/CV step-down module (DC-DC 5A)",
     "XL4015 5A DC-DC step-down CC CV module with potentiometers", 1.20, 2.50, "trim CV to 5.10 V, CC to 4.5 A BEFORE connecting servos; v1: TPS565208 5 A sync buck"),
    ("C4", 1, "Aluminium electrolytic low-ESR", "1000 uF 10 V (or 16 V), 10x16", "Panasonic EEU-FR1A102 / Rubycon ZLH",
     "1000uF 10V low ESR radial", 0.10, 0.40, "solder at PCA9685 V+ terminal (Adafruit footprint C2)"),
    ("C5, C8", 2, "MLCC 0805 X7R", "100 nF 50 V", "generic", "0805 100nF", 0.003, 0.05, ""),
    ("U3", 1, "Buck B module", "7-20 V in, 5.0 V fixed, 5 A rated (used <= 2.2 A)", "Mini560 Pro 5V (sync buck module)",
     "Mini560 step-down DC-DC 5V fixed output 5A", 0.80, 1.80, "fixed output: cannot be mis-trimmed into the phone"),
    ("C6", 1, "Aluminium electrolytic", "100 uF 10 V", "generic", "100uF 16V radial", 0.02, 0.10, "Buck B output"),
    ("D3", 1, "Schottky diode SMA", "SS34, 3 A 40 V", "onsemi SS34 / MDD SS34", "SS34 SMA schottky", 0.02, 0.10, "DevKit 5V feed, anti back-feed"),
    ("U4", 1, "USB current-limited power switch", "TPS2553, adj. 75 mA-1.7 A, active-high EN", "TI TPS2553DBVR (SOT-23-6)",
     "TPS2553DBVR power distribution switch", 0.45, 1.20, "alt: SY6280AAC (ILIM via resistor, check EN polarity)"),
    ("R4", 1, "Resistor 0603 1%", "15 kOhm", "generic", "0603 1%", 0.002, 0.05, "I_OS ~1.7 A typ (datasheet eq.)"),
    ("R5", 1, "Resistor 0603", "100 kOhm", "generic", "0603", 0.002, 0.05, "EN pull-down"),
    ("R6", 1, "Resistor 0603", "10 kOhm", "generic", "0603", 0.002, 0.05, "/FAULT pull-up"),
    ("C7", 1, "Al-polymer / electrolytic", "150 uF 10 V", "generic", "150uF 10V", 0.05, 0.20, ">= 120 uF downstream port"),
    ("C9", 1, "MLCC 0603 X7R", "1 uF 16 V", "generic", "0603 1uF", 0.003, 0.05, "U4 input"),
    ("C1", 1, "MLCC 0603 X7R", "1 uF 25 V", "generic", "0603 1uF 25V", 0.003, 0.05, "CH224K VDD"),
    ("A1", 1, "MCU dev board", "ESP32-S3-DevKitC-1 N8R2 (or N16R8)", "Espressif ESP32-S3-DevKitC-1-N8R2",
     "ESP32-S3-DevKitC-1 N8R2 dual USB-C", 3.80, 8.00, "clone boards OK if pinout identical; check LED pin (48 vs 38)"),
    ("A2", 1, "PWM driver breakout", "PCA9685 16-ch 12-bit, 0x40", "Adafruit 815 / generic PCA9685 servo board",
     "PCA9685 16 channel 12-bit PWM servo driver I2C", 1.30, 2.50, "has 220 ohm series R + reverse-pol FET on V+ terminal"),
    ("R7, R8", 2, "Resistor 0603 1%", "10 kOhm", "generic", "0603 1%", 0.002, 0.05, "VSERVO_SENSE divider"),
    ("R9", 1, "Resistor 0603 1%", "100 kOhm", "generic", "0603 1%", 0.002, 0.05, "VIN_SENSE top"),
    ("R10", 1, "Resistor 0603 1%", "22 kOhm", "generic", "0603 1%", 0.002, 0.05, "VIN_SENSE bottom"),
    ("C10, C11", 2, "MLCC 0603", "100 nF", "generic", "0603 100nF", 0.002, 0.05, "ADC filter"),
    ("R11", 1, "Resistor 0603", "2.2 kOhm", "generic", "0603", 0.002, 0.05, "PCA OE pull-up (beats on-board 10k pull-down)"),
    ("R12, R13", 2, "Resistor 0603 1%", "22 kOhm", "generic", "0603 1%", 0.002, 0.05, "Rp CC1/CC2 -> 1.5 A advert."),
    ("U5", 1, "USB ESD protection array", "USBLC6-2SC6", "ST USBLC6-2SC6 (SOT-23-6)", "USBLC6-2SC6 ESD", 0.06, 0.40, "at J2"),
    ("SW1", 1, "Tactile switch 6x6 / 12 mm panel", "momentary", "generic", "12mm momentary push button", 0.05, 0.30, "optional, parallel to BOOT"),
    ("TP1-TP9", 9, "Test point", "loop / turret", "Keystone 5000-series or bare wire loop", "PCB test point", 0.02, 0.10, ""),
    ("M1, M4", 2, "Servo, positional micro", "SG90 9 g, 1.8 kg.cm @ 4.8 V", "TowerPro SG90 or equivalent", "SG90 micro servo 180 deg", 0.90, 2.00, "yaw (geared 1.5x)"),
    ("M2, M3, M5-M8", 6, "Servo, positional micro metal gear", "MG90S 13.4 g, 2.2 kg.cm @ 4.8 V", "TowerPro MG90S or equivalent",
     "MG90S metal gear micro servo 180 deg", 1.80, 3.50, "shoulder, elbow, neck pan/tilt"),
    ("W-ext", "EXT", "Servo extension 3-wire", "EXT", "generic", "servo extension cable JR male-female",
     0.08, 0.30, "lengths computed from the harness model"),
    ("E1, E2", 2, "Capacitive stylus tip", "6 mm conductive rubber / fibre", "replacement stylus tip", "conductive stylus nib 6mm",
     0.10, 0.40, "with 30AWG flex lead to GND"),
    ("W-flex", 1, "Silicone wire 30 AWG", "1 m, black", "generic", "30AWG silicone wire", 0.05, 0.30, "stylus GND leads"),
    ("W-power", 1, "Silicone wire 20 AWG", "1 m red + 1 m black", "generic", "20AWG silicone wire", 0.20, 0.80, "servo rail, VIN"),
    ("W-sig", 1, "Dupont jumper / ribbon 26 AWG", "20 cm, F-F, 20 pcs", "generic", "dupont jumper wire 20cm", 0.15, 0.80, "logic harness"),
    ("J-VH", 4, "JST-VH 2P housing + header", "3.96 mm, 10 A", "JST VHR-2N / B2P-VH", "JST VH 2pin 3.96mm", 0.05, 0.20, "VIN + servo return"),
    ("CBL-HEAD", 1, "USB-C to USB-C cable, USB 2.0, thin silicone", "0.3 m, right-angle phone end, 3 A", "generic",
     "short silicone USB C-C cable 30cm right angle", 0.60, 2.50, "goes up the column to the head phone"),
    ("PCB-v0", 1, "Perfboard / protoboard", "70 x 50 mm, 2.54 mm, double-sided", "generic", "7x5cm double side prototype PCB", 0.10, 0.40,
     "v0 PB power board (cut to 40x17); v1 = custom 2-layer 70x50"),
    ("PCB-v0b", 1, "Perfboard offcut (HEAD board)", "30 x 20 mm", "generic", "prototype PCB offcut", 0.02, 0.10,
     "carries U4 U5 J2 R4-R6 R12 R13 C7-C9 next to the rear panel"),
    ("J-XH", 2, "JST-XH 2P housing + header", "2.5 mm, 3 A", "JST XHP-2 / B2B-XH-A", "JST XH 2pin 2.54mm", 0.03, 0.10,
     "W6 5V_LOGIC to HEAD board (<= 1.9 A)"),
    ("COPPER", 1, "Copper foil tape, conductive adhesive", "60 x 150 mm", "generic", "copper foil tape conductive adhesive", 0.05, 0.30,
     "tray floor ground pad (optional, improves touch registration)"),
    ("HW", 1, "M2/M3 standoffs, screws, heat-shrink, ferrules", "kit", "generic", "M2 nylon standoff kit", 0.20, 1.00, ""),
]


def write_bom():
    tot1k = tot1 = 0.0
    with open(HERE / "bom_electronics.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ref", "qty", "part", "value_spec", "example_mpn_or_module", "marketplace_description",
                    "unit_usd_1k", "unit_usd_single", "ext_usd_1k", "ext_usd_single", "notes"])
        exts = [e for _n, e in servo_lengths().values() if e]
        for ref, q, part, val, mpn, desc, c1k, c1, note in BOM:
            if q == "EXT":
                q = len(exts)
                val = "26 AWG JR, " + ", ".join(f"{e} mm x{exts.count(e)}" for e in sorted(set(exts)))
            w.writerow([ref, q, part, val, mpn, desc, f"{c1k:.3f}", f"{c1:.2f}", f"{q * c1k:.2f}", f"{q * c1:.2f}", note])
            tot1k += q * c1k
            tot1 += q * c1
        w.writerow(["TOTAL", "", "", "", "", "", "", "", f"{tot1k:.2f}", f"{tot1:.2f}",
                    "estimates; excludes PD charger (user-supplied) and PCB assembly"])
    # every designed component must be in the BOM
    bom_refs = set()
    for ref, *_ in BOM:
        for part in ref.replace(" ", "").split(","):
            if "-" in part and part[0].isalpha() and part.split("-")[0][-1].isdigit():
                a, b = part.split("-")
                pre = "".join(ch for ch in a if ch.isalpha())
                for n in range(int(a[len(pre):]), int(b[len(pre):] if b[0].isdigit() is False else b) + 1):
                    bom_refs.add(f"{pre}{n}")
            else:
                bom_refs.add(part)
    for ref in COMPONENTS:
        if ref not in bom_refs:
            fail(f"component {ref} missing from BOM")
    return tot1k, tot1


# =============================================================================
# 6. power_budget.md
# =============================================================================

def write_power_md(sc):
    lens = servo_lengths()
    t = []
    t.append("# Tappet power budget (rev 0.1, 2026-10-11)\n")
    t.append("*Generated by `gen_drawings.py` from the load model in that file - edit the model, not this file.*\n")
    t.append("![power budget](drawings/power_budget.png)\n")
    t.append("## 1. Input\n")
    t.append(f"* USB-C PD, CH224K requests **9 V**. A 20 W charger offers 9 V x 2.22 A = **{PD_W:.1f} W**; "
             "a 27-30 W charger offers 9 V x 3 A = 27 W. **Minimum 20 W, recommended 30 W.**")
    t.append("* F1 3 A-hold PTC limits sustained input to ~27 W regardless of charger; it trips at ~5 A.")
    t.append("* No 9 V PDO (5 V-only charger, or a USB-A to C cable): CH224K stays at 5 V. Buck A then drops out "
             "(~3.5 V servo rail) and Buck B is below its 7 V minimum. Firmware reads `VIN_SENSE` < 7.0 V and refuses "
             "`enable` (`fault{code:\"no_pd\"}`), keeping only the ESP32 alive for diagnostics over UART.\n")
    t.append("## 2. Per-load currents\n")
    t.append("| load | rail | idle | moving (avg) | accel. peak | stall | source / note |")
    t.append("|---|---|---|---|---|---|---|")
    t.append("| SG90 (L/R yaw, x2) | V_SERVO 5.1 V | 10 mA | 150-250 mA (200) | 350 mA | 650+ mA | yaw has no gravity load |")
    t.append("| MG90S (shoulders, elbows, x4) | V_SERVO | 70 mA holding | 250 mA | 400 mA | 750+ mA | holds arm weight |")
    t.append("| MG90S (neck pan / tilt, x2) | V_SERVO | 10 / 70 mA | 250 mA | 400 mA | 750+ mA | tilt carries the phone |")
    t.append("| ESP32-S3 DevKitC-1 | 5V_DEVKIT -> LDO 3V3 | 100 mA | 200 mA | 350 mA (radio TX burst, if Wi-Fi is used) | - | ESP32 Wi-Fi normally off: networking lives on the head |")
    t.append("| PCA9685 | +3V3 | 10 mA | 10 mA | 10 mA | - | |")
    t.append("| Head phone | VBUS_HEAD 5 V | 1.5 A | 1.5 A | 1.7 A (U4 limit) | - | Rp 22k advertises 1.5 A |")
    t.append("")
    t.append("Duty assumption: the firmware motion planner moves **at most 3 servos simultaneously**; the others hold. "
             "Enabling (`OE` low) staggers channels 80 ms apart so the 8 servos never seek at once.\n")
    t.append("## 3. Scenarios (computed)\n")
    t.append(f"Efficiencies: Buck A (XL4015, asynchronous) {EFF_A:.0%}, Buck B (Mini560, synchronous) {EFF_B:.0%}; "
             "fixed overhead 0.15 W (CH224K, LEDs, dividers) plus I^2R in F1/D3/U4.\n")
    t.append("| scenario | I servo | I logic | I phone | P servo | P logic | P phone | P loss | **P in** | I in @ 9 V | 20 W charger |")
    t.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sc:
        ok = "OK" if r["p_in"] <= PD_W * 0.9 else ("tight" if r["p_in"] <= PD_W else "**over**")
        t.append(f"| {r['name']} | {r['i_servo']:.2f} A | {r['i_logic']:.2f} A | {r['i_phone']:.1f} A | "
                 f"{r['p_servo']:.1f} W | {r['p_logic']:.1f} W | {r['p_phone']:.1f} W | {r['p_loss']:.1f} W | "
                 f"**{r['p_in']:.1f} W** | {r['i_in']:.2f} A | {ok} |")
    typ = sc[2]
    pk = sc[3]
    st = sc[4]
    t.append("")
    t.append("## 4. Headroom\n")
    t.append(f"* Typical operation draws **{typ['p_in']:.1f} W** - {PD_W - typ['p_in']:.1f} W "
             f"({(PD_W - typ['p_in']) / PD_W:.0%}) headroom on a 20 W charger.")
    t.append(f"* Acceleration peaks (~100-200 ms) reach **{pk['p_in']:.1f} W**: at the limit of a 20 W charger, "
             "comfortable on 30 W. PD sources tolerate short excursions (OCP is typically 110-150 % of the PDO), and C2 "
             "absorbs the sub-millisecond part.")
    t.append(f"* A 3-servo stall with the phone charging is **{st['p_in']:.1f} W** - over a 20 W budget. This is a fault "
             "case (jammed arm); see the governor below.")
    t.append("* Buck A: average 1-1.5 A, peaks 2.5 A, CC limit 4.5 A; 8 servos all stalled (~5.8 A) is clamped by the CC "
             "limit -> V_SERVO collapses -> `brownout` fault -> firmware relaxes (OE high).")
    t.append("* Buck B: <= 2.1 A (phone 1.7 A worst + logic 0.36 A) on a module rated 5 A: no heatsink needed.\n")
    t.append("## 5. Brownout behaviour and power governor (firmware)\n")
    t.append("1. `V_SERVO` (GPIO4, x2 divider) sampled at 100 Hz, 8-sample median. < **4.3 V** for 30 ms -> "
             "`fault{code:\"brownout\"}`, queue paused, servos hold (not relaxed) unless it persists > 500 ms, then OE high.")
    t.append("2. `VIN` (GPIO5, x(122/22) divider) < **8.3 V** for 50 ms while the phone is charging -> the charger is "
             "folding back: firmware drops `PHONE_VBUS_EN` for 2 s (phone sees a detach, keeps running on its battery), "
             "then re-enables. This keeps servo motion alive on a marginal 20 W charger.")
    t.append("3. `VIN` < 7.0 V at boot -> PD not negotiated -> refuse `enable`, LED amber blink, status `faults:[\"no_pd\"]`.")
    t.append("4. `PHONE_VBUS_FLT` low (GPIO15) -> U4 tripped (short in the head cable / phone) -> `fault{code:\"phone_ocp\"}`, "
             "EN low, retry after 5 s, give up after 3 tries.")
    t.append("5. The ESP32-S3 brown-out detector (2.7 V on 3V3) is the last line: D3 + DevKit LDO keep 3V3 in "
             "regulation down to ~4.0 V on 5V_LOGIC, far below any servo-induced dip, because servos are on a separate buck.\n")
    t.append("## 6. Thermal notes\n")
    t.append(f"* Buck A at {typ['p_servo']:.1f} W out / {EFF_A:.0%}: ~{typ['p_servo'] * (1 / EFF_A - 1):.1f} W dissipated typical, "
             f"~{st['p_servo'] * (1 / EFF_A - 1):.1f} W in the stall case. XL4015 module is fine up to ~2 W without "
             "extra heatsinking; give it 10 mm clearance and vent slots above it.")
    t.append(f"* Buck B: ~{(typ['p_logic'] + typ['p_phone']) * (1 / EFF_B - 1):.1f} W. U4 TPS2553: R_on 85 mOhm x 1.5^2 = 0.19 W "
             "(SOT-23-6, fine). D3: 0.35 V x 0.2 A = 0.07 W.")
    t.append("* DevKit LDO: (4.65 - 3.3) V x 0.2 A = 0.27 W - normal.")
    t.append("* Servo bodies: an MG90S holding the phone on NECK_TILT dissipates ~0.35 W continuously; `relax` when idle "
             "if the head is balanced (park pose is chosen to be gravity-neutral).")
    t.append("* Total heat in the base bay, typical: ~2.5 W. The 240x150 mm bay with vents needs no fan.\n")
    t.append("## 7. Servo lead lengths (from harness model)\n")
    t.append("| joint | needed (mm) | stock lead 250 mm | extension |")
    t.append("|---|---|---|---|")
    for _, ch, joint, _t, _l in SERVOS:
        need, ext = lens[joint]
        t.append(f"| {joint} | {need} | {'OK' if not ext else 'too short'} | {('+%d mm' % ext) if ext else 'none'} |")
    (HERE / "power_budget.md").write_text("\n".join(t) + "\n")


# =============================================================================
# 7. RASTERISE
# =============================================================================

def rasterise(svgs):
    tool = None
    if shutil.which("rsvg-convert"):
        tool = "rsvg"
    elif shutil.which("magick"):
        tool = "magick"
    else:
        try:
            import cairosvg  # noqa: F401
            tool = "cairosvg"
        except ImportError:
            pass
    if not tool:
        print("WARN: no SVG rasteriser found (rsvg-convert / magick / cairosvg) - SVG only")
        return []
    out = []
    for p in svgs:
        png = p.with_suffix(".png")
        if tool == "rsvg":
            subprocess.run(["rsvg-convert", "-b", "white", "-o", str(png), str(p)], check=True)
        elif tool == "magick":
            subprocess.run(["magick", "-density", "96", str(p), str(png)], check=True)
        else:
            import cairosvg
            cairosvg.svg2png(url=str(p), write_to=str(png))
        out.append(png)
    print(f"rasterised {len(out)} PNGs with {tool}")
    return out


# =============================================================================
def main():
    cfg = json.loads(CONFIG_PATH.read_text())
    cross_check(cfg)
    DRAW.mkdir(exist_ok=True)
    sc = scenarios()
    sheets = [sheet_power(), sheet_logic(), sheet_servos(), block_diagram(), wiring_harness(), pinout(),
              power_budget_svg(sc)]
    svgs = [s.save() for s in sheets]
    for ref in COMPONENTS:
        if ref not in DRAWN_REFS:
            fail(f"component {ref} is not drawn on any sheet")
    nets = write_netlist()
    write_pinmap()
    t1k, t1 = write_bom()
    write_power_md(sc)
    rasterise(svgs)
    print(f"components: {len(COMPONENTS)}  nets: {len(nets)}  BOM est: ${t1k:.2f} @1k, ${t1:.2f} single")
    for r in sc:
        print(f"  {r['name']:<48} {r['p_in']:5.1f} W  {r['i_in']:.2f} A")
    if ERRORS:
        print(f"\n{len(ERRORS)} CHECK FAILURES:")
        for e in ERRORS:
            print("  -", e)
        sys.exit(1)
    print("all cross-checks passed (config pins, dividers, servo map, drawn refs, BOM refs, layout)")


if __name__ == "__main__":
    main()
