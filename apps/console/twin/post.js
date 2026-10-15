// Post-processing for the twin: MSAA HDR render -> GTAO ambient occlusion (soft contact shadows where parts meet the
// deck, the bench and each other) -> tone mapping/sRGB (OutputPass, uses renderer.toneMapping).
// Transparent meshes (glass, PP tips, clear plastics) are left out of the AO G-buffer so they neither cast AO nor get
// darkened by it; the transmissive liquid still counts as a solid.
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

class OpaqueGTAOPass extends GTAOPass {
  overrideVisibility() {
    const cache = this._visibilityCache;
    this.scene.traverse((o) => {
      cache.set(o, o.visible);
      if (o.isPoints || o.isLine || o.isLineSegments || (o.isMesh && (o.material?.transparent || o.material?.userData?.noAO))) o.visible = false;
    });
  }
}

export function makeComposer(renderer, scene, camera) {
  const size = renderer.getDrawingBufferSize(new THREE.Vector2());
  const rt = new THREE.WebGLRenderTarget(size.x, size.y, { type: THREE.HalfFloatType, samples: 4 });
  const composer = new EffectComposer(renderer, rt);
  composer.addPass(new RenderPass(scene, camera));
  const ao = new OpaqueGTAOPass(scene, camera, size.x, size.y);
  ao.updateGtaoMaterial({ radius: 14, distanceExponent: 1.6, thickness: 6, scale: 1.0, samples: 12, distanceFallOff: 1.0 });
  ao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, rings: 2, samples: 12 });
  ao.blendIntensity = 0.85;
  composer.addPass(ao);
  composer.addPass(new OutputPass());
  return { composer, ao, setSize(w, h) { composer.setPixelRatio(renderer.getPixelRatio()); composer.setSize(w, h); }, render() { composer.render(); } };
}
