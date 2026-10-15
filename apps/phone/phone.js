// LabRat phone: the robot's face, eyes (camera) and ears/voice, on a pan-tilt stand above the deck.
// One socket, /ws/phone (docs/protocol.md §2): camera frames up, recognised speech up as text, say/face/transcript/snap
// down. With the servo hub paired over Web Bluetooth the page is also a device provider (§1) on the same socket.
import { createFace } from './face.js';
import { createSpeaker, createListener, recognitionSupported } from './voice.js';
import { HubProvider } from './hub_proto.js';
import { BleHub, bleSupported } from './hub_ble.js';

const $ = (s) => document.querySelector(s);
const qs = new URLSearchParams(location.search);
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch {} } };
const TOKEN = qs.get('token') || store.get('labrat_token') || 'labrat';
store.set('labrat_token', TOKEN);
const FPS = +(qs.get('fps') || 2), FRAME_W = +(qs.get('frame_w') || 640), LANG = qs.get('lang') || 'en-US';
const isIOS = /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

// ---------------- UI helpers ----------------
const face = createFace($('#eyes'), { onMood: (m) => { $('#mode').textContent = m; } });
const lines = [];
function transcript(role, text) {
  if (!text) return;
  if (lines.some((l) => l.role === role && l.text === text)) return; // the DO echoes what we already showed (say, our own speech)
  lines.push({ role, text }); while (lines.length > 3) lines.shift();
  const box = $('#transcript');
  for (const el of [...box.children]) if (el.id !== 'interim') el.remove();
  for (const l of lines) { const d = document.createElement('div'); d.className = l.role; d.textContent = (l.role === 'user' ? '› ' : '') + l.text; box.appendChild(d); }
}
const interim = (t) => { $('#interim').textContent = t ? '› ' + t : ''; };
function dbg(msg, level = 'warn') { const s = String(msg); $('#dbg').textContent = s; console.warn('[labrat]', s); send({ t: 'log', level, text: 'phone: ' + s }); }
function err(t) { $('#err').textContent = t; console.error(t); }

// ---------------- socket ----------------
let ws = null, backoff = 500;
function send(o) { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(o)); return true; } return false; }
const provider = new HubProvider({ send, log: (level, text) => send({ t: 'log', level, text }) });

function connect() {
  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/phone?token=${encodeURIComponent(TOKEN)}`;
  let sock;
  try { sock = new WebSocket(url); } catch { setTimeout(connect, backoff); return; }
  ws = sock;
  sock.onopen = () => { backoff = 500; $('#conn').textContent = 'online'; $('#conn').classList.add('on'); provider.socketOpen(); };
  sock.onclose = (e) => {
    if (ws !== sock) return;
    provider.socketClosed(); // stops the hub ping: the firmware watchdog relaxes the servos
    $('#conn').textContent = e.code === 4401 ? 'bad token' : 'offline'; $('#conn').classList.remove('on');
    setTimeout(connect, backoff); backoff = Math.min(8000, backoff * 1.7);
  };
  sock.onerror = () => { try { sock.close(); } catch {} };
  sock.onmessage = (ev) => { let m; try { m = JSON.parse(ev.data); } catch { return; } handle(m); };
}

function handle(m) {
  switch (m.t) {
    case 'face': if (m.mood && !(speaker.busy && m.mood === 'idle')) face.setMood(m.mood); face.setGaze(m.gaze); break;
    case 'say': if (m.text) { transcript('agent', m.text); speak(m.text); } break;
    case 'transcript': transcript(m.role === 'user' ? 'user' : 'agent', m.text); break;
    case 'snap': sendFrame(true); break;
    case 'cmd': if (!provider.handle(m)) send({ t: 'result', id: m.id, ok: false, error: `no device ${m.device} on this phone` }); break;
    case 'ping': send({ t: 'pong' }); break;
  }
}

// ---------------- voice ----------------
const speaker = createSpeaker({
  lang: LANG, voice: qs.get('voice') || '',
  onStart: () => { face.speaking(true); face.setMood('speaking'); },
  onBoundary: () => face.pulse(),
  onEnd: () => { face.speaking(false); if (face.mood === 'speaking') face.setMood('idle'); listener.resume(); },
  onError: (e) => dbg(e),
});
function speak(text) {
  if (!speaker.supported) return dbg('no speechSynthesis on this browser');
  listener.pause(); // hands-free must not hear the phone itself
  speaker.say(text);
  if (isIOS) showHint();
}
function userSaid(text) {
  text = text.trim(); if (!text) return;
  transcript('user', text);
  if (send({ t: 'text', text, final: true })) face.setMood('thinking');
  else dbg('offline: not sent');
}
const listener = createListener({
  lang: LANG, preferLocal: qs.get('ondevice') !== '0',
  onFinal: userSaid,
  onInterim: interim,
  onHeard: () => face.heard(),
  onState: (mode) => {
    face.listening(!!mode);
    $('#talkBtn').classList.toggle('rec', mode === 'ptt');
    if (mode && !speaker.busy) face.setMood('listening');
    else if (!mode && face.mood === 'listening') face.setMood('idle');
  },
  onError: (e) => { dbg(e); setHandsFree(listener.handsFree); },
});

let hintShown = false;
function showHint() { if (hintShown) return; hintShown = true; const h = $('#hint'); h.classList.remove('hidden'); requestAnimationFrame(() => h.classList.add('show')); setTimeout(() => h.classList.remove('show'), 9000); }

function setHandsFree(on) {
  listener.setHandsFree(on);
  $('#hfBtn').classList.toggle('on', listener.handsFree);
  store.set('labrat_hf', listener.handsFree ? '1' : '0');
}

// ---------------- camera ----------------
// Whichever camera faces the deck: front by default, ?cam=back (or the cam button) for the rear one.
const video = $('#cam'); let facing = qs.get('cam') === 'back' ? 'environment' : 'user', camStream = null;
async function startCamera() {
  if (camStream) for (const t of camStream.getTracks()) t.stop();
  camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: facing, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
  video.srcObject = camStream; video.classList.toggle('back', facing === 'environment');
  await video.play().catch(() => {});
}
const fcv = document.createElement('canvas'), fctx = fcv.getContext('2d');
let snapWaiting = false;
function sendFrame(force = false) {
  if (!ws || ws.readyState !== 1) return;
  if (!video.videoWidth) {
    // asked for a snap before the camera is up: try again shortly (at most ~3 s)
    if (force && !snapWaiting) { snapWaiting = true; let n = 0; const t = setInterval(() => { if (video.videoWidth || ++n > 30) { clearInterval(t); snapWaiting = false; if (video.videoWidth) sendFrame(true); } }, 100); }
    return;
  }
  if (!force && ws.bufferedAmount > 512 * 1024) return; // slow link: skip periodic frames, never a requested one
  const w = Math.min(FRAME_W, video.videoWidth), h = Math.round(video.videoHeight * w / video.videoWidth);
  fcv.width = w; fcv.height = h; fctx.drawImage(video, 0, 0, w, h);
  const url = fcv.toDataURL('image/jpeg', 0.6);
  send({ t: 'frame', jpeg: url.slice(url.indexOf(',') + 1), w, h });
}

// ---------------- servo hub (Web Bluetooth) ----------------
const hubStatus = (text, cls = '') => { const el = $('#hubStatus'); el.textContent = text; el.className = cls; };
const hub = bleSupported() ? new BleHub({
  chunk: +(qs.get('ble_chunk') || 20),
  onUp: (link, info, inputs) => { provider.attach(link, info, inputs); hubStatus('hub ok', 'on'); $('#hubBtn').textContent = 'unpair hub'; },
  onDown: (reason) => { provider.detach(reason); hubStatus('hub lost', 'err'); dbg('servo hub: ' + reason); },
  onEvent: (ev) => provider.onEvent(ev),
  onStatus: (t) => { if (t && !hub?.connected) hubStatus(t); },
}) : null;
if (hub) {
  $('#hubBtn').classList.remove('hidden');
  $('#hubBtn').onclick = async () => {
    if (hub.device) { hub.forget(); provider.forget(); hubStatus(''); $('#hubBtn').textContent = 'pair hub'; return; }
    try { await hub.pair(); } catch (e) { if (e.name !== 'NotFoundError') err('hub: ' + e.message); hubStatus(''); } // NotFoundError = chooser cancelled
  };
  hub.restore().catch(() => {});
}

// ---------------- wake lock ----------------
let wakeLock = null;
async function keepAwake() {
  try { if ('wakeLock' in navigator && !wakeLock) { wakeLock = await navigator.wakeLock.request('screen'); wakeLock.addEventListener('release', () => { wakeLock = null; }); } } catch (e) { console.warn('wakeLock', e); }
}
let started = false;
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible' || !started) return;
  keepAwake();
  if (camStream && !camStream.active) startCamera().catch(() => {});
});

// ---------------- start / controls ----------------
$('#startBtn').onclick = async () => {
  speaker.prime(); // inside the gesture: iOS speechSynthesis needs it
  keepAwake();
  try { document.documentElement.requestFullscreen?.().catch?.(() => {}); } catch {}
  const errs = [];
  if (!window.isSecureContext) errs.push('not a secure context: camera and microphone need HTTPS');
  try { await startCamera(); } catch (e) { errs.push('camera: ' + e.message); }
  if (!recognitionSupported()) {
    errs.push('speech recognition unsupported here: type instead');
    $('#talkBtn').classList.add('hidden'); $('#hfBtn').classList.add('hidden'); $('#kbBtn').classList.add('hidden');
    $('#textForm').classList.remove('hidden');
  } else if (qs.get('hf') === '1' || (qs.get('hf') !== '0' && store.get('labrat_hf') === '1')) setHandsFree(true);
  started = true;
  if (errs.length) { err(errs.join('\n')); setTimeout(() => { $('#start').style.display = 'none'; }, 2500); } else $('#start').style.display = 'none';
  $('#controls').classList.remove('hidden');
  setInterval(() => sendFrame(false), 1000 / FPS);
  face.setMood('happy'); setTimeout(() => face.mood === 'happy' && face.setMood('idle'), 1500);
};

const talk = $('#talkBtn');
let held = false;
const down = (e) => { e.preventDefault(); if (held) return; held = true; speaker.cancel(); listener.press(); };
const up = (e) => { e.preventDefault(); if (!held) return; held = false; listener.release(); };
talk.addEventListener('pointerdown', (e) => { talk.setPointerCapture?.(e.pointerId); down(e); });
talk.addEventListener('pointerup', up); talk.addEventListener('pointercancel', up);
talk.addEventListener('contextmenu', (e) => e.preventDefault());
$('#hfBtn').onclick = () => setHandsFree(!listener.handsFree);
$('#kbBtn').onclick = () => { const f = $('#textForm'); f.classList.toggle('hidden'); if (!f.classList.contains('hidden')) $('#textIn').focus(); };
$('#textForm').onsubmit = (e) => { e.preventDefault(); const t = $('#textIn').value; $('#textIn').value = ''; userSaid(t); };
$('#flipBtn').onclick = async () => { facing = facing === 'user' ? 'environment' : 'user'; try { await startCamera(); } catch (e) { err('camera: ' + e.message); } };
$('#previewBtn').onclick = () => video.classList.toggle('show');
if (!recognitionSupported()) { $('#talkBtn').classList.add('hidden'); $('#hfBtn').classList.add('hidden'); }

connect();
window.labrat = { face, speaker, listener, provider, hub, handle, sendFrame };
