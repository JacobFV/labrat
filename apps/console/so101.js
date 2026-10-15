// SO-101 arm loader: parses the official URDF (TheRobotStudio/SO-ARM100, Apache-2.0) and builds a three.js
// hierarchy from its STL meshes. Output is Y-up millimetres with the base resting on y=0.
import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { applyLayerLines } from './twin/layers.js';
import { toCreasedNormals } from 'three/addons/utils/BufferGeometryUtils.js';

const BASE = new URL('./models/so101/', import.meta.url).href;
const URDF = 'so101_new_calib.urdf';

const nums = (s, d = '0 0 0') => (s || d).trim().split(/\s+/).map(Number);
function applyOrigin(obj, el) {
  if (!el) return;
  const [x, y, z] = nums(el.getAttribute('xyz')), [r, p, yw] = nums(el.getAttribute('rpy'));
  obj.position.set(x, y, z);
  obj.rotation.set(r, p, yw, 'ZYX'); // URDF fixed-axis RPY: R = Rz(yaw) Ry(pitch) Rx(roll)
}
const kids = (el, tag) => [...el.children].filter((c) => c.tagName === tag);

const geoCache = new Map();
function loadGeo(loader, file) {
  // creased normals: smooth fillets and bosses, sharp printed edges (plain STL normals are per-facet)
  if (!geoCache.has(file)) geoCache.set(file, loader.loadAsync(BASE + file).then((g) => { try { return toCreasedNormals(g, Math.PI / 6); } catch { g.computeVertexNormals(); return g; } }));
  return geoCache.get(file);
}

export async function loadSO101({ bodyColor = 0xf2f2ee, servoColor = 0x161618, layerLines = true } = {}) {
  const txt = await (await fetch(BASE + URDF)).text();
  const robot = new DOMParser().parseFromString(txt, 'application/xml').documentElement;
  const loader = new STLLoader();
  const bodyMat = new THREE.MeshStandardMaterial({ color: bodyColor, roughness: 0.5, metalness: 0, name: 'print_so101' });
  const servoMat = new THREE.MeshStandardMaterial({ color: servoColor, roughness: 0.45, metalness: 0.1 });

  const links = {}, pending = [];
  for (const l of kids(robot, 'link')) {
    const g = new THREE.Group(); g.name = l.getAttribute('name'); links[g.name] = g;
    for (const v of kids(l, 'visual')) {
      const mesh = v.getElementsByTagName('mesh')[0]; if (!mesh) continue;
      const file = mesh.getAttribute('filename').replace(/^package:\/\/[^/]+\//, '');
      const matName = v.getElementsByTagName('material')[0]?.getAttribute('name') || '';
      const servo = /sts3215|motor|servo/i.test(file) && !/holder/i.test(file) || matName === 'sts3215';
      const holder = new THREE.Group(); applyOrigin(holder, v.getElementsByTagName('origin')[0]); g.add(holder);
      pending.push(loadGeo(loader, file).then((geo) => {
        const m = new THREE.Mesh(geo, servo ? servoMat : bodyMat);
        m.castShadow = m.receiveShadow = true; m.name = file.split('/').pop(); holder.add(m);
        // printed links: the URDF STLs are already in print orientation (they match hardware/so101/stl), so layers
        // stack along mesh +Z; the meshes are in metres (unit 1000 -> 0.2 mm layers)
        if (!servo && layerLines) { m.userData.print_up = [0, 0, 1]; applyLayerLines(m, { up: [0, 0, 1], unit: 1000 }); }
      }));
    }
  }
  const joints = {}, info = {}, childSet = new Set();
  for (const j of kids(robot, 'joint')) {
    const name = j.getAttribute('name'), type = j.getAttribute('type');
    const parent = links[kids(j, 'parent')[0].getAttribute('link')], child = links[kids(j, 'child')[0].getAttribute('link')];
    childSet.add(child.name);
    const frame = new THREE.Group(); frame.name = name + '_frame'; applyOrigin(frame, kids(j, 'origin')[0]);
    const motion = new THREE.Group(); motion.name = name; frame.add(motion); motion.add(child); parent.add(frame);
    if (type === 'revolute' || type === 'continuous') {
      const axis = new THREE.Vector3(...nums(kids(j, 'axis')[0]?.getAttribute('xyz'), '1 0 0')).normalize();
      const lim = kids(j, 'limit')[0];
      info[name] = { axis, lower: +(lim?.getAttribute('lower') ?? -Math.PI), upper: +(lim?.getAttribute('upper') ?? Math.PI) };
      joints[name] = (deg) => motion.quaternion.setFromAxisAngle(axis, THREE.MathUtils.degToRad(+deg || 0));
    }
  }
  await Promise.all(pending);
  const base = Object.values(links).find((l) => !childSet.has(l.name));
  const urdfRoot = new THREE.Group(); urdfRoot.rotation.x = -Math.PI / 2; urdfRoot.scale.setScalar(1000); urdfRoot.add(base);
  const root = new THREE.Group(); root.name = 'so101'; root.add(urdfRoot);
  root.updateMatrixWorld(true);
  const bb = new THREE.Box3().setFromObject(urdfRoot); urdfRoot.position.y = -bb.min.y; // base rests on y=0
  return { root, joints, info, links };
}

// q: {joint: degrees}; gripper may also be given as 0..100 (% open), mapped across the joint limits.
export function setSO101(arm, q = {}) {
  if (!arm || !q) return;
  for (const [k, v] of Object.entries(q)) {
    const set = arm.joints[k]; if (!set || v == null) continue;
    if (k === 'gripper' && arm.info?.gripper && +v >= 0 && +v <= 100) {
      const { lower, upper } = arm.info.gripper;
      set(THREE.MathUtils.radToDeg(lower + (upper - lower) * (+v / 100)));
    } else set(+v);
  }
}
