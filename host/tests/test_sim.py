import json
import socket
import threading
import time

import pytest

from tappet.client import BodyClient, BodyError
from tappet.sim import FirmwareSim, QUEUE_DEPTH


def raw_conn(sim):
    s = socket.create_connection(("127.0.0.1", sim.port), timeout=5)
    f = s.makefile("rwb")
    boot = json.loads(f.readline())
    assert boot == {"evt": "boot", "fw": "tappet-fw", "ver": boot["ver"]}
    return s, f


def rpc(f, obj):
    f.write((json.dumps(obj) + "\n").encode())
    f.flush()
    while True:
        m = json.loads(f.readline())
        if "evt" not in m:
            return m


def test_hello_ping_errors(sim):
    s, f = raw_conn(sim)
    r = rpc(f, {"id": 1, "cmd": "hello"})
    assert r["ok"] and r["id"] == 1 and r["fw"] == "tappet-fw" and r["proto"] == 1 and r["servos"] == 8
    assert "uptime_ms" in r
    assert rpc(f, {"id": 2, "cmd": "ping"})["t"] >= 0
    f.write(b"{not json\n"); f.flush()
    r = json.loads(f.readline())
    assert r["ok"] is False and r["err"] == "bad_json"
    assert rpc(f, {"id": 3, "cmd": "dance"})["err"] == "unknown_cmd"
    assert rpc(f, {"id": 4, "cmd": "move", "arm": "X", "x": 0, "y": 0, "z": 0})["err"] == "bad_args"
    assert rpc(f, {"id": 5, "cmd": "tap", "x": 0, "y": 160})["err"] == "bad_args"
    assert rpc(f, {"id": 6, "cmd": "tap", "x": 0, "y": 160, "z": 13.8})["err"] == "not_enabled"
    assert rpc(f, {"id": 7, "cmd": "servo_raw", "ch": 0, "us": 1500})["err"] == "not_enabled"
    assert rpc(f, {"id": 8, "cmd": "enable"})["ok"]
    r = rpc(f, {"id": 9, "cmd": "move", "arm": "L", "x": 0, "y": 400, "z": 13.8})
    assert r["err"] == "unreachable"
    r = rpc(f, {"id": 10, "cmd": "ik", "arm": "R", "x": 0, "y": 160, "z": 13.8})
    assert r["q"]["YAW"] == pytest.approx(35.3112, abs=1e-3)
    r = rpc(f, {"id": 11, "cmd": "stream", "hz": 50})
    assert r["err"] == "bad_args"
    s.close()


def test_status_shape(body):
    st = body.status()
    for k in ("enabled", "busy", "queue", "joints", "tools", "vservo", "vin", "faults"):
        assert k in st
    assert set(st["joints"]) == {"L_YAW", "L_SHOULDER", "L_ELBOW", "R_YAW", "R_SHOULDER",
                                 "R_ELBOW", "NECK_PAN", "NECK_TILT"}
    assert len(st["tools"]["L"]) == 3


def test_joints_move_fk(body):
    body.enable()
    r = body.joints({"NECK_PAN": 30, "NECK_TILT": -50}, ms=200)
    assert r["queued"] is True
    st = body.status()
    assert st["joints"]["NECK_PAN"] == pytest.approx(30)
    assert st["joints"]["NECK_TILT"] == pytest.approx(-50)
    body.move("R", 20, 150, 30)
    assert body.fk("R") == pytest.approx((20, 150, 30), abs=0.01)
    # clamp to joint limits
    body.joints({"NECK_PAN": 500})
    assert body.status()["joints"]["NECK_PAN"] == pytest.approx(80)
    with pytest.raises(BodyError) as e:
        body.joints({"L_SHOULDER": 10, "L_ELBOW": 0})
    assert e.value.err == "limit"
    with pytest.raises(BodyError) as e:
        body.joints({"FOO": 1})
    assert e.value.err == "bad_args"


def test_min_jerk_timing(sim, body):
    body.enable()
    body.home()
    t0 = sim.sim.now_ms
    body.joints({"NECK_PAN": 60}, ms=1000)
    dt = sim.sim.now_ms - t0
    assert 1000 <= dt <= 1100
    # default duration from max_vel: 60 deg at 180 deg/s, peak = 1.875 x mean
    t0 = sim.sim.now_ms
    body.joints({"NECK_PAN": 0})
    dt = sim.sim.now_ms - t0
    assert 620 <= dt <= 720


def test_min_jerk_profile():
    sim = FirmwareSim(autostart=False)
    sim.handle_line(json.dumps({"id": 1, "cmd": "enable"}))
    sim.handle_line(json.dumps({"id": 2, "cmd": "joints", "q": {"NECK_PAN": 50}, "ms": 1000}))
    traj = []
    for _ in range(100):
        sim.run_ticks(1)
        traj.append(sim.q["NECK_PAN"])
    assert traj[49] == pytest.approx(25, abs=0.01)    # symmetric: half way at t = T/2
    assert traj[9] == pytest.approx(50 * (0.1 ** 3 * (10 - 1.5 + 0.06)), abs=0.01)
    assert traj[-1] == pytest.approx(50)


def test_tap_events_and_touch(sim, body):
    events = []
    body.on_event(events.append)
    body.enable()
    r = body.tap(-20, 180, 13.8)
    assert r["arm"] == "L" and r["queued"]
    assert any(e["evt"] == "done" and e["id"] == r["id"] for e in events)
    t = sim.sim.touchscreen.touches[-1]
    assert (t["wx"], t["wy"]) == pytest.approx((-20, 180), abs=1e-6)
    r = body.tap(20, 180, 13.8)
    assert r["arm"] == "R"
    r = body.tap(-20, 180, 13.8, arm="R")
    assert r["arm"] == "R"
    assert len(sim.sim.touchscreen.touches) == 3
    # tool ends at approach height above the target
    assert body.fk("R") == pytest.approx((-20, 180, 21.8), abs=0.01)


def test_tap_parks_other_arm(sim, body):
    body.enable()
    body.tap(-5, 160, 13.8, arm="L")
    body.tap(5, 160, 13.8, arm="R")      # L tool is within 60 mm -> parked first
    st = body.status()
    assert st["joints"]["L_YAW"] == pytest.approx(20)
    assert st["joints"]["L_SHOULDER"] == pytest.approx(110)


def test_swipe(sim, body):
    body.enable()
    n0 = len(sim.sim.touchscreen.touches)
    r = body.swipe(10, 120, 10, 200, 13.8, ms=300)
    assert r["arm"] == "R"
    touches = sim.sim.touchscreen.touches[n0:]
    assert len(touches) == 1   # one continuous contact
    assert (touches[0]["wx"], touches[0]["wy"]) == pytest.approx((10, 120), abs=1e-6)
    assert body.fk("R") == pytest.approx((10, 200, 21.8), abs=0.01)


def test_queue_and_stop(sim, body):
    body.enable()
    events = []
    body.on_event(events.append)
    ids = [body.send_motion("neck", pan=(i % 2) * 40, ms=2000)["id"] for i in range(QUEUE_DEPTH)]
    with pytest.raises(BodyError) as e:
        body.send_motion("neck", pan=0)
    assert e.value.err == "queue_full"
    assert body.status()["queue"] == QUEUE_DEPTH
    body.stop()
    time.sleep(0.05)
    st = body.status()
    assert st["queue"] == 0 and not st["busy"]
    cancelled = {e["id"] for e in events if e["evt"] == "error"}
    assert cancelled == set(ids)


def test_relax_clears_queue(sim, body):
    body.enable()
    body.send_motion("home", ms=5000)
    body.relax()
    st = body.status()
    assert not st["enabled"] and st["queue"] == 0


def test_watchdog(sim, body):
    events = []
    body.on_event(events.append)
    body.enable()
    body.watchdog(300)
    deadline = time.time() + 5
    while not any(e["evt"] == "watchdog" for e in events) and time.time() < deadline:
        time.sleep(0.01)
    assert any(e["evt"] == "watchdog" for e in events)
    st = body.status()
    assert st["enabled"] is False
    assert st["joints"]["L_YAW"] == pytest.approx(20) and st["joints"]["R_YAW"] == pytest.approx(-20)


def test_heartbeat_keeps_watchdog_alive(sim):
    c = BodyClient.connect(sim.url, heartbeat_s=0.002)  # ~0.1 s simulated at speed 50
    try:
        events = []
        c.on_event(events.append)
        c.enable()
        c.watchdog(3000)   # 60 ms real at speed 50: tolerant of scheduler jitter under load
        time.sleep(0.3)   # 15 s simulated
        assert not any(e["evt"] == "watchdog" for e in events)
        assert c.status()["enabled"]
    finally:
        c.close()


def test_stream(sim, body):
    events = []
    body.on_event(events.append)
    body.stream(20)
    time.sleep(0.2)   # 10 s simulated -> ~200 events
    body.stream(0)
    n = sum(1 for e in events if e["evt"] == "status")
    assert 100 <= n <= 260
    assert "joints" in body.last_status


def test_calib_geom(sim, body):
    j = body.calib_get()
    assert j["R_SHOULDER"]["dir"] == -1 and j["L_YAW"]["gear"] == 1.5
    body.calib_set({"L_YAW": {"s_zero": 93}}, save=True)
    assert body.calib_get()["L_YAW"]["s_zero"] == 93
    assert sim.sim.nvs["joints"]["L_YAW"]["s_zero"] == 93
    with pytest.raises(BodyError):
        body.calib_set({"L_YAW": {"bogus": 1}})
    g = body.geom_get()
    assert g["arms"]["L"]["L1"] == 100 and g["linkage"] == {"rel_min": 20, "rel_max": 160}
    body.geom_set(arms={"L": {"L1": 101}})
    assert body.geom_get()["arms"]["L"]["L1"] == 101
    body.reset_calib()
    assert body.calib_get()["L_YAW"]["s_zero"] == 90
    assert body.geom_get()["arms"]["L"]["L1"] == 100


def test_servo_raw(sim, body):
    body.enable()
    body.servo_raw(6, 1500 + 2000 / 180 * 10)   # NECK_PAN +10 deg
    assert body.status()["joints"]["NECK_PAN"] == pytest.approx(10, abs=0.01)
    with pytest.raises(BodyError):
        body.servo_raw(99, 1500)


def test_button_and_brownout(sim, body):
    events = []
    body.on_event(events.append)
    sim.sim.press_button("short")
    assert body.status()["enabled"]
    sim.sim.press_button("short")
    assert not body.status()["enabled"]
    sim.sim.set_vservo(4.0)
    time.sleep(0.05)
    assert any(e == {"evt": "fault", "code": "brownout"} for e in events)
    assert any(e.get("evt") == "button" and e["action"] == "short" for e in events)
    assert "brownout" in body.status()["faults"]
    sim.sim.set_vservo(5.0)
    assert body.status()["faults"] == []


def _find_bad_path(kin, arm="R"):
    import itertools
    pts = [(x, y, 30.0) for x in range(-40, 121, 20) for y in range(20, 241, 20)]
    ok = [p for p in pts if kin.reachable(arm, *p)]
    for a, b in itertools.combinations(ok, 2):
        mids = [tuple(a[i] + f / 20 * (b[i] - a[i]) for i in range(3)) for f in range(21)]
        if any(not kin.reachable(arm, *m) for m in mids):
            return a, b
    return None


def test_runtime_error_event(sim, body):
    """move checks only the endpoint at enqueue; a path through a limit fails at run time with
    an error event carrying the request id."""
    a, b = _find_bad_path(sim.sim.kin)
    body.enable()
    body.move("R", *a)
    events = []
    body.on_event(events.append)
    with pytest.raises(BodyError) as e:
        body.move("R", *b)
    assert e.value.err in ("limit", "unreachable")
    assert any(ev["evt"] == "error" and ev["err"] == e.value.err for ev in events)
