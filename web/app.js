// LabRat laptop console. Vanilla ES module; talks to /api/* and /ws/ui.
import { Twin, parseColor } from './twin.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const qs = new URLSearchParams(location.search);
let TOKEN = qs.get('token') || (() => { try { return localStorage.getItem('labrat_token'); } catch { return null; } })() || 'labrat';
try { localStorage.setItem('labrat_token', TOKEN); } catch {}

const PILL_IDS = ['so_a', 'so_b', 'tap_a', 'cnc', 'pipette', 'phone', 'mongo', 'voyage', 'aws', 'raindrop', 'modelence', 'elevenlabs', 'llm'];
const PILL_LABEL = { mongo: 'MongoDB Atlas', voyage: 'Voyage AI', aws: 'AWS', raindrop: 'Raindrop', modelence: 'Modelence', elevenlabs: 'ElevenLabs', llm: 'LLM' };
const PILL_ALIAS = { mongo: ['mongo', 'mongodb', 'atlas', 'mongodb_atlas'], voyage: ['voyage', 'voyageai', 'voyage_ai'], aws: ['aws', 'server', 'ec2'], raindrop: ['raindrop'], modelence: ['modelence'], elevenlabs: ['elevenlabs', 'eleven_labs', 'tts'], llm: ['llm', 'anthropic', 'fireworks'] };
const S = {
  devices: {}, services: {}, experiments: new Map(), selExp: null, selVar: null, cmp: new Set(),
  sims: new Map(), // key `${exp}:${variant}` -> frames
  runs: new Map(), frames: null, fi: 0, playing: false, live: true, lastT: 0,
};
const idOf = (o) => o?.id ?? o?._id ?? o?.experiment_id;
const vidOf = (v) => v?.id ?? v?._id ?? v?.variant_id;

// ---------- twin ----------
const twin = new Twin($('#twin'));

// ---------- REST ----------
async function api(path, opts = {}) {
  const r = await fetch(path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN), {
    ...opts, headers: { 'content-type': 'application/json', authorization: 'Bearer ' + TOKEN, 'x-labrat-token': TOKEN, ...(opts.headers || {}) },
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => '')}`.slice(0, 300));
  return r.json().catch(() => ({}));
}

// ---------- status pills ----------
function normStatus(v) {
  if (v == null) return 'off';
  if (typeof v === 'boolean') return v ? 'ok' : 'off';
  if (typeof v === 'string') return v;
  if (typeof v === 'object') {
    if (v.status) return v.status;
    if ('ok' in v) return v.ok ? (v.mock || v.fallback ? 'mock' : 'ok') : (v.fallback || v.mock ? 'mock' : 'error');
    if ('enabled' in v) return v.enabled ? 'ok' : 'mock';
    if ('connected' in v) return v.connected ? 'ok' : 'off';
  }
  return 'ok';
}
function renderPills() {
  const all = { ...S.services, ...S.devices };
  $('#pills').innerHTML = PILL_IDS.map((id) => {
    let v = all[id]; for (const a of PILL_ALIAS[id] || []) if (v == null && all[a] != null) v = all[a];
    if (id === 'aws' && v == null && S.wsUp) v = 'ok'; // we're talking to the AWS-hosted server
    const st = normStatus(v); const cls = st === 'ok' ? 'ok' : st === 'mock' || st === 'fallback' || st === 'degraded' ? 'mock' : st === 'error' ? 'error' : 'off';
    const tip = v != null ? esc(JSON.stringify(v).slice(0, 300)) : 'no data';
    return `<span class="pill ${cls}" title="${tip}"><i></i>${esc(PILL_LABEL[id] || id)}</span>`;
  }).join('');
}
function ingestState(st) {
  if (!st) return;
  const devs = st.devices || {};
  if (Array.isArray(devs)) for (const d of devs) S.devices[d.id] = { ...(S.devices[d.id] || {}), ...d };
  else for (const [k, v] of Object.entries(devs)) S.devices[k] = typeof v === 'object' ? { ...(S.devices[k] || {}), ...v } : v;
  const svc = st.services || st.integrations || st.sponsors || {};
  Object.assign(S.services, svc);
  for (const k of [...PILL_IDS, ...Object.values(PILL_ALIAS).flat()]) if (k in st && k !== 'devices' && !(k in S.devices)) S.services[k] = st[k];
  if ('phone' in st && typeof st.phone !== 'object') S.services.phone = st.phone;
  if (st.phone_connected != null) S.services.phone = st.phone_connected;
  renderPills();
  if (S.live) { const live = {}; for (const [k, v] of Object.entries(S.devices)) live[k] = v.state || v; twin.applyDevices(live); }
  if (st.experiments) for (const e of st.experiments) upsertExp(e, false);
  if (st.latest_frame || st.frame) setThumb(st.latest_frame || st.frame);
}
async function pollState() { try { ingestState(await api('/api/state')); } catch (e) { /* server may be down */ } }

// ---------- camera thumb ----------
function setThumb(j) { if (!j) return; const src = j.startsWith('data:') || j.startsWith('http') || j.startsWith('/') ? j : 'data:image/jpeg;base64,' + j; $('#camThumb').src = src; S.lastFrame = src; }
$('#camThumb').onclick = () => $('#camThumb').classList.toggle('big');

// ---------- chat ----------
const chatLog = $('#chatLog');
function addMsg(role, text, meta = {}) {
  const atBottom = chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 60;
  let el;
  if (role === 'tool') {
    el = document.createElement('div');
    const name = meta.name || meta.tool || (String(text).match(/^(\w+)/) || [])[1] || 'tool';
    const err = meta.error || meta.ok === false;
    const args = meta.args ?? meta.input ?? meta.result ?? text;
    el.className = 'chip' + (err ? ' err' : '');
    el.innerHTML = `<b>⚙ ${esc(name)}</b><span class="args">${esc(typeof args === 'string' ? args : JSON.stringify(args))}</span>`;
    el.onclick = () => el.classList.toggle('open');
  } else {
    // streaming support: meta.stream_id / meta.partial -> update same bubble
    const sid = meta.stream_id || meta.id;
    if (sid && role === 'agent') el = chatLog.querySelector(`[data-sid="${CSS.escape(String(sid))}"]`);
    if (el) { el.textContent = meta.delta ? el.textContent + text : text; }
    else { el = document.createElement('div'); el.className = 'msg ' + (role === 'user' || role === 'agent' ? role : 'system'); el.textContent = text; if (sid) el.dataset.sid = sid; }
  }
  if (!el.isConnected) chatLog.appendChild(el);
  if (atBottom) chatLog.scrollTop = chatLog.scrollHeight;
  if (role === 'agent' || role === 'tool') setAgentStatus(meta.partial || role === 'tool' ? 'working…' : 'idle', role === 'tool' || meta.partial);
}
function setAgentStatus(t, busy) { const s = $('#agentStatus'); s.textContent = t; s.classList.toggle('busy', !!busy); }
$('#chatForm').onsubmit = (e) => {
  e.preventDefault(); const text = $('#chatInput').value.trim(); if (!text) return;
  $('#chatInput').value = ''; addMsg('user', text, { local: true }); setAgentStatus('thinking…', true);
  if (!wsSend({ t: 'chat', text })) api('/api/chat', { method: 'POST', body: JSON.stringify({ text }) }).then((r) => { if (r?.reply || r?.text) addMsg('agent', r.reply || r.text); }).catch((err) => addMsg('system', 'chat failed: ' + err.message));
};
$('#chatInput').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#chatForm').requestSubmit(); } });
$('#abortBtn').onclick = () => { wsSend({ t: 'abort' }) || api('/api/abort', { method: 'POST' }).catch(() => {}); addMsg('system', 'ABORT sent'); };

// ---------- experiments ----------
function variantsOf(e) { return e?.variants || []; }
function scoreOf(v) { const s = v?.score ?? v?.sim?.score ?? v?.outcome?.score ?? v?.result?.score; return s == null ? null : +s; }
function colorOf(v) { return v?.predicted_rgb ?? v?.predicted?.rgb ?? v?.outcome?.rgb ?? v?.sim?.rgb ?? v?.result?.rgb ?? v?.rgb ?? v?.color ?? v?.predicted_color; }
function checksOf(v) {
  const c = v?.checks ?? v?.sim?.checks ?? v?.outcome?.checks ?? v?.result?.checks; if (!c) return [];
  if (Array.isArray(c)) return c.map((x) => typeof x === 'string' ? { name: x, ok: true } : { name: x.name || x.id || '?', ok: x.ok ?? x.pass ?? x.passed, msg: x.msg || x.detail });
  return Object.entries(c).map(([k, x]) => typeof x === 'object' && x ? { name: k, ok: x.ok ?? x.pass ?? x.passed, msg: x.msg || x.detail } : { name: k, ok: !!x });
}
function passOf(v) { const p = v?.pass ?? v?.passed ?? v?.sim?.pass ?? v?.outcome?.pass ?? v?.sim_pass; if (p != null) return !!p; const c = checksOf(v); return c.length ? c.every((x) => x.ok) : null; }
function timeOf(v) { return v?.time_s ?? v?.sim?.time_s ?? v?.outcome?.time_s ?? v?.total_time_s ?? v?.duration_s ?? v?.sim?.total_time; }
function cssColor(c) { const col = parseColor(c); return col ? '#' + col.getHexString() : 'transparent'; }
function bestOf(e) { const vs = variantsOf(e).filter((v) => scoreOf(v) != null && passOf(v) !== false); vs.sort((a, b) => scoreOf(b) - scoreOf(a)); return e?.best_variant_id ?? e?.selected_variant_id ?? (vs[0] && vidOf(vs[0])); }

function upsertExp(e, render = true) {
  const id = idOf(e); if (id == null) return;
  const prev = S.experiments.get(id) || {};
  const merged = { ...prev, ...e };
  if (e.variants && prev.variants) { // merge variants by id, keep frames
    const m = new Map(prev.variants.map((v) => [vidOf(v), v]));
    merged.variants = e.variants.map((v) => ({ ...(m.get(vidOf(v)) || {}), ...v }));
  }
  S.experiments.set(id, merged);
  for (const v of merged.variants || []) if (v.frames?.length) S.sims.set(`${id}:${vidOf(v)}`, v.frames);
  for (const r of merged.runs || []) S.runs.set(idOf(r) ?? r.run_id, { experiment_id: id, ...r });
  if (S.selExp == null) S.selExp = id;
  if (render) renderGallery();
}
async function loadExperiments() {
  try { const r = await api('/api/experiments'); const list = Array.isArray(r) ? r : r.experiments || r.items || []; for (const e of list) upsertExp(e, false); renderGallery(); } catch {}
}
async function loadExp(id) { try { const r = await api('/api/experiments/' + encodeURIComponent(id)); upsertExp(r.experiment || r); } catch {} }

function renderGallery() {
  const exps = [...S.experiments.values()].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
  $('#expList').innerHTML = exps.length ? exps.map((e) => {
    const vs = variantsOf(e); const b = vs.find((v) => vidOf(v) === bestOf(e));
    return `<div class="exp-row ${idOf(e) === S.selExp ? 'sel' : ''}" data-id="${esc(idOf(e))}">
      <div class="goal">${esc(e.goal || e.name || idOf(e))}</div>
      <div class="meta">${esc(e.status || '')} · ${vs.length} variants${b ? ` · best ${scoreOf(b)?.toFixed?.(3) ?? '-'}` : ''}</div></div>`;
  }).join('') : '<div class="empty">No experiments yet. Ask the agent to design one.</div>';
  for (const el of $('#expList').querySelectorAll('.exp-row')) el.onclick = () => { S.selExp = el.dataset.id; S.cmp.clear(); renderGallery(); loadExp(el.dataset.id); };
  renderDetail();
}
function renderDetail() {
  const e = S.experiments.get(S.selExp); const box = $('#expDetail');
  if (!e) { box.innerHTML = ''; return; }
  const vs = variantsOf(e).slice().sort((a, b) => (scoreOf(b) ?? -1e9) - (scoreOf(a) ?? -1e9)); const best = bestOf(e);
  const runs = [...S.runs.values()].filter((r) => r.experiment_id == idOf(e)).sort((a, b) => String(b.created_at || b.started_at || '').localeCompare(String(a.created_at || a.started_at || '')));
  const sel = vs.find((v) => vidOf(v) === S.selVar);
  box.innerHTML = `
    <div><h2>Goal</h2><div style="margin-top:4px">${esc(e.goal || '')}</div>
      ${e.memory_hits?.length ? `<div class="sm mono" style="color:var(--text-3);font-size:10px;margin-top:4px">recalled: ${esc(e.memory_hits.map((m) => m.text || m).join(' · ').slice(0, 200))}</div>` : ''}</div>
    <div class="actions">
      <input id="nVar" type="number" min="1" max="64" value="8" class="mono"><button id="simBtn" class="btn">Simulate N</button>
      <button id="runBtn" class="btn accent" ${best ? '' : 'disabled'}>Run best on real hardware</button>
      <button id="cmpBtn" class="btn ghost" ${S.cmp.size > 1 ? '' : 'disabled'}>Compare (${S.cmp.size})</button>
    </div>
    <div class="cmp-host"></div>
    <div class="cards">${vs.map((v) => card(v, vidOf(v) === best)).join('') || '<div class="empty">No variants yet — hit “Simulate N”.</div>'}</div>
    ${sel ? `<div><h2>Variant ${esc(sel.name || vidOf(sel))} protocol</h2><div class="steps">${esc(stepsText(sel))}</div>${sel.rationale ? `<div style="color:var(--text-2);margin-top:4px">${esc(sel.rationale)}</div>` : ''}</div>` : ''}
    <div><h2>Runs</h2></div>
    ${runs.map(runCard).join('') || '<div class="empty">No real runs yet.</div>'}`;
  box.querySelector('#simBtn').onclick = async () => { const n = +box.querySelector('#nVar').value || 8; addMsg('system', `simulating ${n} variants…`); try { const r = await api(`/api/experiments/${encodeURIComponent(idOf(e))}/simulate`, { method: 'POST', body: JSON.stringify({ n_variants: n }) }); if (r && (r.variants || r.experiment)) upsertExp(r.experiment || { ...r, id: idOf(e) }); } catch (err) { addMsg('system', 'simulate failed: ' + err.message); } };
  box.querySelector('#runBtn').onclick = async () => {
    const vid = S.selVar && vs.some((v) => vidOf(v) === S.selVar) ? S.selVar : best;
    if (!confirm(`Run variant ${vid} on REAL hardware?`)) return;
    try { const r = await api(`/api/experiments/${encodeURIComponent(idOf(e))}/run`, { method: 'POST', body: JSON.stringify({ variant_id: vid }) }); const run = r.run || r; if (idOf(run) || run.run_id) ingestRun(run); addMsg('system', `run requested for ${vid}`); } catch (err) { addMsg('system', 'run failed: ' + err.message); }
  };
  box.querySelector('#cmpBtn').onclick = () => renderCompare(box.querySelector('.cmp-host'), vs.filter((v) => S.cmp.has(vidOf(v))));
  for (const c of box.querySelectorAll('.card')) {
    c.onclick = (ev) => {
      const vid = c.dataset.vid;
      if (ev.shiftKey || ev.metaKey) { S.cmp.has(vid) ? S.cmp.delete(vid) : S.cmp.add(vid); renderDetail(); return; }
      S.selVar = vid; renderDetail(); playVariant(idOf(e), vid);
    };
  }
  for (const rb of box.querySelectorAll('.rate')) {
    const rid = rb.dataset.run; const color = rb.querySelector('input[type=color]');
    for (const b of rb.querySelectorAll('[data-rate]')) b.onclick = async (ev) => {
      ev.stopPropagation(); const prev = S.runs.get(rid) || {};
      const rating = b.dataset.rate === 'save' ? (prev.rating ?? null) : +b.dataset.rate;
      const hex = color.value; const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
      try { await api(`/api/runs/${encodeURIComponent(rid)}/observe`, { method: 'POST', body: JSON.stringify({ rating, rgb }) }); S.runs.set(rid, { ...prev, rating, observed_rgb: rgb }); addMsg('system', `rated run ${rid}: ${rating ?? '-'} rgb(${rgb})`); renderDetail(); }
      catch (err) { addMsg('system', 'observe failed: ' + err.message); }
    };
  }
  for (const b of box.querySelectorAll('[data-approve]')) b.onclick = (ev) => { ev.stopPropagation(); wsSend({ t: 'approve', run_id: b.dataset.approve }); addMsg('system', 'approved run ' + b.dataset.approve); };
}
function card(v, isBest) {
  const vid = vidOf(v); const sc = scoreOf(v); const pass = passOf(v); const t = timeOf(v); const ch = checksOf(v);
  return `<div class="card ${vid === S.selVar ? 'sel' : ''} ${S.cmp.has(vid) ? 'cmp' : ''} ${isBest ? 'best' : ''}" data-vid="${esc(vid)}" title="click: replay sim · shift-click: add to compare">
    <div class="row"><div class="swatch" style="background:${cssColor(colorOf(v))}"></div>
      <div style="min-width:0"><div class="name">${esc(v.name || v.label || vid)}</div><div class="score">${sc == null ? '—' : sc.toFixed(3)}</div></div></div>
    <div class="sm">${pass == null ? 'not simulated' : pass ? '✓ pass' : '✗ fail'}${t != null ? ` · ${(+t).toFixed(1)}s` : ''}${v.status ? ` · ${esc(v.status)}` : ''}</div>
    <div class="checks">${ch.map((c) => `<span class="check ${c.ok ? 'pass' : 'fail'}" title="${esc(c.msg || '')}">${esc(c.name)}</span>`).join('')}</div></div>`;
}
function stepsText(v) {
  const steps = v.protocol?.steps || v.steps || v.protocol || [];
  if (!Array.isArray(steps)) return JSON.stringify(steps, null, 1);
  return steps.map((s, i) => { const op = s.op || s.type || s.action || Object.keys(s)[0]; const args = { ...s }; delete args.op; delete args.type; delete args.action; return `${String(i + 1).padStart(2)} ${op} ${JSON.stringify(args.args || args)}`; }).join('\n');
}
function renderCompare(host, vs) {
  const keys = [...new Set(vs.flatMap((v) => Object.keys(v.params || {})))];
  host.innerHTML = `<div class="cmp-box"><table><tr><th></th>${vs.map((v) => `<th><span class="swatch" style="display:inline-block;width:12px;height:12px;vertical-align:middle;background:${cssColor(colorOf(v))}"></span> ${esc(v.name || vidOf(v))}</th>`).join('')}</tr>
    <tr><td>score</td>${vs.map((v) => `<td>${scoreOf(v)?.toFixed(3) ?? '—'}</td>`).join('')}</tr>
    <tr><td>pass</td>${vs.map((v) => `<td>${passOf(v) ? '✓' : '✗'}</td>`).join('')}</tr>
    <tr><td>time</td>${vs.map((v) => `<td>${timeOf(v) ?? '—'}</td>`).join('')}</tr>
    ${keys.map((k) => `<tr><td>${esc(k)}</td>${vs.map((v) => `<td>${esc(JSON.stringify(v.params?.[k] ?? ''))}</td>`).join('')}</tr>`).join('')}</table></div>`;
}
function runCard(r) {
  const photo = r.photo_jpeg || r.photo_b64 || r.photo || r.image || r.photo_url;
  const src = photo ? (photo.startsWith('data:') || photo.startsWith('http') || photo.startsWith('/') ? photo : 'data:image/jpeg;base64,' + photo) : '';
  const rid = idOf(r) ?? r.run_id; const pending = /await|pending|approval/.test(String(r.status || ''));
  return `<div class="run">${src ? `<img src="${esc(src)}">` : '<img alt="">'}<div style="min-width:0;flex:1">
    <div><b>${esc(r.variant_id || '')}</b> <span class="sm">${esc(r.status || '')}</span></div>
    <div class="sm">${esc(r.created_at || r.started_at || '')}</div>
    ${r.observation ? `<div style="color:var(--text-2)">${esc(r.observation)}</div>` : ''}
    ${r.measured_rgb || r.observed_rgb ? `<div class="sm">observed <span class="swatch" style="display:inline-block;width:12px;height:12px;vertical-align:middle;background:${cssColor(r.measured_rgb || r.observed_rgb)}"></span></div>` : ''}
    ${pending ? `<button class="btn accent" data-approve="${esc(rid)}">Approve run</button>` : `<div class="rate" data-run="${esc(rid)}"><span class="sm">rate</span>${[1, 2, 3, 4, 5].map((n) => `<button data-rate="${n}" class="${(r.rating ?? r.observation_rating) == n ? 'on' : ''}">${n}</button>`).join('')}<input type="color" title="observed colour" value="${cssColor(r.measured_rgb || r.observed_rgb || r.rgb) === 'transparent' ? '#ff9900' : cssColor(r.measured_rgb || r.observed_rgb || r.rgb)}"><button data-rate="save" title="send colour with rating" style="width:auto;padding:0 6px">save</button></div>`}</div></div>`;
}
function ingestRun(run) {
  const rid = idOf(run) ?? run.run_id; if (rid == null) return;
  S.runs.set(rid, { ...(S.runs.get(rid) || {}), ...run });
  if (run.experiment_id != null && !S.experiments.has(run.experiment_id)) upsertExp({ id: run.experiment_id, goal: run.goal || '(experiment)' }, false);
  if (/await|pending|approval/.test(String(run.status || ''))) addMsg('system', `run ${rid} awaiting approval — see gallery`);
  renderGallery();
}
$('#refreshBtn').onclick = () => { loadExperiments(); pollState(); const t = document.querySelector('.tab.on')?.dataset.tab; if (t === 'learn') loadLearning(); if (t === 'mem') searchMemory($('#memQ').value); };

// ---------- tabs ----------
for (const t of document.querySelectorAll('.tab')) t.onclick = () => {
  for (const x of document.querySelectorAll('.tab')) x.classList.toggle('on', x === t);
  for (const p of document.querySelectorAll('.tabpane')) p.classList.toggle('hidden', p.dataset.pane !== t.dataset.tab);
  if (t.dataset.tab === 'learn') loadLearning(); if (t.dataset.tab === 'mem') searchMemory($('#memQ').value);
};

// ---------- learning (sim-model self-improvement) ----------
async function loadLearning() {
  let r = null; try { r = await api('/api/learning'); } catch (e) { r = null; }
  renderLearning(Array.isArray(r) ? r : r?.versions || []);
}
function renderLearning(vs) {
  const box = $('#learnBox');
  if (!vs.length) { box.innerHTML = '<div><h2>Sim-model learning</h2></div><div class="empty">No model versions yet. After real runs are observed, the simulator refits and each version shows up here.</div>'; return; }
  vs = vs.slice().sort((a, b) => (+a.v || 0) - (+b.v || 0));
  const W = 340, H = 200, L = 34, R = 8, T = 12, B = 24;
  const ys = vs.flatMap((v) => [v.error_before, v.error_after]).filter((x) => x != null && isFinite(x)).map(Number);
  const ymax = Math.max(1e-9, ...ys) * 1.1;
  const X = (i) => L + (vs.length === 1 ? (W - L - R) / 2 : i * (W - L - R) / (vs.length - 1));
  const Y = (y) => T + (H - T - B) * (1 - y / ymax);
  const line = (k) => vs.map((v, i) => v[k] == null ? null : `${X(i)},${Y(+v[k])}`).filter(Boolean).join(' ');
  const grid = [0, .25, .5, .75, 1].map((f) => `<line x1="${L}" x2="${W - R}" y1="${Y(ymax * f)}" y2="${Y(ymax * f)}" stroke="#1a1a1a"/><text x="${L - 4}" y="${Y(ymax * f) + 3}" text-anchor="end">${(ymax * f).toPrecision(2)}</text>`).join('');
  const marks = vs.map((v, i) => `${v.error_before != null && v.error_after != null ? `<line x1="${X(i)}" x2="${X(i)}" y1="${Y(+v.error_before)}" y2="${Y(+v.error_after)}" stroke="#6ee7c8" stroke-dasharray="2 2"/>` : ''}
    ${v.error_before != null ? `<circle cx="${X(i)}" cy="${Y(+v.error_before)}" r="3.5" fill="#ff5a5a"><title>v${esc(v.v)} before: ${(+v.error_before).toFixed(4)}</title></circle>` : ''}
    ${v.error_after != null ? `<circle cx="${X(i)}" cy="${Y(+v.error_after)}" r="3.5" fill="#f5d76e"><title>v${esc(v.v)} after: ${(+v.error_after).toFixed(4)}</title></circle>` : ''}
    <text x="${X(i)}" y="${H - 8}" text-anchor="middle">v${esc(v.v)}</text>`).join('');
  const first = vs.find((v) => v.error_before != null)?.error_before, last = [...vs].reverse().find((v) => v.error_after != null)?.error_after;
  const gain = first != null && last != null && +first > 0 ? (1 - last / first) * 100 : null;
  box.innerHTML = `<div><h2>Sim-model prediction error by version</h2>
    ${gain != null ? `<div style="margin-top:4px">Error went from <b class="mono">${(+first).toFixed(4)}</b> to <b class="mono" style="color:var(--accent)">${(+last).toFixed(4)}</b>: <b class="mono" style="color:var(--ok)">${gain.toFixed(1)}% lower</b> across ${vs.length} versions.</div>` : ''}</div>
    <svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}
      <polyline points="${line('error_before')}" fill="none" stroke="#ff5a5a" stroke-opacity=".5"/>
      <polyline points="${line('error_after')}" fill="none" stroke="#f5d76e" stroke-width="1.5"/>${marks}</svg>
    <div class="legend"><span><i style="background:#ff5a5a"></i>before refit</span><span><i style="background:#f5d76e"></i>after refit</span><span><i style="background:#6ee7c8"></i>improvement</span></div>
    <table class="vtable"><tr><th>v</th><th>before</th><th>after</th><th>runs</th><th>params</th></tr>
    ${vs.slice().reverse().map((v) => `<tr><td>${esc(v.v)}</td><td>${v.error_before != null ? (+v.error_before).toFixed(4) : '—'}</td><td>${v.error_after != null ? (+v.error_after).toFixed(4) : '—'}</td><td>${esc(v.n_runs ?? '')}</td><td title="${esc(v.created_at || '')}">${esc(JSON.stringify(v.params || {})).slice(0, 160)}</td></tr>`).join('')}</table>`;
}

// ---------- memory browser ----------
const MEM_TYPES = ['semantic', 'episodic', 'procedural', 'taxonomic'];
async function searchMemory(q) {
  const box = $('#memBox'); box.innerHTML = '<div class="empty">searching…</div>';
  let r; try { r = await api('/api/memory/search?q=' + encodeURIComponent(q || '')); } catch (e) { box.innerHTML = `<div class="empty">memory search failed: ${esc(e.message)}</div>`; return; }
  const res = Array.isArray(r) ? r : r?.results || r?.memories || [];
  if (!res.length) { box.innerHTML = '<div class="empty">No memories found.</div>'; return; }
  const groups = {}; for (const m of res) { const t = MEM_TYPES.includes(m.type) ? m.type : (m.type || 'other'); (groups[t] ||= []).push(m); }
  const order = [...MEM_TYPES, ...Object.keys(groups).filter((k) => !MEM_TYPES.includes(k))];
  box.innerHTML = order.filter((t) => groups[t]).map((t) => `<div class="mem-group"><h2>${esc(t)} · ${groups[t].length}</h2>
    ${groups[t].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).map((m) => `<div class="mem"><div>${esc(m.text ?? m.content ?? JSON.stringify(m))}</div>
      <div class="sm">${m.score != null ? 'score ' + (+m.score).toFixed(3) : ''}${m.tags?.length ? ' · ' + esc(m.tags.join(', ')) : ''}${m.created_at ? ' · ' + esc(m.created_at) : ''}</div></div>`).join('')}</div>`).join('');
}
$('#memForm').onsubmit = (e) => { e.preventDefault(); searchMemory($('#memQ').value); };

// ---------- sim replay ----------
const scrub = $('#scrub');
function setFrames(frames, label) {
  S.frames = frames; S.fi = 0; S.live = false; twin.resetWells();
  scrub.max = Math.max(0, frames.length - 1); scrub.value = 0; showFrame(0);
  $('#twinMode').textContent = 'sim ' + (label || ''); S.playing = true; $('#playBtn').textContent = '❚❚';
}
async function playVariant(eid, vid) {
  let fr = S.sims.get(`${eid}:${vid}`);
  if (!fr) { await loadExp(eid); fr = S.sims.get(`${eid}:${vid}`); }
  if (fr?.length) setFrames(fr, vid); else addMsg('system', `no sim frames for ${vid} yet`);
}
function showFrame(i) {
  if (!S.frames?.length) return;
  i = Math.max(0, Math.min(S.frames.length - 1, i)); S.fi = i; scrub.value = i;
  // wells are cumulative: apply all well updates up to i when scrubbing backwards
  const f = S.frames[i];
  if (i === 0 || S._lastShown == null || i < S._lastShown) { twin.resetWells(); for (let k = 0; k <= i; k++) { if (S.frames[k].wells) twin.setWells(S.frames[k].wells); twin.usedTips(S.frames[k].tips_used); } }
  else for (let k = S._lastShown + 1; k <= i; k++) { if (S.frames[k].wells) twin.setWells(S.frames[k].wells); twin.usedTips(S.frames[k].tips_used); }
  S._lastShown = i;
  twin.applyFrame({ ...f, wells: null });
  $('#scrubLabel').textContent = `t=${(+f.t || 0).toFixed(1)}s ${i + 1}/${S.frames.length}`;
  const c = f.cnc || {}; const p = f.pipette || {};
  $('#twinInfo').textContent = `${f.step ? 'step ' + (typeof f.step === 'string' ? f.step : JSON.stringify(f.step)) + '\n' : ''}cnc  x${(+c.x || 0).toFixed(1)} y${(+c.y || 0).toFixed(1)} z${(+c.z || 0).toFixed(1)}\npip  ${(+p.vol_ul || 0).toFixed(0)}µL tip:${p.tip ?? '-'}`;
}
scrub.oninput = () => { S.playing = false; $('#playBtn').textContent = '▶'; showFrame(+scrub.value); };
$('#playBtn').onclick = () => { if (!S.frames?.length) return; if (S.fi >= S.frames.length - 1) showFrame(0); S.playing = !S.playing; $('#playBtn').textContent = S.playing ? '❚❚' : '▶'; };
$('#liveBtn').onclick = () => { S.live = true; S.playing = false; S.frames = null; $('#twinMode').textContent = 'live'; $('#playBtn').textContent = '▶'; $('#twinInfo').textContent = ''; twin.resetWells(); pollState(); };
let simClock = 0, lastNow = performance.now();
(function tick(now) {
  const dt = (now - lastNow) / 1000; lastNow = now;
  if (S.playing && S.frames?.length) {
    const speed = +$('#speedSel').value; const f0 = S.frames[S.fi], f1 = S.frames[S.fi + 1];
    if (!f1) { S.playing = false; $('#playBtn').textContent = '▶'; }
    else {
      const hasT = f0.t != null && f1.t != null;
      simClock += dt * speed;
      const need = hasT ? Math.max(0.03, f1.t - f0.t) : 0.25;
      if (simClock >= need) { simClock = 0; showFrame(S.fi + 1); }
    }
  }
  requestAnimationFrame(tick);
})(lastNow);

// ---------- websocket ----------
let ws = null, wsBackoff = 500;
function wsSend(o) { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(o)); return true; } return false; }
function connect() {
  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/ui?token=${encodeURIComponent(TOKEN)}`;
  try { ws = new WebSocket(url); } catch { return setTimeout(connect, wsBackoff); }
  ws.onopen = () => { wsBackoff = 500; S.wsUp = true; renderPills(); $('#conn').textContent = 'online'; $('#conn').classList.add('on'); pollState(); loadExperiments(); };
  ws.onclose = () => { S.wsUp = false; $('#conn').textContent = 'offline'; $('#conn').classList.remove('on'); setTimeout(connect, wsBackoff); wsBackoff = Math.min(8000, wsBackoff * 1.7); };
  ws.onerror = () => { try { ws.close(); } catch {} };
  ws.onmessage = (ev) => { let m; try { m = JSON.parse(ev.data); } catch { return; } try { handle(m); } catch (e) { console.error('handle', m.t, e); } };
}
function handle(m) {
  switch (m.t) {
    case 'devices': case 'state': ingestState(m.devices || m.services ? m : { devices: m.data || {} }); break;
    case 'chat': if (m.role === 'user' && m.meta?.source !== 'voice' && [...chatLog.querySelectorAll('.msg.user')].slice(-1)[0]?.textContent === m.text) break; addMsg(m.role, m.text, m.meta || {}); break;
    case 'status': setAgentStatus(m.text || m.status, m.busy ?? m.status !== 'idle'); break;
    case 'experiment': upsertExp(m.experiment); break;
    case 'variant': { const e = S.experiments.get(m.experiment_id); if (e) { const vs = (e.variants || []).filter((v) => vidOf(v) !== vidOf(m.variant)); vs.push(m.variant); upsertExp({ ...e, variants: vs }); } break; }
    case 'sim': {
      S.sims.set(`${m.experiment_id}:${m.variant_id}`, m.frames || []);
      if (m.variant || m.result) { const e = S.experiments.get(m.experiment_id); if (e) upsertExp({ id: m.experiment_id, variants: (e.variants || []).map((v) => vidOf(v) === m.variant_id ? { ...v, ...(m.variant || m.result) } : v) }); }
      if (m.frames?.length && (!S.playing || S.selVar == null || m.variant_id === S.selVar)) { S.selExp ??= m.experiment_id; setFrames(m.frames, m.variant_id); }
      break;
    }
    case 'run': ingestRun(m.run); if (m.run?.frames?.length) setFrames(m.run.frames, 'run'); break;
    case 'frame': setThumb(m.jpeg); break;
    case 'log': if (m.level === 'error' || m.level === 'warn') addMsg('system', `[${m.level}] ${m.text || m.msg || JSON.stringify(m)}`); break;
    case 'transcript': addMsg(m.role === 'user' ? 'user' : 'agent', m.text, { voice: true }); break;
    case 'learning': renderLearning(m.versions || m.learning?.versions || []); break;
    case 'live': if (S.live) twin.applyDevices(m.devices); break;
  }
}
connect();
setInterval(pollState, 3000);
renderPills();
addMsg('system', `token=${TOKEN} · connecting to /ws/ui`);
window.labrat = { S, twin, handle };
