// On-device voice: Web Speech API recognition (push-to-talk or hands-free) and speechSynthesis output.
// The pure helpers (pickVoice, splitSentences, Utterances) are exported for node tests; nothing here touches the
// DOM at import time.

// ---------------- pure helpers ----------------
const GOOD = /natural|neural|premium|enhanced|google us english|google uk english female|samantha|\bava\b|aria|jenny|zoe|serena|daniel|karen|moira|tessa/i;
const BAD = /compact|novelty|eloquence|whisper|bad news|bells|boing|bubbles|cellos|jester|organ|superstar|trinoids|zarvox|albert|\bfred\b|junior|ralph|grandma|grandpa|rocko|shelley|\bflo\b|eddy|\breed\b|sandy|wobble|good news|hysterical/i;

/** Best voice for `lang` (e.g. "en-US"); `pref` is a name substring that wins if present. */
export function pickVoice(voices, lang = 'en-US', pref = '') {
  if (!voices?.length) return null;
  if (pref) { const p = voices.find((v) => v.name.toLowerCase().includes(pref.toLowerCase())); if (p) return p; }
  const base = lang.slice(0, 2).toLowerCase();
  const score = (v) => {
    const vl = (v.lang || '').replace('_', '-').toLowerCase();
    if (!vl.startsWith(base)) return -100;
    let s = vl === lang.toLowerCase() ? 3 : 0;
    if (GOOD.test(v.name)) s += 4;
    if (BAD.test(v.name)) s -= 6;
    if (v.localService) s += 1; // keeps working offline
    if (v.default) s += 0.5;
    return s;
  };
  let best = null, bs = -Infinity;
  for (const v of voices) { const s = score(v); if (s > bs) { bs = s; best = v; } }
  return bs > -100 ? best : voices.find((v) => v.default) || voices[0];
}

/** Split long text into sentence-sized utterances (Chrome cuts utterances off after ~15 s). */
export function splitSentences(text, max = 180) {
  const parts = String(text || '').replace(/\s+/g, ' ').trim().match(/[^.!?;:]+[.!?;:]*\s*/g) || [];
  const out = [];
  for (let p of parts) {
    p = p.trim(); if (!p) continue;
    while (p.length > max) { let cut = p.lastIndexOf(' ', max); if (cut < max / 2) cut = max; out.push(p.slice(0, cut).trim()); p = p.slice(cut).trim(); }
    if (out.length && out[out.length - 1].length + p.length + 1 < 60) out[out.length - 1] += ' ' + p; // glue tiny fragments
    else if (p) out.push(p);
  }
  return out;
}

/**
 * Hands-free end-of-utterance logic, independent of the recogniser. Feed it each result list
 * (`[{text, final}]`, the whole session so far); it calls flush(text) once the speaker has paused:
 * `finalMs` after the last change when everything is final, `interimMs` when some text is still interim.
 */
export class Utterances {
  constructor({ flush, finalMs = 800, interimMs = 1800, timers = globalThis }) { Object.assign(this, { flush, finalMs, interimMs, timers }); this.timer = null; this.last = ''; }
  update(results) {
    const text = results.map((r) => r.text).join(' ').replace(/\s+/g, ' ').trim();
    const allFinal = results.length > 0 && results.every((r) => r.final);
    this.timers.clearTimeout(this.timer); this.timer = null;
    this.last = text;
    if (!text) return;
    this.timer = this.timers.setTimeout(() => this.fire(), allFinal ? this.finalMs : this.interimMs);
  }
  fire() { this.timers.clearTimeout(this.timer); this.timer = null; const t = this.last; this.last = ''; if (t) this.flush(t); }
  cancel() { this.timers.clearTimeout(this.timer); this.timer = null; this.last = ''; }
}

// ---------------- speech synthesis ----------------
export function createSpeaker({ lang = 'en-US', voice: pref = '', rate = 1.05, pitch = 1.05, onStart = () => {}, onEnd = () => {}, onBoundary = () => {}, onError = () => {} } = {}) {
  const synth = globalThis.speechSynthesis;
  let voice = null, queue = [], busy = false, gen = 0;
  const choose = () => { try { voice = pickVoice(synth.getVoices(), lang, pref); } catch {} };
  if (synth) { choose(); synth.addEventListener?.('voiceschanged', choose); }

  function next(myGen) {
    if (myGen !== gen) return;
    const text = queue.shift();
    if (text === undefined) { busy = false; onEnd(); return; }
    const u = new SpeechSynthesisUtterance(text);
    if (voice) { u.voice = voice; u.lang = voice.lang; } else u.lang = lang;
    u.rate = rate; u.pitch = pitch; u.volume = 1;
    let started = false, done = false;
    const finish = () => { if (done) return; done = true; clearTimeout(guard); next(myGen); };
    u.onstart = () => { started = true; onStart(); };
    u.onboundary = (e) => { if (!e.name || e.name === 'word') onBoundary(e); };
    u.onend = finish;
    u.onerror = (e) => { if (e.error !== 'interrupted' && e.error !== 'canceled') onError('speech: ' + e.error); finish(); };
    // iOS sometimes never fires start/end when the engine was not primed in a gesture: don't wedge the queue
    const guard = setTimeout(() => { if (!started && !synth.speaking) { onError('speechSynthesis never started'); finish(); } }, 5000);
    try { synth.resume(); synth.speak(u); } catch (e) { onError('speech: ' + e.message); finish(); }
  }

  return {
    supported: !!synth,
    get busy() { return busy; },
    get voice() { return voice; },
    say(text) {
      if (!synth) return;
      const parts = splitSentences(text); if (!parts.length) return;
      queue.push(...parts);
      if (!busy) { busy = true; next(gen); }
    },
    cancel() {
      if (!synth) return;
      gen++; queue = [];
      const was = busy; busy = false;
      try { synth.cancel(); } catch {}
      if (was) onEnd();
    },
    /** Call inside a user gesture: iOS only lets speechSynthesis talk after one gesture-initiated speak(). */
    prime() { if (!synth) return; try { const u = new SpeechSynthesisUtterance(' '); u.volume = 0; synth.speak(u); } catch {} },
  };
}

// ---------------- speech recognition ----------------
export const recognitionSupported = () => !!(globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition);

/**
 * Listener with two modes:
 *   push-to-talk  press() opens a session, release() stops it and emits the whole utterance via onFinal.
 *   hands-free    setHandsFree(true): sessions run back to back; Utterances decides when the speaker has finished.
 * pause()/resume() mute hands-free while the phone itself is talking (no echo into the recogniser).
 */
export function createListener({ lang = 'en-US', onFinal = () => {}, onInterim = () => {}, onState = () => {}, onHeard = () => {}, onError = () => {}, preferLocal = true } = {}) {
  const SR = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;
  if (!SR) return { supported: false, press() {}, release() {}, setHandsFree() {}, pause() {}, resume() {}, get handsFree() { return false; } };

  let rec = null, mode = null;        // mode of the running session: 'ptt' | 'hf' | null
  let hf = false, paused = false, pttHeld = false, local = false, failures = 0, restartTimer = null;
  let results = [], drop = false; // drop: this hands-free session already delivered its utterance
  const utt = new Utterances({ flush: (t) => { drop = true; onFinal(t); onInterim(''); if (rec && mode === 'hf') rec.abort(); } });

  // Chrome 139+ can recognise fully on-device; use it when the language pack is there (install it if allowed).
  (async () => {
    if (!preferLocal) return;
    try {
      if (SR.available) {
        const a = await SR.available({ langs: [lang], processLocally: true });
        if (a === 'available') local = true;
        else if (a === 'downloadable' && SR.install) SR.install({ langs: [lang], processLocally: true }).then((ok) => { if (ok) local = true; }).catch(() => {});
      }
    } catch {}
  })();

  function start(m) {
    clearTimeout(restartTimer); restartTimer = null;
    if (rec) return;
    const r = new SR();
    r.lang = lang; r.continuous = true; r.interimResults = true; r.maxAlternatives = 1;
    if (local && 'processLocally' in r) r.processLocally = true;
    results = []; drop = false; mode = m; rec = r;
    r.onstart = () => onState(m);
    r.onspeechstart = () => onHeard();
    r.onresult = (e) => {
      if (drop) return;
      results = Array.from(e.results, (x) => ({ text: (x[0]?.transcript || '').trim(), final: x.isFinal }));
      const all = results.map((x) => x.text).join(' ').trim();
      onInterim(all); onHeard(); failures = 0;
      if (mode === 'hf') utt.update(results);
    };
    r.onerror = (e) => {
      if (e.error === 'no-speech' || e.error === 'aborted') return;
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') { hf = false; onError('microphone / speech recognition not allowed'); }
      else if (e.error === 'language-not-supported' && local) { local = false; onError('on-device recognition unavailable, using default'); }
      else { failures++; onError('recognition: ' + e.error); }
    };
    r.onend = () => {
      const m0 = mode; rec = null; mode = null;
      if (m0 === 'ptt') {
        const text = results.map((x) => x.text).join(' ').replace(/\s+/g, ' ').trim();
        onInterim(''); if (text) onFinal(text);
      } else if (m0 === 'hf') utt.fire(); // session ended mid-sentence: send what we have
      results = [];
      onState(null);
      schedule();
    };
    try { r.start(); } catch (e) { rec = null; mode = null; onError('recognition start: ' + e.message); schedule(); }
  }

  function schedule() {
    if (!hf || paused || pttHeld || rec || restartTimer) return;
    const wait = failures ? Math.min(10000, 500 * 2 ** failures) : 250;
    restartTimer = setTimeout(() => { restartTimer = null; if (hf && !paused && !pttHeld) start('hf'); }, wait);
  }
  function stopSession(abort) { utt.cancel(); if (rec) { try { abort ? rec.abort() : rec.stop(); } catch {} } }

  return {
    supported: true,
    get handsFree() { return hf; },
    press() {
      pttHeld = true; clearTimeout(restartTimer); restartTimer = null;
      if (rec && mode === 'hf') { stopSession(true); setTimeout(() => pttHeld && !rec && start('ptt'), 150); return; }
      if (!rec) start('ptt');
    },
    release() {
      pttHeld = false;
      if (rec && mode === 'ptt') { try { rec.stop(); } catch {} } else schedule();
    },
    setHandsFree(on) {
      hf = !!on; failures = 0;
      if (hf) schedule(); else if (mode === 'hf') stopSession(true);
    },
    pause() { paused = true; if (mode === 'hf') stopSession(true); },
    resume() { paused = false; schedule(); },
  };
}
