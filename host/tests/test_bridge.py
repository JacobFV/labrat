import json
import urllib.error
import urllib.request

import pytest

from tappet.bridge import sim_bridge


@pytest.fixture(scope="module")
def bridge(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bridge")
    srv, br = sim_bridge(port=0, speed=60, pin="123456", calib_path=tmp / "cal.json",
                         report_dir=tmp, log=lambda s: None)
    br.start()
    yield br
    br.close()
    srv.close()


def call(br, method, path, body=None, token=None, raw=False):
    url = f"http://127.0.0.1:{br.port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Tappet-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = r.read()
            return r.status, (payload if raw else json.loads(payload)), r.headers
    except urllib.error.HTTPError as e:
        payload = e.read()
        return e.code, (payload if raw else json.loads(payload)), e.headers


@pytest.fixture(scope="module")
def token(bridge):
    st, r, _ = call(bridge, "POST", "/api/pair", {"pin": "123456"})
    assert st == 200
    return r["token"]


def test_pairing_and_auth(bridge):
    st, r, _ = call(bridge, "POST", "/api/pair", {"pin": "000000"})
    assert st == 403 and r["err"] == "bad_pin"
    st, r, _ = call(bridge, "GET", "/api/status")
    assert st == 401 and r["err"] == "unauthorized"
    st, r, _ = call(bridge, "GET", "/api/status", token="nope")
    assert st == 401


def test_status_and_cmd(bridge, token):
    st, r, _ = call(bridge, "GET", "/api/status", token=token)
    assert st == 200
    assert r["link"]["connected"] is True
    assert "joints" in r["body"] and r["calib"]["H_sw"]
    assert r["agent"]["available"] is False
    st, r, _ = call(bridge, "GET", f"/api/status?token={token}")
    assert st == 200
    st, r, _ = call(bridge, "POST", "/api/cmd", {"cmd": "enable"}, token=token)
    assert st == 200 and r["ok"] is True
    st, r, _ = call(bridge, "POST", "/api/cmd", {"cmd": "nope"}, token=token)
    assert st == 200 and r["ok"] is False and r["err"] == "unknown_cmd"


def test_tap_swipe_type_neck_jog(bridge, token):
    call(bridge, "POST", "/api/cmd", {"cmd": "enable"}, token=token)
    seq0 = bridge.touch_log.last_seq
    st, r, _ = call(bridge, "POST", "/api/tap", {"u": 0.25, "v": 0.5}, token=token)
    assert st == 200 and r["ok"] and r["arm"] == "L"
    st, r, _ = call(bridge, "GET", f"/api/touchpad/events?since={seq0}", token=token)
    assert len(r["events"]) == 1
    assert (r["events"][0]["u"], r["events"][0]["v"]) == pytest.approx((0.25, 0.5), abs=1e-4)
    st, r, _ = call(bridge, "POST", "/api/swipe", {"u0": 0.5, "v0": 0.8, "u1": 0.5, "v1": 0.3, "ms": 300},
                    token=token)
    assert st == 200 and r["ok"]
    st, r, _ = call(bridge, "POST", "/api/type", {"text": "Hi 2"}, token=token)
    assert st == 200 and r["taps"] == 7 and r["skipped"] == ""   # shift,h,i,space,123,2,abc
    st, r, _ = call(bridge, "POST", "/api/neck", {"pan": 10, "tilt": -40}, token=token)
    assert st == 200
    st, r0, _ = call(bridge, "POST", "/api/jog", {"arm": "R", "dx": 0, "dy": 0, "dz": 0}, token=token)
    st, r, _ = call(bridge, "POST", "/api/jog", {"arm": "R", "dx": 5, "dy": 0, "dz": 2}, token=token)
    assert st == 200
    assert (r["x"], r["z"]) == pytest.approx((r0["x"] + 5, r0["z"] + 2), abs=0.01)
    st, r, _ = call(bridge, "POST", "/api/jog", {"arm": "R", "dx": 0, "dy": 500, "dz": 0}, token=token)
    assert st == 400 and r["err"] == "unreachable"
    st, r, _ = call(bridge, "POST", "/api/tap", {"u": "x", "v": 0.5}, token=token)
    assert st == 400 and r["err"] == "bad_args"


def test_touchpad_event_no_token(bridge, token):
    st, r, _ = call(bridge, "POST", "/api/touchpad/event", {"x": 196.5, "y": 426, "w": 393, "h": 852, "t": 1})
    assert st == 200 and r["ok"]
    st, r, _ = call(bridge, "GET", f"/api/touchpad/events?since={r['seq'] - 1}", token=token)
    assert r["events"][0]["u"] == pytest.approx(0.5) and r["last"] >= 1
    st, r, _ = call(bridge, "POST", "/api/touchpad/event", {"x": 1})
    assert st == 400


def test_calibration_endpoints(bridge, token):
    st, cal, _ = call(bridge, "GET", "/api/calibration", token=token)
    assert st == 200 and cal["source"] == "default"
    st, r, _ = call(bridge, "POST", "/api/calibration",
                    {"camera_corners": [[10, 10], [300, 12], [310, 600], [5, 590]], "camera_size": [320, 640]},
                    token=token)
    assert st == 200 and r["H_ic"] is not None
    assert bridge.calib_path.exists()
    st, r, _ = call(bridge, "POST", "/api/calibration", {"H_sw": [[0, 0, 0], [0, 0, 0], [0, 0, 0]]}, token=token)
    assert st == 400
    st, r, _ = call(bridge, "POST", "/api/calibration", {"reset": True}, token=token)
    assert r["H_ic"] is None


def test_autocal_and_taptest_endpoints(bridge, token):
    st, r, _ = call(bridge, "POST", "/api/calibrate/auto", {}, token=token)
    assert st == 200 and r["ok"] and len(r["points"]) == 9 and r["rms_mm"] < 0.05
    st, r, _ = call(bridge, "POST", "/api/taptest", {"n": 10}, token=token)
    assert st == 200
    res = r["results"]
    assert res["n"] == 10 and res["registration_rate"] == 1.0 and len(res["samples"]) == 10
    st, r, _ = call(bridge, "POST", "/api/taptest", {"n": 0}, token=token)
    assert st == 400


def test_not_implemented(bridge, token):
    st, r, _ = call(bridge, "GET", "/api/camera.jpg?rectified=1", token=token)
    assert st == 501 and r["err"] == "not_implemented"
    st, r, _ = call(bridge, "POST", "/api/chat", {"text": "hi"}, token=token)
    assert st == 501
    st, r, _ = call(bridge, "POST", "/api/say", {"text": "hi"}, token=token)
    assert st == 200
    st, r, _ = call(bridge, "GET", "/api/nope", token=token)
    assert st == 404


def test_static(bridge):
    if not (bridge.web / "index.html").exists():
        pytest.skip("web/ not present")
    st, body, h = call(bridge, "GET", "/", raw=True)
    assert st == 200 and b"<html" in body.lower() and h["Content-Type"].startswith("text/html")
    st, body, h = call(bridge, "GET", "/touchpad.html", raw=True)
    assert st == 200
    st, body, h = call(bridge, "GET", "/app.js", raw=True)
    assert st == 200 and "javascript" in h["Content-Type"]
    st, body, h = call(bridge, "GET", "/../config/robot.json", raw=True)
    assert st == 404
