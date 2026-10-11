/* Tappet touchpad: runs full-screen ON THE TARGET PHONE and reports every touch-down to the head.
   POST api/touchpad/event {x, y, w, h, t} (CSS px, viewport origin top-left). No pairing needed. */
'use strict';
(() => {
  const cv = document.getElementById('cv');
  const ctx = cv.getContext('2d');
  const countEl = document.getElementById('count');
  const conn = document.getElementById('conn');
  const sizeEl = document.getElementById('size');
  const hint = document.getElementById('hint');
  const marks = []; // {x, y, t0, n, ok}
  const FADE_MS = 6000;
  let count = 0;
  let animating = false;

  // ── hint (hidden when already running standalone) ──
  const standalone = window.navigator.standalone === true ||
    (window.matchMedia && (matchMedia('(display-mode: standalone)').matches || matchMedia('(display-mode: fullscreen)').matches));
  let dismissed = false;
  try { dismissed = sessionStorage.getItem('tappet_tp_hint') === '1'; } catch (_) { /* ignore */ }
  hint.hidden = standalone || dismissed;
  document.getElementById('hint-close').addEventListener('click', () => {
    hint.hidden = true;
    try { sessionStorage.setItem('tappet_tp_hint', '1'); } catch (_) { /* ignore */ }
  });

  // ── reporting ──
  function setConn(kind, text) {
    conn.className = 'conn ' + kind;
    conn.lastElementChild.textContent = text;
  }
  function report(x, y) {
    const body = { x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100, w: window.innerWidth, h: window.innerHeight, t: Date.now() };
    const mark = { x, y, t0: performance.now(), n: ++count, ok: null };
    marks.push(mark);
    if (marks.length > 60) marks.shift();
    countEl.textContent = count;
    kick();
    fetch('api/touchpad/event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      keepalive: true,
      cache: 'no-store',
    }).then((r) => {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      mark.ok = true;
      setConn('ok', 'connected');
    }).catch((e) => {
      mark.ok = false;
      setConn('bad', 'not reaching robot (' + (e && e.message ? e.message : 'error') + ')');
    }).finally(kick);
  }
  const inHint = (target) => target && target.closest && target.closest('#hint');

  document.addEventListener('touchstart', (e) => {
    if (inHint(e.target)) return; // let the dismiss button work
    e.preventDefault();
    for (const t of e.changedTouches) report(t.clientX, t.clientY);
  }, { passive: false });
  // mouse / pen only; touch is handled above (avoids double-reporting)
  document.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'touch' || inHint(e.target)) return;
    e.preventDefault();
    report(e.clientX, e.clientY);
  });

  // ── block every scrolling / zooming / selection gesture ──
  const stop = (e) => { if (!inHint(e.target)) e.preventDefault(); };
  document.addEventListener('touchmove', (e) => e.preventDefault(), { passive: false });
  document.addEventListener('touchend', stop, { passive: false });
  ['gesturestart', 'gesturechange', 'gestureend', 'contextmenu', 'selectstart', 'dblclick', 'dragstart'].forEach((t) =>
    document.addEventListener(t, (e) => e.preventDefault(), { passive: false }));
  document.addEventListener('wheel', (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });

  // ── drawing ──
  function resize() {
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(window.innerWidth * dpr);
    cv.height = Math.round(window.innerHeight * dpr);
    sizeEl.textContent = `${window.innerWidth} × ${window.innerHeight} px · dpr ${Math.round(dpr * 100) / 100}${standalone ? ' · standalone' : ''}`;
    kick();
  }
  function kick() { if (!animating) { animating = true; requestAnimationFrame(frame); } }
  function frame() {
    const dpr = window.devicePixelRatio || 1;
    const w = window.innerWidth, h = window.innerHeight;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    // faint edge frame so you can see the page really fills the screen
    ctx.strokeStyle = 'rgba(255,255,255,.12)';
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, w - 2, h - 2);
    const now = performance.now();
    let alive = false;
    for (let i = marks.length - 1; i >= 0; i--) {
      const m = marks[i];
      const age = (now - m.t0) / FADE_MS;
      if (age >= 1) { marks.splice(i, 1); continue; }
      alive = true;
      const latest = i === marks.length - 1;
      const color = m.ok === false ? '#ef4444' : m.ok ? '#22c55e' : '#facc15';
      ctx.globalAlpha = latest ? 1 : Math.max(0, 0.85 * (1 - age));
      ctx.strokeStyle = color;
      ctx.lineWidth = latest ? 1.5 : 1;
      ctx.beginPath();
      ctx.moveTo(0, m.y); ctx.lineTo(w, m.y);
      ctx.moveTo(m.x, 0); ctx.lineTo(m.x, h);
      ctx.globalAlpha *= latest ? 0.45 : 0.3;
      ctx.stroke();
      ctx.globalAlpha = latest ? 1 : Math.max(0, 1 - age);
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(m.x, m.y, 14, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(m.x, m.y, 3, 0, Math.PI * 2); ctx.fill();
      ctx.font = '12px ui-monospace, Menlo, monospace';
      const label = `#${m.n} ${Math.round(m.x)},${Math.round(m.y)}`;
      const tx = m.x + 18 + 120 > w ? m.x - 18 - ctx.measureText(label).width : m.x + 18;
      ctx.fillText(label, tx, m.y > 24 ? m.y - 8 : m.y + 22);
    }
    ctx.globalAlpha = 1;
    if (alive) requestAnimationFrame(frame); else animating = false;
  }
  window.addEventListener('resize', resize);
  window.addEventListener('orientationchange', () => setTimeout(resize, 200));
  resize();

  // keep the screen awake during calibration / tap tests (where supported)
  let lock = null;
  async function wake() {
    try { if ('wakeLock' in navigator && !lock && document.visibilityState === 'visible') { lock = await navigator.wakeLock.request('screen'); lock.addEventListener('release', () => { lock = null; }); } } catch (_) { /* not allowed */ }
  }
  document.addEventListener('visibilitychange', wake);
  document.addEventListener('touchend', wake, { once: true });
  wake();
})();
