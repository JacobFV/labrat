// DO-side logical devices on top of the provider's `hub` device (protocol.md §1, "Devices the DO implements").
// Each one serialises its actions (except `unlocked` ones), talks to the servo-hub firmware through `hub.line`
// (protocol.md §4), keeps its calibration in a D1 config row (cal:<id>) and its live state in DO storage.
import { CommandError, type Device, type Hub } from "../hub";

export abstract class Logical {
  abstract readonly id: string;
  abstract readonly kind: string;
  abstract readonly defaults: Record<string, any>;
  abstract state: Record<string, any>;
  cal: Record<string, any> = {};
  unlocked = new Set(["stop"]);
  /** Called after every action with the new state (the DO persists it). */
  persist: (id: string, state: Record<string, any>) => void = () => {};
  private chain: Promise<unknown> = Promise.resolve();
  private cfgSent = false;

  constructor(protected hub: Hub) {}

  init(cal: Record<string, any> | null, saved: Record<string, any> | null): void {
    this.cal = { ...structuredClone(this.defaults), ...(cal ?? {}) };
    if (saved) Object.assign(this.state, saved, { moving: false });
  }
  setCal(patch: Record<string, any>): Record<string, any> {
    this.cal = { ...this.cal, ...patch };
    this.cfgSent = false;
    return this.cal;
  }
  /** The hub provider (re)connected or left: channel config must be re-sent. */
  hubChanged(): void { this.cfgSent = false; this.state.moving = false; }

  /** hub channels this device drives, with their firmware cfg (kind pos|cont, rest_us...). */
  protected abstract channels(): Record<string, any>[];

  describe(): Device {
    const hub = this.hub.owner("hub")?.devices.get("hub");
    const status = !hub ? "offline" : hub.status === "mock" ? "mock" : hub.status === "error" ? "error" : "ok";
    return { id: this.id, kind: this.kind, status, info: { via: "hub", channels: this.channels().map((c) => c.ch) }, state: this.snapshot(), provider: null };
  }
  snapshot(): Record<string, any> { return { ...this.state }; }

  async run(action: string, args: Record<string, any> = {}): Promise<Record<string, any>> {
    const fn = (this as any)["a_" + action];
    if (typeof fn !== "function") throw new CommandError(`${this.id}: unknown action ${action}`);
    const exec = async () => {
      try {
        await fn.call(this, args ?? {});
      } finally {
        this.persist(this.id, this.snapshot());
        this.hub.pushDevices(false);
      }
      return this.snapshot();
    };
    if (this.unlocked.has(action)) return exec();
    const p = this.chain.then(exec);
    this.chain = p.catch(() => {});
    return p;
  }

  /** One firmware request through the hub provider; a firmware `ok:false` reply becomes an error. */
  protected async line(msg: Record<string, any>, timeoutS = 10): Promise<any> {
    if (!this.cfgSent && msg.cmd !== "cfg") {
      for (const c of this.channels()) await this.line({ cmd: "cfg", ...c });
      this.cfgSent = true;
    }
    const r = await this.hub.raw("hub", "line", { msg }, timeoutS);
    if (r && r.ok === false) throw new CommandError(`${this.id}: hub ${msg.cmd} failed: ${r.err ?? "error"}`);
    return r ?? {};
  }
}

export const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
