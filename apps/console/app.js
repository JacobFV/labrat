// LabRat laptop console. Vanilla ES module; talks to /api/* and /ws/ui. The data shapes are in docs/experiments.md.
// An experiment is one plate: samples (µL of each stock per beaker) -> dry run -> run (approved) -> photo -> results.
import { Twin } from './twin.js';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const qs = new URLSearchParams(location.search);
const TOKEN = qs.get('token') || (() => { try { return localStorage.getItem('labrat_token'); } catch { return null; } })() || 'labrat';
try { localStorage.setItem('labrat_token', TOKEN); } catch {}
const withToken = (path) => path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN);

// ---------- the lab ----------
const STOCKS = ['water', 'red', 'yellow', 'blue'];
const STOCK_UL = 4500, BEAKER_UL = 5000;
const ROWS = 'ABCDEF', COLS = 7;
const WELLS = Array.from({ length: ROWS.length * COLS }, (_, i) => `${ROWS[i % ROWS.length]}${Math.floor(i / ROWS.length) + 1}`);
const TARGETS = { // same names as cloud/src/sim.ts TARGETS
  orange: [255, 140, 0], green: [40, 170, 60], purple: [128, 40, 160], brown: [130, 75, 30], pink: [255, 150, 180], red: [220, 20, 30],
  yellow: [250, 230, 20], blue: [30, 70, 220], teal: [0, 150, 150], black: [20, 20, 20], lime: [160, 230, 40],
};
const STATUS = {
  draft: ['Needs fixes', 'The dry run found a problem. Fix the samples and save.'],
  ready: ['Ready', 'The dry run passed. Run it on the robot when the deck is set up.'],
  awaiting_approval: ['Waiting for approval', 'Check the deck (empty beakers, full stocks, a full tip rack), then approve.'],
  running: ['Running', 'The robot is filling the plate.'],
  done: ['Done', 'Finished. Measure the photo or enter colours by hand below.'],
  failed: ['Failed', 'The run stopped with an error.'],
  aborted: ['Aborted', 'The run was stopped.'],
  new: ['Not saved', 'Fill in the samples and save to dry-run them.'],
};
const EDITABLE = new Set(['draft', 'ready', 'new']);
const FINISHED = new Set(['done', 'failed', 'aborted']);

const S = {
  exps: new Map(),        // id -> experiment (as returned by the API)
  sel: null,              // selected experiment id, or 'new'
  drafts: new Map(),      // id | 'new' -> {title, target, notes, samples} being edited, not saved yet
  preview: null,          // {key, seq, check, wells, placed, error} from POST /api/dry_run for the draft
  saveError: null,
  selWell: null,
  results: new Map(),     // `${id}:${well}` -> {rgb, rating, note} typed in, not recorded yet
  notes: [],              // notebook entries of the selected experiment
  devices: [], services: {}, phones: 0,
  model: null,
  frames: null, fi: 0, playing: false, live: true,
};

// ---------- small helpers ----------
const hex = (rgb) => rgb ? '#' + rgb.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('') : null;
const rgbOfHex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const dist = (a, b) => (a && b ? Math.round(Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) * 10) / 10 : null);
const total = (s) => STOCKS.reduce((a, k) => a + (+s[k] || 0), 0);
const num = (v, d = 0) => (v == null ? '' : (+v).toFixed(d));
function fmtDate(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000), t = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toDateString() === new Date().toDateString() ? t : `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${t}`;
}
function fmtDur(s) { if (s == null) return ''; s = Math.round(s); return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`; }
function targetRgb(text) {
  const t = String(text ?? '').trim().toLowerCase();
  if (!t) return null;
  if (/^#[0-9a-f]{6}$/.test(t)) return rgbOfHex(t);
  return TARGETS[t] ?? null;
}
function toast(text, kind = '') {
  const el = $('#toast'); el.textContent = text; el.className = 'toast show ' + kind;
  clearTimeout(toast.t); toast.t = setTimeout(() => (el.className = 'toast'), kind === 'bad' ? 7000 : 3500);
}

async function api(path, opts = {}) {
  const r = await fetch(withToken(path), { ...opts, headers: { 'content-type': 'application/json', ...(opts.headers || {}) },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `${r.status} ${r.statusText}`);
  return j;
}
const post = (path, body = {}) => api(path, { method: 'POST', body });

// ---------- twin ----------
const twin = new Twin($('#twin'));

// ---------- devices / top bar ----------
const PILLS = ['cnc', 'pipette', 'hub', 'probe', 'stand', 'microscope', 'so_a', 'so_b', 'phone', 'llm', 'd1'];
const PILL_LABEL = { llm: 'agent LLM', d1: 'database', so_a: 'arm A', so_b: 'arm B' };
function pillClass(status) {
  if (status === 'ok' || status === 'live') return 'ok';
  if (status === 'mock' || status === 'fallback') return 'mock';
  if (!status || status === 'offline') return 'off';
  return 'error';
}
function renderPills() {
  const byId = Object.fromEntries(S.devices.map((d) => [d.id, d]));
  $('#pills').innerHTML = PILLS.map((id) => {
    let cls, tip;
    if (id === 'phone') { cls = S.phones ? 'ok' : 'off'; tip = `${S.phones} phone(s) connected`; }
    else if (S.services[id]) { const v = S.services[id]; cls = pillClass(v.ok ? v.mode : v.mode === 'fallback' ? 'fallback' : 'error'); tip = v.detail; }
    else { const d = byId[id]; cls = pillClass(d?.status); tip = d ? `${d.kind}: ${d.status}` : 'not connected'; }
    return `<span class="pill ${cls}" title="${esc(tip)}"><i></i>${esc(PILL_LABEL[id] || id)}</span>`;
  }).join('');
}
function ingestDevices(m) {
  S.devices = m.devices ?? S.devices; S.services = m.services ?? S.services; S.phones = m.phones ?? S.phones;
  renderPills();
  if (S.live) twin.applyDevices(Object.fromEntries(S.devices.map((d) => [d.id, d.state ?? {}])));
}
async function pollState() {
  try {
    const st = await api('/api/state'); ingestDevices(st);
    if (st.has_frame && !$('#camThumb').src) setThumb((await api('/api/camera_frame')).jpeg);
  } catch { /* server down; the ws reconnect shows it */ }
}
function setThumb(jpeg) { if (jpeg) $('#camThumb').src = 'data:image/jpeg;base64,' + jpeg; }
$('#camThumb').onclick = () => $('#camThumb').classList.toggle('big');
$('#abortBtn').onclick = () => abort();
async function abort() {
  try { await post('/api/abort'); toast('Abort sent: every motor stopped.', 'bad'); }
  catch { wsSend({ t: 'abort' }); toast('Abort sent over the socket.', 'bad'); }
}

// ---------- experiments: data ----------
function upsert(e) {
  const known = S.exps.has(e.id);
  S.exps.set(e.id, e);
  // a new experiment from the agent or a script while you watch: show it
  if (!known && booted && e.source !== 'operator' && Date.now() / 1000 - e.created < 60) {
    toast(`New experiment: ${e.title}`);
    select(e.id, false);
    return;
  }
  if (e.status === 'running' && !S.live) goLive();
  if (S.sel === e.id) refreshPage(); else renderList();
}
async function loadExperiments() {
  try { for (const e of (await api('/api/experiments')).reverse()) S.exps.set(e.id, e); } catch (err) { toast('Could not load experiments: ' + err.message, 'bad'); }
  if (!S.sel) {
    const list = sorted();
    S.sel = (list.find((e) => e.status === 'running' || e.status === 'awaiting_approval') ?? list[0])?.id ?? null;
    if (S.sel) loadNotes();
  }
  renderList(); renderPage();
}
const sorted = () => [...S.exps.values()].sort((a, b) => b.created - a.created);
const current = () => (S.sel === 'new' ? null : S.exps.get(S.sel) ?? null);
const statusOf = (e) => (e ? e.status : 'new');
const isCustom = (e) => e && !e.samples.length; // written as raw steps (Python / agent), no sample table to edit

function select(id, toPage = true) {
  S.sel = id; S.selWell = null; S.saveError = null; S.notes = [];
  if (toPage) showTab('exp');
  renderList(); renderPage();
  if (id !== 'new') loadNotes();
  if (S.drafts.has(id)) schedulePreview();
}
async function loadNotes() {
  const id = S.sel; if (!id || id === 'new') return;
  try { const n = await api('/api/notes?experiment=' + encodeURIComponent(id)); if (S.sel === id) { S.notes = n; renderSection('notes'); } } catch {}
}

// The draft of the selected experiment: made on the first edit, dropped on save.
function draft() {
  const key = S.sel;
  if (!S.drafts.has(key)) {
    const e = current();
    S.drafts.set(key, e
      ? { title: e.title, target: e.target?.name ?? '', notes: e.notes, samples: e.samples.map((s) => ({ ...s })) }
      : { title: 'Untitled', target: '', notes: '', samples: [] });
  }
  return S.drafts.get(key);
}
const dirty = () => S.drafts.has(S.sel);

/** What the page shows: one row per beaker, with the prediction from the draft's dry run when there is a draft. */
function viewOf(e) {
  const st = statusOf(e), d = S.drafts.get(S.sel), editable = EDITABLE.has(st) && !isCustom(e);
  const byWell = new Map((e?.beakers ?? []).map((b) => [b.well, b]));
  if (!editable) return { st, editable, rows: e.beakers.map((b, i) => ({ ...b, i })), check: e.check, target: e.target?.rgb ?? null, byWell };
  const samples = d ? d.samples : e.samples;
  const pv = d && S.preview?.key === S.sel ? S.preview : null;
  const target = d ? targetRgb(d.target) ?? (d.target === e?.target?.name ? e.target.rgb : null) : e?.target?.rgb ?? null;
  const rows = samples.map((s, i) => {
    const well = (pv ? pv.placed[i] : s.well) || '';
    const p = pv ? pv.wells?.[well] : byWell.get(well) && { rgb: byWell.get(well).predicted_rgb, vol: byWell.get(well).vol_ul };
    return { ...s, i, well, predicted_rgb: p?.rgb ?? null, vol_ul: p?.vol ?? null, predicted_to_target: dist(p?.rgb, target) };
  });
  return { st, editable, rows, samples, check: pv ? pv.check : d ? null : e?.check, pending: d && (!pv || pv.stale), previewError: pv?.error, target, byWell };
}

// ---------- dry-run preview while editing ----------
let previewSeq = 0, previewTimer = null;
function cleanSamples(samples) {
  const idx = [], out = [];
  samples.forEach((s, i) => {
    if (!total(s)) return; // an empty row is just an empty line in the table
    const o = {};
    if (s.well) o.well = String(s.well).trim().toUpperCase();
    if (s.label) o.label = s.label;
    for (const k of STOCKS) if (+s[k] > 0) o[k] = +s[k];
    idx.push(i); out.push(o);
  });
  return { idx, out };
}
function schedulePreview() {
  if (S.preview?.key === S.sel) S.preview.stale = true;
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 300);
}
async function runPreview() {
  const key = S.sel, d = S.drafts.get(key); if (!d) return;
  const seq = ++previewSeq, { idx, out } = cleanSamples(d.samples);
  let pv;
  if (!out.length) pv = { error: 'Add a sample: µL of water, red, yellow and/or blue for one beaker.', placed: [], wells: {} };
  else {
    try {
      const r = await post('/api/dry_run', { samples: out });
      const placed = []; idx.forEach((i, j) => (placed[i] = r.samples[j].well));
      pv = { check: { ok: r.ok, errors: r.errors, warnings: r.warnings, duration_s: r.duration_s, tips_used: r.tips_used }, wells: r.wells, placed };
    } catch (err) { pv = { error: err.message, placed: d.samples.map((s) => s.well), wells: {} }; }
  }
  if (seq !== previewSeq || S.sel !== key) return;
  S.preview = { key, ...pv };
  refreshPage();
}

// ---------- experiments: actions ----------
async function save() {
  const key = S.sel, e = current(), d = S.drafts.get(key);
  if (!d) return e;
  const body = { title: d.title };
  if (!e) body.notes = d.notes; // a saved experiment's notes are saved on their own (any status)
  body.target = !d.target.trim() ? null : d.target === e?.target?.name ? e.target : d.target.trim();
  if (!isCustom(e)) body.samples = cleanSamples(d.samples).out;
  try {
    const saved = e ? await api('/api/experiments/' + e.id, { method: 'PATCH', body }) : await post('/api/experiments', body);
    S.drafts.delete(key); S.preview = null; S.saveError = null;
    S.exps.set(saved.id, saved);
    if (key === 'new') { S.sel = saved.id; loadNotes(); }
    renderList(); renderPage();
    toast(saved.status === 'ready' ? 'Saved. The dry run passed.' : 'Saved as a draft: the dry run found problems.', saved.status === 'ready' ? 'ok' : 'warn');
    return saved;
  } catch (err) {
    S.saveError = err.message; renderSection('check'); toast('Not saved: ' + err.message, 'bad');
    return null;
  }
}
function discard() { S.drafts.delete(S.sel); S.preview = null; S.saveError = null; if (S.sel === 'new') S.sel = sorted()[0]?.id ?? null; renderList(); renderPage(); }
async function act(fn, okMsg) {
  try { const r = await fn(); if (r?.id && r.status) upsert(r); if (okMsg) toast(okMsg, 'ok'); return r; }
  catch (err) { toast(err.message, 'bad'); return null; }
}
async function run() {
  let e = current();
  if (dirty()) e = await save();
  if (!e) return;
  if (e.status !== 'ready') return toast('Fix the dry-run problems first.', 'warn');
  await act(() => post(`/api/experiments/${e.id}/run`), 'Waiting for your approval.');
}
const approve = (e) => act(() => post(`/api/experiments/${e.id}/approve`), 'Approved: the robot is starting.');
const repeat = async (e) => { const r = await act(() => post(`/api/experiments/${e.id}/repeat`)); if (r) { select(r.id); toast('Copied into a new experiment. Edit or run it.', 'ok'); } };
async function measure(e) {
  toast('Reading every beaker from the latest photo…');
  try {
    const r = await post(`/api/experiments/${e.id}/measure`);
    loadNotes();
    toast(r.ok ? `Measured ${r.readings.length} beakers (${r.method}).` : `Could not measure: ${r.skipped ?? r.error}`, r.ok ? 'ok' : 'warn');
  } catch (err) { toast(err.message, 'bad'); }
}
async function recordResult(e, well) {
  const key = `${e.id}:${well}`, r = S.results.get(key); if (!r) return;
  try {
    const out = await post(`/api/experiments/${e.id}/results`, { well, rgb: r.rgb, rating: r.rating, note: r.note || null });
    S.results.delete(key);
    renderSection('table'); loadNotes(); // the results note is not pushed over the socket
    const m = out.model;
    toast(`Recorded ${well}.` + (m?.accepted ? ` Colour model improved to v${m.v} (error ${m.error_before} → ${m.error_after}).` : ''), 'ok');
  } catch (err) { toast(err.message, 'bad'); }
}
async function saveNotes(e, text) {
  if (!e) { draft().notes = text; return; }
  if (text === e.notes) return;
  try { S.exps.set(e.id, await api('/api/experiments/' + e.id, { method: 'PATCH', body: { notes: text } })); toast('Notes saved.', 'ok'); }
  catch (err) { toast(err.message, 'bad'); }
}
async function replay(e) {
  try {
    const { frames } = await api(`/api/experiments/${e.id}/replay`);
    if (!frames.length) return toast('Nothing to replay.', 'warn');
    setFrames(frames, e.title);
  } catch (err) { toast(err.message, 'bad'); }
}

// ---------- left: the list ----------
$('#newBtn').onclick = () => { if (!S.drafts.has('new')) S.drafts.set('new', { title: 'Untitled', target: '', notes: '', samples: [{}, {}, {}] }); select('new'); };
$('#allCsv').href = withToken('/api/results.csv');
function renderList() {
  const rows = sorted().map((e) => item(e.id, e.title, e.status, e.beakers.length, fmtDate(e.created), S.drafts.has(e.id)));
  if (S.drafts.has('new')) rows.unshift(item('new', S.drafts.get('new').title || 'Untitled', 'new', cleanSamples(S.drafts.get('new').samples).out.length, '', true));
  $('#expList').innerHTML = rows.join('') || '<div class="empty">No experiments yet.</div>';
  for (const el of $$('.exp-row', $('#expList'))) el.onclick = () => select(el.dataset.id);
}
function item(id, title, st, n, date, unsaved) {
  return `<div class="exp-row ${id === S.sel ? 'sel' : ''}" data-id="${esc(id)}">
    <div class="t">${esc(title)}</div>
    <div class="meta"><span class="st st-${st}">${esc(STATUS[st][0])}</span><span>${n} beaker${n === 1 ? '' : 's'}</span><span>${esc(date)}</span>${unsaved ? '<span class="unsaved">unsaved</span>' : ''}</div></div>`;
}

// ---------- centre: the experiment page ----------
const SECTIONS = ['head', 'actions', 'check', 'plate', 'table', 'notes', 'steps'];
let pageFor = undefined;
function renderPage() {
  const page = $('#page');
  if (S.sel == null) {
    pageFor = null;
    page.innerHTML = `<div class="intro"><h1>No experiment selected</h1>
      <p>An experiment is one plate: you say how many µL of water, red, yellow and blue go into each beaker. LabRat dry-runs it on
      the digital twin and predicts every colour. You approve, the robot fills the plate, the phone photographs it, and each
      beaker gets a measured colour next to its prediction.</p>
      <p><button class="btn accent" id="introNew">+ New experiment</button> or ask the agent, e.g. <i>"find the most orange mix of red and yellow"</i>.</p></div>`;
    $('#introNew').onclick = () => $('#newBtn').click();
    return;
  }
  if (pageFor !== S.sel) {
    pageFor = S.sel;
    page.innerHTML = SECTIONS.map((s) => `<section class="sec sec-${s}" data-sec="${s}"></section>`).join('');
    page.scrollTop = 0;
  }
  for (const s of SECTIONS) renderSection(s);
}
/** Re-render what changed without stealing focus from an input the user is typing in. */
function refreshPage() {
  for (const s of SECTIONS) {
    const el = $(`[data-sec="${s}"]`);
    const typing = document.activeElement?.matches('input:not([type=color]), textarea, select');
    if (typing && el?.contains(document.activeElement)) { if (s === 'table') patchTable(); continue; }
    renderSection(s);
  }
  renderList();
}
function renderSection(name) {
  const el = $(`#page [data-sec="${name}"]`); if (!el) return;
  const e = current(); if (S.sel !== 'new' && !e) return;
  const v = viewOf(e);
  ({ head: secHead, actions: secActions, check: secCheck, plate: secPlate, table: secTable, notes: secNotes, steps: secSteps })[name](el, e, v);
}

function secHead(el, e, v) {
  const d = S.drafts.get(S.sel), title = d ? d.title : e.title, tgt = d ? d.target : e?.target?.name ?? '';
  const tRgb = v.target;
  el.innerHTML = `
    <div class="head-row">
      ${v.editable ? `<input id="titleIn" class="title-in" value="${esc(title)}" placeholder="Title" aria-label="Title">` : `<h1 class="title">${esc(title)}</h1>`}
      <span class="st big st-${v.st}">${esc(STATUS[v.st][0])}</span>
    </div>
    <p class="lede">${esc(STATUS[v.st][1])}${v.st === 'failed' && e.error ? ` <b class="bad">${esc(e.error)}</b>` : ''}</p>
    <div class="meta-row">
      <label class="target">Target colour
        ${v.editable ? `<input id="targetIn" list="targetNames" value="${esc(tgt)}" placeholder="none (e.g. orange or #ff8800)">
          <input id="targetPick" type="color" value="${hex(tRgb) ?? '#ff8c00'}" title="Pick a target colour">
          <datalist id="targetNames">${Object.keys(TARGETS).map((n) => `<option value="${n}">`).join('')}</datalist>`
          : `<b>${esc(tgt || 'none')}</b>`}
        ${tRgb ? `<span class="sw" style="background:${hex(tRgb)}"></span>` : ''}
      </label>
      ${e ? `<span class="mono dim">${esc(e.id)} · ${esc(e.source)} · created ${esc(fmtDate(e.created))}${e.started ? ` · started ${esc(fmtDate(e.started))}` : ''}${e.finished ? ` · took ${fmtDur(e.finished - e.started)}` : ''}</span>` : ''}
    </div>`;
  const t = $('#titleIn', el); if (t) t.oninput = () => { draft().title = t.value; markDirty(false); };
  const ti = $('#targetIn', el); if (ti) ti.oninput = () => { draft().target = ti.value; markDirty(false); refreshPage(); };
  const tp = $('#targetPick', el); if (tp) tp.oninput = () => {
    draft().target = ti.value = tp.value; markDirty(false); refreshPage();
    const sw = $('.target .sw', el); if (sw) sw.style.background = tp.value; else tp.insertAdjacentHTML('afterend', `<span class="sw" style="background:${tp.value}"></span>`);
  };
}
function markDirty(preview = true) {
  if (preview) { schedulePreview(); renderSection('check'); }
  renderSection('actions'); renderList();
}

function secActions(el, e, v) {
  const n = e?.steps.length ?? 0, b = [];
  if (v.editable || (v.st === 'draft' || v.st === 'ready')) {
    b.push(`<button class="btn ${dirty() ? 'accent' : ''}" data-a="save" ${dirty() ? '' : 'disabled'} title="Save and dry-run (Ctrl+S)">${S.sel === 'new' ? 'Save' : dirty() ? 'Save changes' : 'Saved'}</button>`);
    if (dirty()) b.push(`<button class="btn ghost" data-a="discard">Discard changes</button>`);
    const canRun = dirty() ? !v.pending && v.check?.ok && !v.previewError : v.st === 'ready';
    b.push(`<button class="btn ${canRun && !dirty() ? 'accent' : ''}" data-a="run" ${canRun ? '' : 'disabled'} title="${canRun ? 'Ask to run on the robot (you approve next)' : 'The dry run must pass first'}">Run on robot ▸</button>`);
  }
  if (v.st === 'awaiting_approval') {
    b.push(`<button class="btn accent big" data-a="approve">Approve and start</button><button class="btn ghost" data-a="abort">Cancel</button>`);
  }
  let progress = '';
  if (v.st === 'running') {
    const done = e.log.length, cur = e.steps[e.step], el_s = Date.now() / 1000 - e.started;
    progress = `<div class="progress"><div class="bar"><i style="width:${(100 * done / Math.max(1, n)).toFixed(1)}%"></i></div>
      <div class="mono">step ${Math.min(n, (e.step ?? 0) + 1)} of ${n}: ${esc(cur ? stepText(cur) : '')} · ${fmtDur(el_s)} of about ${fmtDur(e.check.duration_s)}</div>
      <div class="logtail mono">${e.log.slice(-4).map((l) => `<div>${l.t.toFixed(1).padStart(6)} s  ${esc(l.msg)}</div>`).join('')}</div></div>`;
    b.push(`<button class="btn danger" data-a="abort">Abort run</button>`);
  }
  if (FINISHED.has(v.st)) {
    b.push(`<button class="btn" data-a="measure" title="Read every beaker from the newest phone photo">Measure from photo</button>`,
      `<button class="btn" data-a="repeat" title="A new experiment with the same samples">Repeat</button>`,
      `<a class="btn" href="${withToken(`/api/experiments/${e.id}.csv`)}" download="${esc(e.id)}.csv">Download CSV</a>`);
  }
  if (e && !(dirty())) b.push(`<button class="btn ghost" data-a="replay" title="Play the dry run on the 3D twin">Replay on twin</button>`);
  el.innerHTML = `<div class="actions">${b.join('')}</div>${progress}`;
  const fns = { save, discard, run, approve: () => approve(e), abort, repeat: () => repeat(e), measure: () => measure(e), replay: () => replay(e) };
  for (const x of $$('[data-a]', el)) x.onclick = () => fns[x.dataset.a]();
}

function secCheck(el, e, v) {
  if (!['new', 'draft', 'ready', 'awaiting_approval'].includes(v.st)) { el.innerHTML = ''; el.hidden = true; return; }
  el.hidden = false;
  const c = v.check, err = S.saveError || v.previewError;
  const used = Object.fromEntries(STOCKS.map((k) => [k, (v.rows ?? []).reduce((a, r) => a + (+r[k] || 0), 0)]));
  const stockLine = `<div class="stocks mono">${STOCKS.map((k) => `<span class="${used[k] > STOCK_UL ? 'bad' : ''}"><i class="dot dot-${k}"></i>${k} ${Math.round(used[k])} / ${STOCK_UL} µL</span>`).join('')}</div>`;
  let body;
  const short = STOCKS.filter((k) => used[k] > STOCK_UL).map((k) => `Not enough ${k}: the samples need ${Math.round(used[k])} µL and the ${k} stock beaker holds ${STOCK_UL} µL.`);
  if (err) body = `<div class="verdict bad">✗ ${esc(err)}</div>`;
  else if (!c) body = `<div class="verdict dim">Checking…</div>`;
  else if (c.ok) body = `<div class="verdict ok">✓ Dry run passed${v.pending ? ' (checking changes…)' : ''}</div>`;
  else {
    const errs = c.errors.map((x) => errWell(e, x));
    body = `<div class="verdict bad">✗ The dry run found ${errs.length} problem${errs.length === 1 ? '' : 's'}${v.pending ? ' (checking changes…)' : ''}</div>
      ${short.map((x) => `<div class="bad">${esc(x)}</div>`).join('')}
      <ul class="errs">${errs.slice(0, 4).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
      ${errs.length > 4 ? `<details><summary>${errs.length - 4} more</summary><ul class="errs">${errs.slice(4).map((x) => `<li>${esc(x)}</li>`).join('')}</ul></details>` : ''}`;
  }
  const facts = c && !err ? `<div class="facts">about <b>${fmtDur(c.duration_s)}</b> on the robot · <b>${c.tips_used.length}</b> tips (${esc(c.tips_used.join(' '))})${c.model_v != null ? ` · colours predicted by model v${c.model_v}` : ''}</div>` : '';
  const warns = c?.warnings?.length && !err ? `<ul class="warns">${c.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : '';
  el.innerHTML = `<h2>Dry run</h2>${body}${facts}${warns}${stockLine}`;
}
/** "step 47: …" -> "B1: step 47: …" using the saved steps (only valid while there are no unsaved edits). */
function errWell(e, msg) {
  const m = /^step (\d+):/.exec(msg); if (!m || !e || dirty()) return msg;
  const i = +m[1], s = e.steps[i]; if (!s) return msg;
  const w = s.dst ?? s.well ?? (s.op === 'aspirate' ? e.steps[i + 1]?.dst : null);
  return WELLS.includes(w) ? `${msg} (filling ${w})` : msg;
}

function secPlate(el, e, v) {
  const rows = v.rows, byWell = new Map(rows.filter((r) => r.well).map((r) => [r.well, r]));
  const t = v.target;
  const closest = (k) => t ? rows.filter((r) => r[k] != null).sort((a, b) => a[k] - b[k])[0]?.well : null;
  const bestM = v.editable ? null : closest('measured_to_target');
  const bestP = v.editable ? closest('predicted_to_target') : e.closest_predicted;
  const cells = [];
  cells.push('<span></span>' + Array.from({ length: COLS }, (_, c) => `<span class="lab">${c + 1}</span>`).join(''));
  for (const r of ROWS) {
    cells.push(`<span class="lab">${r}</span>`);
    for (let c = 1; c <= COLS; c++) {
      const w = r + c, b = byWell.get(w), p = hex(b?.predicted_rgb), m = hex(b?.measured_rgb);
      const bg = p && m ? `linear-gradient(90deg, ${p} 50%, ${m} 50%)` : m || p || '';
      const cls = ['beaker', b ? 'full' : 'empty', w === S.selWell ? 'sel' : '', w === bestM ? 'best' : '', w === bestP ? 'bestp' : ''].join(' ');
      const tip = b ? `${w}${b.label ? ' · ' + b.label : ''}\n${STOCKS.filter((k) => +b[k]).map((k) => `${k} ${b[k]} µL`).join(' + ')}${p ? `\npredicted ${p}` : ''}${m ? `\nmeasured ${m}` : ''}`
        : v.editable ? `${w}: click to add a sample here` : w;
      cells.push(`<button class="${cls}" data-well="${w}" title="${esc(tip)}" style="${bg ? `background:${bg}` : ''}"></button>`);
    }
  }
  const anyM = rows.some((r) => r.measured_rgb);
  el.innerHTML = `<h2>Plate</h2>
    <div class="plate-wrap"><div class="plate">${cells.join('')}</div>
      <div class="legend-col">
        ${t ? `<div class="lg"><span class="sw" style="background:${hex(t)}"></span>target</div>` : ''}
        <div class="lg"><span class="beaker mini full" style="background:linear-gradient(90deg,#c9a227 50%,#e2c66a 50%)"></span>${anyM ? 'left half predicted, right half measured' : 'predicted colour'}</div>
        ${bestP ? `<div class="lg"><span class="beaker mini bestp"></span>closest to target (predicted): <b>${bestP}</b></div>` : ''}
        ${bestM ? `<div class="lg"><span class="beaker mini best"></span>closest to target (measured): <b>${bestM}</b></div>` : ''}
        <div class="lg dim">column 8 holds the stocks (water, red, yellow, blue)</div>
      </div></div>`;
  for (const btn of $$('.beaker[data-well]', el)) btn.onclick = () => clickWell(btn.dataset.well, v);
}
function clickWell(w, v) {
  const row = v.rows.find((r) => r.well === w);
  if (row) { selectWell(w); return; }
  if (!v.editable) return;
  const d = draft(); d.samples.push({ well: w }); S.selWell = w;
  markDirty(); renderSection('table'); renderSection('plate');
  $(`#page tr[data-i="${d.samples.length - 1}"] input[data-k="red"]`)?.focus();
}
function selectWell(w) {
  S.selWell = S.selWell === w ? null : w;
  for (const x of $$('#page [data-well]')) x.classList.toggle('sel', x.dataset.well === S.selWell);
  $(`#page tr[data-well="${S.selWell}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function secTable(el, e, v) {
  const tRgb = v.target, fin = FINISHED.has(v.st);
  const head = `<tr><th>Well</th><th>Label</th>${STOCKS.map((k) => `<th class="n"><i class="dot dot-${k}"></i>${k}</th>`).join('')}<th class="n">total µL</th>
    <th>predicted</th>${v.editable ? '' : '<th>measured</th><th class="n" title="distance between predicted and measured RGB">ΔRGB</th>'}
    ${tRgb ? `<th class="n" title="RGB distance to the target colour">Δ target</th>` : ''}${fin ? '<th>rating</th><th>note</th><th></th>' : ''}${v.editable ? '<th></th>' : ''}</tr>`;
  const body = v.rows.map((r) => v.editable ? editRow(r, tRgb) : resultRow(e, r, tRgb, fin)).join('');
  const custom = isCustom(e) ? '<p class="dim">This experiment was written as robot steps, so there is no sample table to edit.</p>' : '';
  el.innerHTML = `<h2>Samples <span class="h2-note">µL of each stock per beaker</span></h2>${custom}
    <div class="tablewrap"><table class="samples">${head}${body}</table></div>
    ${v.editable ? `<div class="actions"><button class="btn sm" data-t="add">+ Add sample</button>
      <span class="dim">Leave Well empty to fill A1, B1 … F1, A2 … in order. Click an empty beaker on the plate to add one there.</span></div>` : ''}`;
  if (v.editable) bindEditTable(el); else bindResultTable(el, e);
}
function editRow(r, tRgb) {
  const s = draftOrSaved()[r.i] ?? {};
  const p = hex(r.predicted_rgb);
  return `<tr data-i="${r.i}" data-well="${esc(r.well)}" class="${r.well === S.selWell ? 'sel' : ''}">
    <td><input class="well" data-k="well" value="${esc(s.well ?? '')}" placeholder="${esc(r.well || 'auto')}" maxlength="3"></td>
    <td><input class="label" data-k="label" value="${esc(s.label ?? '')}" placeholder="—"></td>
    ${STOCKS.map((k) => `<td class="n"><input class="ul" type="number" min="0" step="10" data-k="${k}" value="${s[k] ? s[k] : ''}" placeholder="0"></td>`).join('')}
    <td class="n mono tot ${total(s) > BEAKER_UL ? 'bad' : ''}">${total(s) || ''}</td>
    <td class="pcell">${p ? `<span class="sw" style="background:${p}" title="${p}"></span>` : ''}</td>
    ${tRgb ? `<td class="n mono dt">${num(r.predicted_to_target, 0)}</td>` : ''}
    <td class="rowbtns"><button class="btn ghost xs" data-t="dup" title="Duplicate this row (a replicate)">copy</button><button class="btn ghost xs" data-t="del" title="Delete">×</button></td></tr>`;
}
const draftOrSaved = () => (S.drafts.get(S.sel) ?? current()).samples;
function bindEditTable(el) {
  el.oninput = (ev) => {
    const inp = ev.target, tr = inp.closest('tr[data-i]'); if (!tr || !inp.dataset.k) return;
    const s = draft().samples[+tr.dataset.i], k = inp.dataset.k;
    if (STOCKS.includes(k)) { if (inp.value === '' || +inp.value === 0) delete s[k]; else s[k] = +inp.value; }
    else if (inp.value.trim()) s[k] = k === 'well' ? inp.value.trim().toUpperCase() : inp.value; else delete s[k];
    const tot = $('.tot', tr); tot.textContent = total(s) || ''; tot.classList.toggle('bad', total(s) > BEAKER_UL);
    markDirty();
  };
  el.onclick = (ev) => {
    const b = ev.target.closest('[data-t]'); if (!b) return;
    const tr = b.closest('tr[data-i]'), d = draft();
    if (b.dataset.t === 'add') d.samples.push({});
    if (b.dataset.t === 'dup') { const s = { ...d.samples[+tr.dataset.i] }; delete s.well; d.samples.splice(+tr.dataset.i + 1, 0, s); }
    if (b.dataset.t === 'del') d.samples.splice(+tr.dataset.i, 1);
    markDirty(); renderSection('table'); renderSection('plate');
    if (b.dataset.t === 'add') $$('#page tr[data-i] input[data-k="red"]').at(-1)?.focus();
  };
  el.onfocusin = (ev) => { const tr = ev.target.closest('tr[data-well]'); if (tr?.dataset.well && tr.dataset.well !== S.selWell) selectWell(tr.dataset.well); };
}
/** While the user is typing in the table: update predictions in place instead of rebuilding the rows. */
function patchTable() {
  const v = viewOf(current());
  for (const r of v.rows) {
    const tr = $(`#page tr[data-i="${r.i}"]`); if (!tr) continue;
    tr.dataset.well = r.well;
    const w = $('input.well', tr); if (w) w.placeholder = r.well || 'auto';
    const p = hex(r.predicted_rgb); $('.pcell', tr).innerHTML = p ? `<span class="sw" style="background:${p}" title="${p}"></span>` : '';
    const dt = $('.dt', tr); if (dt) dt.textContent = num(r.predicted_to_target, 0);
  }
}
function resultRow(e, r, tRgb, fin) {
  const ed = S.results.get(`${e.id}:${r.well}`), p = hex(r.predicted_rgb), m = hex(ed?.rgb ?? r.measured_rgb);
  const rating = ed ? ed.rating : r.rating, note = ed ? ed.note : r.note;
  const meas = fin ? `<label class="mpick" title="Pick the colour you see">${m ? `<span class="sw" style="background:${m}"></span>` : '<span class="sw none">?</span>'}
      <input type="color" data-r="rgb" value="${m ?? p ?? '#888888'}"></label>${r.source && !ed ? `<span class="src">${esc(r.source)}</span>` : ''}`
    : (m ? `<span class="sw" style="background:${m}"></span>` : '');
  const toT = r.measured_to_target ?? null;
  return `<tr data-well="${esc(r.well)}" class="${r.well === S.selWell ? 'sel' : ''} ${ed ? 'edited' : ''}">
    <td class="mono">${esc(r.well)}</td><td>${esc(r.label ?? '')}</td>
    ${STOCKS.map((k) => `<td class="n mono">${r[k] || ''}</td>`).join('')}<td class="n mono">${total(r)}</td>
    <td>${p ? `<span class="sw" style="background:${p}" title="${p}"></span>` : ''}</td>
    <td class="mcell">${meas}</td><td class="n mono">${num(r.error, 0)}</td>
    ${tRgb ? `<td class="n mono">${toT != null ? `<b>${num(toT, 0)}</b>` : ''}<span class="dim" title="predicted"> ${num(r.predicted_to_target, 0)}</span></td>` : ''}
    ${fin ? `<td><select data-r="rating"><option value="">—</option>${[1, 2, 3, 4, 5].map((n) => `<option ${rating == n ? 'selected' : ''}>${n}</option>`).join('')}</select></td>
      <td><input class="note" data-r="note" value="${esc(note ?? '')}" placeholder="—"></td>
      <td><button class="btn xs ${ed ? 'accent' : 'ghost'}" data-r="save" ${ed ? '' : 'disabled'} title="Record this reading">Record</button></td>` : ''}</tr>`;
}
function bindResultTable(el, e) {
  const edit = (tr) => {
    const w = tr.dataset.well, key = `${e.id}:${w}`, b = e.beakers.find((x) => x.well === w);
    if (!S.results.has(key)) S.results.set(key, { rgb: b.measured_rgb, rating: b.rating, note: b.note });
    return S.results.get(key);
  };
  const changed = (tr) => { tr.classList.add('edited'); const s = $('[data-r="save"]', tr); s.disabled = false; s.classList.replace('ghost', 'accent'); };
  el.oninput = (ev) => {
    const inp = ev.target, tr = inp.closest('tr[data-well]'), k = inp.dataset.r; if (!tr || !k || k === 'save') return;
    const r = edit(tr);
    if (k === 'rgb') { r.rgb = rgbOfHex(inp.value); $('.mpick .sw', tr).outerHTML = `<span class="sw" style="background:${inp.value}"></span>`; }
    if (k === 'rating') r.rating = inp.value ? +inp.value : null;
    if (k === 'note') r.note = inp.value;
    changed(tr);
  };
  el.onclick = (ev) => {
    const b = ev.target.closest('[data-r="save"]'); if (b) return recordResult(e, b.closest('tr').dataset.well);
    const tr = ev.target.closest('tr[data-well]'); if (tr && !ev.target.closest('input,select,label')) selectWell(tr.dataset.well);
  };
}

function secNotes(el, e) {
  const text = e ? e.notes : draft().notes;
  el.innerHTML = `<h2>Notes</h2>
    <textarea id="notesIn" rows="3" placeholder="Hypothesis, setup, what you saw… (saved when you click away)">${esc(text)}</textarea>
    ${e ? `<div class="nb"><h3>Notebook entries for this experiment</h3>${S.notes.length ? S.notes.map(noteHtml).join('') : '<p class="dim">None yet. Results are written here automatically.</p>'}</div>` : ''}`;
  $('#notesIn', el).onchange = (ev) => saveNotes(current(), ev.target.value);
}
function noteHtml(n) {
  const ex = n.experiment_id && S.exps.get(n.experiment_id);
  return `<div class="note"><div class="nmeta mono">${esc(fmtDate(n.ts))} · ${esc(n.source)}${ex && n.experiment_id !== S.sel ? ` · <a class="link" data-goto="${esc(ex.id)}">${esc(ex.title)}</a>` : ''}</div><div>${esc(n.text)}</div></div>`;
}

function secSteps(el, e) {
  if (!e) { el.innerHTML = ''; return; }
  const wasOpen = $('details.steps', el)?.open, logOpen = $('details.log', el)?.open;
  const cur = e.status === 'running' ? e.step : -1;
  el.innerHTML = `<details class="steps" ${wasOpen ? 'open' : ''}><summary>Robot steps (${e.steps.length})</summary>
      <ol class="mono">${e.steps.map((s, i) => `<li class="${i === cur ? 'cur' : i < e.log.length ? 'done' : ''}">${esc(stepText(s))}</li>`).join('')}</ol></details>
    ${e.log.length ? `<details class="log" ${logOpen ? 'open' : ''}><summary>Run log (${e.log.length})</summary>
      <div class="mono">${e.log.map((l) => `<div>${l.t.toFixed(1).padStart(6)} s  ${esc(l.msg)}</div>`).join('')}</div></details>` : ''}`;
}
function stepText(s) {
  switch (s.op) {
    case 'pick_tip': return `pick up tip ${s.rack_well}`;
    case 'aspirate': return `draw ${s.ul} µL from ${s.src}`;
    case 'dispense': return `dispense ${s.ul ?? 'all'} µL into ${s.dst}`;
    case 'mix': return `mix ${s.well} (${s.n ?? 3}× ${s.ul ?? 50} µL)`;
    case 'eject_tip': return 'eject tip';
    case 'photo': return 'take a photo of the plate';
    case 'wait': return `wait ${s.s ?? 1} s`;
  }
  const { op, ...rest } = s; return `${op} ${JSON.stringify(rest)}`;
}

document.addEventListener('keydown', (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === 's' && dirty()) { ev.preventDefault(); document.activeElement?.blur?.(); save(); }
});

// ---------- tabs ----------
function showTab(name) {
  for (const x of $$('.tab')) x.classList.toggle('on', x.dataset.tab === name);
  for (const p of $$('.tabpane')) p.classList.toggle('hidden', p.dataset.pane !== name);
  if (name === 'notebook') renderNotebook();
  if (name === 'model') loadModel();
}
for (const t of $$('.tab')) t.addEventListener('click', () => showTab(t.dataset.tab));

// ---------- notebook ----------
let nbQuery = '';
async function renderNotebook() {
  const box = $('#notebook');
  if (!$('#nbForm', box)) {
    box.innerHTML = `<h2>Notebook</h2><p class="dim">Everything written down: your notes, result summaries after each run, and the agent's notes. The agent searches it before planning.</p>
      <form id="nbAdd" class="nb-add"><textarea id="nbText" rows="2" placeholder="Write a note…"></textarea><button class="btn accent">Add note</button></form>
      <form id="nbForm" class="nb-search"><input id="nbQ" placeholder="Search, e.g. orange, tip, B3"><button class="btn">Search</button></form>
      <div id="nbList"></div>`;
    $('#nbForm', box).onsubmit = (ev) => { ev.preventDefault(); nbQuery = $('#nbQ').value; renderNotebook(); };
    $('#nbAdd', box).onsubmit = async (ev) => {
      ev.preventDefault(); const text = $('#nbText').value.trim(); if (!text) return;
      try { await post('/api/notes', { text }); $('#nbText').value = ''; } catch (err) { toast(err.message, 'bad'); }
    };
  }
  try {
    const notes = await api(`/api/notes?k=200&q=${encodeURIComponent(nbQuery)}`);
    $('#nbList').innerHTML = notes.length ? notes.map(noteHtml).join('') : `<p class="empty">${nbQuery ? 'Nothing matches.' : 'No notes yet.'}</p>`;
    for (const a of $$('[data-goto]', $('#nbList'))) a.onclick = () => select(a.dataset.goto);
  } catch (err) { $('#nbList').innerHTML = `<p class="empty">${esc(err.message)}</p>`; }
}

// ---------- colour model ----------
async function loadModel() { try { S.model = await api('/api/model'); } catch (err) { toast(err.message, 'bad'); } renderModel(); }
function renderModel() {
  const box = $('#modelBox'), m = S.model; if (!m) { box.innerHTML = '<p class="empty">Loading…</p>'; return; }
  const vs = m.versions.filter((v) => v.error_before != null || v.error_after != null);
  const cur = m.versions.find((v) => v.v === m.current_v), nRes = m.versions.at(-1)?.n_results ?? 0;
  const p = m.params;
  box.innerHTML = `<h2>Colour model</h2>
    <p>LabRat predicts each beaker's colour from how strongly each dye absorbs red, green and blue light. After new readings it
      refits those numbers and keeps the new version only if it predicts <i>every</i> reading so far better.</p>
    <p>In use: <b class="mono">v${m.current_v}</b>${cur?.error_after != null ? `, mean error <b class="mono">${cur.error_after}</b> (RGB distance)` : ''} · ${nRes} measured beakers so far.</p>
    ${vs.length ? chart(vs) : '<p class="empty">No refits yet. Record measured colours after a run and the model refits itself.</p>'}
    <h3>Dye absorbance (in use)</h3>
    <table class="vtable"><tr><th>stock</th><th>R</th><th>G</th><th>B</th></tr>
      ${Object.entries(p.absorbance).map(([d, a]) => `<tr><td><i class="dot dot-${d}"></i>${esc(d)}</td>${a.map((x) => `<td>${(+x).toFixed(3)}</td>`).join('')}</tr>`).join('')}</table>
    <p class="dim mono">volume gain ${p.vol_gain}</p>
    <h3>Versions</h3>
    <table class="vtable"><tr><th>v</th><th>when</th><th>readings</th><th>error before → after</th><th></th><th>from</th></tr>
      ${m.versions.slice().reverse().map((v) => `<tr class="${v.accepted ? '' : 'dim'}"><td>v${v.v}</td><td>${esc(fmtDate(v.ts))}</td><td>${v.n_results ?? ''}</td>
        <td>${v.error_before ?? '—'} → ${v.error_after ?? '—'}</td><td>${v.v === m.current_v ? '<b>in use</b>' : v.accepted ? 'kept' : 'rejected'}</td>
        <td>${v.experiment_id ? `<a class="link" data-goto="${esc(v.experiment_id)}">${esc(S.exps.get(v.experiment_id)?.title ?? v.experiment_id)}</a>` : v.v === 0 ? 'factory' : ''}</td></tr>`).join('')}</table>`;
  for (const a of $$('[data-goto]', box)) a.onclick = () => select(a.dataset.goto);
}
function chart(vs) {
  const W = 520, H = 200, L = 36, R = 10, T = 12, B = 24;
  const ys = vs.flatMap((v) => [v.error_before, v.error_after]).filter((x) => x != null).map(Number);
  const ymax = Math.max(1e-9, ...ys) * 1.1;
  const X = (i) => L + (vs.length === 1 ? (W - L - R) / 2 : i * (W - L - R) / (vs.length - 1));
  const Y = (y) => T + (H - T - B) * (1 - y / ymax);
  const line = (k) => vs.map((v, i) => v[k] == null ? null : `${X(i)},${Y(+v[k])}`).filter(Boolean).join(' ');
  const grid = [0, .25, .5, .75, 1].map((f) => `<line x1="${L}" x2="${W - R}" y1="${Y(ymax * f)}" y2="${Y(ymax * f)}" style="stroke:var(--chart-grid)"/><text x="${L - 4}" y="${Y(ymax * f) + 3}" text-anchor="end">${(ymax * f).toPrecision(2)}</text>`).join('');
  const marks = vs.map((v, i) => `${v.error_before != null && v.error_after != null ? `<line x1="${X(i)}" x2="${X(i)}" y1="${Y(+v.error_before)}" y2="${Y(+v.error_after)}" style="stroke:var(--chart-gain)" stroke-dasharray="2 2"/>` : ''}
    ${v.error_before != null ? `<circle cx="${X(i)}" cy="${Y(+v.error_before)}" r="3.5" style="fill:var(--chart-before)"><title>v${v.v} before: ${v.error_before}</title></circle>` : ''}
    ${v.error_after != null ? `<circle cx="${X(i)}" cy="${Y(+v.error_after)}" r="3.5" style="${v.accepted ? 'fill:var(--chart-after)' : 'fill:var(--bg-2);stroke:var(--chart-after)'};stroke-width:1.2"><title>v${v.v} after: ${v.error_after}${v.accepted ? '' : ' (rejected)'}</title></circle>` : ''}
    <text x="${X(i)}" y="${H - 8}" text-anchor="middle">v${v.v}</text>`).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}
      <polyline points="${line('error_before')}" fill="none" style="stroke:var(--chart-before)" stroke-opacity=".55"/>
      <polyline points="${line('error_after')}" fill="none" style="stroke:var(--chart-after)" stroke-width="1.75"/>${marks}</svg>
    <div class="legend"><span><i style="background:var(--chart-before)"></i>error before refit</span><span><i style="background:var(--chart-after)"></i>after refit (hollow = rejected)</span></div>`;
}

// ---------- twin replay ----------
const scrub = $('#scrub');
function setFrames(frames, label) {
  S.frames = frames; S.fi = 0; S.live = false; S.lastShown = null; twin.resetWells();
  scrub.max = frames.length - 1; scrub.value = 0; showFrame(0);
  $('#twinMode').textContent = 'replay · ' + label; $('#twinMode').classList.add('busy');
  S.playing = true; $('#playBtn').textContent = '❚❚';
}
function showFrame(i) {
  const fr = S.frames; if (!fr?.length) return;
  i = Math.max(0, Math.min(fr.length - 1, i)); S.fi = i; scrub.value = i;
  // well colours are cumulative: replay every well update up to frame i
  const from = S.lastShown != null && i >= S.lastShown ? S.lastShown + 1 : 0;
  if (from === 0) twin.resetWells();
  for (let k = from; k <= i; k++) if (fr[k].wells) twin.setWells(fr[k].wells);
  S.lastShown = i;
  const f = fr[i]; twin.applyFrame({ ...f, wells: null });
  $('#scrubLabel').textContent = `${(+f.t || 0).toFixed(0)} s · ${i + 1}/${fr.length}`;
  $('#twinInfo').textContent = `${f.label ?? ''}\nx ${(+f.cnc.x).toFixed(1)}  y ${(+f.cnc.y).toFixed(1)}  z ${(+f.cnc.z).toFixed(1)}\ntip ${f.pipette.tip ?? '—'}  ${(+f.pipette.vol_ul).toFixed(0)} µL`;
}
function goLive() {
  S.live = true; S.playing = false; S.frames = null;
  $('#twinMode').textContent = 'live'; $('#twinMode').classList.remove('busy'); $('#playBtn').textContent = '▶'; $('#twinInfo').textContent = ''; $('#scrubLabel').textContent = '';
  scrub.max = 0; twin.resetWells(); pollState();
}
scrub.oninput = () => { if (!S.frames) return; S.playing = false; $('#playBtn').textContent = '▶'; showFrame(+scrub.value); };
$('#playBtn').onclick = () => { if (!S.frames?.length) return; if (S.fi >= S.frames.length - 1) showFrame(0); S.playing = !S.playing; $('#playBtn').textContent = S.playing ? '❚❚' : '▶'; };
$('#liveBtn').onclick = goLive;
let simClock = 0, lastNow = performance.now();
(function tick(now) {
  const dt = (now - lastNow) / 1000; lastNow = now;
  if (S.playing && S.frames?.length) {
    const f0 = S.frames[S.fi], f1 = S.frames[S.fi + 1];
    if (!f1) { S.playing = false; $('#playBtn').textContent = '▶'; }
    else { simClock += dt * +$('#speedSel').value; if (simClock >= Math.max(0.03, f1.t - f0.t)) { simClock = 0; showFrame(S.fi + 1); } }
  }
  requestAnimationFrame(tick);
})(lastNow);

// ---------- agent chat ----------
const chatLog = $('#chatLog');
function addMsg(role, text, meta = {}) {
  const atBottom = chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 60;
  const el = document.createElement('div');
  if (role === 'tool') {
    el.className = 'chip' + (meta.ok === false ? ' err' : '');
    el.innerHTML = `<b>⚙ ${esc(meta.tool ?? 'tool')}</b><span class="args">${esc(text)}</span>`;
    el.onclick = () => el.classList.toggle('open');
    setAgentStatus('working…', true);
  } else {
    el.className = 'msg ' + (role === 'user' || role === 'agent' ? role : 'system');
    el.textContent = text;
    if (role === 'agent' && !meta.interim) setAgentStatus('idle', false);
  }
  chatLog.appendChild(el);
  if (atBottom) chatLog.scrollTop = chatLog.scrollHeight;
}
function setAgentStatus(t, busy) { const s = $('#agentStatus'); s.textContent = t; s.classList.toggle('busy', !!busy); }
$('#chatForm').onsubmit = (ev) => {
  ev.preventDefault(); const text = $('#chatInput').value.trim(); if (!text) return;
  $('#chatInput').value = ''; addMsg('user', text); setAgentStatus('thinking…', true);
  if (!wsSend({ t: 'chat', text })) post('/api/chat', { text }).then((r) => addMsg('agent', r.reply)).catch((err) => addMsg('system', 'chat failed: ' + err.message));
};
$('#chatInput').addEventListener('keydown', (ev) => { if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); $('#chatForm').requestSubmit(); } });

// ---------- websocket ----------
let ws = null, wsBackoff = 500, booted = false;
function wsSend(o) { if (ws?.readyState === 1) { ws.send(JSON.stringify(o)); return true; } return false; }
function setConn(on, text) { const c = $('#conn'); c.textContent = text; c.classList.toggle('on', on); }
function connect() {
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/ui?token=${encodeURIComponent(TOKEN)}`);
  ws.onopen = () => { wsBackoff = 500; setConn(true, 'online'); pollState(); };
  ws.onclose = (ev) => {
    setConn(false, ev.code === 4401 ? 'bad token' : 'offline');
    setTimeout(connect, wsBackoff); wsBackoff = Math.min(8000, wsBackoff * 1.7);
  };
  ws.onmessage = (ev) => { let m; try { m = JSON.parse(ev.data); } catch { return; } try { handle(m); } catch (err) { console.error('ws', m.t, err); } };
}
function handle(m) {
  switch (m.t) {
    case 'devices': ingestDevices(m); break;
    case 'experiment': upsert(m.experiment); break;
    case 'chat':
      if (m.role === 'user' && (m.meta?.source === 'ui' || m.meta?.source === 'rest')) break; // our own message, already shown
      addMsg(m.role, m.text, m.meta ?? {}); break;
    case 'model':
      S.model = m; if (!$('[data-pane="model"]').classList.contains('hidden')) renderModel(); break;
    case 'note':
      if (m.note.experiment_id && m.note.experiment_id === S.sel) { S.notes.unshift(m.note); renderSection('notes'); }
      if (!$('[data-pane="notebook"]').classList.contains('hidden')) renderNotebook();
      break;
    case 'frame': setThumb(m.jpeg); break;
    case 'log': if (m.level === 'error' || m.level === 'warn') addMsg('system', `${m.src}: ${m.text}`); break;
  }
}

// ---------- boot ----------
addMsg('system', 'Ask in plain words, e.g. "find the most orange mix of red and yellow". The agent plans an experiment; you approve the run.');
renderPills();
connect();
await loadExperiments();
booted = true;
setInterval(pollState, 3000);
setInterval(() => { if (current()?.status === 'running') renderSection('actions'); }, 1000); // elapsed time
window.labrat = { S, twin, handle };
