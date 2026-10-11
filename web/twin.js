// LabRat digital twin: CNC 3018 + pipette, deck, 96-well plate, 2x SO-101 (+ phone), optional Tappet arms.
// Units: mm. Deck coords (x right 0..300, y back 0..180) map to three (X = x-150, Z = y-90). Up = +Y.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const ROWS = 'ABCDEFGH';
const SAFE_H = 60; // tip height above deck at cnc z=0
export const DECK = {
  tip_rack: { x: 20, y: 20, pitch: 9 },
  plate: { x: 140, y: 20, pitch: 9, cap: 300 },
  reservoirs: [20, 60, 100, 140].map((y, i) => ({ name: `R${i + 1}`, x: 250, y })),
  waste: { x: 280, y: 170 }, eject_bar: { x: 10, y: 170 },
};
const RES_COLORS = ['#cfe8ff', '#e0312b', '#2a5bd7', '#f2c81d']; // water, red, blue, yellow

const P = (x, y) => new THREE.Vector3(x - 150, 0, y - 90);

export function parseColor(c) {
  if (c == null) return null;
  if (Array.isArray(c)) { const m = c.some(v => v > 1) ? 255 : 1; return new THREE.Color(c[0] / m, c[1] / m, c[2] / m); }
  if (typeof c === 'object' && 'r' in c) { const m = (c.r > 1 || c.g > 1 || c.b > 1) ? 255 : 1; return new THREE.Color(c.r / m, c.g / m, c.b / m); }
  try { return new THREE.Color(c); } catch { return null; }
}

export class Twin {
  constructor(el) {
    this.el = el;
    const r = this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    r.setPixelRatio(Math.min(devicePixelRatio, 2));
    el.appendChild(r.domElement);
    const s = this.scene = new THREE.Scene();
    s.fog = new THREE.Fog(0x000000, 900, 1800);
    this.camera = new THREE.PerspectiveCamera(40, 1, 1, 5000);
    this.camera.position.set(-120, 380, 420);
    this.controls = new OrbitControls(this.camera, r.domElement);
    this.controls.target.set(0, 20, 0);
    this.controls.enableDamping = true;
    s.add(new THREE.HemisphereLight(0xffffff, 0x202020, 1.6));
    const d = new THREE.DirectionalLight(0xffffff, 1.6); d.position.set(150, 400, 200); s.add(d);
    const grid = new THREE.GridHelper(900, 45, 0x2a2a2a, 0x141414); grid.position.y = -0.5; s.add(grid);
    this.wells = {}; this.tips = {};
    this._buildDeck(); this._buildGantry();
    this.arms = {
      so_a: this._buildSO101(new THREE.Vector3(-230, 0, 0), 0x2b2b2b, true),
      so_b: this._buildSO101(new THREE.Vector3(230, 0, 40), 0x2b2b2b, false),
    };
    this.arms.so_b.root.rotation.y = Math.PI;
    this.tappet = this._buildTappet(new THREE.Vector3(0, 0, -200));
    this.setCNC({ x: 150, y: 90, z: 0 });
    this.setPipette({ vol_ul: 0, tip: false });
    for (const k in this.arms) this.setArm(k, { shoulder_pan: 0, shoulder_lift: -20, elbow_flex: 60, wrist_flex: 40, wrist_roll: 0, gripper: 10 });
    new ResizeObserver(() => this.resize()).observe(el);
    this.resize();
    const loop = () => { this.controls.update(); r.render(s, this.camera); requestAnimationFrame(loop); };
    loop();
  }
  resize() { const w = this.el.clientWidth || 1, h = this.el.clientHeight || 1; this.renderer.setSize(w, h); this.camera.aspect = w / h; this.camera.updateProjectionMatrix(); }

  _mat(c, o = {}) { return new THREE.MeshStandardMaterial({ color: c, roughness: .6, metalness: .1, ...o }); }
  _box(w, h, d, c, pos, parent = this.scene, o) { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), this._mat(c, o)); m.position.copy(pos); parent.add(m); return m; }

  _buildDeck() {
    // bed
    this._box(320, 6, 200, 0x111111, new THREE.Vector3(0, -3, 0));
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(300, 0.1, 180)), new THREE.LineBasicMaterial({ color: 0x2a2a2a }));
    edges.position.y = 0.2; this.scene.add(edges);
    // tip rack
    const tr = DECK.tip_rack, rackC = P(tr.x + 49.5, tr.y + 31.5);
    this._box(110, 30, 76, 0x1d1d22, new THREE.Vector3(rackC.x, 15, rackC.z));
    const tipGeo = new THREE.ConeGeometry(2.6, 22, 10); tipGeo.rotateX(Math.PI); tipGeo.translate(0, 11, 0);
    const tipMat = this._mat(0xe8e2c8, { transparent: true, opacity: .85 });
    for (let r = 0; r < 8; r++) for (let c = 0; c < 12; c++) {
      const p = P(tr.x + c * tr.pitch, tr.y + r * tr.pitch); const t = new THREE.Mesh(tipGeo, tipMat);
      t.position.set(p.x, 30, p.z); this.scene.add(t); this.tips[ROWS[r] + (c + 1)] = t;
    }
    // plate
    const pl = DECK.plate, plC = P(pl.x + 49.5, pl.y + 31.5);
    this._box(127, 14, 85, 0xd8d8dc, new THREE.Vector3(plC.x, 7, plC.z), this.scene, { transparent: true, opacity: .35 });
    const wellGeo = new THREE.CylinderGeometry(3.3, 3.3, 1, 18); wellGeo.translate(0, .5, 0);
    const rimGeo = new THREE.TorusGeometry(3.6, .35, 6, 18); rimGeo.rotateX(Math.PI / 2);
    const rimMat = this._mat(0x555560);
    for (let r = 0; r < 8; r++) for (let c = 0; c < 12; c++) {
      const p = P(pl.x + c * pl.pitch, pl.y + r * pl.pitch);
      const m = new THREE.Mesh(wellGeo, this._mat(0x1a1a1f, { roughness: .2, emissive: 0x000000 }));
      m.position.set(p.x, 3, p.z); m.scale.y = .01; this.scene.add(m);
      const rim = new THREE.Mesh(rimGeo, rimMat); rim.position.set(p.x, 14, p.z); this.scene.add(rim);
      this.wells[ROWS[r] + (c + 1)] = m;
    }
    // reservoirs
    this.reservoirs = DECK.reservoirs.map((rv, i) => {
      const p = P(rv.x, rv.y);
      this._box(30, 24, 34, 0xcccccc, new THREE.Vector3(p.x, 12, p.z), this.scene, { transparent: true, opacity: .22 });
      return this._box(27, 16, 31, RES_COLORS[i], new THREE.Vector3(p.x, 8, p.z), this.scene, { transparent: true, opacity: .85 });
    });
    const w = P(DECK.waste.x, DECK.waste.y);
    this._box(30, 30, 18, 0x3a1515, new THREE.Vector3(w.x, 15, w.z));
    const e = P(DECK.eject_bar.x, DECK.eject_bar.y);
    this._box(30, 40, 4, 0x777777, new THREE.Vector3(e.x, 20, e.z), this.scene, { metalness: .7 });
  }

  _buildGantry() {
    const alu = 0x8a8a92;
    for (const sx of [-170, 170]) { this._box(20, 120, 20, alu, new THREE.Vector3(sx, 60, -100), this.scene, { metalness: .6 }); this._box(20, 20, 220, alu, new THREE.Vector3(sx, 6, 0), this.scene, { metalness: .6 }); }
    // the bridge moves in Y (deck depth) along with the carriage for simplicity
    this.bridge = new THREE.Group(); this.scene.add(this.bridge);
    this._box(360, 20, 16, alu, new THREE.Vector3(0, 130, -14), this.bridge, { metalness: .6 });
    this.carriage = new THREE.Group(); this.bridge.add(this.carriage);
    this._box(40, 50, 14, 0x222226, new THREE.Vector3(0, 125, -4), this.carriage);
    this.head = new THREE.Group(); this.carriage.add(this.head);
    // servo pipette body
    this._box(22, 60, 22, 0xf5d76e, new THREE.Vector3(0, 40, 0), this.head, { roughness: .4 });
    this.plunger = this._box(6, 18, 6, 0xeeeeee, new THREE.Vector3(0, 78, 0), this.head);
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(3, 2, 22, 12), this._mat(0xbbbbbb, { metalness: .5 }));
    shaft.position.y = 11; this.head.add(shaft);
    const tg = new THREE.ConeGeometry(2.6, 22, 10); tg.rotateX(Math.PI); tg.translate(0, -11, 0);
    this.headTip = new THREE.Mesh(tg, this._mat(0xe8e2c8, { transparent: true, opacity: .9 })); this.head.add(this.headTip);
    this.headLiquid = new THREE.Mesh(new THREE.CylinderGeometry(1.6, .8, 1, 8), this._mat(0xff8800)); this.headLiquid.position.y = -16; this.head.add(this.headLiquid);
    // head origin = tip end; spindle drop rod
    this.zrod = this._box(8, 1, 8, 0x555555, new THREE.Vector3(0, 0, 0), this.carriage, { metalness: .7 });
  }

  // SO-101 as a simple 6-joint chain
  _buildSO101(pos, color, phone) {
    const root = new THREE.Group(); root.position.copy(pos); this.scene.add(root);
    const c = color, acc = 0xf5d76e;
    this._box(60, 20, 60, 0x181818, new THREE.Vector3(0, 10, 0), root);
    const pan = new THREE.Group(); pan.position.y = 20; root.add(pan);
    this._box(40, 40, 40, c, new THREE.Vector3(0, 20, 0), pan);
    const lift = new THREE.Group(); lift.position.y = 45; pan.add(lift);
    this._box(26, 112, 26, acc, new THREE.Vector3(0, 56, 0), lift, { roughness: .5 });
    const elbow = new THREE.Group(); elbow.position.y = 112; lift.add(elbow);
    this._box(22, 135, 22, c, new THREE.Vector3(0, 67, 0), elbow);
    const wflex = new THREE.Group(); wflex.position.y = 135; elbow.add(wflex);
    this._box(24, 50, 24, acc, new THREE.Vector3(0, 25, 0), wflex);
    const roll = new THREE.Group(); roll.position.y = 50; wflex.add(roll);
    const grip = new THREE.Group(); roll.add(grip);
    let fingerA, fingerB;
    if (phone) {
      // iPhone mounted on the tip, screen (eyes) facing forward
      this._box(72, 150, 8, 0x0b0b0b, new THREE.Vector3(0, 80, 0), roll, { roughness: .3 });
      const scr = this._box(66, 140, .5, 0x10251f, new THREE.Vector3(0, 80, 4.3), roll, { emissive: 0x0b3b30 });
      for (const ex of [-15, 15]) this._box(12, 16, .5, 0x6ee7c8, new THREE.Vector3(ex, 95, 4.7), roll, { emissive: 0x6ee7c8, emissiveIntensity: .8 });
      this._box(10, 10, 4, 0x222222, new THREE.Vector3(-22, 140, -6), roll); // rear camera
      void scr;
    } else {
      fingerA = this._box(6, 50, 14, c, new THREE.Vector3(-10, 25, 0), grip);
      fingerB = this._box(6, 50, 14, c, new THREE.Vector3(10, 25, 0), grip);
    }
    return { root, pan, lift, elbow, wflex, roll, fingerA, fingerB };
  }
  _buildTappet(pos) {
    const g = new THREE.Group(); g.position.copy(pos); this.scene.add(g);
    this._box(40, 14, 40, 0x1a1a1a, new THREE.Vector3(0, 7, 0), g);
    const a = new THREE.Group(); a.position.y = 14; g.add(a);
    this._box(14, 80, 14, 0x6ee7c8, new THREE.Vector3(0, 40, 0), a);
    const b = new THREE.Group(); b.position.y = 80; a.add(b);
    this._box(12, 70, 12, 0x333333, new THREE.Vector3(0, 35, 0), b);
    a.rotation.x = -.3; b.rotation.x = 1.1;
    return { g, a, b };
  }

  setArm(id, q = {}) {
    const a = this.arms[id]; if (!a) return; const d = THREE.MathUtils.degToRad;
    if (q.shoulder_pan != null) a.pan.rotation.y = d(q.shoulder_pan);
    if (q.shoulder_lift != null) a.lift.rotation.z = d(q.shoulder_lift);
    if (q.elbow_flex != null) a.elbow.rotation.z = d(q.elbow_flex);
    if (q.wrist_flex != null) a.wflex.rotation.z = d(q.wrist_flex);
    if (q.wrist_roll != null) a.roll.rotation.y = d(q.wrist_roll);
    if (q.gripper != null && a.fingerA) { const o = Math.max(0, Math.min(1, q.gripper > 1 ? q.gripper / 100 : q.gripper)); a.fingerA.position.x = -6 - 14 * o; a.fingerB.position.x = 6 + 14 * o; }
  }
  setTappet(j) { if (!j) return; const arr = Array.isArray(j) ? j : Object.values(j); if (arr.length > 1) { this.tappet.a.rotation.x = THREE.MathUtils.degToRad(+arr[1] || 0) * .5; } if (arr.length > 2) this.tappet.b.rotation.x = THREE.MathUtils.degToRad(+arr[2] || 0) * .5; if (arr.length) this.tappet.g.rotation.y = THREE.MathUtils.degToRad(+arr[0] || 0); }

  setCNC(c) {
    if (!c) return;
    const x = c.x ?? c.X ?? (c.pos && c.pos[0]), y = c.y ?? c.Y ?? (c.pos && c.pos[1]), z = c.z ?? c.Z ?? (c.pos && c.pos[2]);
    if (y != null) this.bridge.position.z = (+y) - 90 + 14;
    if (x != null) this.carriage.position.x = (+x) - 150;
    if (z != null) {
      const tipH = SAFE_H + (+z); this.head.position.set(0, tipH + 22, -14);
      const top = 150, ht = tipH + 92; this.zrod.scale.y = Math.max(1, top - ht); this.zrod.position.set(0, (top + ht) / 2, -14);
    }
    this.cnc = { x, y, z };
  }
  setPipette(p) {
    if (!p) return;
    const tip = !!(p.tip ?? p.has_tip);
    this.headTip.visible = tip;
    const v = +(p.vol_ul ?? p.volume ?? 0);
    this.headLiquid.visible = tip && v > 0.5; this.headLiquid.scale.y = Math.max(.1, Math.min(18, v / 15)); this.headLiquid.position.y = -20 + this.headLiquid.scale.y / 2;
    if (p.rgb || p.color) { const c = parseColor(p.rgb || p.color); if (c) this.headLiquid.material.color.copy(c); }
    if (p.frac != null) this.plunger.position.y = 78 - 12 * p.frac;
    if (typeof p.tip === 'string' && this.tips[p.tip]) this.tips[p.tip].visible = false;
  }
  setWells(wells) {
    if (!wells) return;
    for (const [k, w] of Object.entries(wells)) {
      const m = this.wells[k]; if (!m) continue;
      const vol = +(w.vol ?? w.vol_ul ?? w.volume ?? 0);
      const c = parseColor(w.rgb ?? w.color);
      if (c) { m.material.color.copy(c); m.material.emissive.copy(c).multiplyScalar(.25); }
      m.scale.y = Math.max(.01, Math.min(1, vol / DECK.plate.cap) * 11);
    }
  }
  resetWells() { for (const m of Object.values(this.wells)) { m.scale.y = .01; m.material.color.set(0x1a1a1f); m.material.emissive.set(0); } for (const t of Object.values(this.tips)) t.visible = true; }
  usedTips(list) { for (const k of list || []) if (this.tips[k]) this.tips[k].visible = false; }

  applyFrame(f) {
    if (!f) return;
    if (f.cnc) this.setCNC(f.cnc);
    if (f.pipette) this.setPipette(f.pipette);
    if (f.wells) this.setWells(f.wells);
    if (f.arms) for (const [k, q] of Object.entries(f.arms)) this.setArm(k, q);
    for (const k of ['so_a', 'so_b']) if (f[k]) this.setArm(k, f[k].q || f[k].joints || f[k]);
  }
  // live state: {so_a:{q|joints}, cnc:{x,y,z}|{mpos:[..]}, pipette:{...}, tap_a:{joints}}
  applyDevices(devs) {
    if (!devs) return;
    for (const k of ['so_a', 'so_b']) { const d = devs[k]; if (d) { const q = d.q || d.joints || d.state?.q; if (q) this.setArm(k, q); } }
    const c = devs.cnc; if (c) { const pos = c.pos || c.mpos || c.wpos || c.state || c; if (Array.isArray(pos)) this.setCNC({ pos }); else this.setCNC(pos); }
    if (devs.pipette) this.setPipette(devs.pipette.state || devs.pipette);
    const t = devs.tap_a; if (t) this.setTappet(t.joints || t.state?.joints);
  }
}
