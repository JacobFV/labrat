"""uv run pytest  (from apps/cli)"""
import asyncio
import json

import httpx
import pytest
import websockets

from labrat_cli import Lab, LabError
from labrat_cli import main as cli

EXP = {
    "id": "exp_abc123", "title": "Orange sweep", "status": "done", "target": {"name": "orange", "rgb": [255, 140, 0]},
    "steps": [{"op": "pick_tip"}] * 4, "step": 4, "error": None, "log": [{"msg": "photo"}],
    "check": {"ok": True, "errors": [], "warnings": ["A2 is shallow"], "duration_s": 412.3, "tips_used": ["A1", "B1"], "model_v": 3},
    "beakers": [
        {"well": "A1", "label": None, "water": 0, "red": 20, "yellow": 180, "blue": 0, "predicted_rgb": [240, 205, 4],
         "measured_rgb": [236, 199, 30], "error": 26.8, "predicted_to_target": 66.3, "measured_to_target": 70.1, "rating": 4, "note": "nice"},
        {"well": "B1", "label": "rep", "water": 0, "red": 100, "yellow": 100, "blue": 0, "predicted_rgb": [250, 150, 4],
         "measured_rgb": None, "error": None, "predicted_to_target": 12.0, "measured_to_target": None, "rating": None, "note": None},
    ],
    "closest_predicted": "B1", "closest_measured": "A1",
}


def fake_server(log: list):
    """A tiny stand-in for the Worker's REST routes."""
    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        log.append((req.method, req.url.path, body, dict(req.url.params), req.headers.get("authorization")))
        p = req.url.path
        if p == "/api/experiments" and req.method == "POST":
            if not body.get("samples"):
                return httpx.Response(400, json={"error": "give samples or steps"})
            return httpx.Response(200, json={**EXP, "status": "ready", "samples": body["samples"]})
        if p == "/api/experiments":
            return httpx.Response(200, json=[EXP])
        if p.endswith("/run"):
            return httpx.Response(200, json={**EXP, "status": "running" if body["auto"] else "awaiting_approval"})
        if p == "/api/state":
            return httpx.Response(200, json={"active": [{"id": "exp_w", "title": "t", "status": "awaiting_approval", "step": 0, "n_steps": 4}]})
        if p.endswith("/approve"):
            return httpx.Response(200, json={**EXP, "id": p.split("/")[3], "status": "running"})
        if p.endswith(".csv"):
            return httpx.Response(200, text="experiment,well\nexp_abc123,A1\n", headers={"content-type": "text/csv"})
        if p.endswith("/results"):
            return httpx.Response(200, json={"results": body["results"], "model": {"v": 4, "accepted": True}})
        if p == "/api/notes":
            return httpx.Response(200, json=[{"text": "orange is red 100", "experiment_id": "exp_abc123"}])
        if p == "/api/experiments/exp_abc123":
            return httpx.Response(200, json=EXP)
        if p.startswith("/api/experiments/"):
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(404, json={"error": "no route"})
    return httpx.MockTransport(h)


def test_lab_client():
    log = []
    lab = Lab("http://x", token="tok", transport=fake_server(log))
    e = lab.new("Orange sweep", target="orange", samples=[{"red": r, "yellow": 200 - r} for r in range(20, 200, 20)])
    assert e["status"] == "ready" and len(e["samples"]) == 9
    assert log[-1][2] == {"title": "Orange sweep", "target": "orange", "samples": e["samples"]} and log[-1][4] == "Bearer tok"
    assert lab.run(e["id"])["status"] == "awaiting_approval"
    assert lab.run(e["id"], approve=True, wait=True)["status"] == "done"  # wait polls GET until finished
    assert lab.approve()["id"] == "exp_w"  # no id: the one waiting, from /api/state
    assert lab.csv(e["id"]).startswith("experiment,well")
    assert lab.results(e["id"], [{"well": "A1", "rgb": [1, 2, 3]}])["model"]["accepted"]
    assert log[-1][2] == {"results": [{"well": "A1", "rgb": [1, 2, 3]}]}
    lab.notes("orange")
    assert log[-1][3] == {"q": "orange"}
    with pytest.raises(LabError, match="give samples or steps"):
        lab.new("empty")
    with pytest.raises(LabError, match="not found"):
        lab.get("exp_nope")


def test_sweep_and_rgb():
    target, s = cli.sweep("orange", 3)
    assert target == "orange" and s == [{"red": 150, "yellow": 50}, {"red": 100, "yellow": 100}, {"red": 50, "yellow": 150}]
    assert cli.sweep("blue+water", 1, 100) == (None, [{"blue": 50, "water": 50}])
    with pytest.raises(ValueError):
        cli.sweep("red+ink")
    assert cli.parse_rgb("#ff8800") == [255, 136, 0] == cli.parse_rgb("255,136,0")
    assert cli.parse_rgb("-") is None


def test_show_exp_and_commands(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.console, "width", 200)
    log = []
    app = cli.App("http://x", "t")
    app.lab = Lab("http://x", transport=fake_server(log))
    app.replay = False
    monkeypatch.chdir(tmp_path)

    async def go():
        for c in ("/exp", "/show exp_abc123", "/result exp_abc123 b1 #ff8800 5 too red", "/csv exp_abc123", "/notes orange",
                  "/sweep orange 3", "/run exp_abc123", "/approve", "/show exp_nope"):
            try:
                await app.command(c)
            except Exception as e:  # noqa: BLE001  (REPL prints these)
                print("ERR", e)
    asyncio.run(go())
    out = capsys.readouterr().out
    for want in ("exp_abc123", "orange", "★ A1", "r20 y180", "236,199,30", "A2 is shallow", "rating", "nice", "rep",
                 "recorded B1", "accepted", "exp_abc123.csv", "orange is red 100", "/approve exp_abc123",
                 "approved exp_w", "ERR GET /api/experiments/exp_nope: not found"):
        assert want in out, want
    assert ("POST", "/api/experiments/exp_abc123/results", {"results": [{"well": "B1", "rgb": [255, 136, 0], "rating": 5, "note": "too red"}]}) in [x[:3] for x in log]
    assert (tmp_path / "exp_abc123.csv").read_text().startswith("experiment")


def test_experiment_ws_lines(capsys):
    app = cli.App("http://x", "t")
    app.handle({"t": "experiment", "experiment": {**EXP, "status": "ready"}})  # replay: silent
    app.replay = False
    app.handle({"t": "experiment", "experiment": {**EXP, "status": "awaiting_approval"}})
    app.handle({"t": "experiment", "experiment": {**EXP, "status": "running", "step": 1}})
    app.handle({"t": "experiment", "experiment": {**EXP, "status": "running", "step": 1}})  # same quarter: silent
    app.handle({"t": "experiment", "experiment": {**EXP, "status": "done"}})
    app.handle({"t": "model", "current_v": 4, "versions": [{"v": 4, "n_results": 9, "error_before": 30, "error_after": 20, "accepted": True}]})
    app.handle({"t": "note", "note": {"text": "Results for Orange sweep"}})
    out = capsys.readouterr().out
    assert "ready" not in out.split("awaiting_approval")[0]
    assert out.count("running") == 1 and "/approve exp_abc123" in out and "/csv exp_abc123" in out
    assert "refit v4" in out and "accepted" in out and "Results for Orange sweep" in out

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

    asyncio.run(go())
    out = capsys.readouterr().out
    for want in ("pipette", "servo_syringe", "120.0 µl", "probe", "retracted", "stand", "offline", "providers bridge:b1",
                 "phone:p1", "llm=on", "pipette.aspirate", "Done."):
        assert want in out, want
    assert {"t": "chat", "text": "take 50 µl"} in got
