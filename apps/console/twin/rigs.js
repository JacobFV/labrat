// Moving sub-assemblies driven by device state: rotate a GLB node about an axis through a pivot.
//
// Expected rig shapes (all angles in degrees, axes/pivots in the frame of the driven node's PARENT, glTF/three
// convention: Y up, mm, i.e. the same frame the node's own translation is written in):
//
// apps/console/models/syringe_rig.json  (cnc3018.glb; the tool head may gain a node "probe_arm")
//   "probe": { "node": "probe_arm", "axis": [1,0,0], "pivot": [x,y,z],       // pivot optional (default: node origin)
//              "deployed_deg": 90, "retracted_deg": 0,
//              "modelled_deg": 0 }                                         // GLB pose = this angle (default retracted_deg)
//
// apps/console/models/phone_stand/rig.json  (+ phone_stand.glb next to it)
//   { "pan":  { "node": "stand_pan",  "axis": [0,1,0], "pivot": [0,0,0],  "modelled_deg": 0, "sign": 1, "min": -170, "max": 170 },
//     "tilt": { "node": "stand_tilt", "axis": [1,0,0], "pivot": [0,95,0], "modelled_deg": 0, "sign": 1, "min": -10,  "max": 100 },
//     "place": { "position": [0, 0, -225], "rotation_y_deg": 0 },          // optional: GLB root on the bench top (twin
//                                                                         //   bench frame: X right as seen from the back, Z = toward the wall)
//     "phone": { "node": "phone_mount", "position": [0,0,0], "rotation_deg": [0,0,0] }, // optional: hang the twin's phone here
//     "units": "mm" }                                                     // "m" scales the GLB by 1000
//   Protocol (docs/protocol.md): stand {pan, tilt} deg, 0/0 = looking straight down at the deck centre; the GLB should be
//   modelled at pan = tilt = modelled_deg (default 0). Angle applied = sign * (deg - modelled_deg), clamped to [min, max].
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

export class NodeRotor {
  constructor(node, { axis = [1, 0, 0], pivot = null, modelled_deg = 0, sign = 1, min = -Infinity, max = Infinity } = {}) {
    this.node = node; this.axis = new THREE.Vector3(...axis).normalize(); this.pivot = pivot ? new THREE.Vector3(...pivot) : null;
    this.zero = +modelled_deg || 0; this.sign = sign; this.min = min ?? -Infinity; this.max = max ?? Infinity;
    this.restP = node.position.clone(); this.restQ = node.quaternion.clone(); this.deg = this.zero;
  }
  set(deg) {
    if (deg == null || !Number.isFinite(+deg)) return;
    this.deg = Math.max(this.min, Math.min(this.max, +deg));
    const q = new THREE.Quaternion().setFromAxisAngle(this.axis, THREE.MathUtils.degToRad(this.sign * (this.deg - this.zero)));
    this.node.quaternion.copy(q).multiply(this.restQ);
    if (this.pivot) this.node.position.copy(this.restP).sub(this.pivot).applyQuaternion(q).add(this.pivot);
  }
}

// -> {root, rig, pan: NodeRotor|null, tilt: NodeRotor|null, phoneMount: Object3D|null} or null when the files are absent
export async function loadPhoneStand(baseUrl) {
  const rigUrl = new URL('rig.json', baseUrl).href, glbUrl = new URL('phone_stand.glb', baseUrl).href;
  let rig;
  try { const r = await fetch(rigUrl); if (!r.ok) return null; rig = await r.json(); } catch { return null; }
  try { const h = await fetch(glbUrl, { method: 'HEAD' }); if (!h.ok) return null; } catch { return null; }
  const g = await new GLTFLoader().loadAsync(glbUrl);
  const root = g.scene; root.name = 'phone_stand_glb';
  if (rig.units === 'm') root.scale.setScalar(1000);
  const node = (spec) => (spec?.node ? root.getObjectByName(spec.node) : null);
  const mk = (spec) => { const n = node(spec); if (spec && !n) console.warn('phone_stand rig: node not found', spec.node); return n ? new NodeRotor(n, spec) : null; };
  return { root, rig, pan: mk(rig.pan), tilt: mk(rig.tilt), phoneMount: node(rig.phone) };
}
