// Servo hub over Web Bluetooth (Nordic UART service, docs/protocol.md §4). Android Chrome / desktop Chrome only:
// iOS Safari has no navigator.bluetooth, so the page hides the pair button there.
import { HubLink, DEFAULT_CHUNK } from './hub_proto.js';

export const NUS_SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';
export const NUS_RX = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'; // host writes
export const NUS_TX = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'; // hub notifies
export const HUB_NAME = 'labrat-hub';

export const bleSupported = () => typeof navigator !== 'undefined' && !!navigator.bluetooth;

/**
 * One paired hub. Events (callbacks):
 *   onUp(link, info, inputs)  GATT connected, notifications on; info = firmware hello, inputs = first read
 *   onDown(reason)      GATT dropped (it retries by itself with backoff until forget())
 *   onEvent(ev)         firmware {"ev":...} lines
 *   onStatus(text)      human-readable status for the UI
 */
export class BleHub {
  constructor({ onUp = () => {}, onDown = () => {}, onEvent = () => {}, onStatus = () => {}, chunk = DEFAULT_CHUNK } = {}) {
    Object.assign(this, { onUp, onDown, onEvent, onStatus, chunk });
    this.device = null; this.rx = null; this.tx = null; this.link = null;
    this.writeChain = Promise.resolve(); this.retry = 0; this.retryTimer = null; this.wanted = false;
    this._onDisc = () => this._disconnected('GATT disconnected');
    this._onNotify = (e) => this.link?.feed(e.target.value);
  }

  /** Must run inside a user gesture (chooser). */
  async pair() {
    const device = await navigator.bluetooth.requestDevice({ filters: [{ name: HUB_NAME }, { services: [NUS_SERVICE] }], optionalServices: [NUS_SERVICE] });
    return this.use(device);
  }

  /** Reconnect to a hub this origin was already granted (Chrome's getDevices(); may be behind a flag). */
  async restore() {
    if (!navigator.bluetooth?.getDevices) return false;
    const devs = await navigator.bluetooth.getDevices();
    const d = devs.find((x) => x.name === HUB_NAME);
    if (!d) return false;
    await this.use(d);
    return true;
  }

  async use(device) {
    if (this.device && this.device !== device) this.forget();
    this.device = device; this.wanted = true;
    device.removeEventListener('gattserverdisconnected', this._onDisc);
    device.addEventListener('gattserverdisconnected', this._onDisc);
    // out of range / powered off: report it and keep retrying in the background
    try { await this._connect(); } catch (e) { this._disconnected('connect failed: ' + e.message); }
  }

  async _connect() {
    clearTimeout(this.retryTimer); this.retryTimer = null;
    const d = this.device; if (!d || !this.wanted) return;
    this.onStatus(`connecting ${d.name || 'hub'}…`);
    const server = await d.gatt.connect();
    const svc = await server.getPrimaryService(NUS_SERVICE);
    this.rx = await svc.getCharacteristic(NUS_RX);
    this.tx = await svc.getCharacteristic(NUS_TX);
    this.link = new HubLink({ write: (chunks) => this._write(chunks), onEvent: (ev) => this.onEvent(ev), chunk: this.chunk,
      onStray: (l) => { if (!/^\s*\{\s*("ok"\s*:\s*true\s*)?\}\s*$/.test(l)) console.debug('[hub] stray', l); } });
    this.tx.removeEventListener('characteristicvaluechanged', this._onNotify);
    this.tx.addEventListener('characteristicvaluechanged', this._onNotify);
    await this.tx.startNotifications();
    let info = {}, inputs = {};
    try { const h = await this.link.request({ cmd: 'hello' }, 3000); const { id, ok, ...rest } = h; info = rest; } catch (e) { console.warn('[hub] hello', e); }
    try { const r = await this.link.request({ cmd: 'read' }, 3000); if (r.inputs) inputs = r.inputs; } catch {}
    this.retry = 0;
    this.onStatus(`hub ${info.fw || 'connected'}`);
    this.onUp(this.link, { name: d.name, ...info }, inputs);
  }

  /** GATT writes must not overlap: chain them. Prefer acknowledged writes when the characteristic allows them. */
  _write(chunks) {
    const rx = this.rx;
    const p = this.writeChain.then(async () => {
      if (!rx || !this.device?.gatt?.connected) throw new Error('hub not connected');
      const withResp = rx.properties?.write && rx.writeValueWithResponse;
      for (const c of chunks) {
        if (withResp) await rx.writeValueWithResponse(c);
        else if (rx.writeValueWithoutResponse) await rx.writeValueWithoutResponse(c);
        else await rx.writeValue(c);
      }
    });
    this.writeChain = p.catch(() => {});
    return p;
  }

  _disconnected(reason) {
    if (this.link) { this.link.close(reason); this.link = null; }
    this.rx = this.tx = null; this.writeChain = Promise.resolve();
    this.onDown(reason);
    if (!this.wanted || this.retryTimer) return;
    const wait = Math.min(15000, 1000 * 2 ** this.retry++);
    this.onStatus(`hub lost, retry in ${Math.round(wait / 1000)} s`);
    this.retryTimer = setTimeout(() => { this.retryTimer = null; this._connect().catch((e) => this._disconnected('reconnect failed: ' + e.message)); }, wait);
  }

  /** Stop using the hub (unpair button). */
  forget() {
    this.wanted = false; clearTimeout(this.retryTimer); this.retryTimer = null;
    if (this.link) { this.link.close('unpaired'); this.link = null; }
    try { this.device?.removeEventListener('gattserverdisconnected', this._onDisc); this.device?.gatt?.disconnect(); } catch {}
    this.device = null; this.rx = this.tx = null;
    this.onStatus('');
  }

  get connected() { return !!(this.link && this.device?.gatt?.connected); }
}
