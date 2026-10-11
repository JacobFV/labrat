// LabRat digital twin: CNC 3018 + pipette, deck, 96-well plate, 2x SO-101 (+ phone), optional Tappet arms.
// Units: mm. Deck coords (x right 0..300, y back 0..180) map to three (X = x-150, Z = y-90). Up = +Y.
// The desk is y=0. The CNC (web/models/cnc3018.glb, built by cad/cnc3018/cnc3018.py) sits in this.cncRoot,
// raised so its bed top (= deck plane) is at cncRoot y=0. Like a real 3018 the BED moves in Y (deck items ride
// on it), the X carriage moves along the gantry, and cnc pos is the SPINDLE axis; the pipette sits 45 mm in
// front of it (deck_cal pipette_offset dy=-45).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { loadSO101, setSO101 } from './so101.js';

const ROWS = 'ABCDEFGH';
const SAFE_H = 60; // tip height above deck at cnc z=0
const BED_H = 72; // desk -> bed top (GLB feet bottom at -72)
const PIP_DY = -45; // pipette axis vs spindle axis (deck y)
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
    const r = this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    r.setPixelRatio(Math.min(devicePixelRatio, 2));
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.toneMapping = THREE.ACESFilmicToneMapping; r.toneMappingExposure = 1.05;
    r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFSoftShadowMap;
    el.appendChild(r.domElement);
    const s = this.scene = new THREE.Scene();
    s.fog = new THREE.Fog(0x0b0c0e, 1600, 3200);
    try { // subtle studio environment so anodised alu and steel read as metal
      const pm = new THREE.PMREMGenerator(r); s.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture; s.environmentIntensity = 0.55;
    } catch (e) { console.warn('env map', e); }
    this.camera = new THREE.PerspectiveCamera(38, 1, 1, 6000);
    this.camera.position.set(-640, 640, -1080);
    this.controls = new OrbitControls(this.camera, r.domElement);
    this.controls.target.set(60, 110, -120);
    this.controls.enableDamping = true;
    s.add(new THREE.HemisphereLight(0xf2f5ff, 0x2a2622, 1.1));
    const d = this.sun = new THREE.DirectionalLight(0xfff6ea, 2.4); d.position.set(-380, 820, -460);
    d.castShadow = true; d.shadow.mapSize.set(2048, 2048); d.shadow.bias = -0.0004; d.shadow.normalBias = 0.6; d.shadow.radius = 4;
    Object.assign(d.shadow.camera, { left: -650, right: 650, top: 650, bottom: -650, near: 100, far: 2200 }); s.add(d);
    const fill = new THREE.DirectionalLight(0xdfe8ff, 0.6); fill.position.set(500, 300, 600); s.add(fill);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(4000, 4000), new THREE.ShadowMaterial({ opacity: 0.32 }));
    ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; s.add(ground);
    const grid = new THREE.GridHelper(1600, 32, 0x2a2a2a, 0x161616); grid.position.y = -0.3; s.add(grid);
    this.cncRoot = new THREE.Group(); this.cncRoot.position.y = BED_H; s.add(this.cncRoot);
    this.deck = new THREE.Group(); this.deck.name = 'deck'; this.cncRoot.add(this.deck); // rides on the Y bed
    this.wells = {}; this.tips = {};
    this._buildDeck(); this._buildGantry();
    this.arms = {
      so_a: this._buildSO101(new THREE.Vector3(-345, 0, -40), 0x2b2b2b, false),
      so_b: this._buildSO101(new THREE.Vector3(345, 0, -40), 0x2b2b2b, false),
    };
    this.arms.so_b.root.rotation.y = Math.PI;
    this.tappet = this._buildTappet(new THREE.Vector3(0, 0, -330));
    this.tappet.g.visible = false; // optional hardware: shown only once it reports state
    this._buildPhoneStand(); this._buildLaptop();
    this._shadows(this.scene);
    this._loadCNC(); this._loadArms();
    try { window.__twin = this; } catch { /* no window */ }
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
    this.bedBox = this._box(320, 6, 200, 0x111111, new THREE.Vector3(0, -3, 0), this.deck);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(300, 0.1, 180)), new THREE.LineBasicMaterial({ color: 0x2a2a2a }));
    edges.position.y = 0.2; this.deck.add(edges);
    // tip rack
    const tr = DECK.tip_rack, rackC = P(tr.x + 49.5, tr.y + 31.5);
    this._box(110, 30, 76, 0x1d1d22, new THREE.Vector3(rackC.x, 15, rackC.z), this.deck);
    const tipGeo = new THREE.ConeGeometry(2.6, 22, 10); tipGeo.rotateX(Math.PI); tipGeo.translate(0, 11, 0);
    const tipMat = this._mat(0xe8e2c8, { transparent: true, opacity: .85 });
    for (let r = 0; r < 8; r++) for (let c = 0; c < 12; c++) {
      const p = P(tr.x + c * tr.pitch, tr.y + r * tr.pitch); const t = new THREE.Mesh(tipGeo, tipMat);
      t.position.set(p.x, 30, p.z); this.deck.add(t); this.tips[ROWS[r] + (c + 1)] = t;
    }
    // plate
    const pl = DECK.plate, plC = P(pl.x + 49.5, pl.y + 31.5);
    this._box(127, 14, 85, 0xd8d8dc, new THREE.Vector3(plC.x, 7, plC.z), this.deck, { transparent: true, opacity: .35 });
    const wellGeo = new THREE.CylinderGeometry(3.3, 3.3, 1, 18); wellGeo.translate(0, .5, 0);
    const rimGeo = new THREE.TorusGeometry(3.6, .35, 6, 18); rimGeo.rotateX(Math.PI / 2);
    const rimMat = this._mat(0x555560);
    for (let r = 0; r < 8; r++) for (let c = 0; c < 12; c++) {
      const p = P(pl.x + c * pl.pitch, pl.y + r * pl.pitch);
      const m = new THREE.Mesh(wellGeo, this._mat(0x1a1a1f, { roughness: .2, emissive: 0x000000 }));
      m.position.set(p.x, 3, p.z); m.scale.y = .01; this.deck.add(m);
      const rim = new THREE.Mesh(rimGeo, rimMat); rim.position.set(p.x, 14, p.z); this.deck.add(rim);
      this.wells[ROWS[r] + (c + 1)] = m;
    }
    // reservoirs
    this.reservoirs = DECK.reservoirs.map((rv, i) => {
      const p = P(rv.x, rv.y);
      this._box(30, 24, 34, 0xcccccc, new THREE.Vector3(p.x, 12, p.z), this.deck, { transparent: true, opacity: .22 });
      return this._box(27, 16, 31, RES_COLORS[i], new THREE.Vector3(p.x, 8, p.z), this.deck, { transparent: true, opacity: .85 });
    });
    const w = P(DECK.waste.x, DECK.waste.y);
    this._box(30, 30, 18, 0x3a1515, new THREE.Vector3(w.x, 15, w.z), this.deck);
    const e = P(DECK.eject_bar.x, DECK.eject_bar.y);
    this._box(30, 40, 4, 0x777777, new THREE.Vector3(e.x, 20, e.z), this.deck, { metalness: .7 });
  }

  _buildGantry() {
    const alu = 0x8a8a92;
    this.fbGantry = new THREE.Group(); this.cncRoot.add(this.fbGantry); // fallback boxes, hidden once the GLB loads
    for (const sx of [-170, 170]) { this._box(20, 120, 20, alu, new THREE.Vector3(sx, 60, 100), this.fbGantry, { metalness: .6 }); this._box(20, 20, 220, alu, new THREE.Vector3(sx, 6, 0), this.fbGantry, { metalness: .6 }); }
    // the bridge is fixed (the bed moves in Y); tip sits at Z = PIP_DY
    this.bridge = new THREE.Group(); this.bridge.position.z = PIP_DY + 14; this.fbGantry.add(this.bridge);
    this._box(360, 20, 16, alu, new THREE.Vector3(0, 130, -14), this.bridge, { metalness: .6 });
    this.carriage = new THREE.Group(); this.bridge.add(this.carriage);
    this._box(40, 50, 14, 0x222226, new THREE.Vector3(0, 125, -4), this.carriage);
    this.head = new THREE.Group(); this.carriage.add(this.head);
    // servo pipette body
    this.headBody = this._box(22, 60, 22, 0xf5d76e, new THREE.Vector3(0, 40, 0), this.head, { roughness: .4 });
    this.plunger = this._box(6, 18, 6, 0xeeeeee, new THREE.Vector3(0, 78, 0), this.head);
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(3, 2, 22, 12), this._mat(0xbbbbbb, { metalness: .5 }));
    shaft.position.y = 11; this.head.add(shaft); this.headShaft = shaft;
    const tg = new THREE.ConeGeometry(2.6, 22, 10); tg.rotateX(Math.PI); tg.translate(0, -11, 0);
    this.headTip = new THREE.Mesh(tg, this._mat(0xe8e2c8, { transparent: true, opacity: .9 })); this.head.add(this.headTip);
    this.headLiquid = new THREE.Mesh(new THREE.CylinderGeometry(1.6, .8, 1, 8), this._mat(0xff8800)); this.headLiquid.position.y = -16; this.head.add(this.headLiquid);
    // head origin = tip end; spindle drop rod
    this.zrod = this._box(8, 1, 8, 0x555555, new THREE.Vector3(0, 0, 0), this.carriage, { metalness: .7 });
  }

  _shadows(o) { o.traverse((m) => { if (m.isMesh && !m.material?.transparent) { m.castShadow = true; m.receiveShadow = true; } }); }

  // high-quality 3018 PRO model (cad/cnc3018/cnc3018.py). Node origins = machine centre (x150,y90,z0), so the
  // driven transforms are plain offsets. On failure the box gantry stays as a fallback.
  _loadCNC() {
    new GLTFLoader().load(new URL('./models/cnc3018.glb', import.meta.url).href, (g) => {
      const root = g.scene; root.name = 'cnc3018';
      const n = (k) => root.getObjectByName(k);
      const nodes = { base: n('cnc_base'), bed: n('cnc_y_bed'), xc: n('cnc_x_carriage'), z: n('cnc_z'), plunger: n('pipette_plunger'), tip: n('pipette_tip') };
      if (!nodes.bed || !nodes.xc || !nodes.z) { console.warn('cnc3018.glb: missing nodes, keeping fallback'); return; }
      root.traverse((m) => {
        if (!m.isMesh) return; m.castShadow = true; m.receiveShadow = true;
        const mt = m.material; if (mt && mt.name === 'tip') { mt.transparent = true; mt.opacity = .8; m.castShadow = false; }
      });
      this.cncRoot.add(root);
      this.fbGantry.visible = false; this.bedBox.visible = false;
      // keep the twin's liquid indicator; hang it on cnc_z at the pipette tip (pipette axis at Z = PIP_DY)
      nodes.z.add(this.head); this.head.position.set(0, SAFE_H + 22, PIP_DY);
      for (const m of [this.headBody, this.plunger, this.headShaft]) m.visible = false;
      this.headTip.material.visible = false; // the GLB has the real tip
      nodes.tip.visible = this.headTip.visible;
      this.cncNodes = nodes; this.cncModel = root;
      if (this.cnc) this.setCNC(this.cnc);
    }, undefined, (e) => console.warn('cnc3018.glb failed to load, using box fallback', e));
  }

  // official SO-101 URDF + meshes (web/models/so101, Apache-2.0); fallback = simple chains
  async _loadArms() {
    const place = { so_a: { pos: [-345, 0, -40], rotY: 0 }, so_b: { pos: [345, 0, -40], rotY: Math.PI } };
    for (const k of Object.keys(place)) {
      try {
        const arm = await loadSO101({ bodyColor: k === 'so_a' ? 0xf4f3ee : 0xffd11f, servoColor: 0x141416 });
        const a = this.arms[k]; const pl = place[k];
        // pan axis sits ~39 mm along +X of the URDF base origin: put the pan axis at pl.pos
        arm.root.position.set(pl.pos[0] - 39 * Math.cos(pl.rotY), 0, pl.pos[2] + 39 * Math.sin(pl.rotY)); arm.root.rotation.y = pl.rotY;
        this.scene.add(arm.root); this._shadows(arm.root);
        a.root.visible = false; a.so101 = arm;
        if (a.q) setSO101(arm, a.q);
      } catch (e) { console.warn('SO-101 model failed, keeping simple arm', k, e); }
    }
  }

  // iPhone on a static tripod at the front-right corner, ~350 mm up, looking 45 deg down at the bed
  _buildPhoneStand() {
    const g = this.phoneStand = new THREE.Group(); g.name = 'phone_stand'; this.scene.add(g);
    const base = new THREE.Vector3(200, 0, -400), head = new THREE.Vector3(165, 350, -330);
    const metal = this._mat(0x2c2e33, { metalness: .7, roughness: .35 });
    const rod = (a, b, r) => { const v = b.clone().sub(a); const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, v.length(), 16), metal); m.position.copy(a).add(b).multiplyScalar(.5); m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), v.normalize()); g.add(m); return m; };
    const hub = new THREE.Vector3(base.x, 120, base.z);
    for (let i = 0; i < 3; i++) { const a = i * 2 * Math.PI / 3 + .4; rod(hub, new THREE.Vector3(base.x + 150 * Math.cos(a), 0, base.z + 150 * Math.sin(a)), 4.5); }
    const top = new THREE.Vector3(base.x, 300, base.z); rod(new THREE.Vector3(base.x, 110, base.z), top, 7);
    rod(top, head, 5);
    const ball = new THREE.Mesh(new THREE.SphereGeometry(11, 20, 14), metal); ball.position.copy(head); g.add(ball);
    const target = new THREE.Vector3(0, BED_H + 10, 0);
    const phone = this.phone = new THREE.Group(); phone.position.copy(head); g.add(phone);
    phone.lookAt(head.clone().multiplyScalar(2).sub(target)); // +Z (screen) away from the bed, rear camera at the bed
    const off = new THREE.Group(); off.position.z = 20; phone.add(off);
    const body = new THREE.Mesh(new THREE.BoxGeometry(71.6, 147.6, 7.8), this._mat(0x0d0e10, { metalness: .5, roughness: .25 })); off.add(body);
    const clamp = this._box(80, 22, 14, 0x1b1c1f, new THREE.Vector3(0, 0, -9), off); void clamp;
    const cv = document.createElement('canvas'); cv.width = 256; cv.height = 528; const cx = cv.getContext('2d');
    cx.fillStyle = '#05080a'; cx.fillRect(0, 0, 256, 528);
    cx.fillStyle = '#6ee7c8'; cx.shadowColor = '#6ee7c8'; cx.shadowBlur = 24;
    for (const ex of [78, 178]) { cx.beginPath(); cx.ellipse(ex, 230, 30, 48, 0, 0, Math.PI * 2); cx.fill(); }
    cx.shadowBlur = 0; cx.fillStyle = '#05080a'; for (const ex of [86, 186]) { cx.beginPath(); cx.arc(ex, 214, 9, 0, Math.PI * 2); cx.fill(); }
    cx.fillStyle = '#6ee7c8'; cx.font = 'bold 22px ui-monospace, monospace'; cx.textAlign = 'center'; cx.fillText('labrat eye', 128, 440);
    const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
    const scr = new THREE.Mesh(new THREE.PlaneGeometry(66, 142), new THREE.MeshStandardMaterial({ color: 0x111111, emissive: 0xffffff, emissiveMap: tex, roughness: .08, metalness: .1 }));
    scr.position.z = 3.95; off.add(scr);
    const cam = this._box(22, 22, 2, 0x16171a, new THREE.Vector3(-18, 55, -4.9), off);
    for (const [lx, ly] of [[-23, 60], [-13, 50]]) { const l = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, 2, 20), this._mat(0x050505, { roughness: .05, metalness: .8 })); l.rotation.x = Math.PI / 2; l.position.set(lx, ly, -6); off.add(l); }
    void cam;
  }

  // open laptop on the desk, off to the side (outside CNC + arm reach), dark console on screen
  _buildLaptop() {
    const g = this.laptop = new THREE.Group(); g.name = 'laptop'; this.scene.add(g);
    g.position.set(560, 0, -470); g.rotation.y = Math.PI + 0.6;
    const alu = this._mat(0xa7acb3, { metalness: .8, roughness: .32 });
    const base = new THREE.Mesh(new THREE.BoxGeometry(310, 14, 220), alu); base.position.y = 7; g.add(base);
    this._box(270, 1, 105, 0x16171a, new THREE.Vector3(0, 14.4, -40), g, { roughness: .7 });
    for (let r = 0; r < 5; r++) for (let c = 0; c < 14; c++) this._box(16, 1.2, 15, 0x222327, new THREE.Vector3(-121 + c * 18.6, 15.2, -82 + r * 19.5), g, { roughness: .6 });
    this._box(110, .6, 66, 0x9aa0a7, new THREE.Vector3(0, 14.3, 60), g, { metalness: .6, roughness: .25 });
    const lid = new THREE.Group(); lid.position.set(0, 14, -110); lid.rotation.x = -THREE.MathUtils.degToRad(20); g.add(lid);
    const shell = new THREE.Mesh(new THREE.BoxGeometry(310, 215, 6), alu); shell.position.set(0, 107.5, -3); lid.add(shell);
    this._box(304, 209, .6, 0x050506, new THREE.Vector3(0, 107.5, .3), lid, { roughness: .1 });
    const cv = document.createElement('canvas'); cv.width = 640; cv.height = 400; const c = cv.getContext('2d');
    c.fillStyle = '#0b0d10'; c.fillRect(0, 0, 640, 400); c.fillStyle = '#16191e'; c.fillRect(0, 0, 640, 26);
    for (const [i, col] of ['#ff5f57', '#febc2e', '#28c840'].entries()) { c.fillStyle = col; c.beginPath(); c.arc(16 + i * 18, 13, 5, 0, 7); c.fill(); }
    c.font = '15px ui-monospace, Menlo, monospace';
    const lines = [['#6ee7c8', 'labrat> run protocol serial_dilution.yaml'], ['#8a93a3', '[cnc]   grbl 1.1h  ok  home  x0 y0 z0'], ['#8a93a3', '[pip]   tip A1 picked   rest_us=667'],
      ['#c9d1d9', '[step 3/24] aspirate 50 uL  R2 -> plate B1'], ['#c9d1d9', '[step 4/24] dispense 50 uL  plate B1  mix x3'], ['#8a93a3', '[so_a]  watching bed  conf 0.97'],
      ['#8a93a3', '[eye]   plate detected  96 wells  rot 0.4 deg'], ['#f2c81d', '[warn]  R1 low: 3.2 mL'], ['#c9d1d9', '[step 5/24] eject tip -> waste'], ['#6ee7c8', 'labrat> _']];
    lines.forEach(([col, t], i) => { c.fillStyle = col; c.fillText(t, 18, 58 + i * 32); });
    const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
    const scr = new THREE.Mesh(new THREE.PlaneGeometry(286, 179), new THREE.MeshStandardMaterial({ color: 0x000000, emissive: 0xffffff, emissiveMap: tex, emissiveIntensity: .9, roughness: .15 }));
    scr.position.set(0, 110, .7); lid.add(scr);
  }

  // SO-101 as a simple 6-joint chain (fallback until the URDF model loads)
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
    const a = this.arms[id]; if (!a || !q) return; const d = THREE.MathUtils.degToRad;
    a.q = { ...(a.q || {}), ...q };
    if (a.so101) { setSO101(a.so101, q); return; }
    if (q.shoulder_pan != null) a.pan.rotation.y = d(q.shoulder_pan);
    if (q.shoulder_lift != null) a.lift.rotation.z = d(q.shoulder_lift);
    if (q.elbow_flex != null) a.elbow.rotation.z = d(q.elbow_flex);
    if (q.wrist_flex != null) a.wflex.rotation.z = d(q.wrist_flex);
    if (q.wrist_roll != null) a.roll.rotation.y = d(q.wrist_roll);
    if (q.gripper != null && a.fingerA) { const o = Math.max(0, Math.min(1, q.gripper > 1 ? q.gripper / 100 : q.gripper)); a.fingerA.position.x = -6 - 14 * o; a.fingerB.position.x = 6 + 14 * o; }
  }
  setTappet(j) { if (!j) return; this.tappet.g.visible = true; const arr = Array.isArray(j) ? j : Object.values(j); if (arr.length > 1) { this.tappet.a.rotation.x = THREE.MathUtils.degToRad(+arr[1] || 0) * .5; } if (arr.length > 2) this.tappet.b.rotation.x = THREE.MathUtils.degToRad(+arr[2] || 0) * .5; if (arr.length) this.tappet.g.rotation.y = THREE.MathUtils.degToRad(+arr[0] || 0); }

  setCNC(c) {
    if (!c) return;
    const x = c.x ?? c.X ?? (c.pos && c.pos[0]), y = c.y ?? c.Y ?? (c.pos && c.pos[1]), z = c.z ?? c.Z ?? (c.pos && c.pos[2]);
    // a 3018 moves the BED in Y: the deck (and the GLB bed) slide so deck point y sits under the spindle (world Z=0)
    if (y != null) { const bz = -((+y) - 90); this.deck.position.z = bz; if (this.cncNodes) this.cncNodes.bed.position.z = bz; }
    if (x != null) { this.carriage.position.x = (+x) - 150; if (this.cncNodes) this.cncNodes.xc.position.x = (+x) - 150; }
    if (z != null && this.cncNodes) this.cncNodes.z.position.y = +z;
    if (z != null && !this.cncNodes) {
      const tipH = SAFE_H + (+z); this.head.position.set(0, tipH + 22, -14);
      const top = 150, ht = tipH + 92; this.zrod.scale.y = Math.max(1, top - ht); this.zrod.position.set(0, (top + ht) / 2, -14);
    }
    this.cnc = { x, y, z };
  }
  setPipette(p) {
    if (!p) return;
    const tip = !!(p.tip ?? p.has_tip);
    this.headTip.visible = tip; if (this.cncNodes?.tip) this.cncNodes.tip.visible = tip;
    const v = +(p.vol_ul ?? p.volume ?? 0);
    this.headLiquid.visible = tip && v > 0.5; this.headLiquid.scale.y = Math.max(.1, Math.min(18, v / 15)); this.headLiquid.position.y = -20 + this.headLiquid.scale.y / 2;
    if (p.rgb || p.color) { const c = parseColor(p.rgb || p.color); if (c) this.headLiquid.material.color.copy(c); }
    if (p.frac != null) { this.plunger.position.y = 78 - 12 * p.frac; if (this.cncNodes?.plunger) this.cncNodes.plunger.position.y = -12 * p.frac; }
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
