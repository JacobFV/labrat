import json
from pathlib import Path

import pytest

from labrat import deck_cal, sim, tooling


@pytest.fixture(autouse=True)
def tmp_cal(tmp_path, monkeypatch):
    monkeypatch.setattr(deck_cal, "PATH", tmp_path / "deck_cal.json")
    deck_cal.reset()
    yield
    deck_cal.reset()


def test_tool_offsets_match_cad():
    p = Path(__file__).resolve().parents[2] / "cad" / "syringe" / "tools.json"
    if not p.exists():
        pytest.skip("cad/syringe/tools.json not built")
    cad = json.loads(p.read_text())["tools"]
    for name, nom in deck_cal.TOOLS_NOMINAL.items():
        assert [nom["dx"], nom["dy"], nom["dz"]] == pytest.approx(cad[name]["offset"], abs=1e-3), name
    assert cad["pipette"]["offset"] == [0.0, 0.0, 0.0]


def test_plan_moves_the_spindle_by_the_tool_offset():
    x, y = sim.well_xy("C3", sim.PLATE)
    p = tooling.plan("pipette", "C3")
    assert p["spindle"][:2] == pytest.approx([x, y])
    m = tooling.plan("microscope", "C3", z=-10)
    o = deck_cal.tool_offset("microscope")
    assert m["spindle"] == pytest.approx([round(x - o[0], 2), round(y - o[1], 2), round(-10 - o[2], 2)])
    # a taught tool offset moves the plan
    deck_cal.update({"tools": {"microscope": {"dx": -10.0}}})
    assert tooling.plan("microscope", "C3", z=-10)["spindle"][0] == pytest.approx(round(x + 10.0, 2))


def test_reach_is_reported():
    # the microscope hangs 63 mm in front of the spindle: the back rows are out of its reach
    far = tooling.plan("microscope", x=150.0, y=170.0)
    assert not far["reachable"] and "y=" in far["why"]
    with pytest.raises(ValueError):
        tooling.plan("laser", "A1")
