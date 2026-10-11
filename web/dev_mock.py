#!/usr/bin/env python3
"""Tiny stdlib-only mock of the LabRat server for UI dev.

    python3 web/dev_mock.py [port]      -> http://localhost:8765/?token=labrat  and  /phone

Serves web/ at / and phone/ at /phone, fakes /api/* and the /ws/ui + /ws/phone websockets
(minimal RFC6455, text frames only) with a fake experiment, sim frames, chat + tool chips.
"""
import base64, hashlib, json, math, os, random, socket, struct, sys, threading, time, uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB, PHONE = os.path.join(ROOT, "web"), os.path.join(ROOT, "phone")
ROWS = "ABCDEFGH"
UI_CLIENTS, LOCK = [], threading.Lock()
LATEST_FRAME = {"jpeg": None}


def mix(r, y, b=0.0):
    tot = r + y + b + 1e-9
    c = [255 - (r * 30 + y * 20 + b * 230) / tot, 255 - (r * 220 + y * 60 + b * 120) / tot, 255 - (r * 200 + y * 230 + b * 20) / tot]
    return [int(max(0, min(255, v))) for v in c]


def make_variant(i):
    red = random.randint(20, 150); yel = 200 - red
    rgb = mix(red, yel)
    score = 1 - abs(red / 200 - 0.35) * 1.6 + random.uniform(-0.05, 0.05)
    well = f"{ROWS[i // 12]}{i % 12 + 1}"
    steps = [{"op": "pick_tip", "rack_well": f"A{i + 1}"}, {"op": "aspirate", "src": "R2", "ul": red}, {"op": "dispense", "dst": well, "ul": red},
             {"op": "eject_tip"}, {"op": "pick_tip", "rack_well": f"B{i + 1}"}, {"op": "aspirate", "src": "R4", "ul": yel}, {"op": "dispense", "dst": well, "ul": yel},
             {"op": "mix", "well": well, "ul": 100, "n": 3}, {"op": "eject_tip"}, {"op": "photo", "target": "plate"}]
    checks = {"volume_cap": True, "tip_once": True, "bounds": random.random() > 0.1, "collision": True}
    return {"id": f"v{i + 1}", "name": f"red {red} / yellow {yel}", "params": {"red_ul": red, "yellow_ul": yel, "well": well},
            "protocol": {"steps": steps}, "score": round(score, 3), "predicted_rgb": rgb, "checks": checks,
            "pass": all(checks.values()), "time_s": round(random.uniform(40, 90), 1), "well": well, "red": red, "yel": yel}


def sim_frames(v):
    frames, t = [], 0.0
    red_xy, yel_xy = (250, 60), (250, 140)
    r, c = ROWS.index(v["well"][0]), int(v["well"][1:]) - 1
    well_xy = (140 + c * 9, 20 + r * 9)
    vol = {"r": 0, "y": 0}
    path = [((20 + int(v["id"][1:]) * 9 - 9, 20), "pick_tip"), (red_xy, "aspirate red"), (well_xy, "dispense"), ((10, 170), "eject"),
            ((20 + int(v["id"][1:]) * 9 - 9, 29), "pick_tip"), (yel_xy, "aspirate yellow"), (well_xy, "dispense"), (well_xy, "mix"), ((10, 170), "eject")]
    pos = (150, 90); tip = False; pv = 0
    for (tx, ty), label in path:
        for k in range(8):
            a = (k + 1) / 8
            frames.append({"t": round(t, 2), "step": label, "cnc": {"x": pos[0] + (tx - pos[0]) * a, "y": pos[1] + (ty - pos[1]) * a, "z": 0}, "pipette": {"vol_ul": pv, "tip": tip}})
            t += 0.25
        pos = (tx, ty)
        for z in (-10, -25, -35, -25, -10, 0):
            if z == -35:
                if label == "pick_tip": tip = True
                if label == "aspirate red": pv = v["red"]
                if label == "aspirate yellow": pv = v["yel"]
                if label == "dispense":
                    if vol["r"] == 0: vol["r"] = v["red"]
                    else: vol["y"] = v["yel"]
                    pv = 0
                if label == "eject": tip = False
            f = {"t": round(t, 2), "step": label, "cnc": {"x": tx, "y": ty, "z": z}, "pipette": {"vol_ul": pv, "tip": tip}}
            if label in ("dispense", "mix") and z == -35:
                f["wells"] = {v["well"]: {"rgb": mix(vol["r"], vol["y"]), "vol": vol["r"] + vol["y"]}}
            frames.append(f); t += 0.3
    return frames


EXP = {"id": "exp1", "goal": "make the most orange-looking well using red & yellow", "status": "simulated", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "variants": [], "runs": []}


LEARNING = {"versions": [{"v": i + 1, "created_at": f"2026-10-11T1{i}:00", "params": {"k_red": round(1 + .1 * i, 2), "k_yellow": round(.9 - .05 * i, 2)},
                           "error_before": round(.32 * .75 ** i, 4), "error_after": round(.32 * .75 ** i * .7, 4), "n_runs": i * 2} for i in range(4)]}
MEMORIES = [{"type": "semantic", "text": "Red dye is ~3x stronger than yellow at equal volume; orange needs ~35% red."},
            {"type": "episodic", "text": "Run run42 (2026-10-11 14:05): well A3 came out brownish; photo shows bubble from fast dispense."},
            {"type": "procedural", "text": "Always mix 3x at 100 uL after the second dispense, then eject the tip over the bar."},
            {"type": "taxonomic", "text": "orange ⊂ secondary colors; made from red + yellow (subtractive)."},
            {"type": "semantic", "text": "Reservoir R4 contains yellow dye, R2 red dye."}]


def simulate(n):
    EXP["variants"] = [make_variant(i) for i in range(n)]
    for v in EXP["variants"]:
        broadcast({"t": "sim", "experiment_id": "exp1", "variant_id": v["id"], "frames": sim_frames(v)})
    broadcast({"t": "experiment", "experiment": EXP})


STATE = lambda: {"devices": {
    "so_a": {"kind": "so101", "status": "mock", "q": {"shoulder_pan": 20 * math.sin(time.time() / 3), "shoulder_lift": -20, "elbow_flex": 70, "wrist_flex": 30, "wrist_roll": 0, "gripper": 0}},
    "so_b": {"kind": "so101", "status": "mock", "q": {"shoulder_pan": -15, "shoulder_lift": -10 + 15 * math.sin(time.time() / 2), "elbow_flex": 50, "wrist_flex": 20, "wrist_roll": 0, "gripper": 40}},
    "tap_a": {"kind": "tappet", "status": "error"}, "cnc": {"kind": "grbl", "status": "mock", "x": 150 + 100 * math.sin(time.time() / 4), "y": 90, "z": -10},
    "pipette": {"kind": "servo_pipette", "status": "mock", "vol_ul": 0, "tip": True}, "phone": {"status": "ok" if LATEST_FRAME["jpeg"] else "off"}},
    "services": {"mongo": {"ok": False, "fallback": "memory"}, "voyage": "mock", "raindrop": True, "elevenlabs": {"ok": True}, "modelence": "off", "aws": {"ok": True, "region": "us-west-2"}, "llm": {"ok": True, "provider": "anthropic"}}}


# ---------------- minimal websocket ----------------
def ws_handshake(h):
    key = h.headers.get("Sec-WebSocket-Key")
    acc = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    h.send_response(101); h.send_header("Upgrade", "websocket"); h.send_header("Connection", "Upgrade"); h.send_header("Sec-WebSocket-Accept", acc); h.end_headers()
    h.wfile.flush(); h.close_connection = True
    return h.connection


def ws_send(sock, obj):
    data = json.dumps(obj).encode(); n = len(data)
    hdr = bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n))
    sock.sendall(hdr + data)


def recv_exact(sock, n):
    b = b""
    while len(b) < n:
        c = sock.recv(n - len(b))
        if not c: raise ConnectionError
        b += c
    return b


def ws_recv(sock):
    while True:
        b1, b2 = recv_exact(sock, 2); op = b1 & 0x0F; n = b2 & 0x7F
        if n == 126: n = struct.unpack(">H", recv_exact(sock, 2))[0]
        elif n == 127: n = struct.unpack(">Q", recv_exact(sock, 8))[0]
        mask = recv_exact(sock, 4) if b2 & 0x80 else b"\0\0\0\0"
        data = bytes(x ^ mask[i % 4] for i, x in enumerate(recv_exact(sock, n)))
        if op == 8: raise ConnectionError
        if op == 9: sock.sendall(bytes([0x8A, 0])); continue
        if op in (1, 2, 0): return data.decode(errors="ignore")


def broadcast(obj):
    with LOCK:
        for s in list(UI_CLIENTS):
            try: ws_send(s, obj)
            except Exception: UI_CLIENTS.remove(s)


def agent_reply(text):
    def run():
        broadcast({"t": "chat", "role": "tool", "text": "recall", "meta": {"name": "recall", "args": {"query": text}}})
        time.sleep(0.6)
        broadcast({"t": "chat", "role": "tool", "text": "design_experiment", "meta": {"name": "design_experiment", "args": {"goal": text, "n_variants": 8}}})
        time.sleep(0.6); simulate(8)
        broadcast({"t": "chat", "role": "tool", "text": "simulate", "meta": {"name": "simulate", "args": {"experiment_id": "exp1"}}})
        sid = uuid.uuid4().hex; msg = "I simulated 8 variants. The best is about 35% red / 65% yellow. Want me to run it on the real plate?"
        acc = ""
        for w in msg.split(" "):
            acc += w + " "; broadcast({"t": "chat", "role": "agent", "text": acc.strip(), "meta": {"stream_id": sid, "partial": True}}); time.sleep(0.05)
        broadcast({"t": "chat", "role": "agent", "text": msg, "meta": {"stream_id": sid}})
    threading.Thread(target=run, daemon=True).start()


class H(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

    def translate_path(self, path):
        p = path.split("?")[0]
        if p.startswith("/phone"):
            rel = p[len("/phone"):].lstrip("/") or "index.html"; return os.path.join(PHONE, rel)
        return os.path.join(WEB, p.lstrip("/") or "index.html")

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/ws/ui": return self.ws_ui()
        if p == "/ws/phone": return self.ws_phone()
        if p == "/api/state": return self._json(STATE())
        if p == "/api/experiments": return self._json([EXP])
        if p.startswith("/api/experiments/"): return self._json({**EXP, "variants": [{**v, "frames": sim_frames(v)} for v in EXP["variants"]]})
        if p == "/api/learning": return self._json(LEARNING)
        if p == "/api/memory/search":
            from urllib.parse import parse_qs, urlparse
            q = (parse_qs(urlparse(self.path).query).get("q") or [""])[0].lower()
            res = [m for m in MEMORIES if not q or any(w in m["text"].lower() for w in q.split())] or MEMORIES[:2]
            return self._json({"results": [{**m, "score": round(random.uniform(0.5, 0.95), 3)} for m in res]})
        if p == "/healthz": return self._json({"ok": True})
        if p == "/phone": self.send_response(301); self.send_header("Location", "/phone/"); self.end_headers(); return
        return super().do_GET()

    def do_POST(self):
        p = self.path.split("?")[0]; n = int(self.headers.get("content-length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        if p.endswith("/simulate"): threading.Thread(target=simulate, args=(int(body.get("n_variants", 8)),), daemon=True).start(); return self._json({"ok": True})
        if p.endswith("/run"):
            run = {"id": "run" + uuid.uuid4().hex[:6], "experiment_id": "exp1", "variant_id": body.get("variant_id"), "status": "awaiting_approval", "created_at": time.strftime("%H:%M:%S")}
            EXP["runs"].append(run); broadcast({"t": "run", "run": run}); return self._json({"run": run})
        if p.startswith("/api/runs/") and p.endswith("/observe"):
            rid = p.split("/")[3]
            for r in EXP["runs"]:
                if r["id"] == rid: r.update(rating=body.get("rating"), observed_rgb=body.get("rgb")); broadcast({"t": "run", "run": r})
            last = LEARNING["versions"][-1]
            LEARNING["versions"].append({"v": last["v"] + 1, "created_at": time.strftime("%H:%M:%S"), "params": {"k_red": round(random.uniform(.8, 1.2), 3)},
                                         "error_before": last["error_after"] * 1.1, "error_after": round(last["error_after"] * 0.8, 4), "n_runs": last["n_runs"] + 1})
            broadcast({"t": "learning", **LEARNING})
            return self._json({"ok": True})
        if p == "/api/chat": agent_reply(body.get("text", "")); return self._json({"ok": True})
        return self._json({"ok": True})

    def ws_ui(self):
        sock = ws_handshake(self)
        with LOCK: UI_CLIENTS.append(sock)
        try:
            ws_send(sock, {"t": "devices", **STATE()})
            if LATEST_FRAME["jpeg"]: ws_send(sock, {"t": "frame", "jpeg": LATEST_FRAME["jpeg"]})
            while True:
                m = json.loads(ws_recv(sock))
                if m.get("t") == "chat": broadcast({"t": "chat", "role": "user", "text": m["text"]}); agent_reply(m["text"])
                elif m.get("t") == "approve":
                    for r in EXP["runs"]:
                        if r["id"] == m.get("run_id"):
                            r["status"] = "done"; r["observation"] = "Well looks orange (mock)."
                            if LATEST_FRAME["jpeg"]: r["photo_jpeg"] = LATEST_FRAME["jpeg"]
                            broadcast({"t": "run", "run": r})
                elif m.get("t") == "abort": broadcast({"t": "log", "level": "warn", "text": "abort received"})
        except Exception: pass
        finally:
            with LOCK:
                if sock in UI_CLIENTS: UI_CLIENTS.remove(sock)

    def ws_phone(self):
        sock = ws_handshake(self)
        try:
            ws_send(sock, {"t": "say", "text": "Hello, I am LabRat.", "audio_b64": None})
            while True:
                m = json.loads(ws_recv(sock))
                if m.get("t") == "frame":
                    LATEST_FRAME["jpeg"] = m["jpeg"]; broadcast({"t": "frame", "jpeg": m["jpeg"]})
                elif m.get("t") == "audio":
                    ws_send(sock, {"t": "transcript", "role": "user", "text": f"(mock heard {len(m.get('b64',''))//1000} kB {m.get('mime')})"})
                    ws_send(sock, {"t": "face", "mood": "thinking", "gaze": [0.3, -0.4]}); time.sleep(0.8)
                    ws_send(sock, {"t": "say", "text": "Got it. Simulating now.", "audio_b64": None})
                    ws_send(sock, {"t": "face", "mood": "happy", "gaze": [0, 0]})
        except Exception: pass


def ticker():
    while True:
        time.sleep(1); broadcast({"t": "devices", **STATE()})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    threading.Thread(target=ticker, daemon=True).start()
    print(f"mock LabRat on http://localhost:{port}/?token=labrat  (phone: /phone/)")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
