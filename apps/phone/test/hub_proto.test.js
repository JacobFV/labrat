// cd apps/phone && npm test   (node --test test/*.test.js)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chunkLine, LineAssembler, HubLink, HubProvider, timeoutFor, helloMsg, MAX_LINE } from '../hub_proto.js';
import { pickVoice, splitSentences, Utterances } from '../voice.js';

const enc = new TextEncoder(), dec = new TextDecoder();
const join = (chunks) => dec.decode(Uint8Array.from(chunks.flatMap((c) => [...c])));
const tick = () => new Promise((r) => setImmediate(r));

// Manual fake timers so timeouts and the 1 s ping are deterministic.
function fakeTimers() {
  let now = 0, seq = 0; const q = new Map();
  return {
    setTimeout: (fn, ms) => { const id = ++seq; q.set(id, { fn, at: now + ms }); return id; },
    clearTimeout: (id) => q.delete(id),
    setInterval: (fn, ms) => { const id = ++seq; q.set(id, { fn, at: now + ms, every: ms }); return id; },
    clearInterval: (id) => q.delete(id),
    advance(ms) {
      const end = now + ms;
      for (;;) {
        let nextId = null, next = null;
        for (const [id, t] of q) if (t.at <= end && (!next || t.at < next.at)) { nextId = id; next = t; }
        if (!next) break;
        now = next.at;
        if (next.every) next.at += next.every; else q.delete(nextId);
        next.fn();
      }
      now = end;
    },
    get size() { return q.size; },
  };
}

test('chunkLine: 20-byte chunks, newline-terminated, size limit', () => {
  const msg = { cmd: 'servo', ch: 2, deg: 90, ms: 400, id: 12 };
  const chunks = chunkLine(msg);
  assert.ok(chunks.every((c) => c.length <= 20));
  assert.equal(join(chunks), JSON.stringify(msg) + '\n');
  assert.equal(chunkLine(msg, 512).length, 1);
  assert.throws(() => chunkLine({ cmd: 'x', pad: 'a'.repeat(MAX_LINE) }), /too long/);
});

test('LineAssembler: lines split across notifications, several per notification, split UTF-8', () => {
  const got = []; const a = new LineAssembler((l) => got.push(l));
  const bytes = enc.encode('{"id":1,"ok":true}\n{"ev":"in","name":"µ","v":1}\r\n\n{"id":2');
  for (let i = 0; i < bytes.length; i += 7) a.push(new DataView(bytes.buffer, i, Math.min(7, bytes.length - i)));
  assert.deepEqual(got, ['{"id":1,"ok":true}', '{"ev":"in","name":"µ","v":1}']);
  a.push(enc.encode(',"ok":false}\n'));
  assert.equal(got[2], '{"id":2,"ok":false}');
  a.push(enc.encode('x'.repeat(5000))); assert.equal(a.buf, ''); // runaway garbage dropped
});

test('timeoutFor: timed commands get their ms plus slack', () => {
  assert.equal(timeoutFor({ cmd: 'read' }), 5000);
  assert.equal(timeoutFor({ cmd: 'pulse', ms: 3000 }), 8000);
  assert.equal(timeoutFor({ cmd: 'pulse', timeout_ms: 100 }), 100);
});

function rig() {
  const timers = fakeTimers(), written = [], events = [];
  const link = new HubLink({ write: async (chunks) => { written.push(JSON.parse(join(chunks))); }, onEvent: (e) => events.push(e), timers });
  const reply = (o) => link.feed(enc.encode(JSON.stringify(o) + '\n'));
  return { timers, written, events, link, reply };
}

test('HubLink: assigns ids, matches out-of-order replies, ignores strays, routes events', async () => {
  const { written, events, link, reply } = rig();
  const p1 = link.request({ cmd: 'pulse', ch: 0, us: 1700, ms: 2000, id: 999 }); // caller id is overwritten
  const p2 = link.request({ cmd: 'read' });
  await tick();
  assert.deepEqual(written.map((w) => w.id), [1, 2]);
  assert.equal(written[0].cmd, 'pulse');
  reply({ ok: true });                            // id-less ping reply: stray
  reply({ ev: 'in', name: 'syr_home', v: 1 });
  reply({ id: 2, ok: true, inputs: { syr_home: 1 } });
  reply({ id: 1, ok: false, err: 'preempted' });
  assert.deepEqual(await p2, { id: 2, ok: true, inputs: { syr_home: 1 } });
  assert.deepEqual(await p1, { id: 1, ok: false, err: 'preempted' });
  assert.deepEqual(events, [{ ev: 'in', name: 'syr_home', v: 1 }]);
  assert.equal(link.pending.size, 0);
});

test('HubLink: timeout and close reject pending requests', async () => {
  const { timers, link } = rig();
  const p = link.request({ cmd: 'read' });
  timers.advance(5001);
  await assert.rejects(p, /timeout/);
  const q = link.request({ cmd: 'servo', ch: 1, deg: 10, ms: 500 });
  timers.advance(5000); // 500 + 5000 slack: not yet
  link.close('GATT disconnected');
  await assert.rejects(q, /GATT disconnected/);
});

test('HubLink: write failure rejects', async () => {
  const link = new HubLink({ write: async () => { throw new Error('GATT write failed'); }, timers: fakeTimers() });
  await assert.rejects(link.request({ cmd: 'read' }), /GATT write failed/);
});

function providerRig() {
  const r = rig(), out = [];
  const prov = new HubProvider({ send: (o) => { out.push(o); return true; }, timers: r.timers });
  r.link.onEvent = (e) => prov.onEvent(e); // as BleHub wires it
  return { ...r, out, prov };
}

test('HubProvider: hello on attach / socket open, ping only while both are up', async () => {
  const { timers, written, out, prov, link } = providerRig();
  prov.attach(link, { fw: 'servo-hub/1.0', name: 'labrat-hub' });
  assert.equal(out.length, 0, 'socket down: nothing sent');
  timers.advance(3000); await tick();
  assert.equal(written.length, 0, 'no ping without the DO socket');
  prov.socketOpen();
  assert.deepEqual(out[0], helloMsg('ok', { fw: 'servo-hub/1.0', name: 'labrat-hub' }));
  assert.deepEqual(out[0].devices[0], { id: 'hub', kind: 'servo_hub', status: 'ok', info: { transport: 'ble', fw: 'servo-hub/1.0', name: 'labrat-hub' } });
  assert.equal(out[1].t, 'state');
  timers.advance(3000); await tick();
  assert.equal(written.filter((w) => w.cmd === 'ping').length, 3);
  assert.ok(written.every((w) => w.id === undefined), 'pings carry no id');
  prov.socketClosed();
  timers.advance(5000); await tick();
  assert.equal(written.length, 3, 'pinging stops when the DO socket closes');
  out.length = 0;
  prov.socketOpen(); // reconnect: re-announce
  assert.equal(out[0].t, 'hello');
});

test('HubProvider: cmd line -> result, ok:false replies and unknown actions', async () => {
  const { written, out, prov, link, reply } = providerRig();
  prov.attach(link, {}); prov.socketOpen(); out.length = 0;
  assert.equal(prov.handle({ t: 'cmd', id: 'ab12', device: 'hub', action: 'line', args: { msg: { cmd: 'servo', ch: 3, deg: 45 } } }), true);
  await tick();
  assert.deepEqual(written.at(-1), { cmd: 'servo', ch: 3, deg: 45, id: 1 });
  reply({ id: 1, ok: true, deg: 45 }); await tick(); await tick();
  assert.deepEqual(out.at(-1), { t: 'result', id: 'ab12', ok: true, data: { id: 1, ok: true, deg: 45 } });

  prov.handle({ t: 'cmd', id: 'cd34', device: 'hub', action: 'line', args: { msg: { cmd: 'pulse', ch: 0, us: 1300, ms: 800 } } });
  await tick(); reply({ id: 2, ok: false, err: 'preempted' }); await tick(); await tick();
  assert.deepEqual(out.at(-1), { t: 'result', id: 'cd34', ok: false, data: { id: 2, ok: false, err: 'preempted' }, error: 'preempted' });

  prov.handle({ t: 'cmd', id: 'ef56', device: 'hub', action: 'dance', args: {} }); await tick();
  assert.equal(out.at(-1).ok, false); assert.match(out.at(-1).error, /unknown action/);
  prov.handle({ t: 'cmd', id: 'ef57', device: 'hub', action: 'line', args: {} }); await tick();
  assert.equal(out.at(-1).ok, false);
  assert.equal(prov.handle({ t: 'cmd', id: 'x', device: 'cnc', action: 'home' }), false, 'other devices are not ours');
});

test('HubProvider: events forwarded, detach reports error and fails pending, forget retracts', async () => {
  const { out, prov, link, reply, timers } = providerRig();
  prov.attach(link, {}); prov.socketOpen(); out.length = 0;
  reply({ ev: 'in', name: 'syr_home', v: 1 });
  assert.deepEqual(out[0], { t: 'event', device: 'hub', data: { ev: 'in', name: 'syr_home', v: 1 } });
  assert.equal(out[1].devices.hub.inputs.syr_home, 1);
  prov.handle({ t: 'cmd', id: 'p1', device: 'hub', action: 'line', args: { msg: { cmd: 'read' } } });
  await tick();
  prov.detach('GATT disconnected'); await tick(); await tick();
  const res = out.find((o) => o.t === 'result');
  assert.deepEqual([res.ok, res.error], [false, 'GATT disconnected']);
  const hello = out.filter((o) => o.t === 'hello').at(-1);
  assert.equal(hello.devices[0].status, 'error');
  prov.handle({ t: 'cmd', id: 'p2', device: 'hub', action: 'line', args: { msg: { cmd: 'read' } } }); await tick();
  assert.equal(out.at(-1).error, 'hub not connected');
  prov.forget();
  assert.deepEqual(out.at(-1), { t: 'hello', devices: [] });
  prov.socketClosed(); assert.equal(timers.size, 0, 'no timers left running');
});

test('pickVoice prefers good voices in the right language', () => {
  const v = (name, lang, extra = {}) => ({ name, lang, localService: false, default: false, ...extra });
  const voices = [v('Albert', 'en-US', { localService: true }), v('Thomas', 'fr-FR'), v('Samantha', 'en-US', { localService: true }), v('Google US English', 'en-US'), v('Daniel', 'en-GB')];
  assert.equal(pickVoice(voices, 'en-US').name, 'Samantha');
  assert.equal(pickVoice(voices, 'fr-FR').name, 'Thomas');
  assert.equal(pickVoice(voices, 'en-US', 'daniel').name, 'Daniel');
  assert.equal(pickVoice([], 'en-US'), null);
});

test('splitSentences keeps utterances short', () => {
  assert.deepEqual(splitSentences('Hi. Okay!'), ['Hi. Okay!'], 'tiny fragments are glued');
  assert.deepEqual(splitSentences('Moving the pipette to well A1 and aspirating fifty microlitres. Then I will dispense it into B2 and take a photo.'),
    ['Moving the pipette to well A1 and aspirating fifty microlitres.', 'Then I will dispense it into B2 and take a photo.']);
  const long = splitSentences('word '.repeat(100));
  assert.ok(long.length > 1 && long.every((s) => s.length <= 180));
  assert.deepEqual(splitSentences('  '), []);
});

test('Utterances: flush after a pause, longer wait for interim text', () => {
  const timers = fakeTimers(), got = [];
  const u = new Utterances({ flush: (t) => got.push(t), timers });
  u.update([{ text: 'aspirate fifty', final: false }]);
  timers.advance(1000); assert.deepEqual(got, []);
  u.update([{ text: 'aspirate fifty microlitres', final: true }]);
  timers.advance(799); assert.deepEqual(got, []);
  timers.advance(1); assert.deepEqual(got, ['aspirate fifty microlitres']);
  u.update([{ text: 'from', final: false }]);
  timers.advance(1800); assert.deepEqual(got.at(-1), 'from');
  u.update([{ text: 'x', final: true }]); u.cancel(); timers.advance(5000); assert.equal(got.length, 2);
});
