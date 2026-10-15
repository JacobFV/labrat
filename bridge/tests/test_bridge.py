"""All-mock bridge vs a tiny fake /ws/bridge server: one command per device + validation + events + watchdog."""
import asyncio
import json
import os
import uuid

os.environ.setdefault("LABRAT_MOCK_SPEEDUP", "20")

from websockets.asyncio.server import serve

from labrat_bridge.bridge import ALL_IDS, Bridge, bridge_url, build_devices
from labrat_bridge.grbl import validate_gcode
from labrat_bridge.common import DeviceError

CMDS = [
    ("so_a", "pose", {"name": "ready", "ms": 200}),
    ("so_b", "pose", {"name": "rest", "ms": 200}),
    ("so_b", "joints", {"q": {"shoulder_pan": 10, "gripper": 30}, "ms": 200}),
    ("so_b", "gripper", {"open": 1.0, "ms": 100}),
    ("so_a", "read", {}),
    ("hub", "line", {"msg": {"cmd": "hello"}}),
    ("hub", "line", {"msg": {"cmd": "cfg", "ch": 1, "kind": "pos"}}),
    ("hub", "line", {"msg": {"cmd": "servo", "ch": 1, "deg": 120, "ms": 200}}),
    ("hub", "line", {"msg": {"cmd": "servo", "ch": 2, "deg": 60, "ms": 150}}),
    ("hub", "line", {"msg": {"cmd": "pulse", "ch": 0, "us": 700, "ms": 4000, "until": "syr_home"}}),
    ("hub", "read", {}),
    ("cnc", "home", {}),
    ("cnc", "move", {"x": 20, "y": 20, "z": -5, "f": 3000}),
    ("cnc", "jog", {"dx": 1}),
    ("cnc", "gcode", {"lines": ["G90 G21", "G0 X30 Y25", "G1 Z-10 F200", "G4 P0.1"]}),
    ("cnc", "status", {}),
]
BAD = [
    ("cnc", "move", {"x": 400, "y": 0, "z": 0}),
    ("cnc", "gcode", {"lines": ["M8"]}),
    ("so_a", "pose", {"name": "nope"}),
    ("hub", "line", {"msg": {"cmd": "servo", "ch": 0, "deg": 90}}),     # ch0 is continuous: firmware says "kind"
    ("hub", "line", {"msg": {"nocmd": 1}}),
    ("nope", "x", {}),
]


def test_validate_gcode():
    out, segs, end, _ = validate_gcode(["G0 X10 Y10 F5000", "G91", "G1 Z-5 F900"], (0, 0, 0))
    assert out[0].endswith("F1500") and "G1" in out[0]
    assert out[-1].endswith("F500") and end == (10, 10, -5)
    for bad in (["G1 Z5"], ["$J=X1"], ["G1 X301"], ["M30"], ["G1 A5"]):
        try:
            validate_gcode(bad, (0, 0, 0))
        except DeviceError:
            continue
        raise AssertionError(f"accepted {bad}")


def test_bridge_all_mock():
    asyncio.run(asyncio.wait_for(_run(), 60))


async def _run():
    got: dict = {"hello": None, "states": 0, "results": {}, "events": []}
    done = asyncio.Event()
    token_seen = []

    async def handler(ws):
        token_seen.append(ws.request.path)
        hello = json.loads(await ws.recv())
        got["hello"] = hello
        ids = {}
        for dev, act, args in CMDS + BAD:
            rid = str(uuid.uuid4())
            ids[rid] = (dev, act)
            await ws.send(json.dumps({"t": "cmd", "id": rid, "device": dev, "action": act, "args": args}))
            if dev in ("cnc", "so_a", "so_b"):
                # wait for result to keep per-device order deterministic
                while rid not in got["results"]:
                    m = json.loads(await ws.recv())
                    if m["t"] == "state":
                        got["states"] += 1
                    elif m["t"] == "result":
                        got["results"][m["id"]] = (ids[m["id"]], m)
        while len(got["results"]) < len(ids):
            m = json.loads(await ws.recv())
            if m["t"] == "state":
                got["states"] += 1
                got["last_state"] = m
            elif m["t"] == "result":
                got["results"][m["id"]] = (ids[m["id"]], m)
            elif m["t"] == "event":
                got["events"].append(m)
        # an input change on the hub comes up as an event
        devs["hub"].firmware.set_input("in1", 1)
        while not any(e["data"].get("name") == "in1" for e in got["events"]):
            m = json.loads(await ws.recv())
            if m["t"] == "event":
                got["events"].append(m)
        done.set()

    devs = build_devices(set(ALL_IDS))
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        br = Bridge(devs, bridge_url(f"ws://127.0.0.1:{port}", "labrat"))
        task = asyncio.create_task(br.run())
        await done.wait()
        # leaving the context closes the server -> link loss -> watchdog after 3 s
    await asyncio.sleep(4.0)
    br.stop_event.set()
    task.cancel()

    assert "token=labrat" in token_seen[0]
    assert {d["id"] for d in got["hello"]["devices"]} == set(ALL_IDS)
    assert all(d["status"] == "mock" for d in got["hello"]["devices"])
    res = list(got["results"].values())
    ok = [(k, m) for k, m in res if m["ok"]]
    bad = [(k, m) for k, m in res if not m["ok"]]
    for k, m in res:
        print(k, "OK" if m["ok"] else "ERR", json.dumps(m.get("data") or m.get("error"))[:110])
    assert len(ok) == len(CMDS), [(k, m.get("error")) for k, m in bad]
    assert len(bad) == len(BAD)
    assert got["states"] >= 3
    cnc = devs["cnc"].state()
    assert cnc["wpos"] == [30.0, 25.0, -10.0]
    hub_ok = {json.dumps(m["data"].get("stopped_by")) for (d, a), m in res if d == "hub" and m["ok"]}
    assert '"syr_home"' in hub_ok, "the mock plunger reaches its bottom stop and ends the pulse early"
    kind_err = [m for (d, a), m in res if d == "hub" and not m["ok"] and "kind" in m["error"]]
    assert kind_err and kind_err[0]["data"]["err"] == "kind", "a firmware ok:false reply comes back as data"
    ev = [e for e in got["events"] if e["device"] == "hub"]
    assert any(e["data"] == {"ev": "in", "name": "syr_home", "v": 1} for e in ev)
    assert any(e["data"] == {"ev": "in", "name": "in1", "v": 1} for e in ev)
    hub = devs["hub"]
    assert hub.state()["inputs"]["in1"] == 1
    # link lost: the bridge stops pinging, the (mock) firmware watchdog relaxes everything by itself
    assert hub.link_up is False
    assert hub.firmware.wd_count >= 1
    assert all(us == 0 for us in hub.firmware.us)
    assert br.safe_done
    for d in devs.values():
        d.close()


def test_probe_and_microscope_mock(monkeypatch):
    import base64

    from labrat_bridge.grbl import MockGrbl
    from labrat_bridge.microscope import Microscope

    monkeypatch.setenv("LABRAT_MOCK_PROBE_Z", "-12.5")
    cnc = MockGrbl(speedup=200)
    cnc.a_move(x=50, y=40, z=-5)
    r = cnc.a_probe(dz=-20, f=100)
    assert r["hit"] and r["z"] == -12.5 and cnc.pos[2] == -5.0          # retracted to the start height
    r = cnc.a_probe(dz=-3)                                              # surface out of range: no hit, no alarm
    assert not r["hit"] and r["z"] is None
    m = Microscope(mock=True)
    s = m.a_snap(width=64, height=48)
    assert s["mime"] == "image/png" and (s["w"], s["h"]) == (64, 48)
    assert base64.b64decode(s["image_b64"])[:8] == b"\x89PNG\r\n\x1a\n"
