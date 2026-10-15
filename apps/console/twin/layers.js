// Procedural FDM look for 3D-printed parts: layer lines as a normal perturbation (no textures).
//
// Each printed mesh gets a (cached) clone of its material whose shader computes the layer coordinate
// s = dot(objectPosition, print_up) * unit / pitch (layers), and tilts the shading normal along the print direction
// with the slope of a bead profile (crest = extruded bead, groove = seam between layers, per-layer jitter) plus a weak
// ~1.4 mm banding (Z wobble). Each term is band-limited: it fades out (fwidth(s)) before a period drops below ~2.5 px,
// so far away it reads as smooth plastic rather than moire noise.
// Grooves are a touch darker and rougher, crests a touch glossier: highlights stretch along the layers (the anisotropic
// sheen of a printed wall).
//
// print_up is in the mesh's OBJECT space (the frame of its geometry), taken from, in order:
//   mesh.userData.print_up / any ancestor's userData.print_up (GLB node extras, e.g. {"print_up":[0,1,0]})
//   the `up` option, else [0,1,0] (glTF/three local up).
import * as THREE from 'three';

export const PRINTED_RE = /print|petg|(^|_)(pla|fdm|asa)(_|$)/i;

const cache = new Map();

export function isPrintedMaterial(mat) { return !!mat && PRINTED_RE.test(mat.name || ''); }

function findPrintUp(obj) {
  for (let o = obj; o; o = o.parent) {
    const u = o.userData?.print_up;
    if (Array.isArray(u) && u.length === 3) return u.map(Number);
    if (o.userData?.printed === false) return null;
  }
  return undefined;
}

// patch one mesh. opts: {up:[x,y,z] object-space print direction, unit: mm per object unit, pitch: mm, strength}
export function applyLayerLines(mesh, opts = {}) {
  if (!mesh?.isMesh || !mesh.material || Array.isArray(mesh.material)) return;
  const fromData = findPrintUp(mesh);
  if (fromData === null) return;
  const up = new THREE.Vector3(...(fromData || opts.up || [0, 1, 0])).normalize();
  const unit = opts.unit ?? 1, pitch = opts.pitch ?? 0.2, strength = opts.strength ?? 0.32;
  const src = mesh.material;
  const key = `${src.uuid}|${up.toArray().map((v) => v.toFixed(3))}|${unit}|${pitch}|${strength}`;
  let m = cache.get(key);
  if (!m) {
    m = src.clone(); m.name = src.name;
    m.userData = { ...src.userData, layerLines: { up: up.toArray(), unit, pitch } };
    const U = { uPrintUp: { value: up }, uLayerScale: { value: unit / pitch }, uLayerStrength: { value: strength } };
    m.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, U);
      sh.vertexShader = sh.vertexShader
        .replace('#include <common>', '#include <common>\nuniform vec3 uPrintUp; uniform float uLayerScale; varying float vLayerS; varying vec3 vPrintUpV;')
        .replace('#include <begin_vertex>', `#include <begin_vertex>
  vLayerS = dot(transformed, uPrintUp) * uLayerScale;
  vPrintUpV = normalize((modelViewMatrix * vec4(uPrintUp, 0.0)).xyz);`);
      sh.fragmentShader = sh.fragmentShader
        .replace('#include <common>', '#include <common>\nuniform float uLayerStrength; varying float vLayerS; varying vec3 vPrintUpV;')
        .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>
  {
    // band-limited: each term fades out before it drops below ~2.5 px per period (no moire)
    float fw = max(fwidth(vLayerS), 1e-4);                   // layers per pixel
    float f1 = 1.0 - smoothstep(0.08, 0.40, fw);              // individual 0.2 mm beads
    float f1h = 1.0 - smoothstep(0.04, 0.20, fw);             // their sharper 2nd harmonic
    float f2 = 1.0 - smoothstep(0.08, 0.40, fw / 7.0);        // ~1.4 mm extrusion banding (Z wobble / flow ripple)
    vec3 u = vPrintUpV - normal * dot(normal, vPrintUpV);     // print direction projected onto the surface
    float ul = length(u);
    if ((f1 + f2) > 0.0 && ul > 1e-3) {
      float ph = 6.2831853 * vLayerS;
      float rnd = fract(sin(floor(vLayerS) * 12.9898) * 43758.5453);   // per-layer extrusion variation
      float bead = (sin(ph) + 0.3 * sin(2.0 * ph) * f1h) * (0.7 + 0.6 * rnd) * f1;
      float band = sin(ph / 7.0 + 1.3 * sin(vLayerS * 0.091)) * f2;
      u /= ul;
      normal = normalize(normal + u * (bead * uLayerStrength + band * 0.10) * ul);
      float groove = (0.5 + 0.5 * cos(ph)) * f1 * ul;          // 1 at the seam between two layers
      diffuseColor.rgb *= 1.0 - 0.07 * groove - 0.025 * band * ul;
      roughnessFactor = clamp(roughnessFactor * (0.85 + 0.35 * groove), 0.04, 1.0);  // glossy crests: streaky sheen
    }
  }`);
    };
    m.customProgramCacheKey = () => 'layers2';
    cache.set(key, m);
  }
  mesh.material = m;
}

// patch every printed mesh under root (material name match, or userData.print_up / printed:true on the node path)
export function applyLayerLinesTree(root, { upByMaterial = {}, unit = 1, defaultUp = [0, 1, 0], test } = {}) {
  root.traverse((o) => {
    if (!o.isMesh || Array.isArray(o.material)) return;
    const flagged = findPrintUp(o);
    const printed = test ? test(o) : (isPrintedMaterial(o.material) || flagged !== undefined || o.userData?.printed === true);
    if (!printed || flagged === null) return;
    applyLayerLines(o, { up: upByMaterial[o.material.name] || defaultUp, unit });
  });
}
