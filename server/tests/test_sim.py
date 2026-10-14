import time

from labrat import sim


def test_sim_20_variants_fast_and_sane():
    goal = "make the most orange-looking well using red & yellow"
    vs = sim.generate_variants(goal, 20)
    t0 = time.perf_counter()
    rs = [sim.simulate(v["protocol"], goal) for v in vs]
    dt = time.perf_counter() - t0
    assert dt < 0.5, dt
    assert all(r["ok"] for r in rs), [r["errors"] for r in rs if not r["ok"]]
    best = max(rs, key=lambda r: r["score"])
    assert best["score"] > 0.85
    r_, g, b = best["predicted_rgb"]
    assert r_ > 180 and 60 < g < 200 and b < 60  # orange-ish
    assert best["frames"] and {"t", "cnc", "pipette", "wells"} <= set(best["frames"][-1])


def test_checks_catch_errors(monkeypatch):
    monkeypatch.setitem(sim.PLATE, "cap_ul", 400.0)   # a small beaker, so 450 uL overflows
    bad = [
        {"op": "aspirate", "src": "red", "ul": 50},  # no tip
        {"op": "pick_tip", "rack_well": "A1"},
        {"op": "aspirate", "src": "red", "ul": 150},
        {"op": "dispense", "dst": "A1", "ul": 150},
        {"op": "aspirate", "src": "yellow", "ul": 100},  # contamination
        {"op": "dispense", "dst": "A1", "ul": 100},
        {"op": "eject_tip"},
        {"op": "pick_tip", "rack_well": "A2"},
        {"op": "aspirate", "src": "blue", "ul": 200},
        {"op": "dispense", "dst": "A1", "ul": 200},  # overflow
        {"op": "eject_tip"},
        {"op": "pick_tip", "rack_well": "A1"},  # reused tip
    ]
    r = sim.simulate(bad, "make orange")
    errs = " ".join(r["errors"])
    assert not r["ok"]
    for frag in ("without tip", "contamination", "overflow", "already used"):
        assert frag in errs, (frag, errs)


def test_deck_matches_cad():
    """sim deck constants = cad/deck/deck.json (written by cad/deck/deck.py, the deck's source of truth)."""
    import json
    from pathlib import Path

    import pytest

    p = Path(__file__).resolve().parents[2] / "cad" / "deck" / "deck.json"
    if not p.exists():
        pytest.skip("cad/deck/deck.json not built")
    d = json.loads(p.read_text())
    b, t = d["beakers"], d["tip_rack"]
    assert sim.PLATE["origin"] == tuple(b["a1"]) and sim.PLATE["pitch"] == b["pitch"]
    assert (sim.PLATE["rows"], sim.PLATE["cols"]) == (b["rows"], b["mix_cols"])
    assert sim.TIP_RACK["origin"] == tuple(t["a1"]) and sim.TIP_RACK["pitch"] == t["pitch"]
    for name, well in b["stocks"].items():
        r, c = "ABCDEF".index(well[0]), int(well[1:]) - 1
        assert sim.RESERVOIRS[name] == (b["a1"][0] + c * b["pitch"], b["a1"][1] + r * b["pitch"])
    assert sim.SAFE_TIP_H == d["head"]["tip_end_at_z0"]
    assert -sim.BOUNDS["z"][0] == d["head"]["z_travel"]
    assert tuple(d["waste"]) == sim.WASTE and tuple(d["eject_bar"]) == sim.EJECT_BAR


def test_reach_limits():
    # aspirating a stock below the reachable level is an error; small mixes skip the mix step
    r = sim.simulate([{"op": "pick_tip", "rack_well": "A1"}, {"op": "aspirate", "src": "red", "ul": 200},
                      {"op": "dispense", "dst": "A1", "ul": 200}, {"op": "mix", "well": "A1"}, {"op": "eject_tip"}])
    assert r["ok"] and any("mix skipped" in w for w in r["warnings"])
    proto = [{"op": "pick_tip", "rack_well": "A1"}]
    for _ in range(18):
        proto += [{"op": "aspirate", "src": "blue", "ul": 200}, {"op": "dispense", "dst": "B2", "ul": 200}]
    r = sim.simulate(proto + [{"op": "eject_tip"}])
    assert not r["ok"] and any("only reaches liquid" in e for e in r["errors"])
