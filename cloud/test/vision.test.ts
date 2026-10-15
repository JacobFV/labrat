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

  it("measures every beaker of an experiment from a photo and feeds the model", async () => {
    await runInDurableObject(stub(), async (inst: Lab) => {
      const c = inst.c;
      await vision.setCal(c, CORNERS, 640, 480);
      const e = await lab.create(c, { title: "two beakers", samples: [{ well: "C5", red: 100, yellow: 100 }, { well: "D2", blue: 80, yellow: 120 }] });
      const seen = { C5: [225, 95, 25], D2: [40, 160, 70] }; // the "real" results
      c.hub.latestFrame = { jpeg: toB64(synthPlate(seen)), w: 640, h: 480, ts: 1 };
      const res = await lab.measure(c, e.id);
      expect(res.ok, JSON.stringify(res)).toBe(true);
      expect(res.method).toBe("camera_cal");
      for (const r of res.readings) expect(maxDiff(r.rgb, (seen as any)[r.well])).toBeLessThanOrEqual(8);
      expect(res.results.length).toBe(2);
      expect(res.model.v).toBeGreaterThan(0);
      const v = await lab.view(c, await lab.must(c, e.id));
      expect(v.beakers.every((b: any) => b.source === "camera" && b.measured_rgb)).toBe(true);
      c.hub.latestFrame = null;
    });
  });

  it("skips without a frame or without a fresh one", async () => {
    await runInDurableObject(stub(), async (inst: Lab) => {
      const c = inst.c;
      const e = await lab.create(c, { title: "green", samples: [{ blue: 100, yellow: 100 }] });
      c.hub.latestFrame = null;
      let res = await vision.measure(c, e);
      expect(res.ok).toBe(false);
      expect(res.skipped).toBe("no phone frame");
      c.hub.latestFrame = { jpeg: toB64(synthPlate({})), w: 640, h: 480, ts: 100 };
      res = await vision.measure(c, e, { freshAfter: 200, waitS: 0.1 });
      expect(res.skipped).toBe("no fresh phone frame");
      c.hub.latestFrame = null;
    });
  });
});
