// Animated eyes + mouth on a full-screen canvas. Moods come from the DO ({"t":"face"}) and from the local voice
// state; the mouth is driven by speech-synthesis boundary events (pulse()) with a synthetic wiggle as fallback.

const MOODS = {
  idle:      { color: [110, 231, 200], open: 1.0,  lid: 0,    brow: 0 },
  listening: { color: [110, 200, 255], open: 1.12, lid: 0,    brow: 0.1 },
  thinking:  { color: [245, 215, 110], open: 0.75, lid: 0.25, brow: -0.1 },
  speaking:  { color: [110, 231, 200], open: 1.0,  lid: 0,    brow: 0 },
  happy:     { color: [245, 215, 110], open: 0.9,  lid: 0,    brow: 0.2, smile: true },
  alert:     { color: [255, 90, 90],   open: 1.25, lid: 0,    brow: 0.3 },
};
const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export function createFace(canvas, { onMood = () => {} } = {}) {
  const ctx = canvas.getContext('2d');
  const st = {
    mood: 'idle', gaze: [0, 0], tgaze: [0, 0], blink: 0, nextBlink: 2, lastInput: 0,
    talk: 0,          // current mouth opening 0..1
    speaking: false,  // an utterance is playing
    lastPulse: 0,     // time of the last word boundary (ms); without boundaries the mouth wiggles
    listening: false, // recogniser open -> ring under the mouth
    heard: 0,         // decays; bumped when the recogniser hears speech
  };
  const cur = { ...MOODS.idle, color: [...MOODS.idle.color] };

  function resize() { const d = Math.min(devicePixelRatio || 1, 2); canvas.width = innerWidth * d; canvas.height = innerHeight * d; ctx.setTransform(d, 0, 0, d, 0, 0); }
  addEventListener('resize', resize); resize();

  function roundRect(x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }

  let lastT = performance.now();
  function draw(now) {
    const dt = Math.min(0.1, (now - lastT) / 1000); lastT = now;
    const tgt = MOODS[st.mood] || MOODS.idle, k = 1 - Math.pow(0.001, dt);
    cur.open = lerp(cur.open, tgt.open, k); cur.lid = lerp(cur.lid, tgt.lid, k); cur.brow = lerp(cur.brow, tgt.brow, k);
    for (let i = 0; i < 3; i++) cur.color[i] = lerp(cur.color[i], tgt.color[i], k);

    st.nextBlink -= dt;
    if (st.nextBlink <= 0) { st.blink = 1; st.nextBlink = Math.random() < 0.2 ? 0.25 : 2 + Math.random() * 4; }
    st.blink = Math.max(0, st.blink - dt * 7);
    const blinkAmt = st.blink > 0 ? Math.sin(st.blink * Math.PI) : 0;

    if (now - st.lastInput > 4000 && Math.random() < dt * 0.6) st.tgaze = [(Math.random() * 2 - 1) * 0.6, (Math.random() * 2 - 1) * 0.35];
    if (st.mood === 'thinking') st.tgaze = [Math.sin(now / 900) * 0.5, -0.45];
    const kg = 1 - Math.pow(0.0001, dt);
    st.gaze[0] = lerp(st.gaze[0], st.tgaze[0], kg); st.gaze[1] = lerp(st.gaze[1], st.tgaze[1], kg);

    // mouth: word boundaries open it and it closes between them; no boundary events for a while -> wiggle
    let target = 0;
    if (st.speaking) {
      const since = now - st.lastPulse;
      target = since < 600 ? Math.max(0, 1 - since / 220) * 0.9 + 0.1 : (0.5 + 0.5 * Math.sin(now / 70)) * (0.5 + 0.5 * Math.sin(now / 173));
    }
    st.talk = lerp(st.talk, target, 1 - Math.pow(0.00001, dt));
    st.heard = Math.max(0, st.heard - dt * 1.5);

    const W = innerWidth, H = innerHeight;
    ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
    const portrait = H > W, size = Math.min(W, H) * (portrait ? 0.26 : 0.22);
    const cx = W / 2, cy = H * (portrait ? 0.4 : 0.45), gap = size * 1.25;
    const [r, g, b] = cur.color.map(Math.round), col = `rgb(${r},${g},${b})`;
    const breathe = 1 + Math.sin(now / 1400) * 0.015;
    for (const side of [-1, 1]) {
      const ex = cx + side * gap + st.gaze[0] * size * 0.35, ey = cy + st.gaze[1] * size * 0.35;
      const w = size * 0.8 * breathe, h = size * cur.open * breathe * (1 - blinkAmt * 0.92) * (1 - st.talk * 0.08);
      ctx.save(); ctx.shadowColor = `rgba(${r},${g},${b},.55)`; ctx.shadowBlur = size * 0.45; ctx.fillStyle = col;
      roundRect(ex - w / 2, ey - h / 2, w, Math.max(4, h), Math.min(w, Math.max(4, h)) * 0.45); ctx.fill(); ctx.restore();
      if (h > size * 0.3) {
        const pw = w * 0.32, ph = h * 0.38, px = ex + st.gaze[0] * w * 0.18, py = ey + st.gaze[1] * h * 0.15;
        ctx.fillStyle = 'rgba(0,0,0,.85)'; ctx.beginPath(); ctx.ellipse(px, py, pw / 2, ph / 2, 0, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.beginPath(); ctx.arc(px - pw * 0.18, py - ph * 0.2, pw * 0.14, 0, Math.PI * 2); ctx.fill();
      }
      if (cur.lid > 0.02) { ctx.fillStyle = '#000'; ctx.save(); ctx.translate(ex, ey - h / 2); ctx.rotate(side * -0.15); ctx.fillRect(-w, -h, w * 2, h + h * cur.lid); ctx.restore(); }
      if (tgt.smile) { ctx.fillStyle = '#000'; ctx.beginPath(); ctx.ellipse(ex, ey + h * 0.62, w * 0.7, h * 0.42, 0, 0, Math.PI * 2); ctx.fill(); }
      if (Math.abs(cur.brow) > 0.05) {
        ctx.strokeStyle = col; ctx.lineWidth = size * 0.07; ctx.lineCap = 'round'; ctx.beginPath();
        const by = ey - h / 2 - size * 0.25; ctx.moveTo(ex - w * 0.45, by + side * cur.brow * size * 0.3); ctx.lineTo(ex + w * 0.45, by - side * cur.brow * size * 0.3); ctx.stroke();
      }
    }
    const mw = size * 0.9, mh = Math.max(3, size * 0.06 + st.talk * size * 0.32), my = cy + size * 1.05;
    ctx.fillStyle = `rgba(${r},${g},${b},${st.talk > 0.05 ? 0.9 : 0.35})`;
    roundRect(cx - mw / 2, my - mh / 2, mw, mh, mh / 2); ctx.fill();
    if (st.listening) {
      ctx.strokeStyle = 'rgba(110,200,255,.8)'; ctx.lineWidth = 3; ctx.beginPath();
      ctx.arc(cx, my, size * (0.22 + 0.04 * Math.sin(now / 300) + st.heard * 0.35), 0, Math.PI * 2); ctx.stroke();
    }
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);

  canvas.addEventListener('click', (e) => { st.tgaze = [(e.clientX / innerWidth) * 2 - 1, (e.clientY / innerHeight) * 2 - 1]; st.lastInput = performance.now(); });

  return {
    state: st,
    get mood() { return st.mood; },
    setMood(m) { if (MOODS[m]) { st.mood = m; onMood(m); } },
    setGaze(g) { if (Array.isArray(g)) { st.tgaze = [clamp(+g[0] || 0, -1, 1), clamp(+g[1] || 0, -1, 1)]; st.lastInput = performance.now(); } },
    speaking(on) { st.speaking = !!on; if (on) st.lastPulse = performance.now(); },
    pulse() { st.lastPulse = performance.now(); },
    listening(on) { st.listening = !!on; },
    heard() { st.heard = 1; },
  };
}
