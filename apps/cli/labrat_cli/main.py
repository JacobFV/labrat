"""LabRat terminal: chat with the agent, run experiments (one plate each), watch the robot.

uv run labrat [--server URL] [--token T] ["/command" | "chat line"]      (or LABRAT_SERVER / LABRAT_TOKEN)

REST goes through labrat_cli.Lab (run in a thread); /ws/ui streams chat, tool chips, experiments, devices.
API contract: docs/experiments.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

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

from .lab import FINISHED, Lab

# Theme-neutral: never paint a background; GOLD reads on light and dark terminals.
GOLD = "#c9970a"
LINE = "grey50"
THEME = Theme({"gold": GOLD, "dim": "dim", "ok": "green", "warn": "yellow", "err": "red", "txt": "default"})
console = Console(theme=THEME, highlight=False)
PT_STYLE = Style.from_dict({
    "prompt": f"{GOLD} bold", "bottom-toolbar": "noreverse bg:default fg:default",
    "bottom-toolbar.gold": f"{GOLD} bold", "bottom-toolbar.ok": "ansigreen",
    "bottom-toolbar.warn": "ansiyellow", "bottom-toolbar.err": "ansired",
})
STOCKS = ("water", "red", "yellow", "blue")
SWEEPS = {"orange": ("red", "yellow"), "green": ("yellow", "blue"), "purple": ("red", "blue"),
          "lime": ("yellow", "blue"), "pink": ("red", "water")}
HELP = [
    ("<text>", "chat with the agent"),
    ("/exp", "list experiments"),
    ("/show <id>", "beakers: recipe, predicted vs measured colour, dry-run check"),
    ("/new <file.json>", "create from {title, target?, samples | steps, notes?}"),
    ("/sweep <colour|a+b> [n] [µL]", "n beakers from all-a to all-b (orange = red+yellow), target the colour"),
    ("/repeat <id>", "a fresh copy to run again"),
    ("/run <id>", "ask to run (then /approve)"),
    ("/approve [id]", "approve the waiting experiment"),
    ("/abort", "stop everything"),
    ("/result <id> <well> <#rrggbb|r,g,b|-> [1-5] [note]", "record a reading by hand"),
    ("/measure <id>", "read every beaker from the latest phone photo"),
    ("/csv <id|all> [path]", "save the beaker table"),
    ("/notes [q]  /note [exp_id] <text>", "the notebook"),
    ("/model", "colour model versions"),
    ("/status", "devices and what is running"),
    ("/jog cnc dx dy dz  /cal", "jog the CNC (mm) · deck calibration wizard"),
    ("/help  /quit", ""),
]
# cloud/src/deck_cal.ts TARGETS: each grid is fitted from its A1 and far corner (tip rack H12, beaker grid F7)
CAL_TARGETS = ["tip_rack.A1", "tip_rack.H12", "plate.A1", "plate.F7", "reservoir.water", "reservoir.red",
               "reservoir.blue", "reservoir.yellow", "waste", "eject_bar"]
STATUS_STYLE = {"draft": "warn", "ready": "ok", "awaiting_approval": "warn", "running": "gold", "done": "ok",
                "failed": "err", "aborted": "err"}


def _f(x, nd: int = 1) -> str:
    return f"{x:.{nd}f}" if isinstance(x, (int, float)) else "?"


def dev_summary(d: dict) -> str:
    """One-line state of a device, by kind."""
    st = d.get("state") or {}
    k = d.get("kind")
    if d.get("id") == "cnc" or k == "grbl":
        p = st.get("wpos") or st.get("pos")
        return (f"({_f(p[0])},{_f(p[1])},{_f(p[2])}) " if p else "") + str(st.get("state") or "")
    if k == "servo_syringe":
        s = f"{_f(st.get('vol_ul'))} µl @ {_f(st.get('pos_mm'), 2)}/{_f(st.get('stroke_mm'))} mm"
        return s + ("" if st.get("homed") else " · not homed") + (" · moving" if st.get("moving") else "")
    if k == "servo_arm":
        return ("deployed" if st.get("deployed") else "retracted") + (f" {_f(st.get('deg'), 0)}°" if "deg" in st else "") + (" · moving" if st.get("moving") else "")
    if k == "pan_tilt":
        return f"pan {_f(st.get('pan'), 0)}° tilt {_f(st.get('tilt'), 0)}°" + (" · moving" if st.get("moving") else "")
    if k == "servo_hub":
        info = d.get("info") or {}
        ins = st.get("inputs") or {}
        return " ".join(x for x in (info.get("transport"), info.get("fw"), " ".join(f"{n}={v}" for n, v in ins.items())) if x)
    return json.dumps(st, separators=(",", ":"))[:90] if st else ""


def status_style(s: str) -> str:
    return {"ok": "ok", "mock": "warn"}.get(s, "err")


def swatch(rgb, w: int = 4) -> Text:
    if not rgb:
        return Text("·" * w, style="dim")
    r, g, b = (int(x) for x in rgb[:3])
    return Text(" " * w, style=f"on rgb({r},{g},{b})")


def parse_rgb(s: str) -> list[int] | None:
    """'#ff8800', 'ff8800', '255,136,0' -> [r, g, b]; '-' -> None."""
    if s == "-":
        return None
    h = s.lstrip("#")
    if "," not in s and len(h) == 6:
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    v = [int(float(x)) for x in s.split(",")]
    if len(v) != 3:
        raise ValueError(f"colour {s!r}: use #rrggbb or r,g,b")
    return v


def sweep(spec: str, n: int = 9, total: float = 200) -> tuple[str | None, list[dict]]:
    """'orange' or 'red+yellow' -> (target, n samples from mostly-a to mostly-b, each `total` µL)."""
    a, b = SWEEPS[spec] if spec in SWEEPS else spec.split("+")
    if a not in STOCKS or b not in STOCKS:
        raise ValueError(f"sweep {spec!r}: use {', '.join(SWEEPS)} or two of {'+'.join(STOCKS)}")
    xs = [round(total * (i + 1) / (n + 1)) for i in range(n)]
    return (spec if spec in SWEEPS else None), [{a: total - x, b: x} for x in xs]


def recipe(b: dict) -> str:
    return " ".join(f"{k[0]}{round(b[k])}" for k in STOCKS if b.get(k))


def progress(e: dict, width: int = 20) -> Text:
    n = max(1, len(e.get("steps") or []) or e.get("n_steps") or 1)
    i = min(n, e.get("step") or 0)
    fill = int(width * i / n)
    return Text("█" * fill, style=GOLD) + Text("░" * (width - fill), style=LINE) + Text(f" {i}/{n}", style="dim")


class App:
    def __init__(self, server: str, token: str) -> None:
        self.server = server.rstrip("/")
        self.token = token
        self.lab = Lab(self.server, token)
        self.devices: dict[str, dict] = {}
        self.providers: list[dict] = []
        self.services: dict = {}
        self.phones = 0
        self.got_devices = asyncio.Event()
        self.connected = False
        self.exps: dict[str, dict] = {}
        self.seen: dict[str, tuple] = {}
        self.ws = None
        self.busy = False
        self.session: PromptSession | None = None
        self.replay = True  # suppress chatter from the initial snapshot

    async def call(self, fn, *a, **kw):
        return await asyncio.to_thread(fn, *a, **kw)

    # ---------- toolbar ----------
    def toolbar(self):
        parts = [("class:bottom-toolbar.gold", " ● labrat "), ("", f"{self.server}  ")]
        parts.append(("class:bottom-toolbar.ok" if self.connected else "class:bottom-toolbar.err", "ws " + ("up" if self.connected else "down") + "  "))
        roles = {p.get("role") for p in self.providers}
        for role in ("bridge", "phone"):
            parts.append((f"class:bottom-toolbar.{'ok' if role in roles else 'err'}", f"{role} {'on' if role in roles else 'off'}  "))
        for d in self.devices.values():
            parts.append((f"class:bottom-toolbar.{status_style(d.get('status', '?'))}", "■ "))
            extra = " " + dev_summary(d).split(" · ")[0].strip() if d["id"] in ("cnc", "pipette") else ""
            parts.append(("", f"{d['id']}{extra}  "))
        for e in self.exps.values():
            if e.get("status") == "running":
                parts.append(("class:bottom-toolbar.gold", f"▶ {e['id']} {e.get('step') or 0}/{len(e.get('steps') or [])}  "))
            elif e.get("status") == "awaiting_approval":
                parts.append(("class:bottom-toolbar.warn", f"? {e['id']} needs /approve  "))
        if self.busy:
            parts.append(("class:bottom-toolbar.gold", "· thinking…"))
        return parts

    # ---------- websocket ----------
    async def ws_loop(self) -> None:
        while True:
            try:
                async with websockets.connect(self.ws_url(), max_size=32 * 2**20) as ws:
                    self.ws, self.connected, self.replay = ws, True, True
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

    def ws_url(self) -> str:
        return self.server.replace("http", "ws", 1) + f"/ws/ui?token={self.token}"

    def _end_replay(self) -> None:
        self.replay = False

    def _inv(self) -> None:
        if self.session and self.session.app.is_running:
            self.session.app.invalidate()

    def handle(self, m: dict) -> None:
        t = m.get("t")
        if t == "devices":
            devs = m.get("devices") or []
            if isinstance(devs, dict):  # tolerate {id: device}
                devs = [{"id": k, **v} for k, v in devs.items()]
            self.devices = {d["id"]: d for d in devs}
            self.providers = m.get("providers") or []
            self.services = m.get("services") or {}
            self.phones = m.get("phones", 0)
            self.got_devices.set()
        elif t == "chat":
            self.chat(m.get("role"), m.get("text", ""), m.get("meta") or {})
        elif t == "status":
            self.busy = bool(m.get("busy"))
        elif t == "experiment":
            self.experiment(m["experiment"])
        elif t == "model" and not self.replay:
            last = (m.get("versions") or [{}])[-1]
            console.print(f"[gold]◆ colour model[/] refit v{last.get('v', '?')} on {last.get('n_results', '?')} readings, "
                          f"err {_f(last.get('error_before'))} → {_f(last.get('error_after'))}: "
                          + ("[ok]accepted[/]" if last.get("accepted") else f"[dim]kept v{m.get('current_v')}[/]"))
        elif t == "note" and not self.replay:
            n = m.get("note") or {}
            console.print(Text("✎ ", style=GOLD) + Text((n.get("text") or "")[:240], style="dim"))
        elif t == "transcript":
            self.chat("user" if m.get("role") == "user" else "agent", m.get("text", ""), {"voice": True})
        elif t == "log" and m.get("level") in ("error", "warn", "warning"):
            console.print(f"[warn]⚠ {m.get('text')}[/]")
        self._inv()

    def chat(self, role: str, text: str, meta: dict) -> None:
        if role == "tool":
            name = meta.get("tool") or text.split("(")[0]
            rest = text[len(name):] if text.startswith(name) else text
            chip = Text()
            chip.append(f" ⚙ {name} ", style=f"bold {GOLD} reverse" if meta.get("ok", True) else "bold red reverse")
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

    def experiment(self, e: dict) -> None:
        """Terse live line: on a status change, and at each quarter of a run."""
        self.exps[e["id"]] = e
        n = max(1, len(e.get("steps") or []))
        key = (e.get("status"), (4 * (e.get("step") or 0)) // n if e.get("status") == "running" else 0)
        old = self.seen.get(e["id"])
        self.seen[e["id"]] = key
        if self.replay or old == key:
            return
        st = e.get("status", "")
        line = Text(f"◆ {e['id']} ", style="bold") + Text(st, style=STATUS_STYLE.get(st, "txt")) + Text(f"  {e.get('title', '')}", style="dim")
        if st == "running":
            line = line + Text("  ") + progress(e)
            if e.get("log"):
                line.append("  " + e["log"][-1].get("msg", ""), style="dim")
        console.print(line)
        if st == "draft" and (e.get("check") or {}).get("errors"):
            console.print(f"  [err]{'; '.join(e['check']['errors'][:3])}[/]")
        elif st == "awaiting_approval":
            console.print(f"  [warn]needs approval →[/] /approve {e['id']}")
        elif st in FINISHED:
            if e.get("error"):
                console.print(f"  [err]{e['error']}[/]")
            console.print(f"  [dim]/show {e['id']} · /csv {e['id']}[/]")

    # ---------- views ----------
    def exp_table(self, es: list[dict]) -> None:
        tb = Table(border_style=LINE, header_style="dim")
        for c in ("id", "status", "beakers", "target", "best", "title"):
            tb.add_column(c, no_wrap=c != "title")
        for e in es:
            bs = {b["well"]: b for b in e.get("beakers") or []}
            tgt = e.get("target") or {}
            best_w = e.get("closest_measured") or e.get("closest_predicted")
            best = bs.get(best_w) or {}
            st = e.get("status", "")
            tb.add_row(Text(e["id"], style=GOLD), Text(st, style=STATUS_STYLE.get(st, "txt")), str(len(bs)),
                       (swatch(tgt.get("rgb"), 2) + Text(" " + (tgt.get("name") or ""))) if tgt else Text("—", style="dim"),
                       (swatch(best.get("measured_rgb") or best.get("predicted_rgb"), 2) + Text(f" {best_w}")) if best_w else Text("—", style="dim"),
                       e.get("title", ""))
        console.print(tb)

    def show(self, e: dict) -> None:
        ck = e.get("check") or {}
        st = e.get("status", "")
        tgt = e.get("target")
        head = Text(f"{e['id']} ", style=f"bold {GOLD}") + Text(e.get("title", ""), style="bold") + Text("  ") + Text(st, style=STATUS_STYLE.get(st, "txt"))
        if tgt:
            head += Text("  target ") + swatch(tgt.get("rgb"), 2) + Text(f" {tgt.get('name') or tgt.get('rgb')}", style="dim")
        console.print(head)
        info = f"  dry run {'[ok]ok[/]' if ck.get('ok') else '[err]failed[/]'} · {_f(ck.get('duration_s'), 0)} s · {len(ck.get('tips_used') or [])} tips · model v{ck.get('model_v', '?')}"
        if st in ("running", *FINISHED):
            info += f" · step {e.get('step') or 0}/{len(e.get('steps') or [])}"
        console.print(info)
        errs = ck.get("errors") or []
        for err in errs[:5]:
            console.print(f"  [err]✕ {err}[/]")
        if len(errs) > 5:
            console.print(f"  [err]… {len(errs) - 5} more[/]")
        for w in ck.get("warnings") or []:
            console.print(f"  [warn]! {w}[/]")
        if e.get("error"):
            console.print(f"  [err]{e['error']}[/]")
        tb = Table(border_style=LINE, header_style="dim")
        cols = ["well", "µL", "predicted", "measured", "err"] + (["→target"] if tgt else []) + ["rating", "label / note"]
        for c in cols:
            tb.add_column(c, no_wrap=c != "label / note")
        for b in e.get("beakers") or []:
            star = "★ " if b["well"] == e.get("closest_measured") else "☆ " if b["well"] == e.get("closest_predicted") else "  "
            rgbtxt = lambda rgb: (swatch(rgb, 3) + Text(" " + ",".join(str(x) for x in rgb), style="dim")) if rgb else Text("—", style="dim")  # noqa: E731
            to_t = b.get("measured_to_target") if b.get("measured_rgb") else b.get("predicted_to_target")
            row = [Text(star + b["well"], style=GOLD if star.strip() else "txt"), recipe(b),
                   rgbtxt(b.get("predicted_rgb")), rgbtxt(b.get("measured_rgb")), _f(b.get("error")) if b.get("error") is not None else "—"]
            if tgt:
                row.append(_f(to_t) if to_t is not None else "—")
            row += [str(b["rating"]) if b.get("rating") else "", " · ".join(x for x in (b.get("label"), b.get("note")) if x)]
            tb.add_row(*row)
        console.print(tb)
        hint = {"draft": "fix the samples and /new again (or edit it in the console)", "ready": f"/run {e['id']}",
                "awaiting_approval": f"/approve {e['id']}", "done": f"/csv {e['id']} · /repeat {e['id']}"}.get(st)
        if tgt:
            console.print("  [dim]★ closest measured · ☆ closest predicted[/]")
        if hint:
            console.print(f"  [dim]next: {hint}[/]")

    # ---------- commands ----------
    async def command(self, line: str) -> bool:
        try:
            args = shlex.split(line)
        except ValueError:
            args = line.split()
        cmd, a = args[0].lower(), args[1:]
        L = self.lab

        def need(n: int, usage: str):
            if len(a) < n:
                raise RuntimeError("usage: " + usage)

        if cmd in ("/quit", "/exit", "/q"):
            return False
        if cmd in ("/help", "/h", "/?"):
            tb = Table(show_header=False, box=None)
            for c, d in HELP:
                tb.add_row(Text(c, style=GOLD), Text(d, style="dim"))
            console.print(tb)
        elif cmd == "/status":
            await self.status()
        elif cmd in ("/exp", "/experiments", "/ls"):
            self.exp_table((await self.call(L.experiments))[:20])
        elif cmd == "/show":
            need(1, "/show <id>")
            self.show(await self.call(L.get, a[0]))
        elif cmd == "/new":
            need(1, "/new <file.json>   {title, target?, samples | steps, notes?}")
            b = json.loads(Path(a[0]).expanduser().read_text())
            self.show(await self.call(L.new, b.pop("title", Path(a[0]).stem), **b))
        elif cmd == "/sweep":
            need(1, "/sweep <orange|green|purple|a+b> [n=9] [µL=200]")
            target, samples = sweep(a[0], int(a[1]) if len(a) > 1 else 9, float(a[2]) if len(a) > 2 else 200)
            self.show(await self.call(L.new, f"{a[0]} sweep", samples=samples, target=target))
        elif cmd == "/repeat":
            need(1, "/repeat <id>")
            self.show(await self.call(L.repeat, a[0]))
        elif cmd == "/run":
            need(1, "/run <id>")
            e = await self.call(L.run, a[0])
            console.print(f"[warn]◆ {e['id']} {e['status']}[/] → /approve {e['id']}" if e["status"] == "awaiting_approval" else f"◆ {e['id']} {e['status']}")
        elif cmd == "/approve":
            if self.ws:
                await self.ws.send(json.dumps({"t": "approve", **({"id": a[0]} if a else {})}))
                console.print("[ok]✓ approve sent[/]")
            else:
                e = await self.call(L.approve, a[0] if a else None)
                console.print(f"[ok]✓ approved {e['id']}[/] ({e['status']})")
        elif cmd == "/abort":
            if self.ws:
                await self.ws.send(json.dumps({"t": "abort"}))
            else:
                await self.call(L.abort)
            console.print("[err]■ abort sent[/]")
        elif cmd == "/result":
            need(3, "/result <id> <well> <#rrggbb|r,g,b|-> [rating 1-5] [note…]")
            row = {"well": a[1].upper(), "rgb": parse_rgb(a[2])}
            if len(a) > 3:
                row["rating"] = int(a[3])
            if len(a) > 4:
                row["note"] = " ".join(a[4:])
            r = await self.call(L.results, a[0], [row])
            mv = r.get("model") or {}
            console.print(f"[ok]✓ recorded {row['well']}[/]" + (f" · model v{mv.get('v', mv.get('current_v', '?'))} {'accepted' if mv.get('accepted') else 'unchanged'}" if mv else ""))
        elif cmd == "/measure":
            need(1, "/measure <id>")
            r = await self.call(L.measure, a[0])
            console.print(f"[ok]✓ measured {len(r.get('readings') or [])} beakers[/] ({r.get('method')})" if r.get("ok")
                          else f"[warn]measure skipped: {r.get('skipped') or r.get('error')}[/]")
        elif cmd == "/csv":
            need(1, "/csv <id|all> [path]")
            exp_id = None if a[0] == "all" else a[0]
            text = await self.call(L.csv, exp_id)
            path = Path(a[1] if len(a) > 1 else f"{exp_id or 'labrat_results'}.csv").expanduser()
            path.write_text(text)
            console.print(f"[ok]✓ {path}[/] [dim]{text.count(chr(10)) - 1} rows[/]")
        elif cmd == "/notes":
            for n in await self.call(L.notes, " ".join(a)):
                console.print(Text(f"{n.get('experiment_id') or '':<11} ", style=GOLD) + Text(n.get("text", "")[:300]))
        elif cmd == "/note":
            need(1, "/note [exp_id] <text>")
            exp_id = a.pop(0) if a[0].startswith("exp_") and len(a) > 1 else None
            await self.call(L.note, " ".join(a), exp_id)
            console.print("[ok]✓ noted[/]")
        elif cmd == "/model":
            r = await self.call(L.model)
            tb = Table(title=f"[gold]colour model[/] · current v{r.get('current_v')}", title_justify="left", border_style=LINE, header_style="dim")
            for c in ("v", "err before", "err after", "readings", "kept", "from"):
                tb.add_column(c)
            for v in r.get("versions", []):
                tb.add_row(str(v.get("v")), _f(v.get("error_before"), 2), _f(v.get("error_after"), 2), str(v.get("n_results") or ""),
                           Text("✓", style="ok") if v.get("accepted") else Text("·", style="dim"), v.get("experiment_id") or "")
            console.print(tb)
        elif cmd == "/jog":
            need(4, "/jog cnc dx dy dz")
            r = await self.call(L._call, "POST", "/api/jog", {"device": a[0], "dx": float(a[1]), "dy": float(a[2]), "dz": float(a[3])})
            console.print(f"[ok]✓ jog[/] → {r.get('pos')}")
        elif cmd == "/cal":
            await self.cal_wizard()
        else:
            console.print(f"[err]unknown command {cmd}[/] — /help")
        return True

    async def status(self) -> None:
        if not self.got_devices.is_set():
            await self.snapshot()
        prov_of = {dev: p.get("role", "?") for p in self.providers for dev in p.get("devices", [])}
        tb = Table(border_style=LINE, header_style="dim", title="[gold]devices[/]", title_justify="left")
        for c in ("id", "kind", "via", "status", "state"):
            tb.add_column(c)
        for d in sorted(self.devices.values(), key=lambda d: d["id"]):
            s = d.get("status", "")
            via = prov_of.get(d["id"]) or ("hub" if d.get("kind") in ("servo_syringe", "servo_arm", "pan_tilt") else "—")
            tb.add_row(Text(d["id"], style=GOLD), d.get("kind", ""), Text(via, style="dim"), Text(s, style=status_style(s)),
                       Text(dev_summary(d), style="dim"))
        console.print(tb)
        provs = "  ".join(f"{p.get('role')}:{p.get('pid', '')[:6]}" for p in self.providers) or "[err]none[/]"
        on = lambda v: v.get("ok") if isinstance(v, dict) else v  # noqa: E731
        svc = "  ".join(f"{k}={'[ok]on[/]' if on(v) else '[err]off[/]'}" + (f" [dim]({v['mode']})[/]" if isinstance(v, dict) and v.get("mode") else "")
                        for k, v in self.services.items())
        console.print(f"  providers {provs}  phones={self.phones}  {svc}")
        try:
            active = (await self.call(self.lab.state)).get("active") or []
        except Exception:  # noqa: BLE001
            active = [e for e in self.exps.values() if e.get("status") in ("awaiting_approval", "running")]
        for x in active:
            st = x["status"]
            console.print(f"  [{STATUS_STYLE.get(st, 'txt')}]{st}[/] {x['id']} {x.get('title', '')}"
                          + (f" [dim]{x.get('step') or 0}/{x.get('n_steps', '?')}[/]" if st == "running" else f" → /approve {x['id']}"))

    async def chat_once(self, text: str, timeout: float = 180.0) -> None:
        """One-shot chat: send a line on /ws/ui and stream everything until the agent's reply."""
        async with websockets.connect(self.ws_url(), max_size=32 * 2**20) as ws:
            self.replay = True
            await ws.send(json.dumps({"t": "chat", "text": text}))
            console.print(Text("you ", style="dim") + Text(text, style="txt"))
            async with asyncio.timeout(timeout):
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("t") == "chat" and m.get("role") == "user" and m.get("text") == text:
                        self.replay = False  # everything before our own echo was the connect replay
                        continue
                    self.handle(m)
                    if m.get("t") == "chat" and m.get("role") == "agent":
                        break

    async def snapshot(self, timeout: float = 3.0) -> None:
        """One-shot mode: open /ws/ui just long enough to receive the replayed devices message."""
        try:
            async with websockets.connect(self.ws_url(), max_size=32 * 2**20) as ws:
                async with asyncio.timeout(timeout):
                    async for raw in ws:
                        self.handle(json.loads(raw))
                        if self.got_devices.is_set():
                            break
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"no devices from {self.ws_url().split('?')[0]} ({e or type(e).__name__})") from None

    async def cal_wizard(self) -> None:
        if not self.session:
            raise RuntimeError("/cal is interactive: run it inside the REPL")
        get_cal = lambda: self.call(self.lab._call, "GET", "/api/deck_cal")  # noqa: E731
        post = lambda path, body: self.call(self.lab._call, "POST", path, body)  # noqa: E731
        console.print(Panel.fit(
            "[gold]Deck calibration[/]\nJog the PIPETTE TIP onto each target, then teach.\n"
            "keys: [gold]x+1 y-0.1 z+10[/] (jog mm) · [gold]t[/] teach · [gold]n[/] next/skip · [gold]p[/] prev · "
            "[gold]off dx dy dz[/] set pipette offset · [gold]show[/] · [gold]q[/] done",
            border_style=GOLD))
        i = 0
        while 0 <= i < len(CAL_TARGETS):
            tgt = CAL_TARGETS[i]
            cal = await get_cal()
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
                    r = await post("/api/deck_cal/teach", {"target": tgt})
                    tp = r["tip"]
                    console.print(f"  [ok]✓ taught {r['target']}[/] tip ({tp['x']:.2f},{tp['y']:.2f},{tp['z']:.2f})")
                    i += 1
                elif ans == "show":
                    self.cal_table(cal)
                elif ans.startswith("off"):
                    _, dx, dy, dz = ans.split()
                    await post("/api/deck_cal", {"pipette_offset": {"dx": float(dx), "dy": float(dy), "dz": float(dz)}})
                elif ans[0] in "xyz":
                    d = float(ans[1:])
                    r = await post("/api/jog", {"device": "cnc", **{k: (d if k[1] == ans[0] else 0.0) for k in ("dx", "dy", "dz")}})
                    if r.get("pos"):
                        self.devices.setdefault("cnc", {"id": "cnc"}).setdefault("state", {})["wpos"] = r["pos"]
                else:
                    console.print("[dim]?[/]")
            except Exception as e:  # noqa: BLE001
                console.print(f"[err]✕ {e}[/]")
        self.cal_table(await get_cal())

    def cal_table(self, cal: dict) -> None:
        o = cal["pipette_offset"]
        tb = Table(title=f"[gold]deck calibration[/] · offset dx={o['dx']} dy={o['dy']} dz={o['dz']}", title_justify="left",
                   border_style=LINE, header_style="dim")
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
                                          ("\nchat with the agent, or /help  (/exp, /sweep orange, /show <id>)", "dim")),
                            border_style=LINE, expand=False))
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
                try:
                    if line.startswith("/"):
                        if not await self.command(line):
                            break
                        continue
                    console.print(Text("you ", style="dim") + Text(line, style="txt"))
                    if self.ws:
                        await self.ws.send(json.dumps({"t": "chat", "text": line}))
                        self.busy = True
                    else:
                        console.print(Text("labrat ", style=f"bold {GOLD}") + Text(await self.call(self.lab.chat, line)))
                except Exception as e:  # noqa: BLE001
                    console.print(f"[err]✕ {e}[/]")
        wst.cancel()


def main() -> None:
    ap = argparse.ArgumentParser(prog="labrat", description="LabRat terminal interface")
    ap.add_argument("--server", default=os.environ.get("LABRAT_SERVER", "http://127.0.0.1:8787"),
                    help="LabRat Worker origin (env LABRAT_SERVER; default %(default)s)")
    ap.add_argument("--token", default=os.environ.get("LABRAT_TOKEN", "labrat"), help="LABRAT_TOKEN (env LABRAT_TOKEN)")
    ap.add_argument("-c", "--command", help="run one /command, or send one chat line and wait for the reply, then exit")
    ap.add_argument("line", nargs="*", help="same as -c")
    a = ap.parse_args()
    app = App(a.server, a.token)
    once = a.command or " ".join(a.line)
    if once:
        async def one():
            try:
                if once.startswith("/"):
                    app.replay = False
                    await app.command(once)
                else:
                    await app.chat_once(once)
            except Exception as e:  # noqa: BLE001
                console.print(f"[err]✕ {e}[/]")
                sys.exit(1)
        asyncio.run(one())
        return
    try:
        asyncio.run(app.main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
