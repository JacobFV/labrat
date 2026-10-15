"""uv run pytest  (from apps/cli)"""
import asyncio
import json

import websockets

from labrat_cli import main as cli

DEVICES = {
    "t": "devices",
    "providers": [{"pid": "b1", "role": "bridge", "at": 1, "devices": ["cnc", "so_a"]},
                  {"pid": "p1", "role": "phone", "at": 2, "devices": ["hub"]}],
    "devices": [
        {"id": "cnc", "kind": "grbl", "status": "ok", "state": {"wpos": [1, 2, 3], "state": "Idle"}},
        {"id": "so_a", "kind": "so101", "status": "mock", "state": {}},
        {"id": "hub", "kind": "servo_hub", "status": "ok", "info": {"transport": "ble", "fw": "servo-hub/1.0"},
         "state": {"inputs": {"syr_home": 0}}},
        {"id": "pipette", "kind": "servo_syringe", "status": "ok",
         "state": {"pos_mm": 4.5, "stroke_mm": 40, "vol_ul": 120, "homed": True, "moving": False}},
        {"id": "probe", "kind": "servo_arm", "status": "ok", "state": {"deployed": False, "deg": 10}},
        {"id": "stand", "kind": "pan_tilt", "status": "offline", "state": {"pan": 0, "tilt": -20, "moving": True}},
    ],
    "phones": 1, "services": {"llm": True},
}


def test_dev_summary_kinds():
    d = {x["id"]: x for x in DEVICES["devices"]}
    assert cli.dev_summary(d["cnc"]) == "(1.0,2.0,3.0) Idle"
    assert cli.dev_summary(d["pipette"]) == "120.0 µl @ 4.50/40.0 mm"
    assert cli.dev_summary(d["probe"]) == "retracted 10°"
    assert cli.dev_summary(d["stand"]) == "pan 0° tilt -20° · moving"
    assert cli.dev_summary(d["hub"]) == "ble servo-hub/1.0 syr_home=0"
    assert cli.dev_summary({"id": "x", "kind": "?", "state": {}}) == ""


def test_handle_devices_and_toolbar():
    app = cli.App("http://127.0.0.1:1", "t")
    app.handle(DEVICES)
    assert set(app.devices) == {"cnc", "so_a", "hub", "pipette", "probe", "stand"}
    bar = "".join(t for _, t in app.toolbar())
    assert "bridge on" in bar and "phone on" in bar and "pipette 120.0 µl" in bar
    app.handle({"t": "devices", "providers": [], "devices": {"cnc": {"status": "offline"}}})
    assert app.devices["cnc"]["id"] == "cnc"
    assert "bridge off" in "".join(t for _, t in app.toolbar())


def test_status_and_chat_over_fake_ws(capsys):
    got = []

    async def peer(ws):
        await ws.send(json.dumps(DEVICES))
        async for raw in ws:
            m = json.loads(raw)
            got.append(m)
            if m["t"] == "chat":
                await ws.send(json.dumps({"t": "chat", "role": "user", "text": m["text"]}))
                await ws.send(json.dumps({"t": "chat", "role": "tool", "text": "pipette.aspirate(ul=50)", "meta": {"tool": "pipette.aspirate", "ok": True}}))
                await ws.send(json.dumps({"t": "chat", "role": "agent", "text": "Done."}))

    async def go():
        async with websockets.serve(peer, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            app = cli.App(f"http://127.0.0.1:{port}", "t")
            app.replay = False
            await app.command("/status")
            await app.chat_once("take 50 µl")
            await app.http.aclose()

    asyncio.run(go())
    out = capsys.readouterr().out
    for want in ("pipette", "servo_syringe", "120.0 µl", "probe", "retracted", "stand", "offline", "providers bridge:b1",
                 "phone:p1", "llm=on", "pipette.aspirate", "Done."):
        assert want in out, want
    assert {"t": "chat", "text": "take 50 µl"} in got
