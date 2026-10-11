// Camera -> plate calibration: click the centres of wells A1, A12, H12, H1 on the latest phone frame.
// Self-contained (adds its own button to the top bar + a modal). POSTs /api/camera_cal.
const qs = new URLSearchParams(location.search);
const TOKEN = qs.get('token') || (() => { try { return localStorage.getItem('labrat_token'); } catch { return null; } })() || 'labrat';
const WELLS = ['A1', 'A12', 'H12', 'H1'];

async function api(path, opts = {}) {
  const r = await fetch(path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN), {
    ...opts, headers: { 'content-type': 'application/json', authorization: 'Bearer ' + TOKEN, 'x-labrat-token': TOKEN },
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

const css = `
.cc-back{position:fixed;inset:0;background:rgba(26,24,20,.45);display:flex;align-items:center;justify-content:center;z-index:1000;padding:16px}
.cc-box{background:var(--bg-2,#fff);border:1px solid var(--border-1,#E4E0D4);box-shadow:var(--shadow-lg,0 18px 50px rgba(26,24,20,.18));border-radius:4px;max-width:min(920px,100%);max-height:100%;overflow:auto;padding:12px;color:var(--text-1,#1A1814);font:13px var(--font-sans,system-ui)}
.cc-head{display:flex;gap:8px;align-items:center;margin-bottom:8px}.cc-head b{flex:1}
.cc-stage{position:relative;display:inline-block;cursor:crosshair;max-width:100%}
.cc-stage img{display:block;max-width:100%;height:auto}
.cc-dot{position:absolute;width:14px;height:14px;margin:-7px 0 0 -7px;border:2px solid var(--accent-fill,#F5D76E);box-shadow:0 0 0 1px #1A1814,inset 0 0 0 1px #1A1814;border-radius:50%;pointer-events:none}
.cc-dot span{position:absolute;left:14px;top:-4px;font:11px var(--font-mono,monospace);color:#1A1814;background:var(--accent-fill,#F5D76E);padding:0 3px;border-radius:2px}
.cc-msg{margin-top:8px;color:var(--text-2,#5E594D);font-family:var(--font-mono,monospace)}`;
const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

function open() {
  const back = document.createElement('div'); back.className = 'cc-back';
  back.innerHTML = `<div class="cc-box"><div class="cc-head"><b>Camera calibration</b>
    <button class="btn ghost" data-a="reload">Reload frame</button><button class="btn ghost" data-a="undo">Undo</button>
    <button class="btn accent" data-a="save" disabled>Save</button><button class="btn ghost" data-a="close">Close</button></div>
    <div class="cc-stage"><img alt="latest phone frame"></div><div class="cc-msg"></div></div>`;
  document.body.appendChild(back);
  const img = back.querySelector('img'), stage = back.querySelector('.cc-stage'), msg = back.querySelector('.cc-msg');
  const save = back.querySelector('[data-a=save]');
  let pts = [];
  const prompt = () => {
    msg.textContent = pts.length < 4 ? `Click the centre of well ${WELLS[pts.length]} (${pts.length + 1}/4)` : 'All 4 set. Save to store the homography.';
    save.disabled = pts.length < 4;
  };
  const draw = () => {
    stage.querySelectorAll('.cc-dot').forEach((d) => d.remove());
    pts.forEach(([x, y], i) => {
      const d = document.createElement('div'); d.className = 'cc-dot';
      d.style.left = (100 * x / img.naturalWidth) + '%'; d.style.top = (100 * y / img.naturalHeight) + '%';
      d.innerHTML = `<span>${WELLS[i]}</span>`; stage.appendChild(d);
    });
    prompt();
  };
  const load = async () => {
    msg.textContent = 'loading frame…';
    try {
      const f = await api('/api/camera_frame');
      img.src = 'data:image/jpeg;base64,' + f.jpeg;
      await img.decode().catch(() => {});
      const cal = await api('/api/camera_cal').catch(() => null);
      if (cal && cal.corners && cal.image_w === img.naturalWidth) pts = cal.corners.map((p) => p.slice());
      draw();
    } catch (e) { msg.textContent = 'No phone frame yet: open /phone on the iPhone first. (' + e.message + ')'; }
  };
  stage.addEventListener('click', (ev) => {
    if (pts.length >= 4 || !img.naturalWidth) return;
    const r = img.getBoundingClientRect();
    pts.push([(ev.clientX - r.left) * img.naturalWidth / r.width, (ev.clientY - r.top) * img.naturalHeight / r.height]);
    draw();
  });
  back.addEventListener('click', async (ev) => {
    const a = ev.target.dataset && ev.target.dataset.a;
    if (ev.target === back || a === 'close') back.remove();
    else if (a === 'undo') { pts.pop(); draw(); }
    else if (a === 'reload') { pts = []; load(); }
    else if (a === 'save') {
      try {
        await api('/api/camera_cal', { method: 'POST', body: JSON.stringify({ corners: pts, image_w: img.naturalWidth, image_h: img.naturalHeight }) });
        msg.textContent = 'Saved. Runs with a photo step will now auto-observe the target well.';
      } catch (e) { msg.textContent = 'Save failed: ' + e.message; }
    }
  });
  load();
}

const btn = document.createElement('button');
btn.className = 'btn ghost'; btn.textContent = 'Calibrate cam'; btn.title = 'Click 4 plate corner wells on the latest phone frame';
btn.addEventListener('click', open);
const host = document.querySelector('.topright');
if (host) host.insertBefore(btn, host.querySelector('#abortBtn')); else document.body.appendChild(btn);
