"""The `hub` device (bridge/labrat_bridge/servo_hub.py) against the in-process firmware emulation."""
import json
import threading
import time

import pytest

from labrat_bridge.common import DeviceError
from labrat_bridge.servo_hub import HubLink, MockHubFirmware, ServoHub, timeout_for


@pytest.fixture
def hub():
    h = ServoHub("hub", mock=True)
    events = []
    h.event_sink = lambda dev, ev: events.append((dev, ev))
    h.events = events
    yield h
    h.close()


def test_hello_info(hub):
    assert hub.status == "mock" and hub.kind == "servo_hub"
    assert hub.info["fw"].startswith("servo-hub/1.")
    assert hub.info["channels"] == 16
    assert "syr_home" in hub.info["inputs"] and "estop" in hub.info["inputs"]


def test_line_assigns_ids_and_strips_caller_id(hub):
    r = hub.a_line({"cmd": "ping", "id": 999, "timeout_ms": 500})
    assert r["ok"] is True and r["id"] != 999
    seen = hub.firmware.lines_seen[-1]
    assert "timeout_ms" not in seen and seen["id"] == r["id"]


def test_concurrent_motions_reply_on_completion(hub):
    out = {}

    def run(name, msg):
        t0 = time.monotonic()
        out[name] = (hub.a_line(msg), time.monotonic() - t0)

    ts = [threading.Thread(target=run, args=("a", {"cmd": "servo", "ch": 2, "deg": 30, "ms": 400})),
          threading.Thread(target=run, args=("b", {"cmd": "servo", "ch": 3, "deg": 150, "ms": 150}))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert out["a"][0]["deg"] == 30 and out["b"][0]["deg"] == 150
    assert 0.35 < out["a"][1] < 1.5 and 0.1 < out["b"][1] < out["a"][1]
    assert hub.firmware.us[2] == round(500 + 2000 * 30 / 180)


def test_preempted_and_errors_carry_the_reply(hub):
    res = {}

    def slow():
        try:
            hub.a_line({"cmd": "pulse", "ch": 4, "us": 1600, "ms": 2000})
        except DeviceError as e:
            res["e"] = e

    t = threading.Thread(target=slow)
    t.start()
    time.sleep(0.1)
    hub.a_line({"cmd": "off", "ch": 4})
    t.join(3)
    assert res["e"].data["err"] == "preempted"
    with pytest.raises(DeviceError) as ei:
        hub.a_line({"cmd": "servo", "ch": 0, "deg": 10})
    assert ei.value.data == {"id": ei.value.data["id"], "ok": False, "err": "kind"}
    with pytest.raises(DeviceError):
        hub.a_line({"nocmd": True})
    with pytest.raises(DeviceError):
        hub.a_line({"cmd": "cfg", "ch": 1, "pad": "x" * 300})  # > 256 bytes


def test_pulse_until_home_switch(hub):
    hub.firmware.plunger_mm = 0.5
    r = hub.a_line({"cmd": "pulse", "ch": 0, "us": 1200, "ms": 3000, "until": "syr_home"})
    assert r["stopped_by"] == "syr_home" and r["ms"] < 1000
    assert hub.firmware.us[0] == 1500  # back to neutral (rest_us)
    assert ("hub", {"ev": "in", "name": "syr_home", "v": 1}) in hub.events
    assert hub.state()["inputs"]["syr_home"] == 1
    r = hub.a_line({"cmd": "pulse", "ch": 0, "us": 1200, "ms": 3000, "until": "syr_home"})
    assert r["ms"] == 0 and r["stopped_by"] == "syr_home"  # already at the stop


def test_estop_event_and_refusal(hub):
    hub.a_line({"cmd": "servo", "ch": 1, "deg": 90})
    hub.firmware.set_input("estop", 1)
    time.sleep(0.05)
    assert ("hub", {"ev": "estop", "v": 1}) in hub.events
    assert hub.firmware.us[1] == 0
    with pytest.raises(DeviceError) as ei:
        hub.a_line({"cmd": "servo", "ch": 1, "deg": 10})
    assert ei.value.data["err"] == "estop"
    hub.firmware.set_input("estop", 0)
    assert hub.a_line({"cmd": "servo", "ch": 1, "deg": 10})["ok"]


def test_ping_only_while_linked(hub, monkeypatch):
    fw = hub.firmware
    hub.a_line({"cmd": "servo", "ch": 1, "deg": 45})
    hub.on_link(True)
    time.sleep(2.6)
    assert fw.wd_count == 0 and fw.us[1] > 0
    assert any(m == {"cmd": "ping"} for m in fw.lines_seen)
    hub.on_link(False)
    time.sleep(3.2)
    assert fw.wd_count == 1 and fw.us[1] == 0
    assert ("hub", {"ev": "wd"}) in hub.events


def test_cfg_roundtrip(hub):
    r = hub.a_line({"cmd": "cfg", "ch": 0, "kind": "cont", "rest_us": 1480})
    assert r["cfg"] == {"ch": 0, "min_us": 700, "max_us": 2300, "rest_us": 1480, "kind": "cont"}
    with pytest.raises(DeviceError):
        hub.a_line({"cmd": "cfg", "ch": 0, "rest_us": 2400})
    r = hub.a_read()
    assert len(r["ch"]) == 16 and set(r["inputs"]) >= {"syr_home", "estop"}


def test_timeout_for():
    assert timeout_for({"cmd": "ping"}) == 5.0
    assert timeout_for({"cmd": "servo", "ms": 2000}) == 7.0
    assert timeout_for({"cmd": "x", "timeout_ms": 250}) == 0.25


def test_link_timeout_and_close():
    class Silent:
        def __init__(self):
            self.ev = threading.Event()

        def readline(self):
            self.ev.wait()
            return b""

        def write(self, data):
            pass

        def close(self):
            self.ev.set()

    link = HubLink(Silent())
    with pytest.raises(DeviceError, match="no reply"):
        link.request({"cmd": "ping"}, timeout=0.2)
    res = {}

    def wait():
        try:
            link.request({"cmd": "ping"}, timeout=5)
        except DeviceError as e:
            res["e"] = str(e)

    t = threading.Thread(target=wait)
    t.start()
    time.sleep(0.1)
    link.close()
    t.join(2)
    assert "closed" in res["e"]
