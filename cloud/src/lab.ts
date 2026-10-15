// Experiments, simulation orchestration and real-run execution on hardware via the hub.
// Safety invariant: no real run without a passing simulation; a run waits for human approval unless auto-approved.
import * as sim from "./sim";
import { flag, type Ctx } from "./env";
import { CommandError, sleep } from "./hub";
import { hex, now } from "./store";
import * as vision from "./vision";

const F_XY = 3000, F_Z = 600;
const PARK: [number, number] = [290, 175]; // out of the camera's view

export function simChecks(s: sim.SimResult) {
  const errs = s.errors ?? [], warns = s.warnings ?? [];
  const out = [{ name: "safety", ok: !errs.length, msg: errs.slice(0, 3).join("; ") }, { name: "clean", ok: !warns.length, msg: warns.slice(0, 3).join("; ") }];
  if (s.target) out.push({ name: "colour", ok: (s.score ?? 0) >= 0.8, msg: `target ${s.target.name} score ${s.score}` });
  return out;
}

/** Variant for UI/REST: no frames; sim results flattened to score/predicted_rgb/checks/pass/time_s. */
export function flatVariant(v: sim.Variant) {
  const { frames: _f, ...out } = v as any;
  const s = v.sim;
  if (s) Object.assign(out, { score: s.score, predicted_rgb: s.predicted_rgb, pass: !!s.ok, time_s: s.duration_s, checks: simChecks(s), has_frames: !!v.protocol });
  return out;
}

/** Experiment for UI/REST (no frames). */
export const strip = (e: any) => ({ ...e, variants: (e.variants ?? []).map(flatVariant) });

/** Frames are never persisted: re-run the (deterministic) sim from the protocol. */
export function frames(e: any, v: sim.Variant): sim.Frame[] {
  if (!v.protocol) return [];
  const r = sim.simulate(v.protocol, e.goal ?? "");
  v.sim ??= { ...r, frames: undefined };
  return r.frames ?? [];
}

export function variant(e: any, vid: string): sim.Variant {
  const v = (e.variants ?? []).find((x: sim.Variant) => x.id === vid);
  if (!v) throw new Error(`no variant ${vid} in ${e.id}`);
  return v;
}

const emitExperiment = (c: Ctx, e: any) => c.hub.ui({ t: "experiment", experiment: strip(e) });

export async function emitRun(c: Ctx, run: any): Promise<void> {
  await c.store.putRun(run);
  c.hub.ui({ t: "run", run });
}

async function mustExperiment(c: Ctx, id: string) {
  const e = await c.store.getExperiment(id);
  if (!e) throw new Error(`no experiment ${id}`);
  return e;
}

export async function createExperiment(c: Ctx, goal: string, nVariants = 12, variants?: sim.Variant[] | null, source = "deterministic") {
  const n = Math.max(1, Math.min(Math.trunc(nVariants || 12), sim.PLATE.rows * sim.PLATE.cols));
  const vs = variants ?? sim.generateVariants(goal, n, Math.floor(Date.now() / 1000) % 10000);
  const e = await c.store.putExperiment({ id: "exp_" + hex(6), goal, status: "designed", source, variants: vs, best_variant_id: null, selected_variant_id: null, created: now() });
  await c.store.event("experiment_created", { experiment_id: e.id, goal, n: vs.length, source });
  emitExperiment(c, e);
  return e;
}

export async function simulateExperiment(c: Ctx, expId: string, nVariants?: number | null) {
  const e = await mustExperiment(c, expId);
  e.variants ??= [];
  if (nVariants && nVariants > e.variants.length) e.variants.push(...sim.generateVariants(e.goal, nVariants, e.variants.length + 7).slice(e.variants.length));
  const t0 = performance.now();
  const frameSets: Record<string, sim.Frame[]> = {};
  for (const v of e.variants as sim.Variant[]) {
    const r = sim.simulate(v.protocol, e.goal);
    frameSets[v.id] = r.frames ?? [];
    delete r.frames;
    v.sim = r;
  }
  const dt = performance.now() - t0;
  const ranked = (e.variants as sim.Variant[]).filter((v) => v.sim!.ok).sort((a, b) => b.sim!.score - a.sim!.score);
  e.best_variant_id = ranked[0]?.id ?? null;
  e.status = "simulated";
  e.sim_summary = { n: e.variants.length, passing: ranked.length, sim_ms: Math.round(dt * 10) / 10, best_score: ranked[0]?.sim!.score ?? null };
  await c.store.putExperiment(e);
  await c.store.event("simulated", { experiment_id: expId, ...e.sim_summary });
  emitExperiment(c, e);
  if (ranked.length) {
    const b = ranked[0];
    c.bg(c.store.remember(`Simulated ${e.variants.length} variants for '${e.goal}': best ${b.id} ${JSON.stringify(b.params)} predicted rgb(${b.sim!.predicted_rgb?.join(", ")}) score ${b.sim!.score}.`,
      { tags: ["sim", expId], type: "episodic", confidence: 0.5, meta: { experiment_id: expId } }), "remember sim");
  }
  for (const v of [...e.variants].sort((a: sim.Variant, b: sim.Variant) => Number(a.id !== e.best_variant_id) - Number(b.id !== e.best_variant_id)))
    c.hub.ui({ t: "sim", experiment_id: expId, variant_id: v.id, score: v.sim.score, ok: v.sim.ok, predicted_rgb: v.sim.predicted_rgb, variant: flatVariant(v), frames: frameSets[v.id] });
  return e;
}

export async function selectVariant(c: Ctx, expId: string, vid: string, reason = "") {
  const e = await mustExperiment(c, expId);
  variant(e, vid);
  Object.assign(e, { selected_variant_id: vid, selection_reason: reason });
  await c.store.putExperiment(e);
  emitExperiment(c, e);
  return e;
}

// ---------------- runs ----------------
export async function requestRun(c: Ctx, expId: string, vid?: string | null, auto = false) {
  const e = await mustExperiment(c, expId);
  vid = vid || e.selected_variant_id || e.best_variant_id;
  if (!vid) throw new Error("no variant selected and no simulated best variant; simulate first");
  const v = variant(e, vid);
  if (!v.sim) throw new Error(`variant ${vid} has not been simulated`);
  if (!v.sim.ok) throw new Error(`variant ${vid} failed simulation checks: ${JSON.stringify(v.sim.errors.slice(0, 3))}`);
  const run = { id: "run_" + hex(6), experiment_id: expId, variant_id: vid, status: "awaiting_approval", step: 0, n_steps: v.protocol.length, log: [] as any[],
    created: now(), predicted_rgb: v.sim.predicted_rgb };
  await emitRun(c, run);
  await c.store.event("run_requested", { run_id: run.id, experiment_id: expId, variant_id: vid });
  if (auto || flag(c.env.LABRAT_AUTO_APPROVE)) return approve(c, run.id);
  return run;
}

export async function approve(c: Ctx, runId: string) {
  const run = await c.store.getRun(runId);
  if (!run) throw new Error(`no run ${runId}`);
  if (run.status !== "awaiting_approval") return run;
  Object.assign(run, { status: "running", started: now() });
  await emitRun(c, run);
  const task = { aborted: false };
  c.runs.set(runId, task);
  c.bg(execute(c, run, task), `run ${runId}`);
  return run;
}

/** Stop everything: flag running runs, fail in-flight commands, then stop every motion device. */
export async function abort(c: Ctx): Promise<void> {
  for (const t of c.runs.values()) t.aborted = true;
  c.hub.failAll("aborted");
  const stops: [string, string][] = [["cnc", "stop"], ["so_a", "stop"], ["so_b", "stop"]];
  const jobs = stops.filter(([d]) => c.hub.online(d)).map(([d, a]) => c.hub.cmd(d, a, {}, 5));
  if (c.hub.online("hub")) jobs.push(c.hub.raw("hub", "line", { msg: { cmd: "stop" } }, 5));
  const res = await Promise.allSettled(jobs);
  for (const l of Object.values(c.hub.logical)) l.state.moving = false;
  for (const r of res) if (r.status === "rejected") c.hub.log("warn", `abort: ${r.reason?.message ?? r.reason}`);
  await c.store.event("abort", {});
}

/** Absolute CNC move always sending full x,y,z (tracked position fills gaps). */
async function mv(c: Ctx, p: { x?: number; y?: number; z?: number }, f = F_XY) {
  for (const k of ["x", "y", "z"] as const) if (p[k] != null) c.pos[k] = Math.round(Number(p[k]) * 100) / 100;
  await c.hub.cmd("cnc", "move", { ...c.pos, f }, 60);
}

async function goto(c: Ctx, x: number, y: number, z: number) {
  await mv(c, { z: sim.Z_SAFE }, F_Z);
  await mv(c, { x, y }, F_XY);
  await mv(c, { z }, F_Z);
}

const pip = (c: Ctx, action: string, args: Record<string, any> = {}) => c.hub.cmd("pipette", action, args);

export async function execStep(c: Ctx, s: sim.Step, run?: any): Promise<string> {
  switch (s.op) {
    case "pick_tip": {
      const [x, y, zt] = sim.wellXyz(s.rack_well, sim.TIP_RACK, sim.Z_TIP);
      await goto(c, x, y, zt);
      await mv(c, { z: sim.Z_SAFE }, F_Z);
      return `picked tip ${s.rack_well}`;
    }
    case "aspirate": {
      const [, x, y, z] = sim.loc(s.src);
      await goto(c, x, y, sim.Z_SAFE); // above the liquid
      await pip(c, "prime");            // plunger to its air-cushion ready height while above liquid
      await mv(c, { z }, F_Z);
      await pip(c, "aspirate", { ul: s.ul });
      await mv(c, { z: sim.Z_SAFE }, F_Z);
      return `aspirated ${s.ul} µL from ${s.src}`;
    }
    case "dispense": {
      const [, x, y, z] = sim.loc(s.dst);
      await goto(c, x, y, z);
      await pip(c, "dispense", { ul: s.ul ?? null });
      await pip(c, "blowout");
      await mv(c, { z: sim.Z_SAFE }, F_Z);
      return `dispensed ${s.ul} µL into ${s.dst}`;
    }
    case "mix": {
      const [, x, y, z] = sim.loc(s.well);
      const ul = Math.min(Number(s.ul ?? 50), 190);
      await goto(c, x, y, sim.Z_SAFE);
      await pip(c, "prime");
      await mv(c, { z }, F_Z);
      for (let i = 0; i < Math.trunc(Number(s.n ?? 3)); i++) {
        await pip(c, "aspirate", { ul });
        await pip(c, "dispense", { ul });
      }
      await pip(c, "blowout");
      await mv(c, { z: sim.Z_SAFE }, F_Z);
      return `mixed ${s.well}`;
    }
    case "eject_tip": {
      const [ex, ey, ez] = sim.ejectXyz();
      await goto(c, ex, ey, ez);
      await mv(c, { y: ey + 8 }, 600);
      await mv(c, { z: sim.Z_SAFE }, F_Z);
      await pip(c, "rest");
      return "ejected tip";
    }
    case "photo": {
      // Park the pipette out of view, aim the stand at the deck (when one is connected), ask the phone for a frame and
      // require one captured after both finished.
      await mv(c, { z: sim.Z_SAFE }, F_Z);
      await mv(c, { x: PARK[0], y: PARK[1] }, F_XY);
      if (c.hub.online("hub")) await c.hub.cmd("stand", "look", { at: "deck" });
      const tReady = now();
      c.hub.phone({ t: "snap" });
      const frame = await c.hub.waitFrame(tReady, Number(c.env.LABRAT_PHOTO_WAIT_S ?? 3));
      if (run) {
        run.photo_after = tReady;
        if (frame) run.photo_ts = frame.ts;
      }
      return frame ? "photo taken" : "photo requested (no fresh phone frame)";
    }
    case "wait":
      await sleep(Number(s.s ?? 1) * 1000);
      return `waited ${s.s ?? 1} s`;
  }
  throw new Error(`unknown op ${s.op}`);
}

async function execute(c: Ctx, run: any, task: { aborted: boolean }): Promise<void> {
  const e = await c.store.getExperiment(run.experiment_id);
  const v = variant(e, run.variant_id);
  try {
    for (const [i, s] of v.protocol.entries()) {
      if (task.aborted) throw new CommandError("aborted");
      run.step = i;
      const msg = await execStep(c, s, run);
      run.log.push({ i, op: s.op, msg, t: Math.round((now() - run.started) * 10) / 10 });
      await emitRun(c, run);
    }
    run.status = "done";
    if (c.hub.latestFrame && run.photo_ts == null) run.photo_ts = c.hub.latestFrame.ts;
    if (v.protocol.some((s) => s.op === "photo") && flag(c.env.LABRAT_AUTO_OBSERVE, true))
      // only a frame captured after the photo step's park counts (never a stale pre-run frame)
      c.bg(vision.autoObserve(c, run.id, { freshAfter: run.photo_after ?? now() }), "auto observe");
    run.observation = `Ran '${e.goal}' variant ${v.id} params=${JSON.stringify(v.params)} predicted_rgb=${JSON.stringify(v.sim!.predicted_rgb)} score=${v.sim!.score} in ${Math.round(now() - run.started)} s on real hardware.`;
    await c.store.remember(run.observation, { tags: ["run", e.id], meta: { run_id: run.id }, type: "episodic", source_run_id: run.id, confidence: 0.9 });
  } catch (err) {
    run.status = task.aborted ? "aborted" : "failed";
    if (!task.aborted) run.error = (err as Error).message;
  } finally {
    run.finished = now();
    c.runs.delete(run.id);
    await emitRun(c, run);
    await c.store.event("run_" + run.status, { run_id: run.id, error: run.error ?? null });
  }
}
