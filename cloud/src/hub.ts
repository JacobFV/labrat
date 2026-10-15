// WebSocket hub (protocol.md §1-3): device providers (bridge, phone with the BLE servo hub), command routing by device
// id with a pending-command map and timeouts, UI + phone broadcast, the latest phone frame, and the DO-side logical
// devices (pipette / probe / stand) layered on the provider's `hub` device.
import { hex, now } from "./store";
import { Logical, makeLogical } from "./devices";
import { Z_SAFE } from "./sim";

export type Role = "ui" | "phone" | "bridge";
export interface Attachment { role: Role; pid: string; at: number; hello?: DeviceDecl[] }
export interface DeviceDecl { id: string; kind: string; status: string; info?: Record<string, unknown> }
export interface Device extends DeviceDecl { info: Record<string, unknown>; state: Record<string, unknown>; provider: string | null }
export interface Frame { jpeg: string; w?: number; h?: number; ts: number }

export class CommandError extends Error {}

interface Provider { pid: string; role: Role; at: number; ws: WebSocket; devices: Map<string, Device> }
interface Pending { resolve(d: any): void; reject(e: Error): void; timer: ReturnType<typeof setTimeout>; pid: string }

const LOGICAL_KINDS = new Set(["pipette", "probe", "stand"]);

export class Hub {
  providers = new Map<string, Provider>();
  offline = new Map<string, Device>(); // devices whose provider left and nobody else announced them
  pending = new Map<string, Pending>();
  latestFrame: Frame | null = null;
  logical: Record<string, Logical>;
  log: (level: string, text: string, src?: string) => void = () => {};
  onEvent: (device: string, data: any) => void = () => {};
  services: () => Record<string, unknown> = () => ({});
  private lastDevBcast = 0;
  private devTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private state: DurableObjectState) {
    this.logical = makeLogical(this);
    // rebuild providers after hibernation from the sockets' attachments (state arrives again within ~1 s)
    for (const ws of state.getWebSockets()) {
      const a = ws.deserializeAttachment() as Attachment | null;
      if (a?.hello) this.hello(ws, a, a.hello, false);
    }
  }

  // ---------- sockets ----------
  sockets(role: Role): WebSocket[] { return this.state.getWebSockets(role); }

  send(ws: WebSocket, msg: unknown): boolean {
    try { ws.send(JSON.stringify(msg)); return true; } catch { return false; }
  }
  ui(msg: unknown): void { const s = JSON.stringify(msg); for (const ws of this.sockets("ui")) try { ws.send(s); } catch { /* closing */ } }
  phone(msg: unknown): void { const s = JSON.stringify(msg); for (const ws of this.sockets("phone")) try { ws.send(s); } catch { /* closing */ } }

  // ---------- provider messages (§1) ----------
  /** Returns true if the message was a provider message. */
  onProviderMessage(ws: WebSocket, a: Attachment, msg: any): boolean {
    switch (msg.t) {
      case "hello": {
        const devs: DeviceDecl[] = (msg.devices ?? []).filter((d: any) => d && typeof d.id === "string");
        a.hello = devs;
        a.at = now();
        try { ws.serializeAttachment(a); } catch { ws.serializeAttachment({ ...a, hello: devs.map(({ id, kind, status }) => ({ id, kind, status })) }); }
        this.hello(ws, a, devs, true);
        return true;
      }
      case "state": {
        const p = this.providers.get(a.pid);
        for (const [id, st] of Object.entries<any>(msg.devices ?? {})) {
          const d = p?.devices.get(id);
          if (d) d.state = st ?? {};
        }
        this.pushDevices(false);
        return true;
      }
      case "result": {
        const pend = this.pending.get(msg.id);
        if (pend) {
          this.pending.delete(msg.id);
          clearTimeout(pend.timer);
          if (msg.ok) pend.resolve(msg.data ?? {});
          else pend.reject(new CommandError(String(msg.error ?? "failed")));
        }
        return true;
      }
      case "event":
        this.onEvent(String(msg.device ?? ""), msg.data ?? {});
        return true;
      case "log":
        this.ui({ t: "log", src: a.role, level: msg.level ?? "info", text: String(msg.text ?? msg.msg ?? "") });
        return true;
    }
    return false;
  }

  private hello(ws: WebSocket, a: Attachment, devs: DeviceDecl[], announce: boolean): void {
    const devices = new Map<string, Device>();
    for (const d of devs) {
      devices.set(d.id, { id: d.id, kind: d.kind ?? "?", status: d.status ?? "ok", info: d.info ?? {}, state: (d as any).state ?? {}, provider: a.pid });
      this.offline.delete(d.id);
    }
    this.providers.set(a.pid, { pid: a.pid, role: a.role, at: a.at, ws, devices });
    if (announce) {
      this.log("info", `${a.role} ${a.pid} hello: ${devs.map((d) => d.id).join(", ") || "(no devices)"}`, "hub");
      if (devices.has("hub")) for (const l of Object.values(this.logical)) l.hubChanged();
      this.pushDevices();
    }
  }

  /** A socket closed: drop its provider, mark devices nobody else provides offline, fail its pending commands. */
  closed(a: Attachment): void {
    const p = this.providers.get(a.pid);
    if (!p) return;
    this.providers.delete(a.pid);
    for (const d of p.devices.values()) if (!this.owner(d.id)) this.offline.set(d.id, { ...d, status: "offline", provider: null });
    for (const [id, pend] of this.pending) {
      if (pend.pid !== a.pid) continue;
      this.pending.delete(id);
      clearTimeout(pend.timer);
      pend.reject(new CommandError(`provider ${a.role} ${a.pid} disconnected`));
    }
    if (p.devices.has("hub")) for (const l of Object.values(this.logical)) l.hubChanged();
    this.pushDevices();
  }

  /** The provider that announced `id` most recently. */
  owner(id: string): Provider | null {
    let best: Provider | null = null;
    for (const p of this.providers.values()) if (p.devices.has(id) && (!best || p.at >= best.at)) best = p;
    return best;
  }

  device(id: string): Device | null {
    if (id in this.logical) return this.logical[id].describe();
    return this.owner(id)?.devices.get(id) ?? this.offline.get(id) ?? null;
  }

  online(id: string): boolean {
    const d = this.device(id);
    return !!d && d.status !== "offline" && d.status !== "error";
  }

  allDevices(): Device[] {
    const out = new Map<string, Device>(this.offline);
    for (const p of [...this.providers.values()].sort((x, y) => x.at - y.at)) for (const d of p.devices.values()) out.set(d.id, d);
    for (const l of Object.values(this.logical)) out.set(l.id, l.describe());
    return [...out.values()];
  }

  devicesMsg() {
    const provs = [...this.providers.values()].map((p) => ({ pid: p.pid, role: p.role, at: p.at, devices: [...p.devices.keys()] }));
    return {
      t: "devices", providers: provs, devices: this.allDevices(), bridge: provs.some((p) => p.role === "bridge"),
      phones: this.sockets("phone").length, services: this.services(),
    };
  }

  /** Broadcast devices (throttled to 5 Hz unless forced). */
  pushDevices(force = true): void {
    const t = Date.now();
    if (!force && t - this.lastDevBcast < 200) {
      this.devTimer ??= setTimeout(() => { this.devTimer = null; this.pushDevices(); }, 200);
      return;
    }
    this.lastDevBcast = t;
    this.ui(this.devicesMsg());
  }

  // ---------- frames ----------
  setFrame(f: Frame): void { this.latestFrame = f; }

  /** Newest phone frame captured strictly after `after` (s); null if none arrives within `timeoutS`. */
  async waitFrame(after: number, timeoutS = 3): Promise<Frame | null> {
    const deadline = Date.now() + timeoutS * 1000;
    for (;;) {
      const f = this.latestFrame;
      if (f?.jpeg && f.ts > after) return f;
      if (Date.now() >= deadline) return null;
      await sleep(50);
    }
  }

  // ---------- commands ----------
  /**
   * Run `action` on `device`: a DO-side logical device, or a provider device routed to its most recent announcer.
   * Enforces the probe-arm rules: cnc.probe only with the arm deployed (retracted afterwards); the arm is retracted
   * before any CNC motion that can take the pipette below safe Z.
   */
  async cmd(device: string, action: string, args: Record<string, any> = {}, timeoutS = 30): Promise<any> {
    if (LOGICAL_KINDS.has(device)) return this.logical[device].run(action, args);
    if (device === "cnc") {
      const probe = this.logical.probe;
      if (action === "probe") {
        if (!probe.state.deployed) throw new CommandError("cnc.probe refused: the probe arm is not deployed (probe.deploy first)");
        try { return await this.raw(device, action, args, timeoutS); }
        finally { await probe.run("retract", {}).catch((e) => this.log("warn", `probe retract failed: ${e.message}`)); }
      }
      const lowers = (action === "move" && args.z != null && Number(args.z) < Z_SAFE - 1e-6) || (action === "jog" && Number(args.dz ?? 0) < 0) || action === "gcode";
      if (lowers) await (probe as any).ensureRetracted();
    }
    return this.raw(device, action, args, timeoutS);
  }

  /** Send a command to the provider owning `device` and wait for its result. */
  raw(device: string, action: string, args: Record<string, any> = {}, timeoutS = 30): Promise<any> {
    const p = this.owner(device);
    if (!p) return Promise.reject(new CommandError(this.providers.size ? `no provider offers device ${device}` : "no hardware provider connected"));
    const id = hex(16);
    if (!(device === "hub" && args?.msg?.cmd === "ping")) this.ui({ t: "log", src: "server", level: "info", text: `→ ${device}.${action} ${JSON.stringify(args)}` });
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new CommandError(`${device}.${action} timed out after ${timeoutS}s`));
      }, timeoutS * 1000);
      this.pending.set(id, { resolve, reject, timer, pid: p.pid });
      if (!this.send(p.ws, { t: "cmd", id, device, action, args })) {
        this.pending.delete(id);
        clearTimeout(timer);
        reject(new CommandError(`${device}.${action}: provider socket closed`));
      }
    }).then((d) => d, (e: Error) => { throw new CommandError(e.message.startsWith(device) ? e.message : `${device}.${action} failed: ${e.message}`); });
  }

  /** Fail every pending command (abort). */
  failAll(reason: string): void {
    for (const [id, pend] of this.pending) {
      this.pending.delete(id);
      clearTimeout(pend.timer);
      pend.reject(new CommandError(reason));
    }
  }
}

export const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
