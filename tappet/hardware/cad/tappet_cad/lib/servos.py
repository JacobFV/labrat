"""SG90 / MG90S micro-servo models, horns and mounting cut-outs.

Servo frame: output shaft axis = +z through the origin (x=y=0); body bottom at z=0; the body's long
axis is x, the shaft is offset toward +x (body spans x in [-(L/2+off), L/2-off]).
"""
from __future__ import annotations

from dataclasses import dataclass

from build123d import Part, Pos, Sketch

from .util import box, cyl_z, rbox, stadium, circle_at, polygon_face
from build123d import extrude


@dataclass(frozen=True)
class ServoSpec:
    name: str
    body_l: float = 22.5      # along x
    body_w: float = 12.2      # along y
    body_h: float = 22.7      # bottom -> top of case (under the tab plate is at tab_z)
    tab_span: float = 32.3
    tab_z: float = 15.9       # underside of the mounting tabs
    tab_t: float = 2.5
    hole_spacing: float = 27.8
    hole_d: float = 2.0
    shaft_off: float = 5.25   # shaft distance from body centre along +x
    boss_r: float = 5.9
    boss_h: float = 4.0
    shaft_d: float = 4.6      # spline OD (mock, slightly under real 4.8 so printed press bores don't clash)
    shaft_h: float = 3.3
    horn_hub_r: float = 3.6
    horn_hub_z0: float = 27.0
    horn_top: float = 32.5    # top face of the horn arm (servo frame z)
    horn_arm_t: float = 1.5
    horn_arm_len: float = 16.0
    color: str = "#2f6fd6"

    @property
    def body_x0(self):
        return -(self.body_l / 2 + self.shaft_off)

    @property
    def body_x1(self):
        return self.body_l / 2 - self.shaft_off

    @property
    def body_cx(self):
        return -self.shaft_off

    @property
    def hole_xs(self):
        return (self.body_cx - self.hole_spacing / 2, self.body_cx + self.hole_spacing / 2)

    @property
    def tab_x0(self):
        return self.body_cx - self.tab_span / 2

    @property
    def tab_x1(self):
        return self.body_cx + self.tab_span / 2

    @property
    def boss_top(self):
        return self.body_h + self.boss_h

    @property
    def shaft_top(self):
        return self.boss_top + self.shaft_h


SG90 = ServoSpec("SG90")
MG90S = ServoSpec("MG90S", body_l=22.8, body_h=22.7, tab_span=32.5, color="#3a3a3a")


def servo_model(s: ServoSpec) -> Part:
    """Mock servo (no horn)."""
    hw = s.body_w / 2
    body = box(s.body_x0, s.body_x1, -hw, hw, 0, s.body_h)
    tabs = box(s.tab_x0, s.tab_x1, -hw, hw, s.tab_z, s.tab_z + s.tab_t)
    for hx in s.hole_xs:
        tabs = tabs - cyl_z(s.hole_d / 2, s.tab_z - 1, s.tab_z + s.tab_t + 1, x=hx)
    boss = cyl_z(s.boss_r, s.body_h, s.boss_top)
    boss2 = cyl_z(2.8, s.body_h, s.body_h + 2.0, x=-7.6)
    shaft = cyl_z(s.shaft_d / 2, s.boss_top, s.shaft_top)
    return body + tabs + boss + boss2 + shaft


def horn_sketch(s: ServoSpec, clearance: float = 0.0) -> Sketch:
    """Single-arm horn outline in the horn plane, arm along +x."""
    c = clearance
    pts = [(0, -3.1 - c), (s.horn_arm_len, -2.0 - c), (s.horn_arm_len, 2.0 + c), (0, 3.1 + c)]
    sk = Sketch() + polygon_face(pts)
    sk = sk + circle_at((0, 0), s.horn_hub_r + c) + circle_at((s.horn_arm_len, 0), 2.0 + c)
    return sk


def horn_model(s: ServoSpec) -> Part:
    """Horn mock in the servo frame (hub on the shaft, arm along +x)."""
    arm = Pos(0, 0, s.horn_top - s.horn_arm_t) * extrude(horn_sketch(s), s.horn_arm_t)
    hub = cyl_z(s.horn_hub_r, s.horn_hub_z0, s.horn_top)
    hub = hub - cyl_z(s.shaft_d / 2 + 0.05, s.horn_hub_z0 - 1, s.shaft_top)
    return arm + hub


def horn_pocket(s: ServoSpec, depth_from_top: float, clearance: float = 0.2) -> Part:
    """Recess (servo frame) that a printed link uses to key onto the horn: from horn_top down
    ``depth_from_top`` into the link, i.e. the region z in [horn_top - horn_arm_t, horn_top + ...].
    Returned as solid to subtract, expressed in the servo frame, spanning z [horn_top - horn_arm_t - 0.2,
    horn_top + depth_from_top]."""
    z0 = s.horn_top - s.horn_arm_t - 0.2
    return Pos(0, 0, z0) * extrude(horn_sketch(s, clearance), depth_from_top + s.horn_arm_t + 0.2)


def horn_screw_holes(s: ServoSpec) -> list[float]:
    return [6.0, 9.5, 13.0]
