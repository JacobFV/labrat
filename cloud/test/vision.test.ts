import { env, runInDurableObject } from "cloudflare:test";
import jpeg from "jpeg-js";
import { describe, expect, it } from "vitest";
import type { Lab } from "../src/do";
import * as lab from "../src/lab";
import * as sim from "../src/sim";
import * as vision from "../src/vision";

// image px of beaker centres A1, A7, F7, F1 (slightly keystoned, like a phone looking down at an angle)
const CORNERS = [[100, 80], [520, 90], [530, 380], [90, 370]];
const CAST = [0.92, 1.0, 1.08]; // camera colour cast to be white-balanced away

function synthPlate(colors: Record<string, number[]>, w = 640, h = 480): vision.Image {
  const H = vision.homography(CORNERS);
  const f = new Float64Array(w * h * 3).fill(60); // dark deck
  const [x0, y0] = vision.project(H, "A1"), [x1, y1] = vision.project(H, "F7");
  for (let y = Math.trunc(y0 - 25); y < Math.trunc(y1 + 25); y++)
    for (let x = Math.trunc(Math.min(x0, 90) - 25); x < Math.trunc(Math.max(x1, 520) + 25); x++) f.fill(245, (y * w + x) * 3, (y * w + x) * 3 + 3); // white plate
  for (const r of sim.ROWS.slice(0, sim.PLATE.rows)) for (let c = 1; c <= sim.PLATE.cols; c++) {
    const name = `${r}${c}`, [cx, cy] = vision.project(H, name), col = colors[name] ?? [245, 245, 245];
    for (let y = Math.floor(cy - 27); y <= cy + 27; y++) for (let x = Math.floor(cx - 27); x <= cx + 27; x++)
      if ((x - cx) ** 2 + (y - cy) ** 2 <= 26 ** 2) for (let k = 0; k < 3; k++) f[(y * w + x) * 3 + k] = col[k];
  }
  const data = new Uint8Array(w * h * 3);
  for (let i = 0; i < data.length; i++) data[i] = Math.min(255, Math.max(0, Math.trunc(f[i] * CAST[i % 3])));
  return { width: w, height: h, data };
}

function toB64(img: vision.Image): string {
  const rgba = new Uint8Array(img.width * img.height * 4);
  for (let i = 0; i < img.width * img.height; i++) { rgba.set(img.data.subarray(i * 3, i * 3 + 3), i * 4); rgba[i * 4 + 3] = 255; }
  const out = jpeg.encode({ data: rgba, width: img.width, height: img.height }, 95).data as Uint8Array;
  let s = "";
  for (const b of out) s += String.fromCharCode(b);
  return btoa(s);
}

/** 2x box downscale (stands in for PIL's resize). */
function half(img: vision.Image): vision.Image {
  const w = img.width / 2, h = img.height / 2, data = new Uint8Array(w * h * 3);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) for (let k = 0; k < 3; k++) {
    const p = (yy: number, xx: number) => img.data[(yy * img.width + xx) * 3 + k];
    data[(y * w + x) * 3 + k] = Math.round((p(2 * y, 2 * x) + p(2 * y, 2 * x + 1) + p(2 * y + 1, 2 * x) + p(2 * y + 1, 2 * x + 1)) / 4);
  }
  return { width: w, height: h, data };
}

const maxDiff = (a: number[], b: number[]) => Math.max(...a.map((v, i) => Math.abs(v - b[i])));
const stub = () => env.LAB.get(env.LAB.idFromName("lab"));

describe("vision", () => {
  it("homography round-trips the corners", () => {
    const H = vision.homography(CORNERS);
    vision.CORNER_WELLS.forEach((w, i) => {
      const [x, y] = vision.project(H, w);
      expect(Math.abs(x - CORNERS[i][0]) < 1e-6 && Math.abs(y - CORNERS[i][1]) < 1e-6).toBe(true);
    });
  });

  it("samples a beaker white-balanced against a blank one", () => {
    const target = [230, 120, 30];
    const img = vision.decode(toB64(synthPlate({ C5: target })));
    const cal = { H: vision.homography(CORNERS), image_w: 640, image_h: 480 };
    const s = vision.sampleWell(img, cal, "C5", "F7");
    expect(maxDiff(s.rgb, target)).toBeLessThanOrEqual(8);
    const raw = vision.sampleWell(img, cal, "C5"); // without white balance the cast is visible
    expect(Math.abs(raw.rgb[2] - target[2] * CAST[2])).toBeLessThanOrEqual(6);
    const s2 = vision.sampleWell(half(img), cal, "C5", "F7"); // calibration done at another resolution still maps
    expect(maxDiff(s2.rgb, target)).toBeLessThanOrEqual(12);
  });

  it("auto-observes a run and feeds the learning loop", async () => {
    await runInDurableObject(stub(), async (inst: Lab) => {
      const c = inst.c;
      const e0 = await lab.createExperiment(c, "make the most orange-looking well using red & yellow", 4);
      const e = await lab.simulateExperiment(c, e0.id);
      const run = await c.store.putRun({ id: "run_vis1", experiment_id: e.id, variant_id: e.best_variant_id, status: "done", log: [] });
      const [target, blank] = await vision.targetAndBlank(c, run);
      expect(blank && blank !== target).toBeTruthy();
      const observed = [225, 95, 25]; // the "real" result: redder than predicted
      c.hub.latestFrame = { jpeg: toB64(synthPlate({ [target]: observed })), w: 640, h: 480, ts: 1 };
      await vision.setCal(c, CORNERS, 640, 480);
      const res = await vision.autoObserve(c, run.id);
      expect(res.ok, JSON.stringify(res)).toBe(true);
      expect(res.estimate.method).toBe("homography");
      expect(res.estimate.well).toBe(target);
      expect(maxDiff(res.estimate.rgb, observed)).toBeLessThanOrEqual(8);
      const obs = (await c.store.observations()).filter((o) => o.run_id === "run_vis1");
      expect(obs[0]?.source).toBe("vision");
      expect(res.rsi.error_before).not.toBeNull();
      expect((await c.store.events(10, "vision.observe")).length).toBe(1);
      expect((await c.store.getRun("run_vis1")).vision.ok).toBe(true);
      c.hub.latestFrame = null;
    });
  });

  it("skips without a frame, and never uses a frame older than the photo step", async () => {
    await runInDurableObject(stub(), async (inst: Lab) => {
      const c = inst.c;
      const e0 = await lab.createExperiment(c, "make green from blue and yellow", 2);
      const e = await lab.simulateExperiment(c, e0.id);
      await c.store.putRun({ id: "run_vis2", experiment_id: e.id, variant_id: e.best_variant_id, status: "done", log: [] });
      c.hub.latestFrame = null;
      let res = await vision.autoObserve(c, "run_vis2");
      expect(res.ok).toBe(false);
      expect(res.skipped).toBe("no phone frame");
      c.hub.latestFrame = { jpeg: toB64(synthPlate({})), w: 640, h: 480, ts: 100 };
      res = await vision.autoObserve(c, "run_vis2", { freshAfter: 200, waitS: 0.1 });
      expect(res.skipped).toBe("no fresh phone frame");
      c.hub.latestFrame = null;
    });
  });
});
