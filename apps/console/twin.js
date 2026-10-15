// LabRat digital twin: CNC 3018 + syringe pipette head, deck (FT-200L-RS tip rack, printed beaker matrix), 2x SO-101
// and the phone stand.
// Units: mm. Deck coords (x right 0..300, y back 0..180) map to three (X = x-150, Z = y-90). Up = +Y.
// The desk is y=0. The CNC (models/cnc3018.glb, built by hardware/cnc3018/cnc3018.py) sits in this.cncRoot,
// raised so its bed top (= deck plane) is at cncRoot y=0. Like a real 3018 the BED moves in Y (deck items ride
// on it), the X carriage moves along the gantry, and cnc pos is the SPINDLE axis. The rack-and-pinion syringe
// (hardware/tool-head) replaces the spindle motor in its clamp, so the tip is on that axis (deck_cal pipette_offset 0).
// Look (apps/console/twin/): layers.js = procedural FDM layer lines on printed parts, labware.js = glass vials with
// screw threads, liquid with meniscus + Beer-Lambert tint, hollow PP tips, rigs.js = probe arm / pan-tilt stand,
// post.js = MSAA + GTAO contact shadows (?twin=low|high, auto-low when slow).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { loadSO101, setSO101 } from './so101.js';
import { applyLayerLines, applyLayerLinesTree } from './twin/layers.js';
import { clearMaterial, liquidMaterial, setLiquidColor, vialGeometry, capGeometry, VialLiquid, VIAL, tipGeometry, tipLiquidGeometry, TIP } from './twin/labware.js';
import { NodeRotor, loadPhoneStand } from './twin/rigs.js';
import { makeComposer } from './twin/post.js';
import { toCreasedNormals } from 'three/addons/utils/BufferGeometryUtils.js';

const ROWS = 'ABCDEFGH';
const SAFE_H = 55; // tip height above deck at cnc z=0 (hardware/tool-head syrhead/spec.py safe_tip_h)
const BED_H = 72; // desk -> bed top (GLB feet bottom at -72)
const PIP_DY = 0; // pipette tip axis vs spindle axis (deck y): coaxial syringe head
const SYR_STROKE = 30; // hardware/tool-head spec.stroke; node motion per mm comes from models/syringe_rig.json (cnc3018.py)
const CLEAR_MATS = { syr_pp_clear: .35, acrylic_clear: .22, polycarbonate_clear: .22, syr_tip_yellow: .8 }; // see-through finishes in the GLB
// print direction (object space of the GLB meshes, three Y-up) per printed material; a node's userData.print_up
// (glTF extras {"print_up":[x,y,z]}) overrides it
const PRINT_UP = { petg_print: [0, 1, 0], print_white: [0, 1, 0], syr_petg_orange: [0, 0, 1], syr_petg_white: [1, 0, 0], print_black: [1, 0, 0] };
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
// deck = hardware/deck/deck.json (DECK-001 beaker matrix, FT-200L-RS tip rack); cloud/src/sim.ts uses the same numbers
export const DECK = {
  tip_rack: { x: 17, y: 19.25, pitch: 9, box: [8, 10], l: 117, w: 81.5, plate_h: 55.4, rim_h: 57 },
  plate: { x: 153.25, y: 32, pitch: 19, rows: 6, cols: 7, cap: 5000 },        // mixing beakers A1-F7
  matrix: { cols: 8, ext: [136.4, 296.1, 15.2, 136.8], top: 11.2, floor: 1.2 },
  beaker: { od: 15.3, id: 13.5, h: 50, bottom: 1.2, ul_per_mm: Math.PI / 4 * 13.5 ** 2 },
  reservoirs: [['water', 32], ['red', 51], ['yellow', 70], ['blue', 89]].map(([name, y]) => ({ name, x: 153.25 + 7 * 19, y })),
  res_fill: 4500,
  waste: { x: 280, y: 170 }, eject_bar: { x: 10, y: 170 },
};
const RES_COLORS = ['#cfe8ff', '#e0312b', '#f2c81d', '#2a5bd7']; // water, red, yellow, blue

const P = (x, y) => new THREE.Vector3(x - 150, 0, y - 90);

// CAD GLBs ship without normals (GLTFLoader then flat-shades): give them creased normals (smooth cylinders and
// fillets, sharp box edges). Also needed by the AO G-buffer, which renders with a normal material.
function smoothCAD(root, crease = 32) {
  const mats = new Set();
  root.traverse((m) => {
    if (!m.isMesh || m.geometry.attributes.normal) return;
    try { m.geometry = toCreasedNormals(m.geometry, THREE.MathUtils.degToRad(crease)); } catch { m.geometry.computeVertexNormals(); }
    (Array.isArray(m.material) ? m.material : [m.material]).forEach((x) => mats.add(x));
  });
  for (const x of mats) { x.flatShading = false; x.needsUpdate = true; }
}

// w x d slab, h tall (y 0..h), with round through-holes at local (x, z); optional bevel on all edges incl. the holes
function holedSlab(w, d, h, holes, r, segs = 24, bevel = 0) {
  const sh = new THREE.Shape(); const b = bevel;
  sh.moveTo(-w / 2 + b, -d / 2 + b); sh.lineTo(w / 2 - b, -d / 2 + b); sh.lineTo(w / 2 - b, d / 2 - b); sh.lineTo(-w / 2 + b, d / 2 - b); sh.closePath();
  for (const [x, z] of holes) { const hp = new THREE.Path(); hp.absarc(x, -z, r + b, 0, Math.PI * 2, true); sh.holes.push(hp); }
  const g = new THREE.ExtrudeGeometry(sh, { depth: h - 2 * b, curveSegments: segs, bevelEnabled: b > 0, bevelThickness: b, bevelSize: b, bevelSegments: 2 });
  g.translate(0, 0, b); g.rotateX(-Math.PI / 2); return g;
}

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
    r.toneMapping = THREE.NeutralToneMapping; r.toneMappingExposure = 1.0;
    r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFSoftShadowMap;
    el.appendChild(r.domElement);
    const s = this.scene = new THREE.Scene();
    s.background = new THREE.Color(0xeef0f2);
    s.fog = new THREE.Fog(0xeef0f2, 3200, 7000);
    try { // studio environment so anodised alu and steel read as metal
      const pm = new THREE.PMREMGenerator(r); s.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture; s.environmentIntensity = 0.5;
    } catch (e) { console.warn('env map', e); }
    this.camera = new THREE.PerspectiveCamera(38, 1, 1, 9000);
    this.camera.position.set(0, BENCH_H + 620, -1250);
    this.controls = new OrbitControls(this.camera, r.domElement);
    this.controls.target.set(-60, BENCH_H + 110, -60);
    this.controls.enableDamping = true; this.controls.maxPolarAngle = Math.PI * 0.49;
    this.stage = new THREE.Group(); this.stage.name = 'bench_top'; this.stage.position.y = BENCH_H; s.add(this.stage);
    // bright, airy lab: sky/bounce hemisphere, soft overhead daylight key (soft shadows), window fill
    s.add(new THREE.HemisphereLight(0xffffff, 0xcfc8bc, 0.6));
    const d = this.sun = new THREE.DirectionalLight(0xfff6e8, 3.0); d.position.set(-650, BENCH_H + 1500, -700); d.target = this.stage;
    d.castShadow = true; d.shadow.mapSize.set(4096, 4096); d.shadow.bias = -0.0002; d.shadow.normalBias = 0.35; d.shadow.radius = 6; d.shadow.blurSamples = 16;
    Object.assign(d.shadow.camera, { left: -1000, right: 1000, top: 1000, bottom: -1000, near: 200, far: 3500 }); d.shadow.camera.updateProjectionMatrix(); s.add(d);
    const fill = new THREE.DirectionalLight(0xe6efff, 0.55); fill.position.set(900, BENCH_H + 600, -900); s.add(fill);
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
    this._buildPhoneStand(); this._buildLaptop();
    this._shadows(this.scene);
    this._loadCNC(); this._loadArms(); this._loadStand();
    try { window.__twin = this; } catch { /* no window */ }
    this.setCNC({ x: 150, y: 90, z: 0 });
    this.setPipette({ vol_ul: 0, tip: false });
    for (const k in this.arms) this.setArm(k, { shoulder_pan: 0, shoulder_lift: 0, elbow_flex: 0, wrist_flex: 0, wrist_roll: 0, gripper: 50 });
    // 'high' = MSAA + GTAO contact shadows + 4k sun shadow; 'low' = plain forward render (auto-selected when frames
    // average > 45 ms for ~3 s). Override with ?twin=low|high or twin.setQuality().
    try { this.post = makeComposer(r, s, this.camera); } catch (e) { console.warn('twin post-processing unavailable', e); }
    const qp = (() => { try { return new URLSearchParams(location.search).get('twin'); } catch { return null; } })();
    this.quality = 'high'; this._autoQ = !qp; if (qp) this.setQuality(qp);
    new ResizeObserver(() => this.resize()).observe(el);
    this.resize();
    const t0 = performance.now(); let last = t0, acc = 0, n = 0, bad = 0;
    const loop = () => {
      requestAnimationFrame(loop);
      const now = performance.now(), dt = now - last; last = now;
      this.controls.update();
      { // keep the depth range tight around what we look at (AO + shadows need depth precision; close-ups need a small near)
        const c = this.camera, d = c.position.distanceTo(this.controls.target), nr = Math.max(0.5, d / 150), fr = Math.max(6000, d * 6);
        if (Math.abs(c.near - nr) > nr * 0.1 || c.far !== fr) { c.near = nr; c.far = fr; c.updateProjectionMatrix(); }
      }
      if (this.post && this.quality === 'high') this.post.render(); else r.render(s, this.camera);
      if (this._autoQ && now - t0 > 8000 && dt < 1000 && document.visibilityState !== 'hidden') { acc += dt; n++; if (n >= 90) { bad = acc / n > 45 ? bad + 1 : 0; if (bad >= 2 && this.quality === 'high') { console.info(`twin: ${(acc / n).toFixed(0)} ms/frame, switching to low quality`); this.setQuality('low'); } acc = 0; n = 0; } }
    };
    loop();
  }
  resize() {
    const w = this.el.clientWidth || 1, h = this.el.clientHeight || 1; this.renderer.setSize(w, h); this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    this.post?.setSize(w, h);
  }
  setQuality(q) {
    this.quality = q === 'low' ? 'low' : 'high';
    const hi = this.quality === 'high', sh = this.sun.shadow, ms = hi ? 4096 : 2048;
    if (sh.mapSize.x !== ms) { sh.mapSize.set(ms, ms); sh.map?.dispose(); sh.map = null; }
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, hi ? 2 : 1)); this.resize();
  }

  _mat(c, o = {}) { return new THREE.MeshStandardMaterial({ color: c, roughness: .6, metalness: .1, ...o }); }
  _box(w, h, d, c, pos, parent = this.scene, o) { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), this._mat(c, o)); m.position.copy(pos); parent.add(m); return m; }

  _buildDeck() {
    // bed
    this.bedBox = this._box(320, 6, 200, 0x111111, new THREE.Vector3(0, -3, 0), this.deck);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(300, 0.1, 180)), new THREE.LineBasicMaterial({ color: 0x2a2a2a }));
    edges.position.y = 0.2; this.deck.add(edges);
    // FT-200L-RS tip rack: blue tray, white deck plate with 96 holes, 96 hollow PP tips hanging from their collars
    const tr = DECK.tip_rack, rackC = P(tr.box[0] + tr.l / 2, tr.box[1] + tr.w / 2);
    const tray = new THREE.Mesh(new THREE.BoxGeometry(tr.l, tr.plate_h - 1.5, tr.w), clearMaterial({ color: 0x2f6fe0, opacity: .55, roughness: .3, edge: .35, diffuse: .9, envMapIntensity: .8 }));
    tray.position.set(rackC.x, (tr.plate_h - 1.5) / 2, rackC.z); tray.renderOrder = 3; tray.name = 'tip_tray'; this.deck.add(tray); // translucent blue PP tray
    const tipXY = []; for (let r = 0; r < 8; r++) for (let c = 0; c < 12; c++) tipXY.push([ROWS[r] + (c + 1), P(tr.x + c * tr.pitch, tr.y + r * tr.pitch)]);
    const plate = new THREE.Mesh(holedSlab(tr.l - .4, tr.w - .4, 1.5, tipXY.map(([, p]) => [p.x - rackC.x, p.z - rackC.z]), 3.45, 16), this._mat(0xeef3fb, { roughness: .4 }));
    plate.position.set(rackC.x, tr.plate_h - 1.5, rackC.z); this.deck.add(plate);
    const tipInst = this.rackTips = new THREE.InstancedMesh(tipGeometry(28), this.tipMat = clearMaterial({ color: 0xf8e27c, opacity: .22, roughness: .28, edge: .45, diffuse: .7, envMapIntensity: .9 }), 96);
    tipInst.name = 'rack_tips'; tipInst.renderOrder = 2; this.deck.add(tipInst);
    const mm = new THREE.Matrix4(), zero = new THREE.Matrix4().makeScale(0, 0, 0);
    tipXY.forEach(([k, p], i) => {
      const on = mm.clone().makeTranslation(p.x, tr.rim_h, p.z); tipInst.setMatrixAt(i, on);
      this.tips[k] = { i, on, shown: true };
    });
    this._tipZero = zero;
    // printed beaker matrix (DECK-001) with blind pockets + 15.3 x 50 mm screw-top glass vials; columns 1-7 mix,
    // column 8 = stocks (A8-D8 water/red/yellow/blue, E8/F8 spare, capped)
    const mx = DECK.matrix, bk = DECK.beaker, pl = DECK.plate;
    const mC = P((mx.ext[0] + mx.ext[1]) / 2, (mx.ext[2] + mx.ext[3]) / 2);
    const vialXY = []; for (let r = 0; r < pl.rows; r++) for (let c = 0; c < pl.cols + 1; c++) vialXY.push([ROWS[r] + (c + 1), P(pl.x + c * pl.pitch, pl.y + r * pl.pitch), c === pl.cols ? r : -1]);
    const holderMat = this._mat(0xe9e6dc, { roughness: .62 }); holderMat.name = 'print_white_holder';
    const holder = new THREE.Group(); holder.name = 'beaker_matrix'; holder.position.set(mC.x, 0, mC.z); this.deck.add(holder);
    const hw = mx.ext[1] - mx.ext[0], hd = mx.ext[3] - mx.ext[2];
    const block = new THREE.Mesh(holedSlab(hw, hd, mx.top - mx.floor, vialXY.map(([, p]) => [p.x - mC.x, p.z - mC.z]), bk.od / 2 + .25, 28, 1.2), holderMat);
    block.position.y = mx.floor; holder.add(block);
    const floor = new THREE.Mesh(new THREE.BoxGeometry(hw, mx.floor, hd), holderMat); floor.position.y = mx.floor / 2; holder.add(floor);
    for (const m of [block, floor]) { m.userData.print_up = [0, 1, 0]; applyLayerLines(m, { up: [0, 1, 0] }); }
    const glassGeo = vialGeometry(VIAL, 40), glassMat = this.glassMat = clearMaterial({ color: 0xffffff, opacity: .07, roughness: .03, edge: .5, diffuse: .12, envMapIntensity: 1.2 });
    const capGeo = capGeometry(96), capMat = this._mat(0x1d1f24, { roughness: .55 });
    const floorH = mx.floor + bk.bottom;
    this.vials = [];
    const vialAt = (key, p, color, vol, cap) => {
      const g = new THREE.Mesh(glassGeo, glassMat); g.position.set(p.x, mx.floor, p.z); g.renderOrder = 2; g.name = 'vial_' + key; this.deck.add(g);
      if (cap) { const c = new THREE.Mesh(capGeo, capMat); c.position.set(p.x, mx.floor + VIAL.h - 8.4, p.z); c.castShadow = c.receiveShadow = true; this.deck.add(c); }
      const liq = new VialLiquid(liquidMaterial(color ?? 0xcfe8ff, { thickness: 5 }), VIAL, 32);
      liq.mesh.position.set(p.x, floorH, p.z); liq.mesh.name = 'liquid_' + key; this.deck.add(liq.mesh); liq.setLevel((vol || 0) / bk.ul_per_mm);
      const v = { key, p, x: null, liq, color: new THREE.Color(color ?? 0xcfe8ff), vol: vol || 0 }; this.vials.push(v); return v;
    };
    this.reservoirs = [];
    for (const [k, p, res] of vialXY) {
      if (res < 0) { this.wells[k] = vialAt(k, p, 0xcfe8ff, 0); continue; }
      const rv = DECK.reservoirs[res];
      if (rv) { const v = vialAt(k, p, RES_COLORS[res], DECK.res_fill); v.name = rv.name; this.reservoirs.push(v); this.wells[k] = v; }
      else vialAt(k, p, null, 0, true); // E8, F8: spare stock vials, capped
    }
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

  // high-quality 3018 PRO model (hardware/cnc3018/cnc3018.py). Node origins = machine centre (x150,y90,z0), so the
  // driven transforms are plain offsets. On failure the box gantry stays as a fallback.
  _loadCNC() {
    new GLTFLoader().load(new URL('./models/cnc3018.glb', import.meta.url).href, (g) => {
      const root = g.scene; root.name = 'cnc3018';
      const n = (k) => root.getObjectByName(k);
      const nodes = { base: n('cnc_base'), bed: n('cnc_y_bed'), xc: n('cnc_x_carriage'), z: n('cnc_z'), tip: n('pipette_tip'),
        syr: ['syringe_carriage', 'syringe_gear_a', 'syringe_gear_b', 'syringe_gear_c'].map((k) => [k, n(k)]).filter(([, o]) => o) };
      if (!nodes.bed || !nodes.xc || !nodes.z) { console.warn('cnc3018.glb: missing nodes, keeping fallback'); return; }
      smoothCAD(root);
      root.traverse((m) => {
        if (!m.isMesh) return; m.castShadow = true; m.receiveShadow = true;
        const mt = m.material; if (mt && CLEAR_MATS[mt.name] != null) { mt.transparent = true; mt.opacity = CLEAR_MATS[mt.name]; mt.depthWrite = false; m.castShadow = false; }
      });
      // clear PP syringe barrel / PC window: real clear plastic instead of flat alpha
      const clear = {}; root.traverse((m) => { const nm = m.material?.name; if (!m.isMesh || !(nm in CLEAR_MATS) || nm === 'syr_tip_yellow') return;
        clear[nm] ??= clearMaterial({ color: m.material.color, opacity: CLEAR_MATS[nm] * .3, roughness: nm === 'syr_pp_clear' ? .12 : .04, edge: .45, diffuse: nm === 'syr_pp_clear' ? .5 : .1 }); m.material = clear[nm]; });
      // FDM layer lines on every printed part, along each part's print direction (hardware/tool-head/README.md):
      // body/clamp upright (Y), carriage on its back face (machine Y = three Z), gears and TM modules on their side (X)
      applyLayerLinesTree(root, { upByMaterial: PRINT_UP });
      this.cncRoot.add(root);
      this.fbGantry.visible = false; this.bedBox.visible = false;
      // keep the twin's liquid indicator; hang it on cnc_z at the pipette tip (pipette axis at Z = PIP_DY)
      nodes.z.add(this.head); this.head.position.set(0, SAFE_H + 22, PIP_DY);
      for (const m of [this.headBody, this.plunger, this.headShaft]) m.visible = false;
      this.headTip.material.visible = false; this.headLiquid.material.visible = false;
      if (nodes.tip) { // replace the CAD tip's look: hollow PP tip + the liquid it holds (top rim at the GLB tip top)
        const box = new THREE.Box3(); nodes.tip.updateWorldMatrix(true, true);
        nodes.tip.traverse((m) => { if (m.isMesh) { m.geometry.computeBoundingBox(); box.union(m.geometry.boundingBox); m.visible = false; } });
        const top = Number.isFinite(box.max.y) ? box.max.y : SAFE_H + TIP.len;
        const tip = new THREE.Mesh(tipGeometry(40), this.tipMat); tip.position.set((box.min.x + box.max.x) / 2 || 0, top, (box.min.z + box.max.z) / 2 || 0); tip.renderOrder = 2; tip.name = 'tip_pp';
        this.tipLiquid = new THREE.Mesh(new THREE.BufferGeometry(), liquidMaterial(0xff8800, { thickness: 2.5 })); this.tipLiquid.visible = false; tip.add(this.tipLiquid);
        nodes.tip.add(tip); this.headTipMesh = tip;
      }
      nodes.tip.visible = this.headTip.visible;
      this.cncNodes = nodes; this.cncModel = root;
      this.probe = this._rigProbe(); if (this.probeState) this.setProbe(this.probeState);
      for (const [, o] of nodes.syr) o.userData.rest = { y: o.position.y, rx: o.rotation.x, ry: o.rotation.y, rz: o.rotation.z };
      fetch(new URL('./models/syringe_rig.json', import.meta.url).href).then((r) => r.json()).then((rig) => { this.syrRig = rig; this.probe = this._rigProbe(); if (this.probeState) this.setProbe(this.probeState); if (this.plungerPos != null) this.setPlunger(this.plungerPos); }).catch((e) => console.warn('syringe_rig.json', e));
      if (this.cnc) this.setCNC(this.cnc);
    }, undefined, (e) => console.warn('cnc3018.glb failed to load, using box fallback', e));
  }

  // official SO-101 URDF + meshes (models/so101, Apache-2.0); fallback = simple chains
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
    new THREE.TextureLoader().load(new URL('./models/laptop_screen.jpg', import.meta.url).href, (t) => { t.colorSpace = THREE.SRGBColorSpace; scrM.emissiveMap = t; scrM.needsUpdate = true; });
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
    add(new THREE.BoxGeometry(BX, BC, BZ), M(0xecebe7, { roughness: .45 }), 0, BENCH_H - BC / 2, 20);
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
    let c = (p.rgb || p.color) ? parseColor(p.rgb || p.color) : null;
    // frames carry no tip colour: an aspirate takes the colour of the vial under the tip
    if (!c && v > (this.tipVol || 0) + 0.5) { const src = this._vialNear(this.cnc); if (src && src.vol > 0) c = src.color; }
    if (!c && v <= 0.5) this.tipColor = null;
    if (c) { this.tipColor = c.clone(); this.headLiquid.material.color.copy(c); }
    this._setTipLiquid(tip ? v : 0);
    this.tipVol = v;
    if (p.pos_mm != null) this.setPlunger(+p.pos_mm, +(p.stroke_mm ?? SYR_STROKE));
    if (typeof p.tip === 'string') this._showTip(p.tip, false);
  }
  _setTipLiquid(v) {
    const m = this.tipLiquid; if (!m) return;
    m.visible = v > 0.5; if (!m.visible) return;
    const key = Math.round(v * 2) / 2; if (m.userData.v !== key) { m.geometry.dispose(); m.geometry = tipLiquidGeometry(key); m.userData.v = key; }
    const col = this.tipColor || new THREE.Color(0xcfe8ff); if (!m.material.userData.color?.equals(col)) setLiquidColor(m.material, col);
  }
  _vialNear(c) {
    if (!c || c.x == null || c.y == null || !this.vials) return null;
    const p = P(+c.x, +c.y); let best = null, bd = 8 * 8;
    for (const v of this.vials) { const d = (v.p.x - p.x) ** 2 + (v.p.z - p.z) ** 2; if (d < bd) { bd = d; best = v; } }
    return best;
  }
  // plunger height above the bottomed stopper (bridge pipette.pos_mm): the carriage slides, the 12:1 train turns
  // (per-mm node motion from models/syringe_rig.json; the GLB is modelled at pos 0)
  setPlunger(pos, stroke = SYR_STROKE) {
    const p = Math.max(0, Math.min(stroke, pos)); this.plungerPos = p;
    this.plunger.position.y = 66 + 12 * p / stroke;
    if (!this.cncNodes || !this.syrRig) return;
    for (const [k, o] of this.cncNodes.syr) {
      const r = this.syrRig.nodes[k]; if (!r) continue;
      if (r.prop === 'position.y') o.position.y = o.userData.rest.y + r.per_mm * p;
      else { const ax = r.prop.split('.')[1]; o.rotation[ax] = o.userData.rest['r' + ax] + r.per_mm * p; }   // rotation.x|y|z
    }
  }
  // wells: {A1: {vol|vol_ul, rgb|color}, ...}; column-8 stocks also by A8-D8 or reservoir name (water/red/...)
  setWells(wells) {
    if (!wells) return;
    for (const [k0, w] of Object.entries(wells)) {
      const k = String(k0).replace(/^reservoir[.:]/i, '');
      const v = this.wells[k] || this.reservoirs.find((r) => r.name === k.toLowerCase()); if (!v || !w) continue;
      const vol = +(w.vol ?? w.vol_ul ?? w.volume ?? v.vol);
      const c = parseColor(w.rgb ?? w.color);
      if (c && !c.equals(v.color)) { v.color.copy(c); setLiquidColor(v.liq.mesh.material, c); }
      v.vol = Math.max(0, Math.min(DECK.plate.cap, vol || 0));
      v.liq.setLevel(v.vol / DECK.beaker.ul_per_mm);   // liquid height (mm)
    }
  }
  resetWells() {
    for (const v of this.vials) { const res = this.reservoirs.indexOf(v); const vol = res >= 0 ? DECK.res_fill : 0, col = new THREE.Color(res >= 0 ? RES_COLORS[res] : 0xcfe8ff);
      v.vol = vol; v.liq.setLevel(vol / DECK.beaker.ul_per_mm); if (!col.equals(v.color)) { v.color.copy(col); setLiquidColor(v.liq.mesh.material, col); } }
    for (const k of Object.keys(this.tips)) this._showTip(k, true);
  }
  usedTips(list) { for (const k of list || []) this._showTip(k, false); }
  _showTip(k, shown) {
    const t = this.tips[k]; if (!t || t.shown === shown) return;
    t.shown = shown; this.rackTips.setMatrixAt(t.i, shown ? t.on : this._tipZero); this.rackTips.instanceMatrix.needsUpdate = true;
  }

  // capacitive probe arm on the tool head (servo): {deg} or {deployed: bool}; no-op until cnc3018.glb has the node
  _rigProbe() {
    const spec = this.syrRig?.probe || {}, node = this.cncModel?.getObjectByName(spec.node || 'probe_arm');
    if (!node) return null;
    return new NodeRotor(node, { axis: spec.axis || [1, 0, 0], pivot: spec.pivot, modelled_deg: spec.modelled_deg ?? spec.retracted_deg ?? 0, min: spec.min, max: spec.max });
  }
  setProbe(s) {
    if (!s) return; this.probeState = { ...(this.probeState || {}), ...s };
    if (!this.probe) return;
    const spec = this.syrRig?.probe || {};
    const deg = s.deg ?? s.angle ?? (s.deployed != null ? (s.deployed ? spec.deployed_deg ?? 90 : spec.retracted_deg ?? 0) : null);
    this.probe.set(deg);
  }
  // pan/tilt phone stand: {pan, tilt} deg (0/0 = looking straight down at the deck centre, docs/protocol.md)
  setStand(s) {
    if (!s) return; this.standState = { ...(this.standState || {}), ...s };
    const { pan, tilt } = this.standState, st = this.standRig;
    if (st) { if (pan != null) st.pan?.set(+pan); if (tilt != null) st.tilt?.set(+tilt); return; }
    // procedural stand: the phone stands upright facing the machine; turn it about the vertical (pan) and pitch it
    // forward over the deck (tilt measured from looking straight down, so 90 = the upright rest pose)
    const d = THREE.MathUtils.degToRad;
    if (pan != null) this.phone.rotation.y = d(+pan);
    this.phone.rotation.order = 'YXZ';
    if (tilt != null) this.phone.rotation.x = d(Math.max(0, Math.min(60, 90 - +tilt)));
  }
  async _loadStand() {
    try {
      const st = await loadPhoneStand(new URL('./models/phone_stand/', import.meta.url).href); if (!st) return;
      const { root, rig } = st, pl = rig.place || {};
      if (pl.position) root.position.fromArray(pl.position); else root.position.copy(this.phoneStand.position);
      if (pl.rotation_y_deg != null) root.rotation.y = THREE.MathUtils.degToRad(pl.rotation_y_deg);
      smoothCAD(root); applyLayerLinesTree(root, { unit: rig.units === 'm' ? 1000 : 1 }); this._shadows(root);
      this.stage.add(root); this.phoneStand.visible = false;
      if (st.phoneMount) {
        const ph = this.phone; ph.position.fromArray(rig.phone.position || [0, 0, 0]);
        ph.rotation.set(...(rig.phone.rotation_deg || [0, 0, 0]).map(THREE.MathUtils.degToRad)); st.phoneMount.add(ph);
      }
      this.standRig = st; if (this.standState) this.setStand(this.standState);
    } catch (e) { console.warn('phone_stand.glb failed, keeping the procedural stand', e); }
  }

  applyFrame(f) {
    if (!f) return;
    if (f.cnc) this.setCNC(f.cnc);
    if (f.pipette) this.setPipette(f.pipette);
    if (f.wells) this.setWells(f.wells);
    if (f.arms) for (const [k, q] of Object.entries(f.arms)) this.setArm(k, q);
    for (const k of ['so_a', 'so_b']) if (f[k]) this.setArm(k, f[k].q || f[k].joints || f[k]);
    if (f.probe) this.setProbe(f.probe);
    if (f.stand) this.setStand(f.stand);
  }
  // live state: {so_a:{q|joints}, cnc:{x,y,z}|{mpos:[..]}, pipette, probe, stand:{...}}
  applyDevices(devs) {
    if (!devs) return;
    for (const k of ['so_a', 'so_b']) { const d = devs[k]; if (d) { const q = d.q || d.joints || d.state?.q; if (q) this.setArm(k, q); } }
    const c = devs.cnc; if (c) { const pos = c.pos || c.mpos || c.wpos || c.state || c; if (Array.isArray(pos)) this.setCNC({ pos }); else this.setCNC(pos); }
    if (devs.pipette) this.setPipette(devs.pipette.state || devs.pipette);
    if (devs.probe) this.setProbe(devs.probe.state || devs.probe);
    if (devs.stand) this.setStand(devs.stand.state || devs.stand);
  }
}
