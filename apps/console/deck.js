// Deck calibration panel (Deck tab): jog the CNC, teach pipette-tip positions, edit pipette offset; the servo-hub tools
// (pipette / probe / stand: state, quick actions, calibration JSON from /api/cal/<id>).
const qs = new URLSearchParams(location.search);
const TOKEN = qs.get('token') || (() => { try { return localStorage.getItem('labrat_token'); } catch { return null; } })() || 'labrat';
const box = document.getElementById('deckBox');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const f2 = (v) => (v == null ? '—' : (+v).toFixed(2));
let cal = null, step = 1, target = 'plate.A1', msg = '', devs = {}, calOpen = null, calText = '';

async function api(path, body) {
  const r = await fetch(path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN), {
    method: body ? 'POST' : 'GET', headers: { 'content-type': 'application/json', authorization: 'Bearer ' + TOKEN },
    body: body ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.error || r.statusText);
  return j;
}
async function cncPos() {
  try { const st = await api('/api/state'); devs = Object.fromEntries((st.devices || []).map((d) => [d.id, d])); const c = devs.cnc; return c?.state?.wpos || c?.state?.mpos; } catch { return null; }
}
// servo-hub tools: quick actions per device (POST /api/device) and their calibration (GET/POST /api/cal/<id>)
const TOOLS = {
  pipette: [['home', {}], ['prime', {}], ['blowout', {}], ['stop', {}]],
  probe: [['deploy', {}], ['retract', {}]],
  stand: [['look deck', { at: 'deck' }], ['look operator', { at: 'operator' }], ['relax', {}]],
};
async function act(dev, label) {
  const [name, args] = TOOLS[dev].find(([l]) => l === label); const action = name.split(' ')[0];
  try { await api('/api/device', { device: dev, action, args }); msg = `${dev}.${action} ok`; } catch (e) { msg = `${dev}.${action} failed: ${e.message}`; }
  render();
}
async function toggleCal(dev) {
  if (calOpen === dev) { calOpen = null; return render(); }
  try { calText = JSON.stringify((await api('/api/cal/' + dev)).cal, null, 1); calOpen = dev; } catch (e) { msg = e.message; }
  render();
}
async function saveCal() {
  try { const patch = JSON.parse(box.querySelector('#calText').value); await api('/api/cal/' + calOpen, patch); msg = `${calOpen} calibration saved`; calOpen = null; } catch (e) { msg = 'cal save failed: ' + e.message; }
  render();
}
const short = (st) => Object.entries(st || {}).filter(([, v]) => v != null && typeof v !== 'object').map(([k, v]) => `${k} ${typeof v === 'number' ? +v.toFixed(2) : v}`).join(' · ');
async function load() { try { cal = await api('/api/deck_cal'); } catch (e) { msg = 'load failed: ' + e.message; } render(); }
async function jog(ax, d) {
  try { const r = await api('/api/jog', { device: 'cnc', dx: ax === 'x' ? d : 0, dy: ax === 'y' ? d : 0, dz: ax === 'z' ? d : 0 }); msg = 'jog ok → ' + (r.pos || []).map(f2).join(', '); } catch (e) { msg = 'jog failed: ' + e.message; }
  render();
}
async function teach() {
  try { const r = await api('/api/deck_cal/teach', { target }); cal = r.deck_cal; msg = `taught ${r.target}: tip (${['x', 'y', 'z'].map((k) => f2(r.tip[k])).join(', ')})`; } catch (e) { msg = 'teach failed: ' + e.message; }
  render();
}
async function forget(name) { try { cal = await api('/api/deck_cal', { points: { [name]: null } }); } catch (e) { msg = e.message; } render(); }
async function saveOffset() {
  const o = {}; for (const k of ['dx', 'dy', 'dz']) o[k] = +box.querySelector('#off_' + k).value;
  try { cal = await api('/api/deck_cal', { pipette_offset: o }); msg = 'offset saved'; } catch (e) { msg = e.message; } render();
}

async function render() {
  if (!cal) { box.innerHTML = '<div class="note">loading…</div>'; return; }
  const pos = await cncPos();
  const o = cal.pipette_offset || {};
  const tip = pos ? [pos[0] + o.dx, pos[1] + o.dy, pos[2] + o.dz] : null;
  const jogRow = (ax) => `<div class="row"><span class="lbl">${ax.toUpperCase()}</span>${[-10, -1, -0.1, 0.1, 1, 10].map((d) => `<button class="btn sm" data-jog="${ax}" data-d="${d}">${d > 0 ? '+' : ''}${d}</button>`).join('')}</div>`;
  const grids = cal.grids || {};
  box.innerHTML = `
    <div class="row"><h2>CNC</h2><span class="pos">spindle ${pos ? pos.map(f2).join(', ') : '—'}</span><span class="note">tip ${tip ? tip.map(f2).join(', ') : '—'}</span></div>
    ${['x', 'y', 'z'].map(jogRow).join('')}
    <div class="row"><select id="deckTarget">${(cal.targets || []).map((t) => `<option ${t === target ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>
      <button class="btn accent" id="teachBtn">Teach</button><span class="note">records tip = spindle + offset</span></div>
    <div class="row"><h2>Pipette offset</h2>${['dx', 'dy', 'dz'].map((k) => `<span class="lbl">${k}</span><input type="number" step="0.1" id="off_${k}" value="${o[k] ?? 0}">`).join('')}<button class="btn sm" id="offBtn">Save</button></div>
    <table><tr><th>target</th><th>tip x</th><th>tip y</th><th>tip z</th><th></th></tr>
      ${Object.entries(cal.points || {}).map(([k, p]) => `<tr><td>${esc(k)}</td><td>${f2(p.x)}</td><td>${f2(p.y)}</td><td>${f2(p.z)}</td><td><button class="btn sm ghost" data-forget="${esc(k)}">×</button></td></tr>`).join('') || '<tr><td colspan="5" class="note">nothing taught — nominal deck in use</td></tr>'}
    </table>
    <div class="note">${Object.entries(grids).map(([g, f]) => f ? `${g}: pitch ${f.pitch} mm, rot ${f.theta_deg}°` : `${g}: nominal`).join(' · ')}</div>
    <h2>Servo-hub tools</h2>
    ${Object.keys(TOOLS).map((d) => `<div class="row"><span class="pos" style="width:56px">${d}</span><span class="note">${esc(devs[d]?.status || '—')}</span>
      ${TOOLS[d].map(([l]) => `<button class="btn sm" data-act="${d}" data-l="${l}">${l}</button>`).join('')}<button class="btn sm ghost" data-cal="${d}">cal</button>
      <span class="note">${esc(short(devs[d]?.state))}</span></div>`).join('')}
    ${calOpen ? `<div class="row"><textarea id="calText" rows="10" class="mono" style="width:100%">${esc(calText)}</textarea><button class="btn sm accent" id="calSave">Save ${calOpen} cal</button></div>` : ''}
    <div class="note">${esc(msg)}</div>`;
  box.querySelectorAll('[data-jog]').forEach((b) => (b.onclick = () => jog(b.dataset.jog, +b.dataset.d)));
  box.querySelector('#deckTarget').onchange = (e) => (target = e.target.value);
  box.querySelector('#teachBtn').onclick = teach;
  box.querySelector('#offBtn').onclick = saveOffset;
  box.querySelectorAll('[data-forget]').forEach((b) => (b.onclick = () => forget(b.dataset.forget)));
  box.querySelectorAll('[data-act]').forEach((b) => (b.onclick = () => act(b.dataset.act, b.dataset.l)));
  box.querySelectorAll('[data-cal]').forEach((b) => (b.onclick = () => toggleCal(b.dataset.cal)));
  box.querySelector('#calSave')?.addEventListener('click', saveCal);
}

document.querySelector('.tab[data-tab="deck"]')?.addEventListener('click', load);
