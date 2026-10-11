"""End-to-end: real uvicorn server + fake bridge (mock devices) + UI client over websockets."""
import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
import websockets

from labrat.app import app

DEVICES = [
    {"id": "so_a", "kind": "so101", "status": "mock", "info": {}},
    {"id": "so_b", "kind": "so101", "status": "mock", "info": {}},
    {"id": "cnc", "kind": "grbl", "status": "mock", "info": {}},
    {"id": "pipette", "kind": "servo_pipette", "status": "mock", "info": {}},
]


@pytest.fixture(scope="module")
def base():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"127.0.0.1:{port}"
    server.should_exit = True
    th.join(5)


async def recv_until(ws, pred, timeout=30):
    async def go():
        while True:
            m = json.loads(await ws.recv())
            if pred(m):
                return m
    return await asyncio.wait_for(go(), timeout)


def test_chat_design_simulate_run(base):
    cmds: list[dict] = []

    async def main():
        async with websockets.connect(f"ws://{base}/ws/bridge?token=labrat") as br:
            await br.send(json.dumps({"t": "hello", "devices": DEVICES}))

            async def bridge_loop():
                async for raw in br:
                    m = json.loads(raw)
                    if m.get("t") == "cmd":
                        cmds.append(m)
                        await br.send(json.dumps({"t": "result", "id": m["id"], "ok": True, "data": {}}))

            bt = asyncio.create_task(bridge_loop())
            async with websockets.connect(f"ws://{base}/ws/ui?token=labrat", max_size=None) as ui:
                devs = await recv_until(ui, lambda m: m["t"] == "devices" and m["bridge"])
                assert {d["id"] for d in devs["devices"]} >= {"cnc", "pipette"}
                await ui.send(json.dumps({"t": "chat", "text": "make orange"}))
                exp = (await recv_until(ui, lambda m: m["t"] == "experiment" and m["experiment"]["status"] == "simulated"))["experiment"]
                assert exp["best_variant_id"] and exp["sim_summary"]["passing"] > 0
                sim_msg = await recv_until(ui, lambda m: m["t"] == "sim")
                assert sim_msg["frames"]
                await recv_until(ui, lambda m: m["t"] == "chat" and m["role"] == "agent" and "Simulated" in m["text"])

                await ui.send(json.dumps({"t": "chat", "text": "run best"}))
                run = (await recv_until(ui, lambda m: m["t"] == "run" and m["run"]["status"] == "awaiting_approval"))["run"]
                await ui.send(json.dumps({"t": "approve", "run_id": run["id"]}))
                done = (await recv_until(ui, lambda m: m["t"] == "run" and m["run"]["status"] in ("done", "failed", "aborted"), 60))["run"]
                assert done["status"] == "done", done.get("error")
                return done
            bt.cancel()

    done = asyncio.run(main())
    actions = {(c["device"], c["action"]) for c in cmds}
    assert {("cnc", "move"), ("pipette", "aspirate"), ("pipette", "dispense")} <= actions, actions
    with httpx.Client(base_url=f"http://{base}") as c:
        assert c.get("/api/experiments").json()
        assert c.get("/healthz").json()["ok"]
        assert c.get("/api/memory/search", params={"q": "orange"}).json()["results"]
        r = c.post(f"/api/runs/{done['id']}/observe", json={"rating": 4, "rgb": [235, 120, 20]})
        assert r.status_code == 200, r.text
        lr = c.get("/api/learning").json()
        assert lr["versions"], lr


def test_bad_token_rejected(base):
    async def main():
        with pytest.raises(Exception):
            async with websockets.connect(f"ws://{base}/ws/ui?token=nope") as ws:
                await asyncio.wait_for(ws.recv(), 3)
    asyncio.run(main())
