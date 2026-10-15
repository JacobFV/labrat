import { SELF } from "cloudflare:test";

export const TOKEN = "labrat";

export async function api<T = any>(path: string, body?: unknown, token = TOKEN): Promise<{ status: number; data: T }> {
  const r = await SELF.fetch(`http://lab${path}${path.includes("?") ? "&" : "?"}token=${token}`, body !== undefined ? { method: "POST", body: JSON.stringify(body) } : {});
  return { status: r.status, data: (await r.json()) as T };
}

/** A test WebSocket client with an inbox and `next(pred)` to await a matching message. */
export class Client {
  inbox: any[] = [];
  closed: { code: number } | null = null;
  private waiters: { pred: (m: any) => boolean; resolve: (m: any) => void }[] = [];
  constructor(public ws: WebSocket) {
    ws.addEventListener("message", (e) => {
      const m = JSON.parse(e.data as string);
      this.inbox.push(m);
      for (const w of [...this.waiters]) if (w.pred(m)) { this.waiters.splice(this.waiters.indexOf(w), 1); w.resolve(m); }
      this.onMessage?.(m);
    });
    ws.addEventListener("close", (e) => { this.closed = { code: e.code }; });
  }
  onMessage?: (m: any) => void;
  send(m: unknown) { this.ws.send(JSON.stringify(m)); }
  /** Next message (including ones already received since `from`) matching `pred`. */
  next(pred: (m: any) => boolean, timeoutMs = 30000, from = 0): Promise<any> {
    const hit = this.inbox.slice(from).find(pred);
    if (hit) return Promise.resolve(hit);
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error(`timeout waiting for message; last: ${JSON.stringify(this.inbox.slice(-3)).slice(0, 600)}`)), timeoutMs);
      this.waiters.push({ pred, resolve: (m) => { clearTimeout(t); resolve(m); } });
    });
  }
  close() { try { this.ws.close(1000); } catch { /* closed */ } }
}

export async function connect(path: string, token = TOKEN): Promise<Client> {
  const r = await SELF.fetch(`http://lab${path}?token=${token}`, { headers: { Upgrade: "websocket" } });
  const ws = r.webSocket;
  if (!ws) throw new Error(`no websocket (${r.status})`);
  const c = new Client(ws);
  ws.accept();
  return c;
}

export const DEVICES = [
  { id: "so_a", kind: "so101", status: "mock", info: {} },
  { id: "so_b", kind: "so101", status: "mock", info: {} },
  { id: "cnc", kind: "grbl", status: "mock", info: {} },
  { id: "microscope", kind: "usb_microscope", status: "mock", info: {} },
  { id: "hub", kind: "servo_hub", status: "mock", info: {} },
];

/** Fake firmware reply to a hub `line` (protocol.md §4). */
export function hubReply(msg: any) {
  switch (msg.cmd) {
    case "pulse": return { ok: true, ...(msg.until ? { stopped_by: msg.until } : {}) };
    case "servo": return { ok: true, deg: msg.deg };
    case "cfg": return { ok: true, cfg: msg };
    default: return { ok: true };
  }
}

/** A provider socket that answers every command (cnc / hub / arms / microscope mocked) and records them. */
export async function fakeProvider(path = "/ws/bridge", devices = DEVICES, answer: boolean | ((m: any) => boolean) = true) {
  const p = await connect(path);
  const cmds: any[] = [];
  const pos = { x: 0, y: 0, z: 0 };
  p.onMessage = (m) => {
    if (m.t !== "cmd") return;
    cmds.push(m);
    if (answer === false || (typeof answer === "function" && !answer(m))) return;
    let data: any = {};
    if (m.device === "hub" && m.action === "line") data = hubReply(m.args.msg);
    else if (m.device === "cnc" && m.action === "move") { Object.assign(pos, m.args); data = { pos: [pos.x, pos.y, pos.z] }; }
    else if (m.device === "cnc" && m.action === "status") data = { wpos: [pos.x, pos.y, pos.z], state: "Idle" };
    else if (m.device === "cnc" && m.action === "probe") data = { hit: true, z: -30, start_z: 0, target_z: m.args.dz };
    else if (m.device === "microscope" && m.action === "snap") data = { image_b64: "AAAA", mime: "image/jpeg", w: 2, h: 2 };
    p.send({ t: "result", id: m.id, ok: true, data });
  };
  p.send({ t: "hello", devices });
  return { p, cmds };
}

export const hubLines = (cmds: any[]) => cmds.filter((c) => c.device === "hub" && c.action === "line").map((c) => c.args.msg);
