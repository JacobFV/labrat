// LabRat digital twin: CNC 3018 + syringe pipette head, deck, 96-well plate, 2x SO-101 (+ phone), optional Tappet arms.
// Units: mm. Deck coords (x right 0..300, y back 0..180) map to three (X = x-150, Z = y-90). Up = +Y.
// The desk is y=0. The CNC (web/models/cnc3018.glb, built by cad/cnc3018/cnc3018.py) sits in this.cncRoot,
// raised so its bed top (= deck plane) is at cncRoot y=0. Like a real 3018 the BED moves in Y (deck items ride
// on it), the X carriage moves along the gantry, and cnc pos is the SPINDLE axis. The rack-and-pinion syringe
// (cad/syringe) replaces the spindle motor in its clamp, so the tip is on that axis (deck_cal pipette_offset 0).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { loadSO101, setSO101 } from './so101.js';

const ROWS = 'ABCDEFGH';
const SAFE_H = 60; // tip height above deck at cnc z=0
const BED_H = 72; // desk -> bed top (GLB feet bottom at -72)
const PIP_DY = 0; // pipette tip axis vs spindle axis (deck y): coaxial syringe head
const SYR_STROKE = 25, SYR_PITCH_R = 9; // cad/syringe: plunger travel (mm), pinion pitch radius (mm)
// SO-101 placement matches the real bench (operator stands at -Z, the CNC front): so_a = operator's LEFT,
// so_b = operator's RIGHT, both at the CNC's front corners, zero-pose reach pointing to the machine BACK (+Z).
// The CNC group is mirrored in X (cncRoot.scale.x = -1) so machine +x reads left->right for the operator, as on
// the real machine (the twin's deck->three map X=x-150, Z=y-90 is otherwise a mirror image).
const ARM_PLACE = { so_a: { pos: [250, 0, -300] }, so_b: { pos: [-250, 0, -300] } };
const ARM_YAW = -Math.PI / 2; // URDF +X (zero-pose reach) -> three +Z (machine back)
const PAN_OFF = 39;           // pan axis is ~39 mm along URDF +X from the base origin
// Recorded calibration zero = upper arm vertical, forearm horizontal toward the back, wrist straight, gripper half
// open. The URDF zero already renders that L pose; per-joint offsets (deg) are added here if a joint ever differs.
const JOINT_ZERO = { shoulder_pan: 0, shoulder_lift: 0, elbow_flex: 0, wrist_flex: 0, wrist_roll: 0, gripper: 0 };
const BENCH_H = 900; // lab floor -> bench top; everything on the bench lives in this.stage (bench-top frame)
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
    s.background = new THREE.Color(0xeef0f2);
    s.fog = new THREE.Fog(0xeef0f2, 3200, 7000);
    try { // studio environment so anodised alu and steel read as metal
      const pm = new THREE.PMREMGenerator(r); s.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture; s.environmentIntensity = 0.7;
    } catch (e) { console.warn('env map', e); }
    this.camera = new THREE.PerspectiveCamera(38, 1, 1, 9000);
    this.camera.position.set(0, BENCH_H + 620, -1250);
    this.controls = new OrbitControls(this.camera, r.domElement);
    this.controls.target.set(-60, BENCH_H + 110, -60);
    this.controls.enableDamping = true; this.controls.maxPolarAngle = Math.PI * 0.49;
    this.stage = new THREE.Group(); this.stage.name = 'bench_top'; this.stage.position.y = BENCH_H; s.add(this.stage);
    // bright, airy lab: sky/bounce hemisphere, soft overhead daylight key (soft shadows), window fill
    s.add(new THREE.HemisphereLight(0xffffff, 0xd8d2c8, 1.35));
    const d = this.sun = new THREE.DirectionalLight(0xfffaf0, 2.2); d.position.set(-300, BENCH_H + 1500, -500); d.target = this.stage;
    d.castShadow = true; d.shadow.mapSize.set(2048, 2048); d.shadow.bias = -0.0004; d.shadow.normalBias = 0.6; d.shadow.radius = 6; d.shadow.blurSamples = 16;
    Object.assign(d.shadow.camera, { left: -1000, right: 1000, top: 1000, bottom: -1000, near: 200, far: 3500 }); s.add(d);
    const fill = new THREE.DirectionalLight(0xe6efff, 0.7); fill.position.set(900, BENCH_H + 600, -900); s.add(fill);
    this._buildLab();
    this.cncRoot = new THREE.Group(); this.cncRoot.position.y = BED_H; this.cncRoot.scale.x = -1; this.stage.add(this.cncRoot);
    this.deck = new THREE.Group(); this.deck.name = 'deck'; this.cncRoot.add(this.deck); // rides on the Y bed
    this.wells = {}; this.tips = {};
    this._buildDeck(); this._buildGantry();
    this.arms = {
      so_a: this._buildSO101(new THREE.Vector3(...ARM_PLACE.so_a.pos), 0x2b2b2b, false),
      so_b: this._buildSO101(new THREE.Vector3(...ARM_PLACE.so_b.pos), 0x2b2b2b, false),
    };
    for (const k in this.arms) this.arms[k].root.rotation.y = ARM_YAW;
    this.tappet = this._buildTappet(new THREE.Vector3(560, 0, 250));
    this.tappet.g.visible = false; // optional hardware: shown only once it reports state
    this._buildPhoneStand(); this._buildLaptop();
    this._shadows(this.scene);
    this._loadCNC(); this._loadArms();
    try { window.__twin = this; } catch { /* no window */ }
    this.setCNC({ x: 150, y: 90, z: 0 });
    this.setPipette({ vol_ul: 0, tip: false });
    for (const k in this.arms) this.setArm(k, { shoulder_pan: 0, shoulder_lift: 0, elbow_flex: 0, wrist_flex: 0, wrist_roll: 0, gripper: 50 });
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
    this._box(110, 30, 76, 0xe8508f, new THREE.Vector3(rackC.x, 15, rackC.z), this.deck, { roughness: .45 });
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
    // syringe head (fallback boxes until the GLB loads)
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
      const nodes = { base: n('cnc_base'), bed: n('cnc_y_bed'), xc: n('cnc_x_carriage'), z: n('cnc_z'), rack: n('syringe_rack'), pinion: n('syringe_pinion'), tip: n('pipette_tip') };
      if (!nodes.bed || !nodes.xc || !nodes.z) { console.warn('cnc3018.glb: missing nodes, keeping fallback'); return; }
      root.traverse((m) => {
        if (!m.isMesh) return; m.castShadow = true; m.receiveShadow = true;
        const mt = m.material; if (mt && (mt.name === 'tip' || mt.name === 'syringe')) { mt.transparent = true; mt.opacity = mt.name === 'tip' ? .8 : .45; mt.depthWrite = false; m.castShadow = false; }
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
    const place = { so_a: { ...ARM_PLACE.so_a, rotY: ARM_YAW }, so_b: { ...ARM_PLACE.so_b, rotY: ARM_YAW } };
    for (const k of Object.keys(place)) {
      try {
        const arm = await loadSO101({ bodyColor: k === 'so_a' ? 0xf4f3ee : 0xffd11f, servoColor: 0x141416 });
        const a = this.arms[k]; const pl = place[k];
        // pan axis sits ~39 mm along +X of the URDF base origin: put the pan axis at pl.pos
        arm.root.position.set(pl.pos[0] - PAN_OFF * Math.cos(pl.rotY), 0, pl.pos[2] + PAN_OFF * Math.sin(pl.rotY)); arm.root.rotation.y = pl.rotY;
        this.stage.add(arm.root); this._shadows(arm.root);
        a.root.visible = false; a.so101 = arm;
        if (a.q) setSO101(arm, a.q);
      } catch (e) { console.warn('SO-101 model failed, keeping simple arm', k, e); }
    }
  }

  // iPhone standing upright (portrait) front-centre on a ~150 mm taped base, in front of the CNC front frame:
  // screen + eyes face the operator (-Z), rear camera looks at the CNC (+Z)
  _buildPhoneStand() {
    const g = this.phoneStand = new THREE.Group(); g.name = 'phone_stand'; g.position.set(0, 0, -225); this.stage.add(g);
    const tape = new THREE.MeshStandardMaterial({ color: 0x9a9ea4, metalness: .55, roughness: .38 });
    const baseGeo = new THREE.CylinderGeometry(34, 44, 150, 9, 3); // crumpled-looking duct-taped block
    const p = baseGeo.attributes.position; for (let i = 0; i < p.count; i++) { const f = 1 + 0.06 * Math.sin(i * 12.9898) ; p.setX(i, p.getX(i) * f); p.setZ(i, p.getZ(i) * f); } baseGeo.computeVertexNormals();
    const base = new THREE.Mesh(baseGeo, tape); base.position.y = 75; g.add(base);
    const skirt = new THREE.Mesh(new THREE.CylinderGeometry(62, 70, 1.2, 7), tape); skirt.position.y = .6; g.add(skirt); // tape flaps on the table
    const phone = this.phone = new THREE.Group(); phone.position.set(0, 150 + 147.6 / 2 - 22, 0); g.add(phone); // bottom slotted ~22 mm into the base
    const body = new THREE.Mesh(new THREE.BoxGeometry(71.6, 147.6, 7.8), this._mat(0x0d0e10, { metalness: .5, roughness: .25 })); phone.add(body);
    const cv = document.createElement('canvas'); cv.width = 256; cv.height = 528; const cx = cv.getContext('2d');
    cx.fillStyle = '#050607'; cx.fillRect(0, 0, 256, 528);
    cx.fillStyle = '#3fe0c0'; cx.shadowColor = '#3fe0c0'; cx.shadowBlur = 18;
    for (const ex of [70, 186]) { cx.beginPath(); cx.arc(ex, 230, 34, 0, Math.PI * 2); cx.fill(); }
    cx.shadowBlur = 0; cx.fillStyle = '#050607'; for (const ex of [74, 190]) { cx.beginPath(); cx.arc(ex, 230, 15, 0, Math.PI * 2); cx.fill(); }
    cx.fillStyle = '#3fe0c0'; cx.fillRect(110, 300, 36, 5);
    cx.fillStyle = '#9aa3ad'; cx.font = '13px ui-sans-serif, sans-serif'; cx.textAlign = 'center'; cx.fillText('labrat eye', 128, 450);
    const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
    const scr = new THREE.Mesh(new THREE.PlaneGeometry(66, 142), new THREE.MeshStandardMaterial({ color: 0x111111, emissive: 0xffffff, emissiveMap: tex, roughness: .08, metalness: .1 }));
    scr.position.z = -3.95; scr.rotation.y = Math.PI; phone.add(scr); // screen toward the operator (-Z)
    this._box(22, 22, 2, 0x16171a, new THREE.Vector3(18, 55, 4.9), phone); // rear camera bump toward the CNC
    for (const [lx, ly] of [[23, 60], [13, 50]]) { const l = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, 2, 20), this._mat(0x050505, { roughness: .05, metalness: .8 })); l.rotation.x = Math.PI / 2; l.position.set(lx, ly, 6); phone.add(l); }
  }

  // thin MacBook-style laptop on the bench, off to the side (outside CNC + arm reach), showing the LabRat console
  _buildLaptop() {
    const g = this.laptop = new THREE.Group(); g.name = 'laptop'; this.stage.add(g);
    g.position.set(-640, 0, -230); g.rotation.y = -0.45; // operator's right, beyond so_b, angled toward the operator
    const W = 304, D = 212, T = 15, r = 9;
    const rr = (w, h, rad) => { const sh = new THREE.Shape(), x = -w / 2, y = -h / 2; sh.moveTo(x + rad, y); sh.lineTo(x + w - rad, y); sh.quadraticCurveTo(x + w, y, x + w, y + rad); sh.lineTo(x + w, y + h - rad); sh.quadraticCurveTo(x + w, y + h, x + w - rad, y + h); sh.lineTo(x + rad, y + h); sh.quadraticCurveTo(x, y + h, x, y + h - rad); sh.lineTo(x, y + rad); sh.quadraticCurveTo(x, y, x + rad, y); return sh; };
    const alu = new THREE.MeshStandardMaterial({ color: 0xc9ccd1, metalness: .85, roughness: .32 });
    const slab = (w, h, t, rad, bev) => { const geo = new THREE.ExtrudeGeometry(rr(w, h, rad), { depth: t - 2 * bev, bevelEnabled: true, bevelThickness: bev, bevelSize: bev, bevelSegments: 3, curveSegments: 8 }); geo.translate(0, 0, bev); return geo; };
    // unibody base: lies flat (shape XY -> XZ), tapered from 15 mm at the hinge to 9 mm at the front edge
    const bg = slab(W, D, T, r, 1.5); bg.rotateX(-Math.PI / 2);
    const pos = bg.attributes.position; for (let i = 0; i < pos.count; i++) { const z = pos.getZ(i); pos.setY(i, pos.getY(i) * Math.min(1, Math.max(0.6, 1 - (D / 2 - z) / D * 0.4))); }
    bg.computeVertexNormals(); const base = new THREE.Mesh(bg, alu); g.add(base);
    // keyboard well + individual keys + trackpad (deck top ~ 13 mm at the keyboard)
    const keyTop = 12.4, kb = new THREE.Group(); g.add(kb);
    this._box(272, .6, 112, 0x1b1c1f, new THREE.Vector3(0, keyTop, 52), kb, { roughness: .8 });
    const keyM = new THREE.MeshStandardMaterial({ color: 0x141518, roughness: .6 }), keyG = new THREE.BoxGeometry(15.5, 1.2, 14.5);
    const rows = [[14, 0], [14, 0], [13, 9], [12, 14], [11, 20]];
    rows.forEach(([n, off], ri) => { for (let c = 0; c < n; c++) { const k = new THREE.Mesh(keyG, keyM); k.position.set(-127 + off + c * 18.6 + (ri > 1 ? 4 : 0), keyTop + .9, 6 + ri * 18.4 + 14); kb.add(k); } });
    const space = new THREE.Mesh(new THREE.BoxGeometry(92, 1.2, 14.5), keyM); space.position.set(0, keyTop + .9, 6 + 5 * 18.4 + 14); kb.add(space);
    const fn = new THREE.Mesh(new THREE.BoxGeometry(15.5, 1.2, 8), keyM); for (let c = 0; c < 14; c++) { const k = fn.clone(); k.position.set(-121 + c * 18.6, keyTop + .9, 10); kb.add(k); }
    this._box(130, .4, 78, 0xb9bdc3, new THREE.Vector3(0, 10.3, -55), g, { metalness: .5, roughness: .22 });
    // lid: 6 mm, hinge at the back edge, open ~110 deg
    const hinge = new THREE.Mesh(new THREE.CylinderGeometry(4.5, 4.5, 250, 20), new THREE.MeshStandardMaterial({ color: 0x2a2c30, metalness: .6, roughness: .4 }));
    hinge.rotation.z = Math.PI / 2; hinge.position.set(0, 13, D / 2 - 3); g.add(hinge);
    const lid = new THREE.Group(); lid.position.set(0, 14, D / 2 - 3); lid.rotation.x = THREE.MathUtils.degToRad(20); g.add(lid);
    const lg = slab(W, D, 6, r, 1); lg.translate(0, D / 2, -6); const shell = new THREE.Mesh(lg, alu); lid.add(shell);
    // shell back is +Z (away from user); flip so the screen faces the user (-Z of the group = front)
    lid.rotation.y = 0;
    const bezel = new THREE.Mesh(new THREE.ShapeGeometry(rr(W - 4, D - 4, r - 1)), new THREE.MeshStandardMaterial({ color: 0x0a0a0b, roughness: .15 }));
    bezel.position.set(0, D / 2, -6.05); bezel.rotation.y = Math.PI; lid.add(bezel);
    const scrM = new THREE.MeshStandardMaterial({ color: 0x000000, emissive: 0xffffff, emissiveIntensity: .95, roughness: .12 });
    new THREE.TextureLoader().load(new URL('./models/laptop/console.jpg', import.meta.url).href, (t) => { t.colorSpace = THREE.SRGBColorSpace; scrM.emissiveMap = t; scrM.needsUpdate = true; });
    const scr = new THREE.Mesh(new THREE.PlaneGeometry(284, 184), scrM); scr.position.set(0, D / 2 + 3, -6.15); scr.rotation.y = Math.PI; lid.add(scr);
    this._box(6, 3, .3, 0x111111, new THREE.Vector3(0, D - 4, -6.2), lid); // camera notch
  }

  // lab room: light floor, off-white back wall with a shelf of lab items, white bench on a steel frame + drawer cabinet
  _buildLab() {
    const s = this.scene, lab = this.lab = new THREE.Group(); lab.name = 'lab'; s.add(lab);
    const M = (c, o = {}) => new THREE.MeshStandardMaterial({ color: c, roughness: .7, metalness: 0, ...o });
    const add = (geo, mat, x, y, z, parent = lab) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); m.castShadow = m.receiveShadow = true; parent.add(m); return m; };
    const BX = 1800, BZ = 900, BC = 30, WALL_Z = 560;
    // floor (light vinyl) + wall + skirting
    const floor = add(new THREE.PlaneGeometry(8000, 8000), M(0xd9d6d0, { roughness: .85 }), 0, 0, 0); floor.rotation.x = -Math.PI / 2; floor.castShadow = false;
    const wall = add(new THREE.PlaneGeometry(8000, 3200), M(0xf3f1ec, { roughness: .95 }), 0, 1600, WALL_Z); wall.rotation.y = Math.PI; wall.castShadow = false;
    add(new THREE.BoxGeometry(8000, 90, 12), M(0xe2e0db), 0, 45, WALL_Z - 6);
    // bench: white laminate top 1800x900x30 with a light grey edge, powder-coated steel frame
    add(new THREE.BoxGeometry(BX, BC, BZ), M(0xf7f7f5, { roughness: .45 }), 0, BENCH_H - BC / 2, 20);
    add(new THREE.BoxGeometry(BX + 2, 4, BZ + 2), M(0xc9ccd0, { roughness: .5 }), 0, BENCH_H - BC + 2, 20);
    const steel = M(0x9aa1a9, { metalness: .6, roughness: .4 });
    for (const x of [-BX / 2 + 40, BX / 2 - 40]) for (const z of [20 - BZ / 2 + 40, 20 + BZ / 2 - 40]) add(new THREE.BoxGeometry(50, BENCH_H - BC, 50), steel, x, (BENCH_H - BC) / 2, z);
    for (const z of [20 - BZ / 2 + 40, 20 + BZ / 2 - 40]) add(new THREE.BoxGeometry(BX - 80, 50, 40), steel, 0, BENCH_H - BC - 25, z);
    for (const x of [-BX / 2 + 40, BX / 2 - 40]) add(new THREE.BoxGeometry(40, 40, BZ - 80), steel, x, 160, 20);
    // drawer cabinet under the left end
    const cab = M(0xe9ebee, { roughness: .5 }), cx = BX / 2 - 300;
    add(new THREE.BoxGeometry(460, BENCH_H - BC - 70, BZ - 120), cab, cx, (BENCH_H - BC - 70) / 2 + 20, 40);
    for (let i = 0; i < 4; i++) {
      const y = 70 + i * 190; add(new THREE.BoxGeometry(440, 176, 6), M(0xf4f5f6, { roughness: .45 }), cx, y + 88, 40 - (BZ - 120) / 2 - 3);
      add(new THREE.BoxGeometry(160, 12, 14), steel, cx, y + 150, 40 - (BZ - 120) / 2 - 12);
    }
    // wall shelf with lab items
    const SH_Y = BENCH_H + 430, SH_D = 240, shZ = WALL_Z - SH_D / 2;
    add(new THREE.BoxGeometry(1300, 22, SH_D), M(0xf8f8f6, { roughness: .5 }), 0, SH_Y, shZ);
    for (const x of [-560, 560]) { add(new THREE.BoxGeometry(14, 160, 14), steel, x, SH_Y - 90, WALL_Z - 7); add(new THREE.BoxGeometry(14, 14, SH_D - 20), steel, x, SH_Y - 18, shZ); }
    const top = SH_Y + 11;
    const glass = new THREE.MeshPhysicalMaterial({ color: 0xffffff, roughness: .05, transmission: .9, thickness: 3, ior: 1.5, transparent: true, opacity: .55 });
    const amber = new THREE.MeshPhysicalMaterial({ color: 0x9a4f12, roughness: .15, transmission: .35, transparent: true, opacity: .9 });
    const bottle = (x, z, mat, cap, h = 170, rad = 38) => {
      add(new THREE.CylinderGeometry(rad, rad, h, 36), mat, x, top + h / 2, z);
      add(new THREE.CylinderGeometry(rad * .55, rad, 26, 36), mat, x, top + h + 13, z);
      add(new THREE.CylinderGeometry(rad * .5, rad * .5, 30, 28), M(cap, { roughness: .4 }), x, top + h + 41, z);
      add(new THREE.BoxGeometry(rad * 1.3, h * .45, 1), M(0xffffff, { roughness: .9 }), x, top + h * .5, z - rad - .3);
    };
    bottle(-480, shZ, amber, 0x1f5fbf); bottle(-385, shZ + 10, amber, 0x1f5fbf, 140, 32);
    bottle(-280, shZ - 5, new THREE.MeshPhysicalMaterial({ color: 0xe8f1ff, roughness: .08, transmission: .85, transparent: true, opacity: .6 }), 0xd23b2f, 200, 44);
    // beaker with blue liquid
    const bk = add(new THREE.CylinderGeometry(48, 46, 130, 40, 1, true), glass, -120, top + 65, shZ); bk.material.side = THREE.DoubleSide;
    add(new THREE.CylinderGeometry(44, 44, 70, 40), M(0x3a7fd9, { roughness: .1, transparent: true, opacity: .75 }), -120, top + 36, shZ);
    // box of pipette tips (blue rack, lid open)
    const tb = new THREE.Group(); tb.position.set(110, top, shZ); lab.add(tb);
    add(new THREE.BoxGeometry(122, 70, 82), M(0x2f6fd0, { roughness: .35 }), 0, 35, 0, tb);
    add(new THREE.BoxGeometry(118, 3, 78), M(0xeef3fb, { roughness: .4 }), 0, 71, 0, tb);
    const tg = new THREE.ConeGeometry(2.8, 48, 10); tg.rotateX(Math.PI);
    const tm = M(0xf2efe2, { roughness: .3 }); const inst = new THREE.InstancedMesh(tg, tm, 96); const mm = new THREE.Matrix4();
    for (let i = 0; i < 96; i++) { mm.makeTranslation(-49.5 + (i % 12) * 9, 72 + 22, -31.5 + Math.floor(i / 12) * 9); inst.setMatrixAt(i, mm); } inst.castShadow = true; tb.add(inst);
    const lidm = add(new THREE.BoxGeometry(124, 4, 84), M(0x7fb0f0, { transparent: true, opacity: .55, roughness: .2 }), 0, 0, 0, tb); lidm.position.set(0, 72 + 40, 42 + 2); lidm.rotation.x = -1.2;
    // second shelf items: a stack of petri dishes and a small box
    for (let i = 0; i < 4; i++) add(new THREE.CylinderGeometry(45, 45, 12, 36), glass, 300, top + 6 + i * 13, shZ);
    add(new THREE.BoxGeometry(150, 110, 120), M(0xf2f2ef, { roughness: .8 }), 470, top + 55, shZ);
    add(new THREE.BoxGeometry(110, 40, 1), M(0x2f6fd0), 470, top + 70, shZ - 60.6);
    // soft ceiling light panels (emissive) above the bench
    for (const x of [-600, 0, 600]) { const p = add(new THREE.BoxGeometry(560, 12, 560), M(0xffffff, { emissive: 0xffffff, emissiveIntensity: 1.2 }), x, 3000, 0); p.castShadow = false; }
  }

  // SO-101 as a simple 6-joint chain (fallback until the URDF model loads)
  _buildSO101(pos, color, phone) {
    const root = new THREE.Group(); root.position.copy(pos); this.stage.add(root);
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
    const g = new THREE.Group(); g.position.copy(pos); this.stage.add(g);
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
    if (a.so101) { const qq = {}; for (const [j, v] of Object.entries(q)) qq[j] = (v == null || JOINT_ZERO[j] == null) ? v : +v + JOINT_ZERO[j]; setSO101(a.so101, qq); return; }
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
    const tipKnown = p.tip !== undefined || p.has_tip !== undefined; // live bridge state has no tip flag
    const tip = tipKnown ? !!(p.tip ?? p.has_tip) : this.headTip.visible;
    this.headTip.visible = tip; if (this.cncNodes?.tip) this.cncNodes.tip.visible = tip;
    const v = +(p.vol_ul ?? p.volume ?? 0);
    this.headLiquid.visible = tip && v > 0.5; this.headLiquid.scale.y = Math.max(.1, Math.min(18, v / 15)); this.headLiquid.position.y = -20 + this.headLiquid.scale.y / 2;
    if (p.rgb || p.color) { const c = parseColor(p.rgb || p.color); if (c) this.headLiquid.material.color.copy(c); }
    if (p.pos_mm != null) this.setPlunger(+p.pos_mm, +(p.stroke_mm ?? SYR_STROKE));
    if (typeof p.tip === 'string' && this.tips[p.tip]) this.tips[p.tip].visible = false;
  }
  // plunger height above its lowest point (bridge pipette.pos_mm): rack slides, pinion turns (GLB modelled at the top)
  setPlunger(pos, stroke = SYR_STROKE) {
    const d = Math.max(0, Math.min(stroke, pos)) - stroke;
    this.plunger.position.y = 78 + 12 * d / stroke;
    if (this.cncNodes?.rack) this.cncNodes.rack.position.y = d;
    if (this.cncNodes?.pinion) this.cncNodes.pinion.rotation.z = d / SYR_PITCH_R;
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
