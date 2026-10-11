/* Tappet remote control. Vanilla JS, no build step. All URLs are relative (same origin). */
'use strict';
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const r4 = (x) => Math.round(x * 1e4) / 1e4;
  const fmt = (x, d = 1) => (typeof x === 'number' && isFinite(x) ? x.toFixed(d) : '—');
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const TOKEN_KEY = 'tappet_token';
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (_) { /* private mode */ } },
  };

  const state = {
    token: store.get(TOKEN_KEY) || '',
    tab: 'control',
    status: null,
    calib: null,
    online: null,
  };

  // ───────────────────────── API ─────────────────────────
  class ApiError extends Error {
    constructor(status, body) {
      super((body && (body.msg || body.err)) || 'HTTP ' + status);
      this.status = status;
      this.body = body || {};
    }
  }
  const ERR_TEXT = {
    unreachable: 'Target is out of reach',
    limit: 'Joint limit reached',
    not_enabled: 'Servos are not enabled — press Enable',
    queue_full: 'Motion queue is full',
    fault: 'Body fault',
    bad_args: 'Bad arguments',
    timeout: 'Timed out',
    no_touch: 'No touch registered on the target phone',
    not_implemented: 'Not available on this host',
    link_down: 'Body link is down',
    network: 'Network error — is the robot reachable?',
    unauthorized: 'Session expired — pair again',
    bad_pin: 'Wrong PIN',
  };
  function errText(e) {
    const b = (e && e.body) || {};
    const base = ERR_TEXT[b.err] || b.err || (e && e.message) || 'Error';
    return b.msg && b.msg !== base && b.msg !== b.err ? `${base}: ${b.msg}` : base;
  }

  /** api('status') GET; api('tap', {...}) POST. opts.timeout ms (0 = none), opts.raw returns the Response. */
  async function api(path, body, opts = {}) {
    const method = opts.method || (body === undefined ? 'GET' : 'POST');
    const headers = {};
    if (state.token) headers['X-Tappet-Token'] = state.token;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const timeout = opts.timeout === undefined ? 20000 : opts.timeout;
    const ctl = timeout ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => ctl.abort(), timeout) : 0;
    let res;
    try {
      res = await fetch('api/' + path, {
        method, headers, cache: 'no-store',
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctl ? ctl.signal : undefined,
      });
    } catch (_) {
      throw new ApiError(0, { err: ctl && ctl.signal.aborted ? 'timeout' : 'network' });
    } finally {
      clearTimeout(timer);
    }
    if (res.status === 401 && path !== 'pair') {
      unauthorized();
      throw new ApiError(401, { err: 'unauthorized' });
    }
    if (opts.raw) return res;
    let data = null;
    try { data = await res.json(); } catch (_) { /* not JSON */ }
    if (!res.ok || (data && data.ok === false)) throw new ApiError(res.status, data || { err: 'http_' + res.status });
    return data || {};
  }

  // ───────────────────────── Toasts ─────────────────────────
  const recentToasts = new Map();
  function toast(msg, kind = '', ms = 3200) {
    const now = Date.now();
    if (recentToasts.get(msg) > now - 2500) return;
    recentToasts.set(msg, now);
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.textContent = msg;
    el.addEventListener('click', () => el.remove());
    const box = $('#toasts');
    box.appendChild(el);
    while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 300); }, ms);
  }
  const toastErr = (e) => { if (e && e.status !== 401) toast(errText(e), 'error', 4500); };

  /** Run an async fn while showing a spinner on a button. */
  async function withBusy(btn, fn) {
    if (btn.classList.contains('busy')) return undefined;
    btn.classList.add('busy');
    btn.disabled = true;
    try { return await fn(); } finally { btn.classList.remove('busy'); btn.disabled = false; }
  }

  function segmented(sel, initial, onChange) {
    const el = $(sel);
    const set = (v) => {
      el.dataset.value = v;
      $$('button', el).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === String(v))));
      if (onChange) onChange(v);
    };
    el.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) set(b.dataset.v); });
    set(initial);
    return () => el.dataset.value;
  }

  // ───────────────────────── Pairing ─────────────────────────
  function showPairing() {
    $('#app').hidden = true;
    $('#pair').hidden = false;
    setTimeout(() => $('#pin').focus(), 50);
  }
  function showApp() {
    $('#pair').hidden = true;
    $('#app').hidden = false;
    pollStatus();
    onTab();
  }
  function unauthorized() {
    if (!state.token && !$('#pair').hidden) return;
    state.token = '';
    store.set(TOKEN_KEY, null);
    $('#pair-err').textContent = 'Session expired — enter the PIN again.';
    showPairing();
  }
  $('#pin').addEventListener('input', (e) => {
    e.target.value = e.target.value.replace(/\D/g, '').slice(0, 6);
    $('#pair-err').textContent = '';
    if (e.target.value.length === 6) $('#pair-form').requestSubmit();
  });
  $('#pair-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const pin = $('#pin').value.trim();
    if (!/^\d{6}$/.test(pin)) { $('#pair-err').textContent = 'The PIN has 6 digits.'; return; }
    withBusy($('#pair-btn'), async () => {
      try {
        const r = await api('pair', { pin });
        if (!r.token) throw new ApiError(500, { err: 'no token returned' });
        state.token = r.token;
        store.set(TOKEN_KEY, r.token);
        $('#pin').value = '';
        $('#pair-err').textContent = '';
        showApp();
      } catch (err) {
        $('#pair-err').textContent = err.status === 403 ? 'Wrong PIN — check the robot\'s face.' : errText(err);
        $('#pin').select();
      }
    });
  });
  $('#logout').addEventListener('click', () => {
    state.token = '';
    store.set(TOKEN_KEY, null);
    $('#pair-err').textContent = '';
    showPairing();
  });

  // ───────────────────────── Tabs ─────────────────────────
  const tabHooks = {};
  function setTab(name) {
    state.tab = name;
    store.set('tappet_tab', name);
    $$('.tab').forEach((s) => { s.hidden = s.dataset.tab !== name; });
    $$('.tabbar button').forEach((b) => {
      if (b.dataset.tab === name) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    window.scrollTo(0, 0);
    onTab();
  }
  function onTab() { if (tabHooks[state.tab]) tabHooks[state.tab](); }
  $$('.tabbar button').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.tab)));
  document.addEventListener('visibilitychange', () => { if (!document.hidden && state.token) { pollStatus(); onTab(); } });

  // ───────────────────────── Status ─────────────────────────
  let statusInflight = false;
  async function pollStatus() {
    if (statusInflight || !state.token || document.hidden) return;
    statusInflight = true;
    try {
      const s = await api('status', undefined, { timeout: 4000 });
      state.status = s;
      state.online = true;
      if (s.calib) setCalib(s.calib);
      renderStatus(s);
    } catch (e) {
      if (e.status !== 401) { state.online = false; renderStatus(null); }
    } finally {
      statusInflight = false;
    }
  }
  setInterval(pollStatus, 1000);

  const JOINTS = ['L_YAW', 'L_SHOULDER', 'L_ELBOW', 'R_YAW', 'R_SHOULDER', 'R_ELBOW', 'NECK_PAN', 'NECK_TILT'];
  function pill(el, text, kind) { el.className = 'pill ' + (kind || ''); el.lastElementChild ? (el.lastElementChild.textContent = text) : (el.textContent = text); }
  function renderStatus(s) {
    const link = $('#sb-link');
    if (!s) {
      pill(link, 'offline', 'bad');
      pill($('#sb-en'), '—');
      $('#sb-busy').hidden = $('#sb-q').hidden = $('#sb-fault').hidden = true;
      return;
    }
    const l = s.link || {};
    pill(link, l.connected ? (l.transport || 'linked') : 'no body', l.connected ? 'ok' : 'bad');
    const b = s.body;
    if (!b) {
      pill($('#sb-en'), '—');
      $('#sb-busy').hidden = $('#sb-q').hidden = $('#sb-fault').hidden = true;
    } else {
      pill($('#sb-en'), b.enabled ? 'enabled' : 'relaxed', b.enabled ? 'ok' : '');
      $('#sb-busy').hidden = !b.busy;
      const q = $('#sb-q');
      q.hidden = !b.queue;
      q.textContent = 'queue ' + b.queue;
      const f = $('#sb-fault');
      const faults = b.faults || [];
      f.hidden = !faults.length;
      f.textContent = 'fault: ' + faults.join(', ');
    }
    if (!$('#sb-details').hidden) renderDetails(s);
    const ag = s.agent || {};
    $('#agent-state').hidden = !ag.busy;
    $('#agent-na').hidden = ag.available !== false;
    if (s.camera && s.camera.available === false) cam.unavailable = true;
    syncNeckFromStatus(b);
  }
  function renderDetails(s) {
    const b = s && s.body;
    const joints = (b && b.joints) || {};
    const names = JOINTS.filter((n) => n in joints).concat(Object.keys(joints).filter((n) => !JOINTS.includes(n)));
    $('#sb-joints').innerHTML = names.length
      ? names.map((n) => `<tr><td>${esc(n)}</td><td>${fmt(joints[n])}</td></tr>`).join('')
      : '<tr><td>no data</td><td></td></tr>';
    const tools = (b && b.tools) || {};
    $('#sb-tools').innerHTML = ['L', 'R'].map((a) => {
      const t = tools[a];
      return `<tr><td>${a}</td><td>${t ? t.map((v) => fmt(v)).join(', ') : '—'}</td></tr>`;
    }).join('');
    $('#sb-volt').textContent = b ? `Servo ${fmt(b.vservo, 2)} V · In ${fmt(b.vin, 2)} V` : 'Body not connected';
    const c = s && s.calib;
    $('#sb-extra').textContent = c ? `Calibration: ${c.source || '?'}${c.rms_mm != null ? ` (rms ${fmt(c.rms_mm, 2)} mm)` : ''}` : '';
  }
  $('#sb-toggle').addEventListener('click', () => {
    const d = $('#sb-details');
    d.hidden = !d.hidden;
    $('#sb-toggle').setAttribute('aria-expanded', String(!d.hidden));
    if (!d.hidden) renderDetails(state.status);
  });

  function setCalib(c) {
    state.calib = c;
    const dev = c && c.device;
    const ar = dev && dev.screen_w > 0 && dev.screen_l > 0 ? dev.screen_w / dev.screen_l : 65.1 / 141.1;
    const frame = $('#phone-frame');
    if (frame.style.getPropertyValue('--ar') !== String(ar)) { frame.style.setProperty('--ar', String(ar)); requestAnimationFrame(phoneDraw); }
    $('#cal-summary').textContent = c
      ? `${(dev && dev.name) || 'device'} · ${c.source || '?'}${c.rms_mm != null ? ` · rms ${fmt(c.rms_mm, 2)} mm` : ''}`
      : '';
    $('#cam-note').textContent = c && !c.H_ic && $('#cam-mode').dataset.value === '1'
      ? 'Rectified view needs the camera screen corners (Calibrate tab).' : '';
  }

  // ───────────────────────── Body commands ─────────────────────────
  const CMD_ARGS = { park: { arm: 'both' } };
  async function sendCmd(cmd) {
    try {
      const r = await api('cmd', Object.assign({ cmd }, CMD_ARGS[cmd] || {}), { timeout: 8000 });
      toast(cmd[0].toUpperCase() + cmd.slice(1) + (r.queued ? ' queued' : ' ✓'), '', 1600);
      pollStatus();
    } catch (e) { toastErr(e); }
  }
  $$('[data-cmd]').forEach((b) => b.addEventListener('click', () => withBusy(b, () => sendCmd(b.dataset.cmd))));
  $('#stop').addEventListener('click', async () => {
    try { await api('cmd', { cmd: 'stop' }, { timeout: 5000 }); toast('Stopped', 'ok', 1800); pollStatus(); } catch (e) { toastErr(e); }
  });

  // ───────────────────────── Target phone canvas ─────────────────────────
  const tapArm = segmented('#tap-arm', store.get('tappet_tap_arm') || 'auto', (v) => store.set('tappet_tap_arm', v));
  const pcv = $('#phone');
  const pctx = pcv.getContext('2d');
  const fx = []; // effects: {kind:'ripple'|'trail'|'hold', ...}
  const dots = []; // real touches from touchpad page
  let gesture = null;
  let animating = false;
  let pendingGestures = 0;

  function sizeCanvas(cv) {
    const r = cv.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(r.width * dpr));
    const h = Math.max(1, Math.round(r.height * dpr));
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    return { w: r.width, h: r.height, dpr };
  }
  function evUV(e) {
    const r = pcv.getBoundingClientRect();
    return [clamp((e.clientX - r.left) / r.width, 0, 1), clamp((e.clientY - r.top) / r.height, 0, 1)];
  }
  function kick() { if (!animating) { animating = true; requestAnimationFrame(phoneFrame); } }
  function phoneFrame() {
    const alive = phoneDraw();
    if (alive) requestAnimationFrame(phoneFrame); else animating = false;
  }
  function phoneDraw() {
    if (pcv.offsetParent === null) return false;
    const { w, h, dpr } = sizeCanvas(pcv);
    const c = pctx;
    const now = performance.now();
    const accent = css('--accent') || '#5b8cff';
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, h);
    // grid
    c.strokeStyle = css('--screen-grid') || 'rgba(255,255,255,.07)';
    c.lineWidth = 1;
    c.beginPath();
    for (let i = 1; i < 4; i++) {
      c.moveTo(Math.round(w * i / 4) + 0.5, 0); c.lineTo(Math.round(w * i / 4) + 0.5, h);
      c.moveTo(0, Math.round(h * i / 4) + 0.5); c.lineTo(w, Math.round(h * i / 4) + 0.5);
    }
    c.stroke();
    // notch-ish speaker hint so orientation is obvious
    c.fillStyle = 'rgba(255,255,255,.14)';
    c.beginPath(); c.roundRect ? c.roundRect(w / 2 - w * 0.14, 8, w * 0.28, 12, 6) : c.rect(w / 2 - w * 0.14, 8, w * 0.28, 12); c.fill();

    let alive = !!gesture;
    // real touches
    for (let i = dots.length - 1; i >= 0; i--) {
      const d = dots[i];
      const age = (now - d.t0) / 8000;
      if (age >= 1) { dots.splice(i, 1); continue; }
      alive = true;
      const x = d.u * w, y = d.v * h;
      c.globalAlpha = 1 - age;
      c.fillStyle = '#facc15';
      c.beginPath(); c.arc(x, y, 5, 0, Math.PI * 2); c.fill();
      c.strokeStyle = '#facc15'; c.lineWidth = 1.5;
      c.beginPath(); c.moveTo(x - 11, y); c.lineTo(x - 7, y); c.moveTo(x + 7, y); c.lineTo(x + 11, y);
      c.moveTo(x, y - 11); c.lineTo(x, y - 7); c.moveTo(x, y + 7); c.lineTo(x, y + 11); c.stroke();
    }
    c.globalAlpha = 1;
    // effects
    for (let i = fx.length - 1; i >= 0; i--) {
      const f = fx[i];
      const p = (now - f.t0) / f.dur;
      if (p >= 1) { fx.splice(i, 1); continue; }
      alive = true;
      if (f.kind === 'ripple') {
        for (const k of [0, 0.25]) {
          const q = p - k;
          if (q < 0) continue;
          c.globalAlpha = Math.max(0, 1 - q);
          c.strokeStyle = f.color; c.lineWidth = 2.5;
          c.beginPath(); c.arc(f.u * w, f.v * h, 6 + q * (f.big ? 60 : 34), 0, Math.PI * 2); c.stroke();
        }
        c.globalAlpha = Math.max(0, 1 - p * 1.5);
        c.fillStyle = f.color;
        c.beginPath(); c.arc(f.u * w, f.v * h, 6, 0, Math.PI * 2); c.fill();
      } else if (f.kind === 'trail') {
        c.globalAlpha = 1 - p;
        drawTrail(c, f.pts, w, h, accent);
      }
      c.globalAlpha = 1;
    }
    // live gesture
    if (gesture) {
      if (gesture.moved) drawTrail(c, gesture.pts, w, h, accent);
      else if (!gesture.long) {
        const p = clamp((now - gesture.t0) / 500, 0, 1);
        const [u, v] = gesture.pts[0];
        c.strokeStyle = accent; c.lineWidth = 3;
        c.beginPath(); c.arc(u * w, v * h, 20, -Math.PI / 2, -Math.PI / 2 + p * Math.PI * 2); c.stroke();
        c.fillStyle = accent; c.beginPath(); c.arc(u * w, v * h, 6, 0, Math.PI * 2); c.fill();
      }
    }
    return alive;
  }
  function drawTrail(c, pts, w, h, color) {
    if (pts.length < 2) return;
    c.strokeStyle = color; c.lineWidth = 5; c.lineCap = 'round'; c.lineJoin = 'round';
    c.beginPath();
    pts.forEach(([u, v], i) => (i ? c.lineTo(u * w, v * h) : c.moveTo(u * w, v * h)));
    c.stroke();
    const [ua, va] = pts[pts.length - 2], [ub, vb] = pts[pts.length - 1];
    const ang = Math.atan2((vb - va) * h, (ub - ua) * w);
    c.fillStyle = color;
    c.beginPath();
    c.moveTo(ub * w + Math.cos(ang) * 10, vb * h + Math.sin(ang) * 10);
    c.lineTo(ub * w + Math.cos(ang + 2.4) * 12, vb * h + Math.sin(ang + 2.4) * 12);
    c.lineTo(ub * w + Math.cos(ang - 2.4) * 12, vb * h + Math.sin(ang - 2.4) * 12);
    c.fill();
    c.beginPath(); c.arc(pts[0][0] * w, pts[0][1] * h, 6, 0, Math.PI * 2); c.fill();
  }
  function setLast(text) { $('#tap-last').textContent = text; }
  async function gestureCall(label, path, body) {
    pendingGestures++;
    setLast(`${label}…${pendingGestures > 1 ? ` (${pendingGestures} pending)` : ''}`);
    try {
      const r = await api(path, body, { timeout: 30000 });
      const pos = typeof r.x === 'number' ? ` at (${fmt(r.x)}, ${fmt(r.y)}, ${fmt(r.z)}) mm` : '';
      setLast(`${label} ✓ arm ${r.arm || '?'}${pos}`);
    } catch (e) {
      setLast(`${label} failed: ${errText(e)}`);
      toastErr(e);
    } finally {
      pendingGestures--;
    }
  }
  function doTap([u, v], holdMs) {
    fx.push({ kind: 'ripple', u, v, t0: performance.now(), dur: holdMs ? 1100 : 700, color: holdMs ? css('--warn') : css('--accent'), big: !!holdMs });
    kick();
    const body = { u: r4(u), v: r4(v), arm: tapArm() };
    if (holdMs) body.hold_ms = holdMs;
    gestureCall(holdMs ? 'Long-press' : 'Tap', 'tap', body);
  }
  pcv.addEventListener('pointerdown', (e) => {
    if (gesture || (e.pointerType === 'mouse' && e.button !== 0)) return;
    e.preventDefault();
    try { pcv.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
    const p = evUV(e);
    gesture = { id: e.pointerId, pts: [p], t0: performance.now(), x0: e.clientX, y0: e.clientY, moved: false, long: false };
    const g = gesture;
    g.timer = setTimeout(() => {
      if (gesture === g && !g.moved) { g.long = true; doTap(g.pts[0], 800); }
    }, 500);
    kick();
  });
  pcv.addEventListener('pointermove', (e) => {
    const g = gesture;
    if (!g || e.pointerId !== g.id || g.long) return;
    if (!g.moved && Math.hypot(e.clientX - g.x0, e.clientY - g.y0) > 10) { g.moved = true; clearTimeout(g.timer); }
    if (g.moved) { g.pts.push(evUV(e)); kick(); }
  });
  function endGesture(e, cancelled) {
    const g = gesture;
    if (!g || e.pointerId !== g.id) return;
    clearTimeout(g.timer);
    gesture = null;
    if (cancelled || g.long) { kick(); return; }
    if (g.moved) {
      const a = g.pts[0], b = g.pts[g.pts.length - 1];
      const ms = clamp(Math.round(performance.now() - g.t0), 150, 1500);
      fx.push({ kind: 'trail', pts: g.pts.slice(), t0: performance.now(), dur: 1200 });
      kick();
      gestureCall('Swipe', 'swipe', { u0: r4(a[0]), v0: r4(a[1]), u1: r4(b[0]), v1: r4(b[1]), ms, arm: tapArm() });
    } else {
      doTap(g.pts[0], 0);
    }
  }
  pcv.addEventListener('pointerup', (e) => endGesture(e, false));
  pcv.addEventListener('pointercancel', (e) => endGesture(e, true));
  pcv.addEventListener('contextmenu', (e) => e.preventDefault());
  if (window.ResizeObserver) new ResizeObserver(() => phoneDraw()).observe(pcv);

  // touchpad overlay
  let tpSince = null;
  let tpInflight = false;
  const overlayBox = $('#tp-overlay');
  overlayBox.checked = store.get('tappet_tp_overlay') !== '0';
  overlayBox.addEventListener('change', () => { store.set('tappet_tp_overlay', overlayBox.checked ? '1' : '0'); tpSince = null; });
  async function pollTouchpad() {
    if (tpInflight || !overlayBox.checked || state.tab !== 'control' || document.hidden || !state.token || $('#app').hidden) return;
    tpInflight = true;
    try {
      const first = tpSince === null;
      if (first) {
        const ls = state.status && state.status.touchpad && state.status.touchpad.last_seq;
        tpSince = typeof ls === 'number' ? ls : 0;
      }
      const r = await api('touchpad/events?since=' + tpSince, undefined, { timeout: 4000 });
      const evs = r.events || [];
      let last = typeof r.last === 'number' ? r.last : tpSince;
      for (const ev of evs) last = Math.max(last, ev.seq || 0);
      if (!(first && tpSince === 0)) {
        const now = performance.now();
        for (const ev of evs) {
          const u = typeof ev.u === 'number' ? ev.u : ev.w ? ev.x / ev.w : null;
          const v = typeof ev.v === 'number' ? ev.v : ev.h ? ev.y / ev.h : null;
          if (u != null && v != null) dots.push({ u, v, t0: now });
        }
        if (evs.length) kick();
      }
      tpSince = last;
    } catch (_) { /* silent; status bar shows connectivity */ } finally {
      tpInflight = false;
    }
  }
  setInterval(pollTouchpad, 700);

  // type
  $('#type-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const inp = $('#type-text');
    const text = inp.value;
    if (!text) return;
    const btn = e.target.querySelector('button');
    await withBusy(btn, async () => {
      try {
        const r = await api('type', { text }, { timeout: 0 });
        inp.value = '';
        toast(`Typed ${r.taps != null ? r.taps + ' taps' : 'text'}${r.skipped ? ` (skipped: ${r.skipped})` : ''}`, 'ok', 2500);
      } catch (err) { toastErr(err); }
    });
  });

  tabHooks.control = () => { requestAnimationFrame(phoneDraw); pollTouchpad(); };

  // ───────────────────────── Neck ─────────────────────────
  const neck = { pan: 0, tilt: 0, pending: null, inflight: false, last: 0, timer: 0, dragging: false, touched: 0 };
  const pad = $('#neckpad');
  function neckRender() {
    const k = $('#neck-knob');
    k.style.left = ((80 - neck.pan) / 160) * 100 + '%';
    k.style.top = ((30 - neck.tilt) / 100) * 100 + '%';
    $('#neck-val').textContent = `pan ${neck.pan}° · tilt ${neck.tilt}°`;
  }
  function neckSet(pan, tilt, immediate) {
    neck.pan = Math.round(clamp(pan, -80, 80));
    neck.tilt = Math.round(clamp(tilt, -70, 30));
    neck.touched = Date.now();
    neckRender();
    neck.pending = { pan: neck.pan, tilt: neck.tilt };
    if (immediate) { clearTimeout(neck.timer); neck.last = 0; }
    neckPump();
  }
  function neckPump() {
    if (!neck.pending || neck.inflight) return;
    const wait = 200 - (Date.now() - neck.last);
    if (wait > 0) { clearTimeout(neck.timer); neck.timer = setTimeout(neckPump, wait); return; }
    const body = neck.pending;
    neck.pending = null;
    neck.inflight = true;
    neck.last = Date.now();
    api('neck', body, { timeout: 5000 }).catch(toastErr).finally(() => { neck.inflight = false; neckPump(); });
  }
  function padEvent(e) {
    const r = pad.getBoundingClientRect();
    const fx_ = clamp((e.clientX - r.left) / r.width, 0, 1);
    const fy = clamp((e.clientY - r.top) / r.height, 0, 1);
    neckSet(80 - fx_ * 160, 30 - fy * 100);
  }
  pad.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    try { pad.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
    neck.dragging = true;
    pad.classList.add('drag');
    padEvent(e);
  });
  pad.addEventListener('pointermove', (e) => { if (neck.dragging) padEvent(e); });
  const padEnd = () => { neck.dragging = false; pad.classList.remove('drag'); neckPump(); };
  pad.addEventListener('pointerup', padEnd);
  pad.addEventListener('pointercancel', padEnd);
  $$('[data-pan]').forEach((b) => b.addEventListener('click', () => neckSet(+b.dataset.pan, +b.dataset.tilt, true)));
  function syncNeckFromStatus(b) {
    if (!b || !b.joints || neck.dragging || Date.now() - neck.touched < 3000) return;
    const p = b.joints.NECK_PAN, t = b.joints.NECK_TILT;
    if (typeof p === 'number' && typeof t === 'number') { neck.pan = Math.round(p); neck.tilt = Math.round(t); neckRender(); }
  }
  neckRender();

  // ───────────────────────── Jog ─────────────────────────
  const jogArm = segmented('#jog-arm', store.get('tappet_jog_arm') || 'L', (v) => store.set('tappet_jog_arm', v));
  const jogStep = segmented('#jog-step', store.get('tappet_jog_step') || '5', (v) => store.set('tappet_jog_step', v));
  $$('.jb').forEach((b) => b.addEventListener('click', async () => {
    const step = +jogStep() * +b.dataset.s;
    const body = { arm: jogArm(), dx: 0, dy: 0, dz: 0 };
    body['d' + b.dataset.d] = step;
    const out = $('#jog-pos');
    try {
      const r = await api('jog', body, { timeout: 8000 });
      out.textContent = `${body.arm}: x ${fmt(r.x)}  y ${fmt(r.y)}  z ${fmt(r.z)} mm`;
      out.style.color = '';
    } catch (e) {
      out.textContent = errText(e);
      out.style.color = 'var(--danger)';
      if (e.status !== 401 && !['unreachable', 'limit'].includes(e.body.err)) toastErr(e);
    }
  }));

  // ───────────────────────── Camera ─────────────────────────
  const cam = { running: false, unavailable: false, url: null };
  segmented('#cam-mode', store.get('tappet_cam_mode') || '0', (v) => { store.set('tappet_cam_mode', v); setCalib(state.calib); });
  async function fetchCamera(rectified) {
    const q = `camera.jpg?rectified=${rectified ? 1 : 0}&token=${encodeURIComponent(state.token)}&_=${Date.now()}`;
    const res = await api(q, undefined, { raw: true, timeout: 8000 });
    if (!res.ok) {
      let body = null;
      try { body = await res.json(); } catch (_) { /* binary */ }
      throw new ApiError(res.status, body || { err: res.status === 501 ? 'not_implemented' : 'http_' + res.status });
    }
    return res.blob();
  }
  function camMsg(text, full) {
    const m = $('#cam-msg');
    m.innerHTML = text;
    m.classList.toggle('full', !!full);
  }
  function camUnavailable() {
    camMsg('<div>Camera not available on this host.<br><button class="btn small" id="cam-retry" type="button" style="margin-top:10px">Retry</button></div>', true);
    $('#cam-img').removeAttribute('src');
    $('#cam-retry').addEventListener('click', () => { cam.unavailable = false; camMsg('Loading…'); camLoop(); });
  }
  async function camLoop() {
    if (cam.running) return;
    if (cam.unavailable) { camUnavailable(); return; }
    cam.running = true;
    let fails = 0;
    while (state.tab === 'camera' && !document.hidden && state.token && !cam.unavailable) {
      const t0 = performance.now();
      try {
        const blob = await fetchCamera($('#cam-mode').dataset.value === '1');
        const url = URL.createObjectURL(blob);
        const img = $('#cam-img');
        await new Promise((res) => { img.onload = img.onerror = res; img.src = url; });
        if (cam.url) URL.revokeObjectURL(cam.url);
        cam.url = url;
        camMsg('');
        fails = 0;
      } catch (e) {
        if (e.status === 501) { cam.unavailable = true; break; }
        if (e.status === 401) break;
        fails++;
        camMsg(esc(errText(e)) + (fails > 1 ? ' — retrying…' : ''));
        await sleep(Math.min(5000, 800 * fails));
      }
      await sleep(Math.max(0, 500 - (performance.now() - t0)));
    }
    cam.running = false;
    if (cam.unavailable) camUnavailable();
  }
  tabHooks.camera = camLoop;

  // ───────────────────────── Chat ─────────────────────────
  const chatLog = $('#chat-log');
  function addMsg(kind, text) {
    const el = document.createElement('div');
    el.className = 'msg ' + kind;
    el.textContent = text;
    chatLog.appendChild(el);
    chatLog.scrollTop = chatLog.scrollHeight;
    el.scrollIntoView({ block: 'nearest' });
    return el;
  }
  const chatText = $('#chat-text');
  chatText.addEventListener('input', () => { chatText.style.height = 'auto'; chatText.style.height = Math.min(140, chatText.scrollHeight) + 'px'; });
  chatText.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#chat-form').requestSubmit(); }
  });
  $('#chat-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = chatText.value.trim();
    if (!text) return;
    chatText.value = '';
    chatText.style.height = 'auto';
    addMsg('me', text);
    const pending = addMsg('bot typing', '');
    try {
      const r = await api('chat', { text }, { timeout: 180000 });
      pending.classList.remove('typing');
      pending.textContent = r.reply || '(no reply)';
    } catch (err) {
      pending.remove();
      if (err.status === 501) { $('#agent-na').hidden = false; addMsg('sys', 'Agent not available on this host.'); } else addMsg('err', errText(err));
    }
    chatLog.scrollTop = chatLog.scrollHeight;
  });
  $('#say-btn').addEventListener('click', (e) => {
    const text = chatText.value.trim();
    if (!text) { toast('Type something to say'); chatText.focus(); return; }
    withBusy(e.currentTarget, async () => {
      try {
        await api('say', { text }, { timeout: 30000 });
        addMsg('sys', `🔊 “${text}”`);
        chatText.value = '';
      } catch (err) {
        if (err.status === 501) toast('Speech not available on this host', 'error'); else toastErr(err);
      }
    });
  });

  // ───────────────────────── Calibration ─────────────────────────
  const tpUrl = new URL('touchpad.html', location.href).href.replace(/[?#].*$/, '');
  $('#tp-url').textContent = tpUrl;
  $('#tp-url').href = tpUrl;
  $('#tp-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(tpUrl); toast('Link copied', 'ok', 1500); } catch (_) {
      const sel = window.getSelection(); const rng = document.createRange();
      rng.selectNodeContents($('#tp-url')); sel.removeAllRanges(); sel.addRange(rng);
      toast('Link selected — copy it manually');
    }
  });
  const calArm = segmented('#cal-arm', 'auto');
  $('#cal-run').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    const out = $('#cal-out');
    out.innerHTML = '<p class="small center"><span class="spinner"></span> Calibrating — the robot is tapping a 3×3 grid…</p>';
    const arm = calArm();
    try {
      const r = await api('calibrate/auto', arm === 'auto' ? {} : { arm }, { timeout: 0 });
      if (r.calibration) setCalib(r.calibration);
      const pts = r.points || [];
      const good = (x) => (x <= 1.5 ? 'good' : x > 3 ? 'poor' : '');
      out.innerHTML = `
        <div class="stat-row">
          <div class="stat ${good(r.rms_mm)}"><b>${fmt(r.rms_mm, 2)}</b><span>RMS error (mm)</span></div>
          <div class="stat ${good(r.max_mm)}"><b>${fmt(r.max_mm, 2)}</b><span>Max error (mm)</span></div>
          <div class="stat"><b>${pts.length}</b><span>Points</span></div>
        </div>
        <div class="table-scroll"><table class="data mono">
          <tr><th>#</th><th>u</th><th>v</th><th>x</th><th>y</th><th>touch u</th><th>touch v</th><th>err mm</th></tr>
          ${pts.map((p, i) => `<tr${p.tu == null ? ' class="miss"' : ''}><td>${i + 1}</td><td>${fmt(p.u, 3)}</td><td>${fmt(p.v, 3)}</td><td>${fmt(p.x)}</td><td>${fmt(p.y)}</td><td>${fmt(p.tu, 3)}</td><td>${fmt(p.tv, 3)}</td><td>${fmt(p.err_mm, 2)}</td></tr>`).join('')}
        </table></div>`;
      toast('Calibration saved', 'ok');
      if ($('#cal-json').value) loadCalJson();
    } catch (err) {
      out.innerHTML = `<p class="err-text">${esc(errText(err))}</p>` +
        (err.body.err === 'no_touch' ? '<p class="muted small">Make sure touchpad.html is open full-screen on the target phone and the stylus reaches the glass.</p>' : '');
    }
  }));

  // camera corner tool
  const cc = { img: null, pts: [], w: 0, h: 0, url: null };
  const ccv = $('#cc-canvas');
  const CC_LABELS = ['TL', 'TR', 'BR', 'BL'];
  const CC_NAMES = ['top-left', 'top-right', 'bottom-right', 'bottom-left'];
  function ccDraw() {
    if (!cc.img) return;
    const c = ccv.getContext('2d');
    c.drawImage(cc.img, 0, 0, cc.w, cc.h);
    const s = Math.max(cc.w, cc.h) / 640;
    const accent = '#38bdf8';
    if (cc.pts.length) {
      c.lineWidth = 2.5 * s; c.strokeStyle = accent; c.fillStyle = 'rgba(56,189,248,.18)';
      c.beginPath();
      cc.pts.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
      if (cc.pts.length === 4) { c.closePath(); c.fill(); }
      c.stroke();
    }
    cc.pts.forEach(([x, y], i) => {
      c.strokeStyle = '#fff'; c.lineWidth = 3 * s;
      c.beginPath(); c.arc(x, y, 9 * s, 0, Math.PI * 2); c.stroke();
      c.strokeStyle = accent; c.lineWidth = 1.5 * s;
      c.beginPath(); c.moveTo(x - 14 * s, y); c.lineTo(x + 14 * s, y); c.moveTo(x, y - 14 * s); c.lineTo(x, y + 14 * s); c.stroke();
      c.font = `bold ${14 * s}px sans-serif`;
      c.fillStyle = '#000'; c.fillText(CC_LABELS[i], x + 12 * s + 1, y - 10 * s + 1);
      c.fillStyle = '#fff'; c.fillText(CC_LABELS[i], x + 12 * s, y - 10 * s);
    });
    const n = cc.pts.length;
    $('#cc-hint').textContent = n < 4 ? `Tap the ${CC_NAMES[n]} (${CC_LABELS[n]}) corner of the screen.` : 'All four corners set — Save to compute the camera homography.';
    $('#cc-undo').disabled = $('#cc-reset').disabled = !n;
    $('#cc-save').disabled = n !== 4;
  }
  $('#cc-grab').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    try {
      const blob = await fetchCamera(false);
      const url = URL.createObjectURL(blob);
      const img = new Image();
      await new Promise((res, rej) => { img.onload = res; img.onerror = () => rej(new ApiError(0, { err: 'Snapshot is not a valid image' })); img.src = url; });
      if (cc.url) URL.revokeObjectURL(cc.url);
      cc.url = url;
      cc.img = img; cc.w = img.naturalWidth; cc.h = img.naturalHeight; cc.pts = [];
      ccv.width = cc.w; ccv.height = cc.h;
      $('#cc-wrap').hidden = false;
      ccDraw();
    } catch (err) {
      if (err.status === 501) toast('Camera not available on this host', 'error'); else toastErr(err);
    }
  }));
  ccv.addEventListener('click', (e) => {
    if (!cc.img || cc.pts.length >= 4) return;
    const r = ccv.getBoundingClientRect();
    cc.pts.push([(e.clientX - r.left) * (cc.w / r.width), (e.clientY - r.top) * (cc.h / r.height)]);
    ccDraw();
  });
  $('#cc-undo').addEventListener('click', () => { cc.pts.pop(); ccDraw(); });
  $('#cc-reset').addEventListener('click', () => { cc.pts = []; ccDraw(); });
  $('#cc-save').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    try {
      const r = await api('calibration', {
        camera_corners: cc.pts.map(([x, y]) => [Math.round(x * 10) / 10, Math.round(y * 10) / 10]),
        camera_size: [cc.w, cc.h],
      });
      setCalib(r);
      toast('Camera corners saved', 'ok');
      if ($('#cal-json').value) showCalJson(r);
    } catch (err) { toastErr(err); }
    ccDraw();
  }));

  // calibration JSON editor
  function prettyJson(obj) {
    // keep numeric arrays (matrix rows, vectors) on one line
    return JSON.stringify(obj, null, 2).replace(/\[\s+([-\d.eE+,\s]+?)\s+\]/g, (_, inner) => '[' + inner.split(/,\s*/).join(', ') + ']');
  }
  function showCalJson(obj) { $('#cal-json').value = prettyJson(obj); }
  async function loadCalJson() {
    try { const r = await api('calibration'); setCalib(r); showCalJson(r); } catch (err) { toastErr(err); }
  }
  $('#cal-load').addEventListener('click', (e) => withBusy(e.currentTarget, loadCalJson));
  $('#cal-save').addEventListener('click', (e) => {
    let obj;
    try { obj = JSON.parse($('#cal-json').value); } catch (err) { toast('Invalid JSON: ' + err.message, 'error', 5000); return; }
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) { toast('Calibration must be a JSON object', 'error'); return; }
    withBusy(e.currentTarget, async () => {
      try { const r = await api('calibration', obj); setCalib(r); showCalJson(r); toast('Calibration saved', 'ok'); } catch (err) { toastErr(err); }
    });
  });
  $('#cal-reset').addEventListener('click', (e) => {
    if (!confirm('Reset the screen calibration to the default for this device?')) return;
    withBusy(e.currentTarget, async () => {
      try { const r = await api('calibration', { reset: true }); setCalib(r); showCalJson(r); toast('Calibration reset', 'ok'); } catch (err) { toastErr(err); }
    });
  });
  tabHooks.calib = () => { if (!$('#cal-json').value) loadCalJson(); };

  // ───────────────────────── Tap test ─────────────────────────
  const ttArm = segmented('#tt-arm', 'auto');
  let ttResults = null;
  $('#tt-run').addEventListener('click', (e) => {
    const n = Math.round(clamp(+$('#tt-n').value || 100, 1, 2000));
    $('#tt-n').value = n;
    const arm = ttArm();
    withBusy(e.currentTarget, async () => {
      const prog = $('#tt-progress');
      const t0 = Date.now();
      const tick = () => { $('#tt-progress-text').textContent = `Running ${n} taps… ${Math.round((Date.now() - t0) / 1000)} s (≈${n} s expected)`; };
      tick();
      prog.hidden = false;
      const iv = setInterval(tick, 1000);
      try {
        const r = await api('taptest', arm === 'auto' ? { n } : { n, arm }, { timeout: 0 });
        ttResults = r.results || {};
        renderTapTest(ttResults);
        toast('Tap test finished', 'ok');
      } catch (err) {
        $('#tt-summary').innerHTML = `<p class="err-text">${esc(errText(err))}</p>`;
      } finally {
        clearInterval(iv);
        prog.hidden = true;
      }
    });
  });
  function renderTapTest(res) {
    const rate = res.registration_rate;
    const cls = (ok, bad) => (ok ? 'good' : bad ? 'poor' : '');
    $('#tt-summary').innerHTML = `
      <div class="stat-row">
        <div class="stat ${cls(rate >= 0.98, rate < 0.9)}"><b>${rate != null ? fmt(rate * 100, 1) + '%' : '—'}</b><span>registered (${res.registered ?? '—'}/${res.n ?? '—'})</span></div>
        <div class="stat"><b>${fmt(res.mean_mm, 2)}</b><span>mean error mm</span></div>
        <div class="stat ${cls(res.p95_mm <= 2, res.p95_mm > 3)}"><b>${fmt(res.p95_mm, 2)}</b><span>p95 error mm</span></div>
        <div class="stat"><b>${fmt(res.max_mm, 2)}</b><span>max error mm</span></div>
        <div class="stat"><b>${fmt(res.cycle_s, 2)}</b><span>s per tap</span></div>
      </div>
      <table class="kv mono small">
        ${[['n', res.n], ['registered', res.registered], ['registration_rate', fmt(rate, 3)], ['mean_mm', fmt(res.mean_mm, 3)], ['p95_mm', fmt(res.p95_mm, 3)], ['max_mm', fmt(res.max_mm, 3)], ['cycle_s', fmt(res.cycle_s, 3)], ['duration_s', fmt(res.duration_s, 1)]]
          .map(([k, v]) => `<tr><td>${k}</td><td>${v ?? '—'}</td></tr>`).join('')}
      </table>`;
    const samples = res.samples || [];
    const cols = ['i', 'u', 'v', 'arm', 'registered', 'tu', 'tv', 'err_mm', 'dx_mm', 'dy_mm'];
    const cell = (s, k) => {
      const x = s[k];
      if (typeof x === 'number') return k === 'i' ? x : fmt(x, k.endsWith('mm') ? 2 : 3);
      if (typeof x === 'boolean') return x ? '✓' : '✗';
      return x == null ? '—' : esc(x);
    };
    $('#tt-samples').innerHTML = `<thead><tr>${cols.map((c) => `<th>${c}</th>`).join('')}</tr></thead><tbody>` +
      samples.map((s) => `<tr${s.registered ? '' : ' class="miss"'}>${cols.map((k) => `<td>${cell(s, k)}</td>`).join('')}</tr>`).join('') + '</tbody>';
    $('#tt-samples-card').hidden = false;
    $('#tt-plot-card').hidden = false;
    requestAnimationFrame(() => drawScatter(samples));
  }
  function drawScatter(samples) {
    const cv = $('#tt-plot');
    if (cv.offsetParent === null) return;
    const { w, h, dpr } = sizeCanvas(cv);
    const c = cv.getContext('2d');
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, h);
    const pts = samples.filter((s) => typeof s.dx_mm === 'number' && typeof s.dy_mm === 'number');
    const ext = Math.max(4, Math.ceil(Math.max(0, ...pts.map((s) => Math.max(Math.abs(s.dx_mm), Math.abs(s.dy_mm)))) + 0.5));
    const cx = w / 2, cy = h / 2, sc = (Math.min(w, h) / 2 - 18) / ext;
    const muted = css('--muted'), border = css('--border');
    c.strokeStyle = border; c.lineWidth = 1;
    c.beginPath(); c.moveTo(cx, 8); c.lineTo(cx, h - 8); c.moveTo(8, cy); c.lineTo(w - 8, cy); c.stroke();
    c.font = '11px ' + css('--font');
    c.fillStyle = muted;
    for (const r of [1, 2, 3]) {
      c.strokeStyle = r === 2 ? muted : border;
      c.setLineDash(r === 2 ? [] : [4, 4]);
      c.beginPath(); c.arc(cx, cy, r * sc, 0, Math.PI * 2); c.stroke();
      c.fillText(r + ' mm', cx + r * sc * 0.707 + 3, cy - r * sc * 0.707 - 3);
    }
    c.setLineDash([]);
    c.fillText('dx →', w - 36, cy - 6);
    c.fillText('dy ↓', cx + 6, h - 10);
    c.fillText(`±${ext} mm`, 8, 16);
    const ok = css('--ok'), bad = css('--danger');
    for (const s of pts) {
      c.globalAlpha = 0.75;
      c.fillStyle = s.registered ? ok : bad;
      c.beginPath(); c.arc(cx + s.dx_mm * sc, cy + s.dy_mm * sc, 3.5, 0, Math.PI * 2); c.fill();
    }
    c.globalAlpha = 1;
  }
  window.addEventListener('resize', () => { if (ttResults && state.tab === 'test') drawScatter(ttResults.samples || []); });
  $('#tt-csv').addEventListener('click', () => {
    const samples = (ttResults && ttResults.samples) || [];
    if (!samples.length) { toast('No samples'); return; }
    const cols = Array.from(samples.reduce((set, s) => { Object.keys(s).forEach((k) => set.add(k)); return set; }, new Set()));
    const q = (x) => { const s = x == null ? '' : String(x); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const csv = [cols.join(',')].concat(samples.map((s) => cols.map((k) => q(s[k])).join(','))).join('\n') + '\n';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const d = new Date();
    const p2 = (x) => String(x).padStart(2, '0');
    a.download = `tappet-taptest-${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}-${p2(d.getHours())}${p2(d.getMinutes())}${p2(d.getSeconds())}.csv`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  });
  tabHooks.test = () => { if (ttResults) requestAnimationFrame(() => drawScatter(ttResults.samples || [])); };

  // ───────────────────────── Boot ─────────────────────────
  const savedTab = store.get('tappet_tab');
  const TABS = $$('.tab').map((t) => t.dataset.tab);
  setCalib(null);
  setTab(TABS.includes(savedTab) ? savedTab : 'control');
  if (state.token) showApp(); else showPairing();
})();
