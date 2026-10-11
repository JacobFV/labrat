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


def test_checks_catch_errors():
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
