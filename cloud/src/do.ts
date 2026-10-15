// The Lab Durable Object (one instance, idFromName("lab")): WebSocket hub for /ws/ui, /ws/phone and /ws/bridge
// (hibernatable API, tagged by role), the REST API, the agent, run execution, approvals and abort.
import { DurableObject } from "cloudflare:workers";
import * as deckCal from "./deck_cal";
import * as lab from "./lab";
import * as learn from "./learn";
import * as vision from "./vision";
import { Agent } from "./agent";
import { flag, type Ctx, type Env } from "./env";
import { Hub, type Attachment, type Role } from "./hub";
import { llmAvailable, model } from "./llm";
import { hex, now, Store } from "./store";

class HttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
const json = (data: unknown, status = 200) => Response.json(data, { status });
const ROLES = new Set<Role>(["ui", "phone", "bridge"]);

type Handler = (m: RegExpMatchArray, body: any, url: URL) => Promise<unknown>;

export class Lab extends DurableObject<Env> {
  store: Store;
  hub: Hub;
  c: Ctx;
  agent: Agent;
  bootError: string | null = null;
  private routes: [string, RegExp, Handler][];

  constructor(state: DurableObjectState, env: Env) {
    super(state, env);
    this.store = new Store(env.DB);
    this.hub = new Hub(state);
    this.c = { env, store: this.store, hub: this.hub, runs: new Map(), pos: { x: 0, y: 0, z: 0 }, bg: (p, what = "task") => this.bg(p, what) };
    this.agent = new Agent(this.c, state.storage);
    this.hub.log = (level, text, src = "server") => this.hub.ui({ t: "log", src, level, text });
    this.hub.onEvent = (dev, data) => this.onDeviceEvent(dev, data);
    this.hub.services = () => this.services();
    for (const l of Object.values(this.hub.logical)) l.persist = (id, st) => void state.storage.put("dev:" + id, st);
    this.routes = this.routeTable();
    state.blockConcurrencyWhile(() => this.boot());
  }

  private async boot(): Promise<void> {
    try {
      deckCal.reset((await this.store.getConfig("deck_cal")) ?? {});
      await learn.applyLatest(this.c);
      for (const l of Object.values(this.hub.logical)) l.init(await this.store.getConfig("cal:" + l.id), (await this.ctx.storage.get("dev:" + l.id)) ?? null);
      await this.agent.load();
      this.bg(learn.seedTaxonomy(this.c), "seed taxonomy");
      this.bg(this.store.trimEvents(), "trim events");
    } catch (e) {
      this.bootError = (e as Error).message; // e.g. D1 migrations not applied
      console.error("lab boot failed:", e);
    }
  }

  private bg(p: Promise<unknown>, what: string): void {
    this.ctx.waitUntil(p.catch((e) => {
      console.error(`${what} failed:`, e);
      this.hub.log("error", `${what} failed: ${(e as Error).message}`);
    }));
  }

  services() {
    const on = llmAvailable(this.env);
    return {
      llm: on ? { ok: true, mode: "live", configured: true, detail: `anthropic ${model(this.env)}` } : { ok: false, mode: "fallback", configured: false, detail: "no ANTHROPIC_API_KEY; rule-based agent" },
      d1: this.bootError ? { ok: false, mode: "fallback", detail: this.bootError } : { ok: true, mode: "live", detail: "Cloudflare D1" },
    };
  }

  // ---------------- WebSockets ----------------
  async fetch(req: Request): Promise<Response> {
    const url = new URL(req.url);
    if (req.headers.get("Upgrade")?.toLowerCase() === "websocket") {
      const role = url.pathname.replace(/^\/ws\//, "") as Role;
      if (!ROLES.has(role)) return new Response("unknown socket", { status: 404 });
      const pair = new WebSocketPair();
      const ws = pair[1];
      if (req.headers.get("x-labrat-reject")) { // unauthenticated (checked by the Worker)
        this.ctx.acceptWebSocket(ws, ["rejected"]);
        ws.close(4401, "unauthorized");
        return new Response(null, { status: 101, webSocket: pair[0] });
      }
      const a: Attachment = { role, pid: hex(8), at: now() };
      this.ctx.acceptWebSocket(ws, [role]);
      ws.serializeAttachment(a);
      if (role === "ui") await this.replay(ws);
      if (role === "phone") this.hub.send(ws, { t: "face", mood: "happy", gaze: [0, 0] });
      if (role !== "ui") this.hub.pushDevices();
      return new Response(null, { status: 101, webSocket: pair[0] });
    }
    return this.rest(req, url);
  }

  private async replay(ws: WebSocket) {
    this.hub.send(ws, this.hub.devicesMsg());
    for (const e of (await this.store.listExperiments(10)).reverse()) this.hub.send(ws, { t: "experiment", experiment: lab.strip(e) });
    for (const r of (await this.store.listRuns(5)).reverse()) this.hub.send(ws, { t: "run", run: r });
  }

  async webSocketMessage(ws: WebSocket, data: string | ArrayBuffer): Promise<void> {
    const a = ws.deserializeAttachment() as Attachment;
    let msg: any;
    try { msg = JSON.parse(typeof data === "string" ? data : new TextDecoder().decode(data)); } catch { return; }
    if (!msg || typeof msg !== "object") return;
    if (a.role !== "ui" && this.hub.onProviderMessage(ws, a, msg)) {
      if (msg.t === "hello") this.bg(this.store.event("provider_hello", { role: a.role, pid: a.pid, devices: (msg.devices ?? []).map((d: any) => d.id) }), "event");
      return;
    }
    if (a.role === "phone") {
      if (msg.t === "frame" && msg.jpeg) {
        this.hub.setFrame({ jpeg: msg.jpeg, w: msg.w, h: msg.h, ts: now() });
        this.hub.ui({ t: "frame", jpeg: msg.jpeg, w: msg.w, h: msg.h });
      } else if (msg.t === "text" && msg.final !== false) {
        this.bg(this.agent.handle(String(msg.text ?? ""), "phone"), "agent");
      }
    } else if (a.role === "ui") {
      const c = this.c;
      if (msg.t === "chat") this.bg(this.agent.handle(String(msg.text ?? ""), "ui"), "agent");
      else if (msg.t === "approve") this.bg((async () => lab.approve(c, msg.run_id || (await c.store.pendingRun())?.id || ""))(), "approve");
      else if (msg.t === "abort") this.bg(lab.abort(c), "abort");
      else if (msg.t === "observe") this.bg(learn.observe(c, msg.run_id ?? "", { rating: msg.rating, rgb: msg.rgb, well: msg.well }), "observe");
    }
  }

  async webSocketClose(ws: WebSocket, code: number): Promise<void> {
    this.dropSocket(ws);
    try { ws.close(code === 1005 ? 1000 : code); } catch { /* already closed */ }
  }

  async webSocketError(ws: WebSocket): Promise<void> { this.dropSocket(ws); }

  private dropSocket(ws: WebSocket) {
    const a = ws.deserializeAttachment() as Attachment | null;
    if (!a) return;
    if (this.hub.providers.has(a.pid)) this.bg(this.store.event("provider_closed", { role: a.role, pid: a.pid }), "event");
    this.hub.closed(a);
    if (a.role === "phone") this.hub.pushDevices();
  }

  /** Unsolicited device events, e.g. servo-hub inputs, watchdog, e-stop. */
  private onDeviceEvent(dev: string, data: any) {
    const ev = data?.ev;
    let level = "info";
    if (dev === "hub" && ev === "estop" && Number(data.v)) {
      level = "error";
      this.bg(lab.abort(this.c), "e-stop abort");
    } else if (dev === "hub" && (ev === "wd" || ev === "boot")) {
      level = "warn";
      for (const l of Object.values(this.hub.logical)) l.hubChanged(); // servos relaxed / channel config lost
      this.hub.pushDevices();
    }
    if (level !== "info") this.bg(this.store.event("device_event", { device: dev, ...data }), "event");
    this.hub.ui({ t: "log", src: dev, level, text: `${dev} event ${JSON.stringify(data)}` });
  }

  // ---------------- REST ----------------
  private async rest(req: Request, url: URL): Promise<Response> {
    let body: any = {};
    if (req.method === "POST") {
      const text = await req.text();
      try { body = text ? JSON.parse(text) : {}; } catch { return json({ error: "invalid JSON body" }, 400); }
    }
    for (const [method, re, h] of this.routes) {
      const m = url.pathname.match(re);
      if (!m || method !== req.method) continue;
      try {
        return json(await h(m, body ?? {}, url));
      } catch (e) {
        if (e instanceof HttpError) return json({ error: e.message }, e.status);
        console.error(e);
        return json({ error: (e as Error).message }, 500);
      }
    }
    return json({ error: "not found" }, 404);
  }

  private routeTable(): [string, RegExp, Handler][] {
    const c = this.c, s = this.store, hub = this.hub;
    const as = (status: number) => (e: Error) => { throw new HttpError(status, e.message); };
    const exp = async (id: string) => (await s.getExperiment(decodeURIComponent(id))) ?? (() => { throw new HttpError(404, "not found"); })();
    const logical = (id: string) => hub.logical[id] ?? (() => { throw new HttpError(404, `no calibrated device ${id} (pipette, probe, stand)`); })();
    return [
      ["GET", /^\/healthz$/, async () => ({ ok: !this.bootError, error: this.bootError, bridge: hub.devicesMsg().bridge, providers: hub.providers.size, llm: llmAvailable(this.env), d1: !this.bootError })],
      ["GET", /^\/api\/state$/, async () => ({
        ...hub.devicesMsg(), uis: hub.sockets("ui").length, llm_configured: llmAvailable(this.env), has_frame: !!hub.latestFrame,
        pending_run: await s.pendingRun(), runs: await s.listRuns(10),
      })],
      ["GET", /^\/api\/experiments$/, async () => (await s.listExperiments(50)).map(lab.strip)],
      ["GET", /^\/api\/experiments\/([^/]+)$/, async (m, _b, url) => {
        const e = await exp(m[1]);
        if (!flag(url.searchParams.get("frames") ?? "")) return lab.strip(e);
        for (const v of e.variants) v.frames = lab.frames(e, v);
        return e;
      }],
      ["GET", /^\/api\/experiments\/([^/]+)\/variants\/([^/]+)\/frames$/, async (m) => {
        const e = await exp(m[1]);
        let v;
        try { v = lab.variant(e, decodeURIComponent(m[2])); } catch (err) { throw new HttpError(404, (err as Error).message); }
        return { experiment_id: e.id, variant_id: v.id, frames: lab.frames(e, v), variant: lab.flatVariant(v) };
      }],
      ["POST", /^\/api\/chat$/, async (_m, b) => ({ reply: await this.agent.handle(String(b.text ?? ""), "rest") })],
      ["POST", /^\/api\/experiments\/([^/]+)\/simulate$/, async (m, b) => lab.strip(await lab.simulateExperiment(c, decodeURIComponent(m[1]), b.n_variants).catch(as(404)))],
      ["POST", /^\/api\/experiments\/([^/]+)\/run$/, async (m, b) => lab.requestRun(c, decodeURIComponent(m[1]), b.variant_id, !!b.auto).catch(as(400))],
      ["GET", /^\/api\/runs$/, async (_m, _b, url) => s.listRuns(Number(url.searchParams.get("limit") ?? 20))],
      ["GET", /^\/api\/runs\/([^/]+)$/, async (m) => (await s.getRun(decodeURIComponent(m[1]))) ?? (() => { throw new HttpError(404, "not found"); })()],
      ["POST", /^\/api\/runs\/([^/]+)\/approve$/, async (m) => lab.approve(c, decodeURIComponent(m[1])).catch(as(404))],
      ["POST", /^\/api\/runs\/([^/]+)\/observe$/, async (m, b) => learn.observe(c, decodeURIComponent(m[1]), { rating: b.rating, rgb: b.rgb, well: b.well, vol_ul: b.vol_ul }).catch(as(404))],
      ["POST", /^\/api\/runs\/([^/]+)\/auto_observe$/, async (m) => vision.autoObserve(c, decodeURIComponent(m[1])).catch(as(404))],
      ["GET", /^\/api\/learning$/, async () => learn.learning(c)],
      ["POST", /^\/api\/abort$/, async () => { await lab.abort(c); return { ok: true }; }],
      ["GET", /^\/api\/memory\/search$/, async (_m, _b, url) => {
        const q = url.searchParams.get("q") ?? "";
        return { query: q, results: await s.recall(q, Number(url.searchParams.get("k") ?? 5), url.searchParams.get("type")) };
      }],
      ["GET", /^\/api\/events$/, async (_m, _b, url) => s.events(Math.min(1000, Number(url.searchParams.get("limit") ?? 100)), url.searchParams.get("kind") ?? undefined)],
      // ---- deck calibration ----
      ["GET", /^\/api\/deck_cal$/, async () => deckCal.summary()],
      ["POST", /^\/api\/deck_cal$/, async (_m, b) => {
        await s.setConfig("deck_cal", deckCal.update(b));
        hub.ui({ t: "deck_cal", deck_cal: deckCal.summary() });
        return deckCal.summary();
      }],
      ["POST", /^\/api\/deck_cal\/teach$/, async (_m, b) => {
        if (!b.target) throw new HttpError(400, "target required");
        const p: number[] = Array.isArray(b.pos) ? b.pos.map(Number) : await this.cncPos();
        const tip = deckCal.teach(b.target, [p[0], p[1], p[2]]);
        await s.setConfig("deck_cal", deckCal.get());
        await s.event("deck_teach", { target: b.target, spindle: p, tip });
        const out = { target: deckCal.canon(b.target), spindle: p, tip, deck_cal: deckCal.summary() };
        hub.ui({ t: "deck_cal", deck_cal: out.deck_cal });
        return out;
      }],
      ["POST", /^\/api\/jog$/, async (_m, b) => {
        if ((b.device ?? "cnc") !== "cnc") throw new HttpError(400, "only cnc jog supported");
        const d = ["dx", "dy", "dz"].map((k) => Number(b[k] ?? 0));
        if (d.some((v) => Math.abs(v) > 50)) throw new HttpError(400, "jog step too large (max 50 mm)");
        const r = await hub.cmd("cnc", "jog", { dx: d[0], dy: d[1], dz: d[2], f: Number(b.f ?? 600) }, 30).catch((e) => { throw new HttpError(400, `jog failed: ${e.message}`); });
        const p = r?.pos ?? null;
        if (p) Object.assign(c.pos, { x: p[0], y: p[1], z: p[2] });
        return { ok: true, pos: p };
      }],
      // One action on any device (provider or logical); the hub's safety rules still apply.
      ["POST", /^\/api\/device$/, async (_m, b) => {
        if (!b.device || !b.action) throw new HttpError(400, "device and action required");
        const r = await hub.cmd(b.device, b.action, b.args ?? {}, Math.min(Number(b.timeout ?? 30), 120)).catch((e) => { throw new HttpError(400, `${b.device}.${b.action} failed: ${e.message}`); });
        return { ok: true, data: r };
      }],
      // ---- logical-device calibration (cal:pipette | cal:probe | cal:stand) ----
      ["GET", /^\/api\/cal\/(\w+)$/, async (m) => ({ device: m[1], cal: logical(m[1]).cal, defaults: logical(m[1]).defaults, state: logical(m[1]).snapshot() })],
      ["POST", /^\/api\/cal\/(\w+)$/, async (m, b) => {
        const l = logical(m[1]);
        const patch = b.reset ? structuredClone(l.defaults) : b;
        delete patch.reset;
        const cal = b.reset ? (l.cal = patch) : l.setCal(patch);
        await s.setConfig("cal:" + l.id, cal);
        await s.event("calibrate", { device: l.id, patch });
        hub.pushDevices();
        return { device: l.id, cal };
      }],
      // ---- camera ----
      ["GET", /^\/api\/camera_cal$/, async () => (await vision.getCal(c)) ?? { id: "camera_cal", corners: null }],
      ["POST", /^\/api\/camera_cal$/, async (_m, b) => vision.setCal(c, b.corners, b.image_w, b.image_h).catch(as(400))],
      ["GET", /^\/api\/camera_frame$/, async () => {
        const f = hub.latestFrame;
        if (!f) throw new HttpError(404, "no phone frame yet");
        return f;
      }],
    ];
  }

  private async cncPos(): Promise<number[]> {
    try {
      const st = await this.hub.cmd("cnc", "status", {}, 5);
      const p = st?.wpos ?? st?.pos;
      if (p) return p.map(Number);
    } catch (e) {
      this.hub.log("warn", `cnc status failed: ${(e as Error).message}`);
    }
    const p = (this.hub.device("cnc")?.state as any)?.wpos;
    if (!p) throw new HttpError(409, "no CNC position available (bridge connected?)");
    return p.map(Number);
  }
}
