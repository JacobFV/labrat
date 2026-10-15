// Labware for the LabRat twin: thin-walled glass vials with a screw-thread neck, liquid with a meniscus and
// Beer-Lambert tint, hollow 200 uL pipette tips (collar, ribs, open end) and the liquid inside a tip.
// Units mm, +Y up. Glass and PP are alpha-blended "clear" materials (see clearMaterial) so the transmissive liquid
// inside them stays visible: three's transmission pass only sees opaque objects, so a transmissive vial would hide
// its liquid.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const V2 = (r, y) => new THREE.Vector2(Math.max(0, r), y);

// revolve polylines about +Y; each run is smooth inside and meets the next at a sharp edge
export function revolve(runs, segs = 40) {
  const geos = runs.filter((r) => r.length > 1).map((run) => new THREE.LatheGeometry(run.map(([r, y]) => V2(r, y)), segs));
  const g = mergeGeometries(geos, false); geos.forEach((x) => x.dispose()); return g;
}

// fillet helper: points of a quarter arc from angle a0 to a1 (rad) around (cr, cy)
const arc = (cr, cy, rad, a0, a1, n = 5) => Array.from({ length: n + 1 }, (_, i) => { const a = a0 + (a1 - a0) * i / n; return [cr + rad * Math.cos(a), cy + rad * Math.sin(a)]; });

// --- materials --------------------------------------------------------------------------------------------------
// Clear dielectric (glass, PP) drawn with premultiplied alpha: what is behind is dimmed by alpha (= opacity, rising
// toward grazing angles: the wall seen edge-on), env/specular reflections are added at full strength, and `diffuse`
// scales the scattered (milky) part: ~0 for glass, ~1 for frosted polypropylene.
export function clearMaterial({ color = 0xffffff, opacity = 0.06, roughness = 0.04, ior = 1.5, edge = 0.55, diffuse = 1, specularIntensity = 1, envMapIntensity = 1.2 } = {}) {
  const m = new THREE.MeshPhysicalMaterial({ color, roughness, metalness: 0, ior, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide, specularIntensity, envMapIntensity, premultipliedAlpha: true });
  m.onBeforeCompile = (sh) => {
    sh.uniforms.uEdge = { value: edge }; sh.uniforms.uDiffuse = { value: diffuse };
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nuniform float uEdge; uniform float uDiffuse;')
      .replace('#include <opaque_fragment>', `{
    float ndv = clamp(abs(dot(normal, normalize(vViewPosition))), 0.0, 1.0);
    float a = clamp(diffuseColor.a + uEdge * pow(1.0 - ndv, 3.0), 0.0, 1.0);
    outgoingLight = totalDiffuse * a * uDiffuse + totalSpecular + totalEmissiveRadiance;
    diffuseColor.a = a;
  }
  #include <opaque_fragment>`)
      .replace('#include <premultiplied_alpha_fragment>', '');
  };
  m.customProgramCacheKey = () => 'clear1';
  return m;
}

// liquid: refractive (IOR 1.33), tinted by Beer-Lambert attenuation over `thickness` mm
export function liquidMaterial(color = 0xcfe8ff, { thickness = 12 } = {}) {
  const m = new THREE.MeshPhysicalMaterial({ color: 0xffffff, roughness: 0.02, metalness: 0, transmission: 1, ior: 1.33, thickness, specularIntensity: 1, envMapIntensity: 1 });
  setLiquidColor(m, color); return m;
}
const WHITE = new THREE.Color(1, 1, 1);
export function setLiquidColor(m, c) {
  const col = c instanceof THREE.Color ? c.clone() : new THREE.Color(c);
  const hsl = {}; col.getHSL(hsl);
  // near-white = water: almost clear. Coloured: attenuate to the colour over ~the vial diameter (deeper at the centre,
  // lighter at the meniscus edge) and let a little of the colour scatter (cloudy dye) so dark mixes still read.
  const water = hsl.s < 0.18 && hsl.l > 0.75;
  m.attenuationColor.copy(water ? new THREE.Color(0.93, 0.97, 1) : col);
  m.attenuationDistance = water ? 400 : m.thickness * 0.55;
  m.color.copy(WHITE).lerp(col, water ? 0.04 : 0.35);
  m.userData.color = col;
}

// --- vial ---------------------------------------------------------------------------------------------------------
// 15.3 x 50 mm screw-top vial (13-425 neck): body OD 15.3 / ID 13.5, 1.2 mm base, shoulder at ~41 mm, neck root
// 11.9 / bore 8.2, 1.6 turns of 2.12 mm-pitch thread, rounded lip. y = 0 at the outside bottom.
export const VIAL = { od: 15.3, id: 13.5, h: 50, base: 1.2, neckRoot: 5.95, neckBore: 4.1, shoulder: 40.5, thread: { r: 5.95, h: 0.55, pitch: 2.12, turns: 1.6, y0: 43.4, w: 1.25 } };

export function vialGeometry(v = VIAL, segs = 40) {
  const R = v.od / 2, Ri = v.id / 2, H = v.h, b = v.base, Rn = v.neckRoot, Rb = v.neckBore, sh = v.shoulder;
  const outerBottom = [[0, 0], [R - 1.8, 0]];
  const outer = [...arc(R - 1.8, 1.8, 1.8, -Math.PI / 2, 0, 5), [R, sh], [R - 0.6, sh + 1.0], [Rn + 0.15, sh + 2.2], [Rn, sh + 2.6], [Rn, H - 0.5]];
  const lip = arc((Rn + Rb) / 2, H - 0.5, (Rn - Rb) / 2, 0, Math.PI, 6);           // rounded lip, outer -> bore
  const inner = [[Rb, H - 0.5], [Rb, sh + 2.4], [Rb + 1.2, sh + 1.0], [Ri - 0.3, sh - 0.6], [Ri, sh - 1.2], [Ri, b + 1.5], ...arc(Ri - 1.5, b + 1.5, 1.5, 0, -Math.PI / 2, 5).slice(1)];
  const innerFloor = [[Ri - 1.5, b], [0, b]];
  const body = revolve([outerBottom, outer, lip, inner, innerFloor], segs);
  const th = threadGeometry(v.thread);
  const g = mergeGeometries([body, th], false); body.dispose(); th.dispose(); return g;
}

// helical ridge: a rounded trapezoid profile swept along a helix of radius r (thread root), tapered in at both ends
export function threadGeometry({ r, h, pitch, turns, y0, w }, perTurn = 72) {
  const prof = [[-0.02, -w / 2], [h * 0.55, -w * 0.32], [h, -w * 0.12], [h, w * 0.12], [h * 0.55, w * 0.32], [-0.02, w / 2]]; // (radial, axial)
  const n = Math.ceil(turns * perTurn), P = prof.length, pos = [], idx = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, a = t * turns * Math.PI * 2, y = y0 + t * turns * pitch;
    const taper = Math.min(1, t / 0.12, (1 - t) / 0.12);                         // run-in / run-out
    const ca = Math.cos(a), sa = Math.sin(a);
    for (const [dr, dy] of prof) { const rr = r + dr * taper; pos.push(rr * sa, y + dy * (0.35 + 0.65 * taper), rr * ca); }
  }
  for (let i = 0; i < n; i++) for (let j = 0; j < P - 1; j++) { const a = i * P + j, b = a + 1, c = a + P, d = c + 1; idx.push(a, c, b, b, c, d); }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx);
  g.computeVertexNormals();
  g.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array((pos.length / 3) * 2), 2));
  return g;
}

// screw cap for 13-425 (OD 15.6, 9.5 tall) with knurled skirt; y = 0 at the cap's open bottom
export function capGeometry(segs = 96) {
  const R = 7.8, Hc = 9.5;
  const g = revolve([[[6.6, 0], [R, 0]], [[R, 0], [R, Hc - 0.6], ...arc(R - 0.6, Hc - 0.6, 0.6, 0, Math.PI / 2, 3).slice(1), [0, Hc]], [[0, Hc - 1.8], [6.6, Hc - 1.8]], [[6.6, Hc - 1.8], [6.6, 0]]], segs);
  const p = g.attributes.position;                                               // knurl: 48 shallow ribs on the skirt
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), z = p.getZ(i), y = p.getY(i), r = Math.hypot(x, z);
    if (r > R - 0.05 && y < Hc - 0.7) { const a = Math.atan2(x, z), f = 1 + 0.022 * Math.max(0, Math.cos(a * 48)) ** 2; p.setX(i, x * f); p.setZ(i, z * f); }
  }
  g.computeVertexNormals(); return g;
}

// liquid column in a vial: y = 0 at the inner floor; a rounded foot that matches the glass fillet, straight wall, and
// a concave meniscus (rises ~0.9 mm at the wall). setLevel moves only the meniscus rows: no rebuild per update.
export class VialLiquid {
  constructor(material, v = VIAL, segs = 32) {
    const Ri = v.id / 2 - 0.03, f = 1.45, L0 = 10, m = 0.9;
    this.min = f + 0.4; this.L0 = L0; this.maxLevel = v.shoulder - v.base - 1.4;
    const foot = [[0, 0], ...arc(Ri - f, f, f, -Math.PI / 2, 0, 5)];
    const wall = [[Ri, f], [Ri, L0 + m]];
    const men = []; for (let i = 0; i <= 10; i++) { const r = Ri * (1 - (i / 10) ** 1.6); men.push([r, L0 + m * Math.exp(-(Ri - r) / 0.9)]); }
    const geo = this.geo = revolve([foot, wall, men], segs);
    const p = geo.attributes.position; this.base = Float32Array.from(p.array);
    this.mov = new Uint8Array(p.count); for (let i = 0; i < p.count; i++) this.mov[i] = p.getY(i) > f + 0.01 ? 1 : 0;
    this.mesh = new THREE.Mesh(geo, material); this.mesh.renderOrder = 1; this.level = L0;
  }
  // level = liquid height at the centre above the inner floor (mm); <= 0.05 hides it
  setLevel(level) {
    level = Math.min(this.maxLevel, +level || 0);
    this.mesh.visible = level > 0.05;
    if (!this.mesh.visible || Math.abs(level - this.level) < 1e-3) return;
    const L = Math.max(this.min, level), dy = L - this.L0, p = this.geo.attributes.position, a = p.array, b = this.base;
    for (let i = 0; i < p.count; i++) if (this.mov[i]) a[i * 3 + 1] = b[i * 3 + 1] + dy;
    p.needsUpdate = true; this.geo.computeBoundingSphere();
    this.mesh.scale.y = level < this.min ? level / this.min : 1;
    this.level = level;
  }
}

// --- 200 uL pipette tip (FT-200L-RS style) --------------------------------------------------------------------------
// y = 0 at the top rim, tip end at y = -51. Thin wall: outer + inner surfaces, open at both ends; collar with a seal
// step, a slightly fuller upper barrel with 6 fine stacking ribs, then the long cone.
export const TIP = { len: 51, rimR: 3.8, boreTop: 3.15, endR: 0.5, endBore: 0.25, coneTopY: -12.6, coneTopIn: 2.25 };
export function tipGeometry(segs = 28, ribs = 6) {
  const T = TIP, E = -T.len;
  const g = revolve([
    [[T.endBore, E], [T.endR, E]],                                                   // open end annulus
    [[T.endR, E], [2.55, T.coneTopY]],                                                // cone
    [[2.55, T.coneTopY], [2.95, -11.6], [3.3, -2.1], [3.42, -1.75]],                  // step + upper barrel
    [[3.42, -1.75], [T.rimR, -1.35], [T.rimR, -0.25], [T.rimR - 0.25, 0]],            // collar
    [[T.rimR - 0.25, 0], [T.boreTop, 0]],                                             // rim
    [[T.boreTop, 0], [3.0, -11.3], [T.coneTopIn + 0.15, -12.2], [T.coneTopIn, T.coneTopY], [T.endBore, E]], // bore
  ], segs);
  const parts = [g];
  for (let i = 0; i < ribs; i++) {                                                    // fine longitudinal ribs
    const rib = new THREE.BoxGeometry(0.28, 8.6, 0.3); const a = i / ribs * Math.PI * 2;
    rib.rotateX(Math.atan2(0.35, 9.5)); rib.translate(0, -6.6, 3.13); rib.rotateY(a); parts.push(rib);
  }
  const out = mergeGeometries(parts, false); parts.forEach((x) => x.dispose()); return out;
}

// liquid inside a tip: fills the cone from the open end up to the height that holds vol_ul, with a small meniscus.
const tipK = (TIP.coneTopIn - TIP.endBore) / (TIP.coneTopY + TIP.len);
export const TIP_CONE_UL = Math.PI / (3 * tipK) * (TIP.coneTopIn ** 3 - TIP.endBore ** 3);
export function tipLiquidGeometry(vol, segs = 20) {
  const r0 = TIP.endBore - 0.02, E = -TIP.len;
  const v = Math.max(0, Math.min(TIP_CONE_UL * 0.995, vol));
  const r1 = Math.cbrt(3 * tipK * v / Math.PI + r0 ** 3), h = (r1 - r0) / tipK, top = E + h;
  const men = Math.min(0.5, r1 * 0.35);
  const geo = revolve([[[0, E], [r0, E]], [[r0, E], [r1, top]], [[r1, top], [r1 * 0.6, top - men * 0.55], [0, top - men]]], segs);
  return geo;
}
