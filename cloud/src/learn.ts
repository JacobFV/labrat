// The colour model learns from results. After new readings come in we refit the dyes' absorbance by ridge-regularised
// least squares in absorbance space (pulled toward the current values) and keep the new version only if it predicts
// every reading so far better than the current one. Each attempt is a row in `model`.
import * as sim from "./sim";
import type { Ctx } from "./env";
import type { Result } from "./store";
import { solve } from "./linalg";

const LAMBDA = 0.3; // ridge strength pulling toward the current params
const BG = 245.0;

/** The installed version (set at boot and on every accepted refit). */
export let currentV = 0;

async function versions(c: Ctx) {
  const vs = await c.store.modelVersions();
  if (vs.length) return vs;
  return [await c.store.putModelVersion({ v: 0, params: sim.factoryParams(), accepted: true })];
}

/** Install the newest accepted version into the simulator (DO boot). */
export async function applyLatest(c: Ctx) {
  const cur = (await versions(c)).filter((v) => v.accepted).at(-1)!;
  sim.setParams(cur.params);
  currentV = cur.v;
}

/** Mean RGB distance between the model's prediction and each reading. */
function meanErr(rs: Result[]): number | null {
  const pts = rs.filter((r) => r.rgb);
  if (!pts.length) return null;
  return pts.reduce((s, r) => s + sim.colorDist(sim.mixRgb(r.comp), r.rgb!), 0) / pts.length;
}

export function fit(rs: Pick<Result, "comp" | "rgb">[], p0: sim.SimParams): sim.SimParams {
  const names = sim.DYE_NAMES as readonly string[];
  let absorb: number[][] = names.map((d) => p0.absorbance[d].map(Number));
  const pts = rs.filter((o) => o.rgb && o.comp && Object.keys(o.comp).length);
  if (pts.length) {
    const C = pts.map((o) => { const row = names.map((d) => Number(o.comp[d] ?? 0)); const s = Math.max(row.reduce((a, b) => a + b, 0), 1e-9); return row.map((x) => x / s); });
    const A = pts.map((o) => o.rgb!.map((v: number) => -Math.log10(Math.min(1, Math.max(1e-3, v / BG)))));
    const k = names.length;
    const lhs = Array.from({ length: k }, (_, i) => Array.from({ length: k }, (_, j) => C.reduce((s, r) => s + r[i] * r[j], 0) + (i === j ? LAMBDA : 0)));
    const rhs = Array.from({ length: k }, (_, i) => [0, 1, 2].map((ch) => C.reduce((s, r, n) => s + r[i] * A[n][ch], 0) + LAMBDA * absorb[i][ch]));
    absorb = solve(lhs, rhs);
    absorb[names.indexOf("water")] = [0, 0, 0];
    absorb = absorb.map((row) => row.map((x) => Math.min(4, Math.max(0, x))));
  }
  return { absorbance: Object.fromEntries(names.map((d, i) => [d, absorb[i].map(sim.r4)])), vol_gain: p0.vol_gain ?? 1 };
}

/** Refit from every measured beaker; adopt the new parameters only if the mean error drops. */
export async function refit(c: Ctx, experimentId: string | null = null) {
  const vs = await versions(c);
  const prev = vs.filter((v) => v.accepted).at(-1)!;
  // one data point per beaker: its newest reading with a colour
  const latest = new Map<string, Result>();
  for (const r of await c.store.results()) if (r.rgb) latest.set(`${r.experiment_id}:${r.well}`, r);
  const rs = [...latest.values()];
  sim.setParams(prev.params);
  const before = meanErr(rs);
  const proposed = fit(rs, prev.params);
  sim.setParams(proposed);
  const after = meanErr(rs);
  const accepted = before != null && after != null && after < before - 1e-6;
  if (!accepted) sim.setParams(prev.params);
  const r2 = (x: number | null) => (x == null ? null : Math.round(x * 100) / 100);
  const ver = await c.store.putModelVersion({ v: vs.at(-1)!.v + 1, params: proposed, error_before: r2(before), error_after: r2(after),
    n_results: rs.filter((r) => r.rgb).length, accepted, experiment_id: experimentId });
  if (accepted) currentV = ver.v;
  const out = { v: ver.v, accepted, error_before: ver.error_before, error_after: ver.error_after, n_results: ver.n_results, current_v: currentV };
  await c.store.event("model_refit", { experiment_id: experimentId, ...out });
  c.hub.ui({ t: "model", ...(await model(c)) });
  return out;
}

export async function model(c: Ctx) {
  const vs = await versions(c);
  return {
    current_v: currentV, params: sim.getParams(),
    versions: vs.map((d) => ({ v: d.v, ts: d.ts, params: d.params, error_before: d.error_before, error_after: d.error_after, n_results: d.n_results, accepted: d.accepted, experiment_id: d.experiment_id })),
  };
}
