// Servo hub line protocol (docs/protocol.md §4) and the provider glue of §1, transport-agnostic and DOM-free so it
// runs under `node --test`. hub_ble.js plugs a Web Bluetooth transport into HubLink; phone.js plugs HubProvider into
// the /ws/phone socket.

export const MAX_LINE = 256;            // firmware limit, bytes including the trailing \n
export const DEFAULT_CHUNK = 20;        // ATT_MTU 23 - 3: always safe, Web Bluetooth does not expose the MTU
export const DEFAULT_TIMEOUT_MS = 5000; // reply timeout for instant commands
export const MOTION_SLACK_MS = 5000;    // extra time allowed on top of a timed command's `ms`

const enc = new TextEncoder();

/** Encode one request as a UTF-8 line and split it into BLE-write-sized chunks. Throws if it exceeds MAX_LINE. */
export function chunkLine(obj, chunk = DEFAULT_CHUNK) {
  const bytes = enc.encode(JSON.stringify(obj) + '\n');
  if (bytes.length > MAX_LINE) throw new Error(`line too long (${bytes.length} > ${MAX_LINE} bytes)`);
  const n = Math.max(1, chunk | 0), out = [];
  for (let i = 0; i < bytes.length; i += n) out.push(bytes.subarray(i, i + n));
  return out;
}

/** Reassembles notification payloads into lines. UTF-8 sequences may be split across notifications. */
export class LineAssembler {
  constructor(onLine, maxBuf = 4 * MAX_LINE) { this.onLine = onLine; this.maxBuf = maxBuf; this.buf = ''; this.dec = new TextDecoder(); }
  push(data) {
    const u8 = data instanceof Uint8Array ? data : new Uint8Array(data.buffer ?? data, data.byteOffset ?? 0, data.byteLength ?? undefined);
    this.buf += this.dec.decode(u8, { stream: true });
    let i;
    while ((i = this.buf.indexOf('\n')) >= 0) {
      const line = this.buf.slice(0, i).replace(/\r$/, '');
      this.buf = this.buf.slice(i + 1);
      if (line.trim()) this.onLine(line);
    }
    if (this.buf.length > this.maxBuf) this.buf = ''; // garbage without newlines: drop it rather than grow forever
  }
  reset() { this.buf = ''; this.dec = new TextDecoder(); }
}

/** Timeout for one firmware request: timed motions reply when they finish. */
export function timeoutFor(msg, fallback = DEFAULT_TIMEOUT_MS) {
  if (Number.isFinite(+msg?.timeout_ms) && +msg.timeout_ms > 0) return +msg.timeout_ms;
  const ms = +msg?.ms;
  return Number.isFinite(ms) && ms > 0 ? ms + MOTION_SLACK_MS : fallback;
}

/**
 * Request/reply matching over a line transport.
 * write(chunks: Uint8Array[]) -> Promise sends one line; feed(bytes) takes notification data.
 * request(msg) assigns the firmware `id` and resolves with the reply object (ok:false replies resolve too: the
 * caller decides); it rejects on timeout, write failure or close().
 */
export class HubLink {
  constructor({ write, onEvent = () => {}, onStray = () => {}, chunk = DEFAULT_CHUNK, timers = globalThis } = {}) {
    this.write = write; this.onEvent = onEvent; this.onStray = onStray; this.chunk = chunk; this.timers = timers;
    this.nextId = 1; this.pending = new Map();
    this.asm = new LineAssembler((l) => this.onLine(l));
  }
  _id() { const id = this.nextId; this.nextId = this.nextId >= 999999 ? 1 : this.nextId + 1; return id; }

  request(msg, timeoutMs = timeoutFor(msg)) {
    const id = this._id();
    const { timeout_ms, ...rest } = msg || {}; // timeout_ms is ours, not the firmware's
    const line = { ...rest, id };
    let chunks;
    try { chunks = chunkLine(line, this.chunk); } catch (e) { return Promise.reject(e); }
    return new Promise((resolve, reject) => {
      const timer = this.timers.setTimeout(() => { this.pending.delete(id); reject(new Error(`hub timeout (${msg.cmd} id ${id})`)); }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      Promise.resolve().then(() => this.write(chunks)).catch((e) => {
        if (!this.pending.has(id)) return;
        this.timers.clearTimeout(timer); this.pending.delete(id); reject(e);
      });
    });
  }

  /** Fire-and-forget line without an id (the 1 s watchdog ping). */
  notify(msg) { return Promise.resolve().then(() => this.write(chunkLine(msg, this.chunk))); }

  feed(bytes) { this.asm.push(bytes); }

  onLine(line) {
    let m; try { m = JSON.parse(line); } catch { return this.onStray(line); }
    if (!m || typeof m !== 'object') return this.onStray(line);
    if (m.ev !== undefined) return this.onEvent(m);
    const p = m.id !== undefined ? this.pending.get(m.id) : undefined;
    if (!p) return this.onStray(line); // e.g. a reply to an id-less ping, or one that already timed out
    this.timers.clearTimeout(p.timer); this.pending.delete(m.id); p.resolve(m);
  }

  /** Fail every pending request (GATT disconnect). */
  close(reason = 'hub disconnected') {
    for (const [id, p] of this.pending) { this.timers.clearTimeout(p.timer); p.reject(new Error(reason)); this.pending.delete(id); }
    this.asm.reset();
  }
}

// ---------------- provider (§1) ----------------
export const HUB_ID = 'hub';

export const helloMsg = (status, info = {}) => ({ t: 'hello', devices: [{ id: HUB_ID, kind: 'servo_hub', status, info: { transport: 'ble', ...info } }] });
export const stateMsg = (state) => ({ t: 'state', devices: { [HUB_ID]: state } });
export const eventMsg = (data) => ({ t: 'event', device: HUB_ID, data });
export const resultMsg = (id, ok, data = {}, error) => (ok ? { t: 'result', id, ok: true, data } : { t: 'result', id, ok: false, data, error: error || 'error' });

/**
 * Glue between a DO socket and a HubLink. The page calls:
 *   attach(link, info) / detach(reason) when the hub's GATT link comes and goes,
 *   socketOpen() / socketClosed() for the DO socket,
 *   handle(msg) for every DO message (returns true when it consumed it).
 * send(obj) -> bool writes to the DO socket.
 */
export class HubProvider {
  constructor({ send, log = () => {}, timers = globalThis, pingMs = 1000, stateMs = 1000 } = {}) {
    this.send = send; this.log = log; this.timers = timers; this.pingMs = pingMs; this.stateMs = stateMs;
    this.link = null; this.info = {}; this.status = null; this.sockUp = false;
    this.inputs = {}; this.lastEvent = null; this.pingTimer = null; this.stateTimer = null; this.announced = false;
  }

  get state() { return { connected: !!this.link, inputs: this.inputs, last_event: this.lastEvent }; }

  announce() {
    if (!this.sockUp || !this.status) return;
    this.send(helloMsg(this.status, this.info));
    this.send(stateMsg(this.state));
    this.announced = true;
  }

  attach(link, info = {}, inputs = {}) {
    this.link = link; this.info = info; this.status = 'ok'; this.inputs = { ...inputs };
    this.announce(); this._timers();
  }

  /** GATT link lost (or the user unpaired). status 'error' keeps the device listed but unusable. */
  detach(reason = 'disconnected', status = 'error') {
    if (this.link) this.link.close(reason);
    this.link = null; this.status = status; this.info = { ...this.info, error: reason };
    this.announce(); this._timers();
  }

  /** User forgot the hub: retract the device from this provider entirely. */
  forget() {
    if (this.link) this.link.close('unpaired');
    this.link = null; this.status = null; this.info = {};
    if (this.sockUp && this.announced) this.send({ t: 'hello', devices: [] });
    this.announced = false; this._timers();
  }

  socketOpen() { this.sockUp = true; this.announced = false; this.announce(); this._timers(); }
  socketClosed() { this.sockUp = false; this.announced = false; this._timers(); }

  _timers() {
    const wantPing = this.sockUp && !!this.link;
    if (wantPing && !this.pingTimer) {
      this.pingTimer = this.timers.setInterval(() => { this.link?.notify({ cmd: 'ping' }).catch((e) => this.log('warn', 'hub ping: ' + e.message)); }, this.pingMs);
    } else if (!wantPing && this.pingTimer) { this.timers.clearInterval(this.pingTimer); this.pingTimer = null; }
    const wantState = this.sockUp && !!this.status;
    if (wantState && !this.stateTimer) this.stateTimer = this.timers.setInterval(() => this.send(stateMsg(this.state)), this.stateMs);
    else if (!wantState && this.stateTimer) { this.timers.clearInterval(this.stateTimer); this.stateTimer = null; }
  }

  /** Firmware event -> DO. */
  onEvent(ev) {
    if (ev.ev === 'in' && ev.name !== undefined) this.inputs = { ...this.inputs, [ev.name]: ev.v };
    if (ev.ev === 'estop') this.inputs = { ...this.inputs, estop: ev.v };
    this.lastEvent = ev.ev;
    if (ev.ev === 'wd') this.log('warn', 'servo hub watchdog fired: servos relaxed');
    if (this.sockUp) { this.send(eventMsg(ev)); this.send(stateMsg(this.state)); }
  }

  handle(m) {
    if (!m || m.t !== 'cmd' || m.device !== HUB_ID) return false;
    this.run(m).then((r) => this.send(r));
    return true;
  }

  async run(m) {
    const { id, action, args = {} } = m;
    if (!this.link) return resultMsg(id, false, {}, 'hub not connected');
    let msg;
    if (action === 'line') {
      msg = args.msg;
      if (!msg || typeof msg !== 'object' || typeof msg.cmd !== 'string') return resultMsg(id, false, {}, 'line needs args.msg {cmd,...}');
    } else if (action === 'stop') msg = { cmd: 'stop' };
    else if (action === 'read') msg = { cmd: 'read' };
    else return resultMsg(id, false, {}, `unknown action ${action}`);
    try {
      const reply = await this.link.request(msg);
      if (reply.inputs && typeof reply.inputs === 'object') this.inputs = { ...this.inputs, ...reply.inputs };
      return reply.ok === false ? resultMsg(id, false, reply, reply.err || 'hub error') : resultMsg(id, true, reply);
    } catch (e) {
      return resultMsg(id, false, {}, e.message || String(e));
    }
  }
}
