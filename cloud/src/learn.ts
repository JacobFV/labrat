// Learning loop. After each real run an observation (operator rating / RGB, or vision) comes in. We:
//  1. compare predicted vs observed well colour,
//  2. refit the simulator: dye absorbance by ridge-regularised least squares in absorbance space (pulled toward the
//     previous parameters), pipette volume gain from measured vs commanded volume,
//  3. store a versioned sim_params row and install it only if the mean error over all observations improves,
//  4. re-score every past variant with the new parameters,
//  5. promote the run's protocol to procedural memory only if its own prediction error improved (and rating >= 3),
//  6. write a semantic fact and an `rsi.update` event.
import * as sim from "./sim";
import type { Ctx } from "./env";
import { emitRun, variant } from "./lab";
import { now } from "./store";
import { solve } from "./linalg";

const LAMBDA = 0.3; // ridge strength pulling toward previous params
const BG = 245.0;

const dist = (a: number[], b: number[]) => sim.colorDist(a, b);

export async function currentVersion(c: Ctx) {
  const vs = await c.store.simVersions();
  const acc = vs.filter((v) => v.accepted);
  return acc.length ? acc[acc.length - 1] : ensureBaseline(c);
}

export async function ensureBaseline(c: Ctx) {
  const vs = await c.store.simVersions();
  if (vs.length) return vs[0];
  return c.store.putSimVersion({ v: 0, params: sim.factoryParams(), n_runs: 0, accepted: true, note: "factory defaults" });
}

/** Install the newest accepted parameters into the simulator (DO boot). */
export async function applyLatest(c: Ctx) {
  sim.setParams((await currentVersion(c)).params);
}

function meanErr(obs: any[]): number | null {
  const pts = obs.filter((o) => o.rgb);
  if (!pts.length) return null;
  return pts.reduce((s, o) => s + dist(sim.mixRgb(o.comp), o.rgb), 0) / pts.length;
}

export function fit(obs: any[], p0: sim.SimParams): sim.SimParams {
  const names = sim.DYE_NAMES as readonly string[];
  let absorb: number[][] = names.map((d) => p0.absorbance[d].map(Number));
  const pts = obs.filter((o) => o.rgb && o.comp && Object.keys(o.comp).length);
  if (pts.length) {
    const C = pts.map((o) => { const row = names.map((d) => Number(o.comp[d] ?? 0)); const s = Math.max(row.reduce((a, b) => a + b, 0), 1e-9); return row.map((x) => x / s); });
    const A = pts.map((o) => o.rgb.map((v: number) => -Math.log10(Math.min(1, Math.max(1e-3, v / BG)))));
    const k = names.length;
    const lhs = Array.from({ length: k }, (_, i) => Array.from({ length: k }, (_, j) => C.reduce((s, r) => s + r[i] * r[j], 0) + (i === j ? LAMBDA : 0)));
    const rhs = Array.from({ length: k }, (_, i) => [0, 1, 2].map((ch) => C.reduce((s, r, n) => s + r[i] * A[n][ch], 0) + LAMBDA * absorb[i][ch]));
    absorb = solve(lhs, rhs);
    absorb[names.indexOf("water")] = [0, 0, 0];
    absorb = absorb.map((row) => row.map((x) => Math.min(4, Math.max(0, x))));
  }
  const gains = obs.filter((o) => o.vol_ul && o.cmd_vol_ul).map((o) => o.vol_ul / o.cmd_vol_ul);
  const gain = gains.length ? Math.min(1.5, Math.max(0.5, gains.reduce((a, b) => a + b, 0) / gains.length)) : (p0.vol_gain ?? 1);
  return { absorbance: Object.fromEntries(names.map((d, i) => [d, absorb[i].map(sim.r4)])), vol_gain: sim.r4(gain) };
}

function describe(pred: number[], obs: number[]): string {
  const words = ["red", "green", "blue"].flatMap((ch, i) => { const d = obs[i] - pred[i]; return Math.abs(d) > 20 ? [`${d > 0 ? "more" : "less"} ${ch}`] : []; });
  return words.join(", ") || "close to prediction";
}

async function rescoreAll(c: Ctx, v: number): Promise<number> {
  let n = 0;
  for (const e of await c.store.listExperiments(200)) {
    let changed = false;
    for (const vr of e.variants ?? []) {
      if (!vr.sim) continue;
      const r = sim.simulate(vr.protocol, e.goal, false);
      delete r.frames;
      vr.sim = r;
      n++;
      changed = true;
    }
    if (!changed) continue;
    const ranked = e.variants.filter((x: sim.Variant) => x.sim?.ok).sort((a: sim.Variant, b: sim.Variant) => b.sim!.score - a.sim!.score);
    e.best_variant_id = ranked[0]?.id ?? null;
    e.rescored_with = v;
    await c.store.putExperiment(e);
  }
  return n;
}

export async function observe(c: Ctx, runId: string, o: { rating?: number | null; rgb?: number[] | null; well?: string | null; vol_ul?: number | null; source?: string } = {}) {
  const run = await c.store.getRun(runId);
  if (!run) throw new Error(`no run ${runId}`);
  const e = await c.store.getExperiment(run.experiment_id);
  const v = variant(e, run.variant_id);
  await ensureBaseline(c);
  const base = sim.simulate(v.protocol, e.goal, false);
  const well = (o.well || base.best_well || "A1").toUpperCase();
  const w = base.wells[well] ?? ({} as Partial<sim.WellResult>);
  const comp = w.comp ?? {};
  const rgb = o.rgb ? o.rgb.map((x) => Math.trunc(Number(x))) : null;
  const rating = o.rating ?? null, source = o.source ?? "operator";
  const cmdVol = v.protocol.filter((s) => s.op === "dispense" && String(s.dst ?? "").toUpperCase() === well).reduce((a, s) => a + Number(s.ul ?? 0), 0);
  const ob = await c.store.putObservation({ run_id: runId, experiment_id: e.id, variant_id: v.id, well, rating, rgb, comp,
    predicted_rgb: w.rgb ?? null, vol_ul: o.vol_ul ?? null, cmd_vol_ul: cmdVol, source });
  Object.assign(run, { observation_rating: rating, observed_rgb: rgb });
  await emitRun(c, run);

  const obs = await c.store.observations();
  const prev = await currentVersion(c);
  const errBefore = meanErr(obs);
  const singleBefore = rgb ? dist(sim.mixRgb(comp), rgb) : null;
  const proposed = fit(obs, prev.params);
  sim.setParams(proposed);
  const errAfter = meanErr(obs);
  const singleAfter = rgb ? dist(sim.mixRgb(comp), rgb) : null;
  const accepted = errBefore != null && errAfter != null && errAfter < errBefore - 1e-6;
  if (!accepted) sim.setParams(prev.params);
  const nv = (await c.store.simVersions()).length;
  const r2 = (x: number | null) => (x == null ? null : Math.round(x * 100) / 100);
  const ver = await c.store.putSimVersion({ v: nv, params: accepted ? proposed : prev.params, proposed_params: proposed,
    error_before: r2(errBefore), error_after: r2(errAfter), n_runs: new Set(obs.map((x) => x.run_id)).size, accepted, trigger_run_id: runId, parent_v: prev.v });
  const rescored = accepted ? await rescoreAll(c, ver.v) : 0;

  if (rgb) {
    const pred = w.rgb ?? [];
    await c.store.remember(`${v.params.dyes.join("+")} at ratios ${JSON.stringify(v.params.ratios)} (${cmdVol.toFixed(0)} µL) looked rgb(${rgb.join(", ")}); predicted rgb(${pred.join(", ")}): real result was ${describe(pred.length ? pred : [0, 0, 0], rgb)}.`,
      { tags: ["observation", e.id], type: "semantic", source_run_id: runId, confidence: 0.8 });
  }
  let promoted = false;
  if (accepted && singleBefore != null && singleAfter != null && singleAfter < singleBefore && (rating ?? 3) >= 3) {
    const steps = v.protocol.map(({ op, ...rest }) => `${op} ${Object.entries(rest).map(([k, x]) => `${k}=${x}`).join(", ")}`).join("; ");
    await c.store.remember(`PROCEDURE for '${e.goal}': ${JSON.stringify(v.params)} → ${steps}`, { tags: ["procedure", e.id], meta: { protocol: v.protocol, params: v.params },
      type: "procedural", source_run_id: runId, confidence: Math.round(Math.max(0.1, 1 - singleAfter / 441.7) * 1000) / 1000 });
    promoted = true;
  }
  const metrics = { v: ver.v, accepted, error_before: ver.error_before, error_after: ver.error_after, run_error_before: singleBefore, run_error_after: singleAfter,
    rescored_variants: rescored, promoted_procedure: promoted, rating, source };
  await c.store.event("rsi.update", { run_id: runId, ...metrics });
  c.hub.ui({ t: "learning", ...(await learning(c)) });
  return { observation: ob, version: ver, ...metrics };
}

export async function learning(c: Ctx) {
  await ensureBaseline(c);
  const vs = await c.store.simVersions();
  const cur = [...vs].reverse().find((v) => v.accepted) ?? vs[0];
  return {
    versions: vs.map((d) => ({ v: d.v, created_at: d.ts, params: d.params, error_before: d.error_before, error_after: d.error_after, n_runs: d.n_runs, accepted: d.accepted, trigger_run_id: d.trigger_run_id })),
    current_v: cur.v,
  };
}

export const TAXONOMY = [
  "Lab devices: arms → SO-101 gripper arms (so_a, so_b, one each side of the CNC); motion stage → 3018 CNC (cnc, GRBL); liquid handler → syringe pipette (pipette, in the CNC head on the spindle axis, 200 µL tips on a luer-to-tip adapter, 12:1 gear train + rack on a continuous-rotation FS90R servo on servo-hub channel 0); probe arm → servo on hub channel 1 that swings the capacitive liquid-level probe down; camera → phone on a pan/tilt stand (stand, hub channels 2 and 3) at the front of the CNC; servo hub → ESP32 + PCA9685 (hub), over USB via the laptop bridge or over Bluetooth via the phone.",
  "Reagents: dyes → red, yellow, blue (subtractive primaries); diluent → water. Stock beakers in column 8 of the beaker matrix: A8 water, B8 red, C8 yellow, D8 blue (4.5 mL each).",
  "Labware: FT-200L-RS tip_rack (8x12, 200 µL tips) at (17,19.25); printed beaker matrix (6x8 beakers of 15.3 x 50 mm on 19 mm, A1 at (153.25,32)), columns 1-7 = mixing beakers A1-F7 (5 mL), column 8 = stocks; waste (280,170); tip eject bar (10,170).",
  "Reagents on hand (not simulated): white vinegar, baking soda, borax, ammonium chloride, pink Himalayan salt, PLA crumbs, food crumbs, the operator's own saliva / mucus / cheek-cell scrapings.",
  "Colours: secondary colours → orange (red+yellow), green (blue+yellow), purple (red+blue); tertiary → brown (all three).",
];

export async function seedTaxonomy(c: Ctx): Promise<void> {
  const have = await c.store.memoryTexts("taxonomic");
  for (const t of TAXONOMY) if (!have.has(t)) await c.store.remember(t, { tags: ["taxonomy"], type: "taxonomic", confidence: 1.0 });
}
