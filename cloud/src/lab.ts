// Experiments. An experiment is one plate: samples (µL of each stock per beaker) -> steps -> dry run -> one real run
// -> results (one reading per beaker). Repeating an experiment makes a new one.
// Safety invariant: nothing runs unless its dry run passed, and a human approves the run unless auto-approved.
import * as sim from "./sim";
import { flag, type Ctx } from "./env";
import { CommandError, sleep } from "./hub";
import { hex, now, type Result } from "./store";
import * as learn from "./learn";
import * as vision from "./vision";

const F_XY = 3000, F_Z = 600;
const PARK: [number, number] = [290, 175]; // out of the camera's view
const EDITABLE = new Set(["draft", "ready"]);
export const ACTIVE = ["awaiting_approval", "running"];

const r1 = (x: number) => Math.round(x * 10) / 10;
const dist = (a: number[] | null | undefined, b: number[] | null | undefined) => (a && b ? r1(sim.colorDist(a, b)) : null);

/** Dry-run the experiment with the current colour model: checks, time, predicted beakers. Sets draft / ready. */
export function check(e: any): any {
  const r = sim.simulate(e.steps, false);
  e.check = { ok: r.ok, errors: r.errors, warnings: r.warnings, duration_s: r.duration_s, tips_used: r.tips_used, model_v: learn.currentV };
  e.predicted = r.wells;
  e.status = r.ok ? "ready" : "draft";
  return e;
}

/** Validate and apply the parts of an experiment a person writes: title, target, samples or steps, notes. */
function apply(e: any, b: any) {
  if (b.title != null) e.title = String(b.title).trim() || e.title;
  if ("target" in b) e.target = sim.parseTarget(b.target);
  if (b.notes != null) e.notes = String(b.notes);
  if (b.samples != null) {
    e.samples = sim.placeSamples(b.samples);
    e.steps = b.steps ?? sim.stepsFor(e.samples);
  } else if (b.steps != null) {
    if (!Array.isArray(b.steps) || b.steps.some((s: any) => !s || typeof s.op !== "string")) throw new Error("steps must be a list of {op, ...}");
    e.steps = b.steps;
    e.samples = [];
  }
  if (!e.steps?.length) throw new Error("an experiment needs samples (or steps)");
}

export async function create(c: Ctx, b: any, source = "operator") {
  const e: any = { id: "exp_" + hex(6), title: "Untitled", target: null, notes: "", samples: [], steps: [], log: [], source, created: now() };
  if (!b.title && b.target) b.title = `Toward ${b.target}`;
  apply(e, b);
  check(e);
  await save(c, e);
  await c.store.event("experiment_created", { id: e.id, title: e.title, n: e.samples.length, source });
  return e;
}

export async function edit(c: Ctx, id: string, b: any) {
  const e = await must(c, id);
  const onlyNotes = Object.keys(b).every((k) => k === "notes");
  if (!onlyNotes && !EDITABLE.has(e.status)) throw new Error(`experiment ${id} is ${e.status}; repeat it to change it`);
  apply(e, b);
  if (!onlyNotes) check(e);
  return save(c, e);
}

/** A fresh copy (same title, target, samples, steps) to run again. */
export async function repeat(c: Ctx, id: string) {
  const e = await must(c, id);
  return create(c, { title: e.title, target: e.target, ...(e.samples?.length ? { samples: e.samples } : { steps: e.steps }), notes: `Repeat of ${e.id}.` }, "repeat");
}

/** Frames for the 3D replay (never stored: the dry run is deterministic). */
export const frames = (e: any): sim.Frame[] => sim.simulate(e.steps, true).frames ?? [];

// ---------------- what clients see ----------------
/** The experiment with one row per beaker: recipe, predicted colour, latest reading, distances to the target. */
export async function view(c: Ctx, e: any) {
  const latest = new Map<string, Result>();
  for (const r of await c.store.results(e.id)) latest.set(r.well, r);
  const wells = [...new Set([...(e.samples ?? []).map((s: sim.Sample) => s.well), ...Object.keys(e.predicted ?? {})])];
  const t = e.target?.rgb;
  const beakers = wells.map((w) => {
    const s = (e.samples ?? []).find((x: sim.Sample) => x.well === w) ?? {};
    const p = e.predicted?.[w], m = latest.get(w);
    return {
      well: w, label: s.label ?? null, ...Object.fromEntries(sim.STOCKS.map((d) => [d, (s as any)[d] ?? 0])),
      predicted_rgb: p?.rgb ?? null, vol_ul: p?.vol ?? null,
      measured_rgb: m?.rgb ?? null, rating: m?.rating ?? null, note: m?.note ?? null, source: m?.source ?? null,
      error: dist(p?.rgb, m?.rgb), ...(t ? { predicted_to_target: dist(p?.rgb, t), measured_to_target: dist(m?.rgb, t) } : {}),
    };
  });
  const closest = (k: "predicted_to_target" | "measured_to_target") =>
    beakers.filter((b: any) => b[k] != null).sort((a: any, b: any) => a[k] - b[k])[0]?.well ?? null;
  return { ...e, beakers, ...(t ? { closest_predicted: closest("predicted_to_target"), closest_measured: closest("measured_to_target") } : {}) };
}

async function save(c: Ctx, e: any) {
  await c.store.putExperiment(e);
  c.hub.ui({ t: "experiment", experiment: await view(c, e) });
  return e;
}

export async function must(c: Ctx, id: string) {
  const e = await c.store.getExperiment(id);
  if (!e) throw new Error(`no experiment ${id}`);
  return e;
}

// ---------------- results ----------------
/** Record readings ({well, rgb?, rating?, note?}), then refit the colour model from every reading so far. */
export async function addResults(c: Ctx, id: string, rows: any[], source = "operator") {
  const e = await must(c, id);
  if (!Array.isArray(rows) || !rows.length) throw new Error("give results: [{well, rgb?, rating?, note?}]");
  const out: Result[] = [];
  for (const r of rows) {
    const well = String(r.well ?? "").toUpperCase();
    const p = e.predicted?.[well];
    if (!p) throw new Error(`${well || "(no well)"} is not a beaker of ${id} (${Object.keys(e.predicted ?? {}).join(", ")})`);
    const rgb = Array.isArray(r.rgb) && r.rgb.length === 3 ? r.rgb.map((x: number) => Math.max(0, Math.min(255, Math.round(Number(x))))) : null;
    const rating = r.rating == null ? null : Math.max(1, Math.min(5, Math.round(Number(r.rating))));
    if (!rgb && rating == null && !r.note) throw new Error(`${well}: give rgb, rating or note`);
    // a reading without a colour (a rating or a note) keeps the beaker's last measured colour
    const last = rgb ? null : (await c.store.results(id)).filter((x) => x.well === well && x.rgb).at(-1);
    out.push(await c.store.addResult({ experiment_id: id, well, rgb: rgb ?? last?.rgb ?? null, rating, note: r.note ? String(r.note) : null, source: r.source ?? source, comp: p.comp, predicted_rgb: p.rgb }));
  }
  const model = await learn.refit(c, id);
  if (model.accepted) for (const x of await c.store.listExperiments(200, [...EDITABLE])) await save(c, check(x)); // re-predict what has not run
  const measured = out.filter((r) => r.rgb);
  if (measured.length) {
    const lines = measured.map((r) => `${r.well} ${Object.entries(r.comp).map(([k, v]) => `${k} ${Math.round(v)}`).join(" + ")} µL: measured rgb(${r.rgb!.join(", ")}), predicted rgb(${(r.predicted_rgb ?? []).join(", ")})`);
    const note = await c.store.addNote(`Results for "${e.title}" (${id}): ${lines.join("; ")}.`, { experiment_id: id, source: "auto" });
    c.hub.ui({ t: "note", note });
  }
  await save(c, await must(c, id)); // re-read: the refit may have re-predicted this experiment
  return { results: out, model };
}

/** Read every beaker of an experiment from the latest phone photo. */
export async function measure(c: Ctx, id: string, o: { freshAfter?: number | null } = {}) {
  const e = await must(c, id);
  const res = await vision.measure(c, e, o);
  if (res.ok) Object.assign(res, await addResults(c, id, res.readings.map((r: any) => ({ well: r.well, rgb: r.rgb })), "camera"));
  await c.store.event("measure", { id, ok: res.ok, skipped: res.skipped ?? null, error: res.error ?? null, method: res.method ?? null });
  c.hub.ui({ t: "log", src: "camera", level: res.ok ? "info" : "warn", text: res.ok ? `measured ${res.readings.length} beakers of ${id} (${res.method})` : `measure skipped: ${res.skipped ?? res.error}` });
  return res;
}

export async function csv(c: Ctx, id?: string): Promise<string> {
  const head = ["experiment", "title", "well", "label", ...sim.STOCKS.map((d) => `${d}_ul`), "predicted_r", "predicted_g", "predicted_b",
    "measured_r", "measured_g", "measured_b", "error", "measured_to_target", "rating", "note", "source"];
  const q = (v: unknown) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const lines = [head.join(",")];
  for (const e of id ? [await must(c, id)] : (await c.store.listExperiments(1000)).reverse()) {
    for (const b of (await view(c, e)).beakers as any[]) {
      if (!id && !b.source) continue; // the all-experiments export is the beakers that have a result
      const p = b.predicted_rgb ?? [], m = b.measured_rgb ?? [];
      lines.push([e.id, e.title, b.well, b.label, ...sim.STOCKS.map((d) => b[d]), p[0], p[1], p[2], m[0], m[1], m[2], b.error, b.measured_to_target, b.rating, b.note, b.source].map(q).join(","));
    }
  }
  return lines.join("\n") + "\n";
}

// ---------------- running ----------------
/** At boot nothing is running: an experiment left `running` was cut off by a restart. */
export async function failInterrupted(c: Ctx) {
  for (const e of await c.store.listExperiments(20, ["running"]))
    if (!c.running.has(e.id)) await c.store.putExperiment(Object.assign(e, { status: "failed", error: "interrupted: the lab restarted mid-run", finished: now() }));
}

export async function requestRun(c: Ctx, id: string, auto = false) {
  const e = await must(c, id);
  if (e.status === "draft") throw new Error(`the dry run of ${id} failed: ${e.check.errors.slice(0, 3).join("; ")}`);
  if (e.status !== "ready") throw new Error(`experiment ${id} is ${e.status}${["done", "failed", "aborted"].includes(e.status) ? "; repeat it to run it again" : ""}`);
  const busy = (await c.store.listExperiments(5, ACTIVE)).find((x) => x.id !== id);
  if (busy) throw new Error(`${busy.id} is ${busy.status}; one experiment at a time`);
  e.status = "awaiting_approval";
  await save(c, e);
  if (auto || flag(c.env.LABRAT_AUTO_APPROVE)) return approve(c, id);
  return e;
}

export async function approve(c: Ctx, id?: string | null) {
  const e = id ? await must(c, id) : (await c.store.listExperiments(1, ["awaiting_approval"]))[0];
  if (!e) throw new Error("no experiment is waiting for approval");
  if (e.status !== "awaiting_approval") return e;
  Object.assign(e, { status: "running", started: now(), step: 0, log: [], error: null });
  await save(c, e);
  const task = { aborted: false };
  c.running.set(e.id, task);
  c.bg(execute(c, e, task), `run ${e.id}`);
  return e;
}

/** Stop everything: flag the running experiment, fail in-flight commands, then stop every motion device. */
export async function abort(c: Ctx): Promise<void> {
  for (const t of c.running.values()) t.aborted = true;
  for (const e of await c.store.listExperiments(5, ["awaiting_approval"])) { e.status = "ready"; await save(c, e); }
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

export async function execStep(c: Ctx, s: sim.Step, e?: any): Promise<string> {
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
      if (e) {
        e.photo_after = tReady;
        if (frame) e.photo_ts = frame.ts;
      }
      return frame ? "photo taken" : "photo requested (no fresh phone frame)";
    }
    case "wait":
      await sleep(Number(s.s ?? 1) * 1000);
      return `waited ${s.s ?? 1} s`;
  }
  throw new Error(`unknown op ${s.op}`);
}

async function execute(c: Ctx, e: any, task: { aborted: boolean }): Promise<void> {
  try {
    for (const [i, s] of (e.steps as sim.Step[]).entries()) {
      if (task.aborted) throw new CommandError("aborted");
      e.step = i;
      const msg = await execStep(c, s, e);
      e.log.push({ i, op: s.op, msg, t: r1(now() - e.started) });
      await save(c, e);
    }
    e.status = "done";
  } catch (err) {
    e.status = task.aborted ? "aborted" : "failed";
    if (!task.aborted) e.error = (err as Error).message;
  } finally {
    e.finished = now();
    c.running.delete(e.id);
    await save(c, e);
    await c.store.event("experiment_" + e.status, { id: e.id, error: e.error ?? null });
  }
  if (e.status === "done" && e.photo_after != null && flag(c.env.LABRAT_AUTO_OBSERVE, true))
    c.bg(measure(c, e.id, { freshAfter: e.photo_after }), "measure"); // only a frame taken after the photo step counts
}
