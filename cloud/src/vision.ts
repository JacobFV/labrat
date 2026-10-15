// Measuring a plate from a phone photo: every beaker that holds liquid -> RGB. Two estimators:
//  1. Calibrated: a camera->deck homography from 4 clicked beaker centres (the mixing grid's corners A1, A7, F7, F1),
//     stored as config `camera_cal`. Sample a small disk at the projected target-beaker centre (median), white-balanced
//     against an empty beaker.
//  2. LLM vision: ask Claude for the target beaker's RGB as JSON.
// Neither available -> skip (the operator can still enter results by hand).
import jpeg from "jpeg-js";
import * as sim from "./sim";
import type { Ctx } from "./env";
import { completeText, llmAvailable } from "./llm";
import { solve } from "./linalg";
import { now } from "./store";

const LAST_ROW = sim.ROWS[sim.PLATE.rows - 1], LAST_COL = sim.PLATE.cols;
export const CORNER_WELLS = ["A1", `A${LAST_COL}`, `${LAST_ROW}${LAST_COL}`, `${LAST_ROW}1`]; // A1, A7, F7, F1
const BLANK_PREF = [CORNER_WELLS[2], CORNER_WELLS[3], CORNER_WELLS[1], "A1", `${LAST_ROW}${LAST_COL - 1}`, `${sim.ROWS[sim.PLATE.rows - 2]}${LAST_COL}`];
const BG = 245.0; // the sim's white background level

export interface CameraCal { id: "camera_cal"; corners: number[][]; image_w: number; image_h: number; wells: string[]; H: number[][]; created_at: number }
export interface Image { width: number; height: number; data: Uint8Array } // RGB, 3 bytes per pixel

// ---------------- geometry ----------------
/** (col, row) of a beaker in pitch units. */
function plateCr(well: string): [number, number] {
  const [x, y] = sim.wellXy(well, sim.PLATE);
  return [(x - sim.PLATE.origin[0]) / sim.PLATE.pitch, (y - sim.PLATE.origin[1]) / sim.PLATE.pitch];
}

/** Grid (col,row) of the 4 corner beakers -> image px (exact 4-point homography, h33 = 1). */
export function homography(corners: number[][]): number[][] {
  const A: number[][] = [], b: number[][] = [];
  CORNER_WELLS.forEach((w, i) => {
    const [u, v] = plateCr(w), [x, y] = corners[i];
    A.push([u, v, 1, 0, 0, 0, -u * x, -v * x]); b.push([x]);
    A.push([0, 0, 0, u, v, 1, -u * y, -v * y]); b.push([y]);
  });
  const h = solve(A, b).map((r) => r[0]);
  return [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1]];
}

function apply(H: number[][], c: number, r: number): [number, number] {
  const p = [0, 1, 2].map((i) => H[i][0] * c + H[i][1] * r + H[i][2]);
  return [p[0] / p[2], p[1] / p[2]];
}

export const project = (H: number[][], well: string) => apply(H, ...plateCr(well));

export function decode(jpegB64: string): Image {
  const bin = atob(jpegB64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const img = jpeg.decode(bytes, { useTArray: true, formatAsRGBA: false, maxMemoryUsageInMB: 256 });
  return { width: img.width, height: img.height, data: img.data as Uint8Array };
}

const median = (xs: number[]) => { const s = [...xs].sort((a, b) => a - b), n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; };

function diskMedian(img: Image, cx: number, cy: number, r: number): number[] | null {
  r = Math.max(2, r);
  const x0 = Math.trunc(Math.max(0, cx - r)), x1 = Math.trunc(Math.min(img.width, cx + r + 1));
  const y0 = Math.trunc(Math.max(0, cy - r)), y1 = Math.trunc(Math.min(img.height, cy + r + 1));
  if (x1 <= x0 || y1 <= y0) return null;
  const ch: number[][] = [[], [], []];
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
    if ((x - cx) ** 2 + (y - cy) ** 2 > r * r) continue;
    const i = (y * img.width + x) * 3;
    for (let k = 0; k < 3; k++) ch[k].push(img.data[i + k]);
  }
  return ch[0].length ? ch.map(median) : null;
}

/** Median RGB in a disk at the projected beaker centre, optionally white-balanced to `blank` (-> BG grey). */
export function sampleWell(img: Image, cal: Pick<CameraCal, "H" | "image_w" | "image_h">, well: string, blank?: string | null) {
  const sx = img.width / Math.max(1, cal.image_w || img.width), sy = img.height / Math.max(1, cal.image_h || img.height);
  const Hs = cal.H.map((row, i) => row.map((v) => v * [sx, sy, 1][i]));
  const [cx, cy] = project(Hs, well);
  const [c, r] = plateCr(well);
  const [px, py] = apply(Hs, c + 1, r);
  const rad = 0.3 * Math.hypot(px - cx, py - cy); // ~30% of the local beaker pitch in image px
  const raw = diskMedian(img, cx, cy, rad);
  if (!raw) throw new Error(`well ${well} projects outside the image (${cx.toFixed(0)},${cy.toFixed(0)})`);
  const r1 = (v: number) => Math.round(v * 10) / 10;
  const out: any = { well, center: [r1(cx), r1(cy)], radius: r1(rad), raw_rgb: raw.map(Math.round) };
  let rgb = raw;
  if (blank) {
    const ref = diskMedian(img, ...project(Hs, blank), rad);
    if (ref && ref.every((v) => v > 20)) {
      const gain = ref.map((v) => BG / v);
      rgb = rgb.map((v, k) => v * gain[k]);
      Object.assign(out, { blank, blank_rgb: ref.map(Math.round), wb_gain: gain.map((g) => Math.round(g * 1000) / 1000) });
    }
  }
  out.rgb = rgb.map((v) => Math.min(255, Math.max(0, Math.round(v))));
  return out as { well: string; rgb: number[]; raw_rgb: number[]; center: number[]; radius: number; blank?: string };
}

// ---------------- calibration storage ----------------
export const getCal = (c: Ctx) => c.store.getConfig<CameraCal>("camera_cal");

export async function setCal(c: Ctx, corners: number[][], imageW: number, imageH: number): Promise<CameraCal> {
  if (!Array.isArray(corners) || corners.length !== 4 || corners.some((p) => !Array.isArray(p) || p.length !== 2))
    throw new Error(`need 4 corners [[x,y],...] in order ${CORNER_WELLS.join(", ")}`);
  const pts = corners.map(([x, y]) => [Number(x), Number(y)]);
  const doc: CameraCal = { id: "camera_cal", corners: pts, image_w: Math.trunc(imageW), image_h: Math.trunc(imageH), wells: CORNER_WELLS, H: homography(pts), created_at: now() };
  await c.store.setConfig("camera_cal", doc);
  await c.store.event("camera_cal", { corners: doc.corners, image_w: doc.image_w, image_h: doc.image_h });
  return doc;
}

// ---------------- LLM fallback ----------------
async function llmRgb(c: Ctx, jpegB64: string, wells: string[]): Promise<{ well: string; rgb: number[] }[] | null> {
  if (!llmAvailable(c.env)) return null;
  const prompt = `This photo shows a lab deck with a printed rack of small clear beakers in 6 rows (A-F, front to back) and 8 columns (1-8, left to right; column 8 holds the stock dyes). Estimate the colour of the liquid in beakers ${wells.join(", ")} as seen from above, white-balanced so an empty beaker reads about (245,245,245). Reply with JSON only: {"A1":[r,g,b], ...}`;
  const text = await completeText(c.env, [{ type: "image", source: { type: "base64", media_type: "image/jpeg", data: jpegB64 } }, { type: "text", text: prompt }], 800);
  const m = /\{[\s\S]*\}/.exec(text);
  if (!m) return null;
  const j = JSON.parse(m[0]);
  const out = wells.filter((w) => Array.isArray(j[w]) && j[w].length === 3).map((w) => ({ well: w, rgb: j[w].map((v: number) => Math.min(255, Math.max(0, Math.trunc(Number(v))))) }));
  return out.length ? out : null;
}

/** Read every beaker of `e` that holds liquid from a phone frame (newer than `freshAfter` if given). Never throws. */
export async function measure(c: Ctx, e: any, o: { freshAfter?: number | null; waitS?: number } = {}): Promise<any> {
  const wells = Object.keys(e.predicted ?? {});
  if (o.freshAfter != null) c.hub.phone({ t: "snap" });
  const frame = o.freshAfter != null ? await c.hub.waitFrame(o.freshAfter, o.waitS ?? Number(c.env.LABRAT_PHOTO_WAIT_S ?? 3)) : c.hub.latestFrame;
  if (!frame?.jpeg) return { ok: false, skipped: o.freshAfter == null ? "no phone frame" : "no fresh phone frame" };
  try {
    const cal = await getCal(c);
    if (cal) {
      const img = decode(frame.jpeg);
      const blank = BLANK_PREF.find((b) => !wells.includes(b)) ?? null;
      return { ok: true, method: "camera_cal", frame_ts: frame.ts, readings: wells.map((w) => sampleWell(img, cal, w, blank)) };
    }
    const readings = await llmRgb(c, frame.jpeg, wells);
    if (readings) return { ok: true, method: "llm", frame_ts: frame.ts, readings };
    return { ok: false, skipped: "no camera calibration and no vision LLM" };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}
