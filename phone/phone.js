// LabRat phone (iPhone on a static stand looking down at the CNC bed; it never moves): animated eyes + camera frames + push-to-talk/VAD audio + TTS playback over /ws/phone.
const $ = (s) => document.querySelector(s);
const qs = new URLSearchParams(location.search);
let TOKEN = qs.get('token') || (() => { try { return localStorage.getItem('labrat_token'); } catch { return null; } })() || 'labrat';
try { localStorage.setItem('labrat_token', TOKEN); } catch {}
const FPS = +(qs.get('fps') || 2), FRAME_W = 640;

// ---------------- eyes ----------------
const cv = $('#eyes'), ctx = cv.getContext('2d');
const face = { mood: 'idle', gaze: [0, 0], tgaze: [0, 0], blink: 0, nextBlink: 2, talk: 0, moodT: 0, lastInput: 0 };
const MOODS = {
  idle:      { color: [110, 231, 200], open: 1.0,  lid: 0,    brow: 0 },
  listening: { color: [110, 200, 255], open: 1.12, lid: 0,    brow: 0.1 },
  thinking:  { color: [245, 215, 110], open: 0.75, lid: 0.25, brow: -0.1 },
  speaking:  { color: [110, 231, 200], open: 1.0,  lid: 0,    brow: 0 },
  happy:     { color: [245, 215, 110], open: 0.9,  lid: 0,    brow: 0.2, smile: true },
  alert:     { color: [255, 90, 90],   open: 1.25, lid: 0,    brow: 0.3 },
};
let cur = { ...MOODS.idle, color: [...MOODS.idle.color] };
function setMood(m) { if (MOODS[m]) { face.mood = m; face.moodT = 0; } $('#mode').textContent = m; }
function resize() { const d = Math.min(devicePixelRatio || 1, 2); cv.width = innerWidth * d; cv.height = innerHeight * d; ctx.setTransform(d, 0, 0, d, 0, 0); }
addEventListener('resize', resize); resize();
const lerp = (a, b, t) => a + (b - a) * t;
let lastT = performance.now();
function draw(now) {
  const dt = Math.min(0.1, (now - lastT) / 1000); lastT = now; face.moodT += dt;
  const tgt = MOODS[face.mood] || MOODS.idle; const k = 1 - Math.pow(0.001, dt);
  cur.open = lerp(cur.open, tgt.open, k); cur.lid = lerp(cur.lid, tgt.lid, k); cur.brow = lerp(cur.brow, tgt.brow, k);
  for (let i = 0; i < 3; i++) cur.color[i] = lerp(cur.color[i], tgt.color[i], k);
  // blink
  face.nextBlink -= dt;
  if (face.nextBlink <= 0) { face.blink = 1; face.nextBlink = 2 + Math.random() * 4; if (Math.random() < .2) face.nextBlink = .25; }
  face.blink = Math.max(0, face.blink - dt * 7);
  const blinkAmt = face.blink > 0 ? Math.sin(face.blink * Math.PI) : 0;
  // gaze: idle saccades unless server set gaze recently
  if (now - face.lastInput > 4000 && Math.random() < dt * 0.6) face.tgaze = [(Math.random() * 2 - 1) * .6, (Math.random() * 2 - 1) * .35];
  if (face.mood === 'thinking') face.tgaze = [Math.sin(now / 900) * .5, -.45];
  face.gaze[0] = lerp(face.gaze[0], face.tgaze[0], 1 - Math.pow(0.0001, dt));
  face.gaze[1] = lerp(face.gaze[1], face.tgaze[1], 1 - Math.pow(0.0001, dt));
  // talking amplitude
  const speaking = playingNow; // driven by real playback events, not by receipt of the say message
  face.talk = speaking ? (0.5 + 0.5 * Math.sin(now / 70)) * (0.5 + 0.5 * Math.sin(now / 173)) : lerp(face.talk, 0, k);

  const W = innerWidth, H = innerHeight; ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
  const portrait = H > W;
  const size = Math.min(W, H) * (portrait ? 0.26 : 0.22);
  const cx = W / 2, cy = H * (portrait ? 0.4 : 0.45), gap = size * 1.25;
  const [r, g, b] = cur.color.map(Math.round); const col = `rgb(${r},${g},${b})`;
  const breathe = 1 + Math.sin(now / 1400) * 0.015;
  for (const side of [-1, 1]) {
    const ex = cx + side * gap + face.gaze[0] * size * .35, ey = cy + face.gaze[1] * size * .35;
    const w = size * .8 * breathe, h = size * cur.open * breathe * (1 - blinkAmt * .92) * (playingNow ? 1 - face.talk * .08 : 1);
    ctx.save();
    ctx.shadowColor = `rgba(${r},${g},${b},.55)`; ctx.shadowBlur = size * .45;
    ctx.fillStyle = col;
    roundRect(ex - w / 2, ey - h / 2, w, Math.max(4, h), Math.min(w, Math.max(4, h)) * .45); ctx.fill();
    ctx.restore();
    // pupil highlight
    if (h > size * .3) { ctx.fillStyle = 'rgba(0,0,0,.85)'; const pw = w * .32, ph = h * .38; ctx.beginPath(); ctx.ellipse(ex + face.gaze[0] * w * .18, ey + face.gaze[1] * h * .15, pw / 2, ph / 2, 0, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.beginPath(); ctx.arc(ex + face.gaze[0] * w * .18 - pw * .18, ey + face.gaze[1] * h * .15 - ph * .2, pw * .14, 0, Math.PI * 2); ctx.fill(); }
    // upper lid (thinking / sleepy) & brow tilt (alert)
    if (cur.lid > .02) { ctx.fillStyle = '#000'; ctx.save(); ctx.translate(ex, ey - h / 2); ctx.rotate(side * -0.15); ctx.fillRect(-w, -h, w * 2, h + h * cur.lid); ctx.restore(); }
    if (MOODS[face.mood].smile) { ctx.fillStyle = '#000'; ctx.beginPath(); ctx.ellipse(ex, ey + h * .62, w * .7, h * .42, 0, 0, Math.PI * 2); ctx.fill(); }
    if (Math.abs(cur.brow) > .05) { ctx.strokeStyle = col; ctx.lineWidth = size * .07; ctx.lineCap = 'round'; ctx.beginPath(); const by = ey - h / 2 - size * .25; ctx.moveTo(ex - w * .45, by + side * cur.brow * size * .3); ctx.lineTo(ex + w * .45, by - side * cur.brow * size * .3); ctx.stroke(); }
  }
  // mouth: a thin bar that wiggles while speaking
  const mw = size * .9, mh = Math.max(3, size * .06 + face.talk * size * .32);
  ctx.fillStyle = `rgba(${r},${g},${b},${face.talk > .05 ? .9 : .35})`;
  roundRect(cx - mw / 2, cy + size * 1.05 - mh / 2, mw, mh, mh / 2); ctx.fill();
  // listening ring / recording indicator
  if (recording) { ctx.strokeStyle = 'rgba(255,90,90,.8)'; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(cx, cy + size * 1.05, size * .2 + level * size * 2, 0, Math.PI * 2); ctx.stroke(); }
  requestAnimationFrame(draw);
}
function roundRect(x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }

// ---------------- transcript ----------------
const lines = [];
function transcript(role, text) {
  if (!text) return; lines.push({ role, text }); while (lines.length > 3) lines.shift();
  $('#transcript').innerHTML = ''; for (const l of lines) { const d = document.createElement('div'); d.className = l.role; d.textContent = (l.role === 'user' ? '› ' : '') + l.text; $('#transcript').appendChild(d); }
}

// ---------------- websocket ----------------
let ws = null, backoff = 500;
function send(o) { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(o)); return true; } return false; }
function connect() {
  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/phone?token=${encodeURIComponent(TOKEN)}`;
  try { ws = new WebSocket(url); } catch { setTimeout(connect, backoff); return; }
  ws.onopen = () => { backoff = 500; $('#conn').textContent = 'online'; $('#conn').classList.add('on'); send({ t: 'hello', ua: navigator.userAgent, w: innerWidth, h: innerHeight }); };
  ws.onclose = () => { $('#conn').textContent = 'offline'; $('#conn').classList.remove('on'); setTimeout(connect, backoff); backoff = Math.min(8000, backoff * 1.7); };
  ws.onerror = () => { try { ws.close(); } catch {} };
  ws.onmessage = (ev) => { let m; try { m = JSON.parse(ev.data); } catch { return; } handle(m); };
}
function handle(m) {
  if (m.t === 'face') { if (m.mood) setMood(m.mood); if (Array.isArray(m.gaze)) { face.tgaze = [Math.max(-1, Math.min(1, +m.gaze[0] || 0)), Math.max(-1, Math.min(1, +m.gaze[1] || 0))]; face.lastInput = performance.now(); } }
  else if (m.t === 'say') { if (m.text) transcript('agent', m.text); say(m.text, m.audio_b64, m.mime); }
  else if (m.t === 'transcript') transcript(m.role === 'user' ? 'user' : 'agent', m.text);
  else if (m.t === 'ping') send({ t: 'pong' });
}

// ---------------- audio out ----------------
// iOS rules this section works around:
//  * media can only start from a user gesture -> ONE <audio> element (in the DOM) is unlocked on Start and on every
//    talk press, and is reused for all playback (new Audio() created later, outside a gesture, is blocked).
//  * Web Audio + speechSynthesis are muted by the ring/silent switch; <audio> with audioSession 'playback' is not.
//  * an open mic (getUserMedia) puts iOS in play-and-record, routing output to the quiet earpiece -> mic is released
//    after every push-to-talk utterance and the session is switched to 'playback' before we play.
const audioEl = document.createElement('audio');
audioEl.playsInline = true; audioEl.setAttribute('playsinline', ''); audioEl.setAttribute('webkit-playsinline', ''); audioEl.preload = 'auto';
audioEl.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none';
document.body.appendChild(audioEl);
const SILENT_MP3 = 'data:audio/mpeg;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4LjI5LjEwMAAAAAAAAAAAAAAA//tAwAAAAAAAAAAAAAAAAAAAAAAASW5mbwAAAA8AAAACAAABhgC7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7//////////////////////////////////////////////////////////////////8AAAAATGF2YzU4LjU0AAAAAAAAAAAAAAAAJAAAAAAAAAAAAYYoRBqpAAAAAAD/+xDEAAPAAAGkAAAAIAAANIAAAARMQU1FMy4xMDBVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV//sQxDsDwAABpAAAACAAADSAAAAEVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV';
let actx = null;          // the ONE shared AudioContext (mic level meter + webaudio fallback playback)
let playingNow = false;   // true only while sound is actually coming out -> drives the mouth
let curUrl = null;        // blob URL of the clip in audioEl (revoked when replaced)

function dbg(msg, report = true) {
  const s = String(msg); const el = $('#dbg'); if (el) el.textContent = s;
  console.warn('[labrat]', s);
  if (report) send({ t: 'log', msg: s });
}
function setAudioSession(type) { try { if (navigator.audioSession && navigator.audioSession.type !== type) navigator.audioSession.type = type; } catch (e) { dbg('audioSession ' + type + ': ' + e.message); } }
function getActx() {
  if (!actx) { const AC = window.AudioContext || window.webkitAudioContext; if (AC) actx = new AC(); }
  return actx;
}
// Call synchronously at the top of every user-gesture handler (Start tap, talk press).
function unlockAudio() {
  try {
    const ac = getActx();
    if (ac) {
      if (ac.state !== 'running') ac.resume().catch(() => {});
      const b = ac.createBuffer(1, 1, 22050), s = ac.createBufferSource(); s.buffer = b; s.connect(ac.destination); s.start(0);
    }
  } catch (e) { dbg('unlock ctx: ' + e.message); }
  // prime the shared <audio> element (only if it isn't busy with a real clip)
  if (audioEl.paused || !playingNow) {
    try {
      audioEl.onended = audioEl.onerror = audioEl.onplaying = null;
      audioEl.muted = false; audioEl.src = SILENT_MP3;
      const p = audioEl.play(); if (p && p.catch) p.catch((e) => dbg('unlock <audio>: ' + (e && e.name) + ' ' + (e && e.message)));
    } catch (e) { dbg('unlock <audio>: ' + e.message); }
  }
  try { if ('speechSynthesis' in window && !speechSynthesis.speaking) { const u = new SpeechSynthesisUtterance(''); u.volume = 0; speechSynthesis.speak(u); } } catch {}
}
let hintShown = false;
function showHint() {
  if (hintShown) return; hintShown = true;
  const h = $('#hint'); if (!h) return; h.classList.add('show'); setTimeout(() => h.classList.remove('show'), 9000);
}

const sayQueue = []; let sayBusy = false;
function say(text, b64, mime) { sayQueue.push({ text, b64, mime }); if (!sayBusy) nextSay(); }
function nextSay() {
  const it = sayQueue.shift();
  if (!it) { sayBusy = false; playingNow = false; if (face.mood === 'speaking') setMood('idle'); return; }
  sayBusy = true; showHint();
  // release a held mic (PTT) so iOS leaves play-and-record (earpiece) before we play
  if (!vadOn) releaseMic();
  if (!micStream) setAudioSession('playback'); // (VAD keeps the mic open; switching would kill capture)
  let finished = false;
  const done = () => { if (finished) return; finished = true; playingNow = false; audioEl.onended = audioEl.onerror = audioEl.onplaying = audioEl.onpause = null; setTimeout(nextSay, 120); };
  if (it.b64) playClip(it, done); else tts(it.text, done);
}
function b64ToBlob(b64, type) {
  const bin = atob(b64); const u8 = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return new Blob([u8], { type });
}
function playClip(it, done) {
  const type = it.mime || 'audio/mpeg';
  let blob; try { blob = b64ToBlob(it.b64, type); } catch (e) { dbg('bad audio_b64: ' + e.message); return tts(it.text, done); }
  if (curUrl) { try { URL.revokeObjectURL(curUrl); } catch {} }
  curUrl = URL.createObjectURL(blob);
  audioEl.onplaying = () => { playingNow = true; setMood('speaking'); };
  audioEl.onended = done;
  audioEl.onpause = () => { if (audioEl.ended) return; playingNow = false; };
  audioEl.onerror = () => { const e = audioEl.error; dbg('audio error ' + (e ? e.code + ' ' + (e.message || '') : '?') + ' -> webaudio'); playWebAudio(blob, it, done); };
  audioEl.muted = false; audioEl.volume = 1;
  audioEl.src = curUrl; // blob: URL, type audio/mpeg
  const p = audioEl.play();
  if (p && p.catch) p.catch((e) => {
    if (e && e.name === 'AbortError') return; // superseded by barge-in / new clip
    dbg('play() rejected: ' + (e && e.name) + ' ' + (e && e.message) + ' -> webaudio');
    audioEl.onerror = null; playWebAudio(blob, it, done);
  });
}
// fallback #1: Web Audio (works if the context was unlocked, but obeys the silent switch)
async function playWebAudio(blob, it, done) {
  try {
    const ac = getActx(); if (!ac) throw new Error('no AudioContext');
    if (ac.state !== 'running') await ac.resume();
    if (ac.state !== 'running') throw new Error('AudioContext ' + ac.state);
    const buf = await new Promise((res, rej) => blob.arrayBuffer().then((ab) => ac.decodeAudioData(ab, res, rej)).catch(rej));
    const src = ac.createBufferSource(); src.buffer = buf; src.connect(ac.destination);
    src.onended = done; playingNow = true; setMood('speaking'); src.start(0); showHint();
    curWebSrc = src;
  } catch (e) { dbg('webaudio failed: ' + (e && e.message) + ' -> speechSynthesis'); tts(it.text, done); }
}
let curWebSrc = null;
// fallback #2: speechSynthesis (also obeys the silent switch)
function tts(text, done) {
  if (!text || !('speechSynthesis' in window)) { if (text) dbg('no speechSynthesis'); return done(); }
  try {
    const u = new SpeechSynthesisUtterance(text); u.rate = 1.05; u.pitch = 1.1; u.volume = 1;
    u.onstart = () => { playingNow = true; setMood('speaking'); };
    u.onend = done; u.onerror = (e) => { dbg('speechSynthesis error: ' + (e && e.error)); done(); };
    speechSynthesis.cancel(); speechSynthesis.speak(u);
    // iOS sometimes never fires onstart/onend when not primed; don't wedge the queue
    setTimeout(() => { if (!playingNow && !speechSynthesis.speaking) { dbg('speechSynthesis never started'); done(); } }, 4000);
  } catch (e) { dbg('speechSynthesis: ' + e.message); done(); }
}
function stopSpeaking() {
  sayQueue.length = 0; sayBusy = false; playingNow = false;
  audioEl.onended = audioEl.onerror = audioEl.onplaying = audioEl.onpause = null;
  try { audioEl.pause(); } catch {}
  try { curWebSrc && curWebSrc.stop(); } catch {} curWebSrc = null;
  try { speechSynthesis?.cancel(); } catch {}
}

// ---------------- camera ----------------
// The phone is fixed on a stand above the CNC bed. Use whichever camera faces the bed: front by default, ?cam=back
// for the rear camera (or the ⟲ cam button). Frames stream continuously; the server waits for one newer than the photo step.
const video = $('#cam'); let facing = qs.get('cam') === 'back' ? 'environment' : 'user'; let camStream = null;
async function startCamera() {
  if (camStream) for (const t of camStream.getTracks()) t.stop();
  camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: facing, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
  video.srcObject = camStream; video.classList.toggle('back', facing === 'environment');
  await video.play().catch(() => {});
}
const fcv = document.createElement('canvas'), fctx = fcv.getContext('2d');
let frameBusy = false;
function sendFrame() {
  if (frameBusy || !video.videoWidth || !ws || ws.readyState !== 1 || ws.bufferedAmount > 512 * 1024) return;
  frameBusy = true;
  const w = FRAME_W, h = Math.round(video.videoHeight * w / video.videoWidth);
  fcv.width = w; fcv.height = h; fctx.drawImage(video, 0, 0, w, h);
  const url = fcv.toDataURL('image/jpeg', 0.6);
  send({ t: 'frame', jpeg: url.slice(url.indexOf(',') + 1), w, h, facing });
  frameBusy = false;
}

// ---------------- mic / recording ----------------
// Push-to-talk acquires the mic on press and releases it (track.stop()) once the utterance is recorded, so iOS
// isn't left in play-and-record (earpiece / ducked output) while the reply plays. VAD mode needs it open.
let micStream = null, micSrc = null, analyser = null, recorder = null, chunks = [], recording = false, recStart = 0, level = 0;
let vadOn = qs.get('vad') === '1', vadThresh = +(qs.get('vad_th') || 0.04), lastLoud = 0;
const MIME = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/aac'].find((m) => window.MediaRecorder?.isTypeSupported?.(m)) || '';
let micPending = null;
async function acquireMic() {
  if (micStream && micStream.active) return micStream;
  if (micPending) return micPending;
  setAudioSession('play-and-record');
  micPending = (async () => {
    const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
    micStream = s;
    try { const ac = getActx(); if (ac) { micSrc = ac.createMediaStreamSource(s); analyser = ac.createAnalyser(); analyser.fftSize = 1024; micSrc.connect(analyser); } }
    catch (e) { console.warn('analyser', e); }
    return s;
  })();
  try { return await micPending; } finally { micPending = null; }
}
function releaseMic() {
  if (recording) return;
  try { micSrc && micSrc.disconnect(); } catch {} micSrc = null; analyser = null; level = 0;
  if (micStream) { for (const t of micStream.getTracks()) { try { t.stop(); } catch {} } micStream = null; }
  if (camStream) for (const t of camStream.getAudioTracks()) { try { t.stop(); camStream.removeTrack(t); } catch {} }
  setAudioSession('playback');
}
const lvlBuf = new Float32Array(1024);
setInterval(() => {
  if (!analyser) { level = 0; return; }
  analyser.getFloatTimeDomainData(lvlBuf); let s = 0; for (const v of lvlBuf) s += v * v; level = Math.sqrt(s / lvlBuf.length);
  if (!vadOn) return;
  const busy = sayBusy || playingNow;
  const now = performance.now();
  if (level > vadThresh && !busy) { lastLoud = now; if (!recording) startRec('vad'); }
  if (recording && recMode === 'vad' && now - lastLoud > 900) stopRec();
}, 50);
let recMode = 'ptt', pttHeld = false;
async function startRec(mode = 'ptt') {
  if (recording) return;
  if (mode === 'ptt') {
    if (sayBusy || playingNow) stopSpeaking(); // barge-in
    try { await acquireMic(); } catch (e) { return err('mic: ' + e.message); }
    if (!pttHeld) { if (!vadOn) releaseMic(); return; } // released before the mic came up
  }
  if (!micStream || recording) return;
  chunks = []; recMode = mode;
  try { recorder = MIME ? new MediaRecorder(micStream, { mimeType: MIME }) : new MediaRecorder(micStream); } catch (e) { return err('MediaRecorder: ' + e.message); }
  const rec = recorder;
  rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  rec.onstop = async () => {
    const dur = performance.now() - recStart; const type = (rec.mimeType || MIME || 'audio/mp4').split(';')[0];
    const myChunks = chunks; chunks = [];
    if (!vadOn) releaseMic(); // free the mic -> iOS leaves play-and-record before the reply arrives
    if (dur < 350 || !myChunks.length) { setMood('idle'); return; }
    const blob = new Blob(myChunks, { type }); const b64 = await blobToB64(blob);
    send({ t: 'audio', mime: type, b64, ms: Math.round(dur) }); setMood('thinking');
  };
  rec.start(); recStart = performance.now(); recording = true; setMood('listening'); $('#talkBtn').classList.add('rec');
}
function stopRec() { if (!recording) return; recording = false; $('#talkBtn').classList.remove('rec'); try { recorder.stop(); } catch { if (!vadOn) releaseMic(); } }
function blobToB64(blob) { return new Promise((res) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1] || ''); r.readAsDataURL(blob); }); }

// ---------------- wake lock ----------------
let wakeLock = null, noSleepVideo = null;
async function keepAwake() {
  try { if ('wakeLock' in navigator) { wakeLock = await navigator.wakeLock.request('screen'); wakeLock.addEventListener?.('release', () => { wakeLock = null; }); return; } } catch (e) { console.warn('wakeLock', e); }
  // fallback: a tiny looping muted video from a canvas stream keeps iOS < 16.4 awake
  try {
    if (noSleepVideo) return noSleepVideo.play();
    const c = document.createElement('canvas'); c.width = c.height = 2; const cc = c.getContext('2d');
    const stream = c.captureStream ? c.captureStream(1) : null; if (!stream) return;
    setInterval(() => { cc.fillStyle = Math.random() < .5 ? '#000' : '#010101'; cc.fillRect(0, 0, 2, 2); }, 1000);
    noSleepVideo = document.createElement('video'); noSleepVideo.muted = true; noSleepVideo.playsInline = true; noSleepVideo.setAttribute('playsinline', '');
    noSleepVideo.srcObject = stream; noSleepVideo.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none'; document.body.appendChild(noSleepVideo); await noSleepVideo.play();
  } catch (e) { console.warn('nosleep', e); }
}
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && started) { keepAwake(); if (camStream && !camStream.active) startCamera().catch(() => {}); } });

// ---------------- start / controls ----------------
let started = false;
function err(t) { $('#err').textContent = t; console.error(t); }
$('#startBtn').onclick = async () => {
  // everything here runs inside the user gesture (iOS requirement)
  unlockAudio();
  keepAwake();
  try { document.documentElement.requestFullscreen?.().catch?.(() => {}); } catch {}
  const errs = [];
  // ask for mic permission now; keep it open only in VAD mode (PTT re-acquires per press, releases after)
  try { await acquireMic(); if (!vadOn) releaseMic(); } catch (e) { errs.push('mic: ' + e.message); }
  try { await startCamera(); } catch (e) { errs.push('camera: ' + e.message); }
  if (!window.isSecureContext) errs.push('not a secure context — camera/mic need HTTPS');
  started = true;
  if (errs.length) { err(errs.join('\n')); setTimeout(() => { $('#start').style.display = 'none'; }, 2500); } else $('#start').style.display = 'none';
  $('#controls').classList.remove('hidden');
  setInterval(sendFrame, 1000 / FPS);
  setMood('happy'); setTimeout(() => face.mood === 'happy' && setMood('idle'), 1500);
};
const talk = $('#talkBtn');
const down = (e) => { e.preventDefault(); if (pttHeld) return; pttHeld = true; if (sayBusy || playingNow) stopSpeaking(); unlockAudio(); startRec('ptt'); };
const up = (e) => { e.preventDefault(); pttHeld = false; unlockAudio(); if (recMode === 'ptt') stopRec(); };
talk.addEventListener('touchstart', down, { passive: false }); talk.addEventListener('touchend', up, { passive: false }); talk.addEventListener('touchcancel', up, { passive: false });
talk.addEventListener('mousedown', down); talk.addEventListener('mouseup', up); talk.addEventListener('mouseleave', (e) => recording && recMode === 'ptt' && up(e));
$('#flipBtn').onclick = async () => { facing = facing === 'user' ? 'environment' : 'user'; try { await startCamera(); } catch (e) { err('camera: ' + e.message); } };
$('#vadBtn').onclick = () => { unlockAudio(); vadOn = !vadOn; if (vadOn) acquireMic().catch((e) => err('mic: ' + e.message)); else if (!recording) releaseMic(); $('#vadBtn').textContent = 'auto: ' + (vadOn ? 'on' : 'off'); $('#vadBtn').classList.toggle('on', vadOn); if (!vadOn && recMode === 'vad') stopRec(); };
$('#previewBtn').onclick = () => video.classList.toggle('show');
if (vadOn) { $('#vadBtn').textContent = 'auto: on'; $('#vadBtn').classList.add('on'); }
// tap the eyes to poke them
cv.addEventListener('click', (e) => { face.tgaze = [(e.clientX / innerWidth) * 2 - 1, (e.clientY / innerHeight) * 2 - 1]; face.lastInput = performance.now(); });

connect();
requestAnimationFrame(draw);
window.labrat = { face, setMood, say, handle };
