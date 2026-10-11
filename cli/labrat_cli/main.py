"""LabRat terminal REPL: streams /ws/ui (chat, tool chips, sims, runs, devices); input -> agent chat.

uv run labrat --server http://127.0.0.1:8100 --token labrat
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys

import httpx
import websockets
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

GOLD = "#f5d76e"
THEME = Theme({"gold": GOLD, "dim": "#6b6b6b", "ok": "#6ee7c8", "warn": "#d8a64f", "err": "#ff5a5a", "txt": "#ededed"})
console = Console(theme=THEME, highlight=False)
PT_STYLE = Style.from_dict({
    "prompt": f"{GOLD} bold", "bottom-toolbar": "bg:#0a0a0a #a8a8a8 noreverse",
    "bottom-toolbar.gold": f"bg:#0a0a0a {GOLD}", "bottom-toolbar.ok": "bg:#0a0a0a #6ee7c8",
    "bottom-toolbar.warn": "bg:#0a0a0a #d8a64f", "bottom-toolbar.err": "bg:#0a0a0a #ff5a5a",
})
HELP = [
    ("<text>", "chat with the agent"),
    ("/status", "devices, pending run, server health"),
    ("/exp", "list experiments"),
    ("/sim <exp> [n]", "simulate experiment (optionally n variants)"),
    ("/run <exp> [variant]", "request a real run (needs approval)"),
    ("/approve [run_id]", "approve pending run"),
    ("/abort", "abort everything"),
    ("/mem <query>", "search memory"),
    ("/learning", "simulator learning versions"),
    ("/jog cnc dx dy dz", "jog the CNC (mm)"),
    ("/cal", "deck calibration wizard"),
    ("/help, /quit", ""),
]
CAL_TARGETS = ["tip_rack.A1", "tip_rack.H12", "plate.A1", "plate.H12", "reservoir.water", "reservoir.red",
               "reservoir.blue", "reservoir.yellow", "waste", "eject_bar"]


def swatch(rgb, w: int = 4) -> Text:
    if not rgb:
        return Text("·" * w, style="dim")
    r, g, b = (int(x) for x in rgb[:3])
    return Text(" " * w, style=f"on rgb({r},{g},{b})")


class App:
    def __init__(self, server: str, token: str) -> None:
        self.server = server.rstrip("/")
        self.token = token
        self.http = httpx.AsyncClient(base_url=self.server, timeout=60, params={"token": token},
                                      headers={"authorization": f"Bearer {token}"})
        self.devices: dict[str, dict] = {}
        self.bridge = False
        self.connected = False
        self.exps: dict[str, dict] = {}
        self.sim_seen: dict[str, str] = {}
        self.runs: dict[str, tuple] = {}
        self.ws = None
        self.busy = False
        self.session: PromptSession | None = None
        self.replay = True  # suppress chatter from the initial snapshot

    # ---------- REST ----------
    async def get(self, path: str, **params):
        r = await self.http.get(path, params={"token": self.token, **params})
        return self._ok(r)

    async def post(self, path: str, body: dict | None = None):
        r = await self.http.post(path, json=body or {})
        return self._ok(r)

    @staticmethod
    def _ok(r: httpx.Response):
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            j = {"text": r.text}
        if r.status_code >= 400:
            raise RuntimeError(j.get("detail") or j.get("error") or r.text if isinstance(j, dict) else r.text)
        return j

    # ---------- toolbar ----------
    def toolbar(self):
        parts = [("class:bottom-toolbar.gold", " ● labrat "), ("", f"{self.server}  ")]
        parts.append(("class:bottom-toolbar.ok" if self.connected else "class:bottom-toolbar.err", "ws " + ("up" if self.connected else "down") + "  "))
        parts.append(("class:bottom-toolbar.ok" if self.bridge else "class:bottom-toolbar.err", "bridge " + ("on" if self.bridge else "off") + "  "))
        for d in self.devices.values():
            st = d.get("status", "?")
            cls = {"ok": "ok", "mock": "warn"}.get(st, "err")
            parts.append((f"class:bottom-toolbar.{cls}", "■ "))
            extra = ""
            if d["id"] == "cnc":
                p = (d.get("state") or {}).get("wpos")
                if p:
                    extra = f"({p[0]:.1f},{p[1]:.1f},{p[2]:.1f})"
            parts.append(("", f"{d['id']}{extra}  "))
        if self.busy:
            parts.append(("class:bottom-toolbar.gold", "· thinking…"))
        return parts

    # ---------- websocket ----------
    async def ws_loop(self) -> None:
        url = self.server.replace("http", "ws", 1) + f"/ws/ui?token={self.token}"
        while True:
            try:
                async with websockets.connect(url, max_size=32 * 2**20) as ws:
                    self.ws, self.connected = ws, True
                    self.replay = True
                    asyncio.get_running_loop().call_later(1.0, self._end_replay)
                    self._inv()
                    async for raw in ws:
                        try:
                            self.handle(json.loads(raw))
                        except Exception as e:  # noqa: BLE001
                            console.print(f"[err]handler error: {e}[/]")
            except Exception as e:  # noqa: BLE001
                if self.connected:
                    console.print(f"[err]✕ disconnected ({e}); retrying…[/]")
            self.ws, self.connected = None, False
            self._inv()
            await asyncio.sleep(2)

    def _end_replay(self) -> None:
        self.replay = False

    def _inv(self) -> None:
        if self.session and self.session.app.is_running:
            self.session.app.invalidate()

    def handle(self, m: dict) -> None:
        t = m.get("t")
        if t == "devices":
            self.bridge = bool(m.get("bridge"))
            self.devices = {d["id"]: d for d in m.get("devices", [])}
            self._inv()
        elif t == "chat":
            self.chat(m.get("role"), m.get("text", ""), m.get("meta") or {})
        elif t == "status":
            self.busy = bool(m.get("busy"))
            self._inv()
        elif t == "experiment":
            self.experiment(m["experiment"])
        elif t == "run":
            self.run(m["run"])
        elif t == "transcript":
            self.chat("user" if m.get("role") == "user" else "agent", m.get("text", ""), {"voice": True})
        elif t == "log" and m.get("level") in ("error", "warn", "warning"):
            console.print(f"[warn]⚠ {m.get('text')}[/]")
        elif t == "deck_cal":
            pass

    def chat(self, role: str, text: str, meta: dict) -> None:
        if role == "tool":
            ok = meta.get("ok", True)
            name = meta.get("tool") or text.split("(")[0]
            rest = text[len(name):] if text.startswith(name) else text
            chip = Text()
            chip.append(f" ⚙ {name} ", style=f"bold #1a1300 on {GOLD}" if ok else "bold white on #ff5a5a")
            chip.append(" " + rest[:220], style="dim")
            console.print(chip)
        elif role == "agent":
            self.busy = False
            console.print(Text("labrat ", style=f"bold {GOLD}") + Text(text, style="txt"))
        elif role == "user":
            if meta.get("voice") or meta.get("source") not in (None, "ui"):
                console.print(Text("you 🎙 " if meta.get("voice") else "you ", style="dim") + Text(text, style="txt"))
        else:
            console.print(Text(f"{role} ", style="dim") + Text(text, style="dim"))
        self._inv()

    def experiment(self, e: dict) -> None:
        self.exps[e["id"]] = e
        ss = e.get("sim_summary")
        if not ss:
            if not self.replay and e["id"] not in self.sim_seen:
                self.sim_seen[e["id"]] = ""
                console.print(f"[gold]◆ experiment[/] {e['id']} [dim]{e.get('goal')}[/] — {len(e.get('variants', []))} variants designed")
            return
        key = json.dumps(ss, sort_keys=True)
        if self.sim_seen.get(e["id"]) == key:
            return
        self.sim_seen[e["id"]] = key
        if self.replay:
            return
        self.sim_table(e)

    def sim_table(self, e: dict, top: int = 8) -> None:
        ss = e.get("sim_summary") or {}
        vs = [v for v in e.get("variants", []) if v.get("sim")]
        vs.sort(key=lambda v: (v["sim"].get("ok", False), v["sim"].get("score") or -1e9), reverse=True)
        tb = Table(title=f"[gold]sim[/] {e['id']} · {e.get('goal')}", title_justify="left", border_style="#2a2a2a",
                   header_style="dim", caption=f"{ss.get('passing')}/{ss.get('n')} pass · {ss.get('sim_ms')} ms · best {e.get('best_variant_id')}",
                   caption_justify="left", caption_style="dim")
        for c, w in (("id", 6), ("color", 6), ("rgb", 15), ("score", 5), ("pass", 4), ("time", 4)):
            tb.add_column(c, no_wrap=True, min_width=w)
        tb.add_column("params", no_wrap=True, overflow="ellipsis", ratio=1)
        for v in vs[:top]:
            s = v["sim"]
            best = v["id"] == e.get("best_variant_id")
            rgb = s.get("predicted_rgb")
            tb.add_row(Text(("★ " if best else "  ") + v["id"], style=GOLD if best else "txt"), swatch(rgb, 6),
                       Text(str(tuple(rgb)) if rgb else "—", style="dim"),
                       f"{s.get('score'):.3f}" if isinstance(s.get("score"), (int, float)) else "—",
                       Text("✓", style="ok") if s.get("ok") else Text("✕ " + "; ".join(s.get("errors", [])[:1])[:40], style="err"),
                       f"{s.get('duration_s') or 0:.0f}s",
                       Text(" ".join(f"{k}={v}" for k, v in (v.get("params") or {}).items() if k != "dyes")[:60], style="dim"))
        console.print(tb)

    def run(self, r: dict) -> None:
        key = (r.get("status"), r.get("step"), len(r.get("log", [])))
        if self.runs.get(r["id"]) == key:
            return
        self.runs[r["id"]] = key
        if self.replay:
            return
        n = max(1, r.get("n_steps") or 1)
        done = len(r.get("log", []))
        width = 24
        fill = int(width * done / n)
        bar = Text("█" * fill, style=GOLD) + Text("░" * (width - fill), style="#2a2a2a")
        st = r.get("status")
        sty = {"done": "ok", "failed": "err", "aborted": "err", "awaiting_approval": "warn"}.get(st, "gold")
        line = Text(f"▶ run {r['id']} ", style="bold") + bar + Text(f" {done}/{n} ", style="dim") + Text(st or "", style=sty)
        if r.get("log"):
            line.append("  " + r["log"][-1].get("msg", ""), style="dim")
        if r.get("predicted_rgb"):
            line.append("  ")
            line.append_text(swatch(r["predicted_rgb"], 2))
        console.print(line)
        if st == "awaiting_approval":
            console.print(f"  [warn]needs approval →[/] /approve {r['id']}")
        if r.get("error"):
            console.print(f"  [err]{r['error']}[/]")

    # ---------- commands ----------
    async def command(self, line: str) -> bool:
        try:
            args = shlex.split(line)
        except ValueError:
            args = line.split()
        cmd, a = args[0].lower(), args[1:]
        if cmd in ("/quit", "/exit", "/q"):
            return False
        if cmd in ("/help", "/h", "/?"):
            tb = Table(show_header=False, box=None)
            for c, d in HELP:
                tb.add_row(Text(c, style=GOLD), Text(d, style="dim"))
            console.print(tb)
        elif cmd == "/status":
            await self.status()
        elif cmd == "/exp":
            es = await self.get("/api/experiments")
            tb = Table(border_style="#2a2a2a", header_style="dim")
            for c in ("id", "status", "n", "best", "goal"):
                tb.add_column(c)
            for e in es[:20]:
                best = next((v for v in e.get("variants", []) if v["id"] == e.get("best_variant_id")), None)
                b = Text(e.get("best_variant_id") or "—")
                if best and best.get("sim"):
                    b = swatch(best["sim"].get("predicted_rgb"), 2) + Text(" " + best["id"])
                tb.add_row(Text(e["id"], style=GOLD), e.get("status", ""), str(len(e.get("variants", []))), b, e.get("goal", ""))
            console.print(tb)
        elif cmd == "/sim":
            if not a:
                raise RuntimeError("usage: /sim <exp> [n]")
            e = await self.post(f"/api/experiments/{a[0]}/simulate", {"n_variants": int(a[1]) if len(a) > 1 else None})
            self.sim_seen[e["id"]] = json.dumps(e.get("sim_summary"), sort_keys=True)
            self.sim_table(e)
        elif cmd == "/run":
            if not a:
                raise RuntimeError("usage: /run <exp> [variant]")
            r = await self.post(f"/api/experiments/{a[0]}/run", {"variant_id": a[1] if len(a) > 1 else None})
            self.run(r)
        elif cmd == "/approve":
            rid = a[0] if a else ((await self.get("/api/state")).get("pending_run") or {}).get("id")
            if not rid:
                raise RuntimeError("no pending run")
            r = await self.post(f"/api/runs/{rid}/approve")
            console.print(f"[ok]✓ approved {rid}[/] ({r.get('status')})")
        elif cmd == "/abort":
            await self.post("/api/abort")
            console.print("[err]■ abort sent[/]")
        elif cmd == "/mem":
            r = await self.get("/api/memory/search", q=" ".join(a) or "experiment", k=8)
            tb = Table(border_style="#2a2a2a", header_style="dim")
            for c in ("score", "type", "text"):
                tb.add_column(c)
            for m in r.get("results", []):
                sc = m.get("score")
                tb.add_row(f"{sc:.2f}" if isinstance(sc, (int, float)) else "", m.get("type", ""), m.get("text", "")[:160])
            console.print(tb)
        elif cmd == "/learning":
            r = await self.get("/api/learning")
            tb = Table(title=f"[gold]learning[/] · current v{r.get('current_v')}", title_justify="left", border_style="#2a2a2a", header_style="dim")
            for c in ("v", "err before", "err after", "runs", "accepted", "vol_gain"):
                tb.add_column(c)
            for v in r.get("versions", []):
                f = lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else "—"  # noqa: E731
                tb.add_row(str(v.get("v")), f(v.get("error_before")), f(v.get("error_after")), str(v.get("n_runs") or ""),
                           Text("✓", style="ok") if v.get("accepted") else Text("·", style="dim"), f((v.get("params") or {}).get("vol_gain")))
            console.print(tb)
        elif cmd == "/jog":
            if len(a) < 4:
                raise RuntimeError("usage: /jog cnc dx dy dz")
            r = await self.post("/api/jog", {"device": a[0], "dx": float(a[1]), "dy": float(a[2]), "dz": float(a[3])})
            console.print(f"[ok]✓ jog[/] → {r.get('pos')}")
        elif cmd == "/cal":
            await self.cal_wizard()
        else:
            console.print(f"[err]unknown command {cmd}[/] — /help")
        return True

    async def status(self) -> None:
        st = await self.get("/api/state")
        h = await self.get("/healthz")
        tb = Table(border_style="#2a2a2a", header_style="dim", title="[gold]devices[/]", title_justify="left")
        for c in ("id", "kind", "status", "state"):
            tb.add_column(c)
        for d in st.get("devices", []):
            s = d.get("status", "")
            tb.add_row(Text(d["id"], style=GOLD), d.get("kind", ""), Text(s, style={"ok": "ok", "mock": "warn"}.get(s, "err")),
                       Text(json.dumps(d.get("state"))[:90], style="dim"))
        console.print(tb)
        flags = "  ".join(f"{k}={'[ok]on[/]' if v else '[err]off[/]'}" for k, v in
                          (("bridge", st.get("bridge")), ("llm", h.get("llm")), ("mongo", h.get("mongo")), ("frame", st.get("has_frame"))))
        console.print(f"  {flags}  phones={st.get('phones')} uis={st.get('uis')}")
        pr = st.get("pending_run")
        if pr:
            console.print(f"  [warn]pending run[/] {pr['id']} ({pr['experiment_id']}:{pr['variant_id']}) → /approve")

    async def cal_wizard(self) -> None:
        console.print(Panel.fit(
            "[gold]Deck calibration[/]\nJog the PIPETTE TIP onto each target, then teach.\n"
            "keys: [gold]x+1 y-0.1 z+10[/] (jog mm) · [gold]t[/] teach · [gold]n[/] next/skip · [gold]p[/] prev · "
            "[gold]off dx dy dz[/] set pipette offset · [gold]show[/] · [gold]q[/] done",
            border_style=GOLD))
        i = 0
        while 0 <= i < len(CAL_TARGETS):
            tgt = CAL_TARGETS[i]
            cal = await self.get("/api/deck_cal")
            o = cal["pipette_offset"]
            p = ((self.devices.get("cnc") or {}).get("state") or {}).get("wpos") or [0, 0, 0]
            have = cal["points"].get(tgt)
            console.print(Text(f"[{i + 1}/{len(CAL_TARGETS)}] {tgt}", style=f"bold {GOLD}") +
                          Text(f"  spindle ({p[0]:.2f},{p[1]:.2f},{p[2]:.2f})  tip ({p[0] + o['dx']:.2f},{p[1] + o['dy']:.2f},{p[2] + o['dz']:.2f})", style="dim") +
                          Text(f"  taught {have['x']:.2f},{have['y']:.2f},{have['z']:.2f}" if have else "  (not taught)", style="ok" if have else "dim"))
            ans = (await self.session.prompt_async(HTML(f"<prompt>cal:{tgt}› </prompt>"))).strip().lower()
            try:
                if ans in ("q", "quit", "done"):
                    break
                if ans in ("n", "s", ""):
                    i += 1
                elif ans == "p":
                    i = max(0, i - 1)
                elif ans == "t":
                    r = await self.post("/api/deck_cal/teach", {"target": tgt})
                    tp = r["tip"]
                    console.print(f"  [ok]✓ taught {r['target']}[/] tip ({tp['x']:.2f},{tp['y']:.2f},{tp['z']:.2f})")
                    i += 1
                elif ans == "show":
                    self.cal_table(cal)
                elif ans.startswith("off"):
                    _, dx, dy, dz = ans.split()
                    await self.post("/api/deck_cal", {"pipette_offset": {"dx": float(dx), "dy": float(dy), "dz": float(dz)}})
                elif ans[0] in "xyz":
                    d = float(ans[1:])
                    r = await self.post("/api/jog", {"device": "cnc", **{k: (d if k[1] == ans[0] else 0.0) for k in ("dx", "dy", "dz")}})
                    if r.get("pos"):
                        self.devices.setdefault("cnc", {"id": "cnc"}).setdefault("state", {})["wpos"] = r["pos"]
                else:
                    console.print("[dim]?[/]")
            except Exception as e:  # noqa: BLE001
                console.print(f"[err]✕ {e}[/]")
        self.cal_table(await self.get("/api/deck_cal"))

    def cal_table(self, cal: dict) -> None:
        o = cal["pipette_offset"]
        tb = Table(title=f"[gold]deck calibration[/] · offset dx={o['dx']} dy={o['dy']} dz={o['dz']}", title_justify="left",
                   border_style="#2a2a2a", header_style="dim")
        for c in ("target", "tip x", "tip y", "tip z"):
            tb.add_column(c)
        for k, p in cal.get("points", {}).items():
            tb.add_row(Text(k, style=GOLD), f"{p['x']:.2f}", f"{p['y']:.2f}", f"{p.get('z', 0):.2f}")
        console.print(tb)
        for g, f in (cal.get("grids") or {}).items():
            console.print(f"  [dim]{g}:[/] " + (f"pitch {f['pitch']} mm · rot {f['theta_deg']}°" if f else "nominal"))

    # ---------- main ----------
    async def main(self) -> None:
        console.print(Panel(Text.assemble(("● LabRat", f"bold {GOLD}"), ("  terminal · ", "dim"), (self.server, "txt"),
                                          ("\nchat with the agent, or /help", "dim")), border_style="#2a2a2a", expand=False))
        self.session = PromptSession(bottom_toolbar=self.toolbar, style=PT_STYLE, refresh_interval=1.0)
        wst = asyncio.create_task(self.ws_loop())
        with patch_stdout(raw=True):
            while True:
                try:
                    line = (await self.session.prompt_async(HTML("<prompt>› </prompt>"))).strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not line:
                    continue
                if line.startswith("/"):
                    try:
                        if not await self.command(line):
                            break
                    except Exception as e:  # noqa: BLE001
                        console.print(f"[err]✕ {e}[/]")
                    continue
                console.print(Text("you ", style="dim") + Text(line, style="txt"))
                if self.ws:
                    await self.ws.send(json.dumps({"t": "chat", "text": line}))
                    self.busy = True
                else:
                    try:
                        r = await self.post("/api/chat", {"text": line})
                        console.print(Text("labrat ", style=f"bold {GOLD}") + Text(r.get("reply", "")))
                    except Exception as e:  # noqa: BLE001
                        console.print(f"[err]✕ {e}[/]")
        wst.cancel()
        await self.http.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(prog="labrat", description="LabRat terminal interface")
    ap.add_argument("--server", default="http://127.0.0.1:8100")
    ap.add_argument("--token", default="labrat")
    ap.add_argument("-c", "--command", help="run one /command (or chat line) and exit")
    a = ap.parse_args()
    app = App(a.server, a.token)
    if a.command:
        async def one():
            app.replay = False
            try:
                await app.command(a.command if a.command.startswith("/") else "/help")
            except Exception as e:  # noqa: BLE001
                console.print(f"[err]✕ {e}[/]")
            await app.http.aclose()
        asyncio.run(one())
        return
    try:
        asyncio.run(app.main())
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
