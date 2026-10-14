import math

import pytest

from labrat import deck_cal, sim


@pytest.fixture(autouse=True)
def tmp_cal(tmp_path, monkeypatch):
    monkeypatch.setattr(deck_cal, "PATH", tmp_path / "deck_cal.json")
    deck_cal.reset()
    yield
    deck_cal.reset()


def test_uncalibrated_is_nominal():
    assert sim.well_xy("A1", sim.PLATE) == (153.25, 32.0)
    assert sim.well_xyz("F7", sim.PLATE, sim.Z_WELL) == (153.25 + 6 * 19, 32.0 + 5 * 19, sim.Z_WELL)
    assert sim.loc("red")[1:] == (153.25 + 7 * 19, 51.0, sim.Z_RES)        # stock beaker B8
    with pytest.raises(ValueError):
        sim.well_xy("G1", sim.PLATE)                                        # 6 rows
    with pytest.raises(ValueError):
        sim.well_xy("A8", sim.PLATE)                                        # column 8 = stocks
    assert sim.eject_xyz() == (10.0, 170.0, -20.0)


def test_offset_roundtrip():
    s = (100.0, 50.0, -10.0)
    assert deck_cal.spindle_to_tip(*s) == s  # default: syringe tip on the spindle axis
    deck_cal.update({"pipette_offset": {"dy": -45.0}})  # an off-axis tool still works
    t = deck_cal.spindle_to_tip(*s)
    assert t == (100.0, 5.0, -10.0)
    assert deck_cal.tip_to_spindle(*t) == s


def test_teach_single_corner_nominal_pitch():
    tip = deck_cal.teach("plate.a1", (150.0, 70.0, -30.0))  # spindle
    assert (tip["x"], tip["y"], tip["z"]) == (150.0, 70.0, -30.0)
    # spindle back out for A1, nominal 19 mm beaker pitch for others
    assert sim.well_xyz("A1", sim.PLATE, -99) == pytest.approx((150.0, 70.0, -30.0))
    assert sim.well_xyz("B3", sim.PLATE, -99) == pytest.approx((188.0, 89.0, -30.0))


def test_two_corners_rotation_and_pitch():
    th = math.radians(2.0)
    p = 9.1
    a1 = (30.0, 40.0)
    u, v = 11 * p, 7 * p
    h12 = (a1[0] + u * math.cos(th) - v * math.sin(th), a1[1] + u * math.sin(th) + v * math.cos(th))
    deck_cal.update({"points": {"tip_rack.A1": {"x": a1[0], "y": a1[1], "z": -38},
                                "tip_rack.H12": {"x": h12[0], "y": h12[1], "z": -38}}})
    f = deck_cal.grid_frame("tip_rack")
    assert f["pitch"] == pytest.approx(p)
    assert f["theta_deg"] == pytest.approx(2.0)
    # C5 in tip coords -> spindle (syringe is coaxial with the spindle clamp: offset 0)
    r, c = 2, 4
    ex = a1[0] + c * p * math.cos(th) - r * p * math.sin(th)
    ey = a1[1] + c * p * math.sin(th) + r * p * math.cos(th)
    assert sim.well_xyz("C5", sim.TIP_RACK, 0) == pytest.approx((ex, ey, -38))


def test_persist_and_reload(tmp_path):
    deck_cal.update({"pipette_offset": {"dy": -44.5}, "points": {"waste": {"x": 280, "y": 120, "z": -5}}})
    deck_cal.reset()
    cal = deck_cal.load()
    assert cal["pipette_offset"]["dy"] == -44.5
    assert sim.loc("waste")[1:] == pytest.approx((280.0, 164.5, -5.0))


def test_sim_uses_calibrated_deck():
    deck_cal.update({"points": {"plate.A1": {"x": 150, "y": 10, "z": -30}}})
    prot = sim.protocol_from_ratios(["red", "yellow"], [0.5, 0.5], 100, "A1")
    r = sim.simulate(prot, "orange")
    xs = [(f["cnc"]["x"], f["cnc"]["y"]) for f in r["frames"]]
    assert (150.0, 10.0) in xs
