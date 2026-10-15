// Digital twin: deck model, CNC trapezoid timing, pipette/tip/liquid accounting, subtractive dye mixing -> RGB,
// safety checks, frames for 3D replay, and goal scoring. Deterministic: frames are re-simulated on demand.
import * as deckCal from "./deck_cal";

// ---------------- deck ----------------
// hardware/deck/deck.json is the source of these numbers; test/sim.test.ts checks they agree.
// "plate" is the mixing grid = columns 1-7 of the printed beaker matrix; its column 8 holds the stock beakers.
export const ROWS = "ABCDEFGH";
export interface Grid { name: string; origin: [number, number]; pitch: number; rows: number; cols: number; cap_ul?: number; label?: string }
export const TIP_RACK: Grid = { name: "tip_rack", origin: [17.0, 19.25], pitch: 9.0, rows: 8, cols: 12 };
export const PLATE: Grid & { cap_ul: number } = { name: "plate", label: "beaker", origin: [153.25, 32.0], pitch: 19.0, rows: 6, cols: 7, cap_ul: 5000.0 };
const STOCK_X = PLATE.origin[0] + 7 * PLATE.pitch;
export const RESERVOIRS: Record<string, [number, number]> = { water: [STOCK_X, 32.0], red: [STOCK_X, 51.0], yellow: [STOCK_X, 70.0], blue: [STOCK_X, 89.0] };
export const RES_CAP_UL = 5000.0, RES_FILL_UL = 4500.0;
export const WASTE: [number, number] = [280.0, 170.0];
export const EJECT_BAR: [number, number] = [10.0, 170.0];
// Z table (spindle z, 0 = top). Tip end at z = 0 is 55 mm above the bed; beaker rims are at 51.2. Dispense 5 mm
// inside the rim; aspirate / mix as deep as Z goes. Tip pick-up needs more than the 3018's 45 mm of Z: Z_TIP is the
// travel limit and taught tip_rack points override it.
export const SAFE_TIP_H = 55.0;
export const Z_SAFE = 0.0, Z_TIP = -45.0, Z_WELL = -9.0, Z_RES = -45.0, Z_MIX = -45.0;
export const BOUNDS: Record<"x" | "y" | "z", [number, number]> = { x: [0.0, 300.0], y: [0.0, 180.0], z: [-45.0, 0.0] };
export const BEAKER_UL_PER_MM = (Math.PI / 4) * 13.5 ** 2; // 15.3 OD beaker, ~0.9 wall
export const BEAKER_FLOOR_H = 2.4; // inside floor above the bed
// liquid the tip can reach: the surface must stay >= 1 mm above the lowest tip end
export const REACH_UL = (SAFE_TIP_H + BOUNDS.z[0] + 1.0 - BEAKER_FLOOR_H) * BEAKER_UL_PER_MM;
export const TIP_CAP_UL = 200.0;
export const FEED_XY = 3000.0, FEED_Z = 600.0; // mm/min
export const ACCEL = 400.0; // mm/s^2
export const PIPETTE_UL_PER_S = 100.0;
// syringe head (devices/pipette.ts PIPETTE_DEFAULTS): plunger height for the twin
export const SYR_UL_PER_MM = (Math.PI / 4) * 17.8 ** 2;
export const SYR_READY_MM = 2.0, SYR_STROKE_MM = 30.0;

// Dye absorbance (per channel R,G,B) at stock concentration
export const DYE_NAMES = ["water", "red", "yellow", "blue"] as const;
const FACTORY: Record<string, number[]> = {
  water: [0.0, 0.0, 0.0], red: [0.05, 1.6, 1.2], yellow: [0.02, 0.06, 1.8], blue: [1.6, 0.7, 0.05],
};
export const DYES: Record<string, number[]> = structuredClone(FACTORY);
let VOL_GAIN = 1.0; // actual/commanded pipette volume (fitted by learn.ts)

export interface SimParams { absorbance: Record<string, number[]>; vol_gain: number }

export const r4 = (x: number) => Math.round(x * 1e4) / 1e4;
export const rnd = (x: number, n = 0) => { const k = 10 ** n; return Math.round(x * k) / k; };

export function getParams(): SimParams {
  return { absorbance: Object.fromEntries(DYE_NAMES.map((d) => [d, DYES[d].map(r4)])), vol_gain: r4(VOL_GAIN) };
}

export function setParams(p: Partial<SimParams>): void {
  for (const [d, v] of Object.entries(p.absorbance ?? {})) if (d in DYES) DYES[d] = v.map(Number);
  if (p.vol_gain != null) VOL_GAIN = Number(p.vol_gain);
}

export function factoryParams(): SimParams {
  return { absorbance: structuredClone(FACTORY), vol_gain: 1.0 };
}

export const TARGETS: Record<string, [number, number, number]> = {
  orange: [255, 140, 0], green: [40, 170, 60], purple: [128, 40, 160], brown: [130, 75, 30],
  pink: [255, 150, 180], red: [220, 20, 30], yellow: [250, 230, 20], blue: [30, 70, 220],
  teal: [0, 150, 150], black: [20, 20, 20], lime: [160, 230, 40],
};

function parseWell(name: string, grid: Grid): [number, number] {
  const m = /^([A-Ha-h])(\d{1,2})$/.exec(name.trim());
  if (!m) throw new Error(`bad well ${name}`);
  const r = ROWS.indexOf(m[1].toUpperCase()), c = Number(m[2]) - 1;
  if (!(c >= 0 && c < grid.cols && r < grid.rows)) throw new Error(`bad well ${name}`);
  return [r, c];
}

export function wellXy(name: string, grid: Grid): [number, number] {
  const [r, c] = parseWell(name, grid);
  const nom: [number, number] = [grid.origin[0] + c * grid.pitch, grid.origin[1] + r * grid.pitch];
  if (grid.name in deckCal.GRIDS) {
    const [x, y] = deckCal.gridSpindle(grid.name, r, c, nom, 0.0);
    return [x, y];
  }
  return nom;
}

/** Calibrated spindle (x, y, z_work) for a grid well (taught z overrides the nominal work height). */
export function wellXyz(name: string, grid: Grid, zNom: number): [number, number, number] {
  const [r, c] = parseWell(name, grid);
  return deckCal.gridSpindle(grid.name, r, c, [grid.origin[0] + c * grid.pitch, grid.origin[1] + r * grid.pitch], zNom);
}

export function ejectXyz(): [number, number, number] {
  return deckCal.pointSpindle("eject_bar", [EJECT_BAR[0], EJECT_BAR[1], -20.0]);
}

const resName = (name: string) => name.trim().toLowerCase().replace("reservoir:", "").replace("res_", "");

/** Resolve a src/dst name to [kind, x, y, z_work]. */
export function loc(name: string): [string, number, number, number] {
  const n = resName(name);
  if (n in RESERVOIRS) {
    const [x, y] = RESERVOIRS[n];
    return ["reservoir", ...deckCal.pointSpindle("reservoir." + n, [x, y, Z_RES])];
  }
  if (n === "waste") return ["waste", ...deckCal.pointSpindle("waste", [WASTE[0], WASTE[1], -10.0])];
  return ["well", ...wellXyz(name, PLATE, Z_WELL)];
}

/** aspirate / mix height in a mixing beaker: the dispense height lowered to the deep (reach) height. */
const dive = (zWork: number) => Math.max(BOUNDS.z[0], zWork + (Z_MIX - Z_WELL));

/** Subtractive mixing: Beer–Lambert on concentration-weighted absorbance. */
export function mixRgb(comp: Record<string, number>): number[] {
  const tot = Object.values(comp).reduce((a, b) => a + b, 0);
  if (tot <= 0) return [235, 235, 235];
  const a = [0, 0, 0];
  for (const d of DYE_NAMES) {
    const c = (comp[d] ?? 0) / tot;
    for (let k = 0; k < 3; k++) a[k] += c * DYES[d][k];
  }
  return a.map((v) => Math.round(245.0 * 10 ** -Math.max(v, 0)));
}

export function moveTime(d: number, feedMmMin: number, accel = ACCEL): number {
  if (d <= 0) return 0;
  const v = feedMmMin / 60;
  if (d < (v * v) / accel) return 2 * Math.sqrt(d / accel);
  return d / v + v / accel;
}

export function targetRgb(goal: string): [string, [number, number, number]] | null {
  const g = goal.toLowerCase();
  const m = /#([0-9a-f]{6})/.exec(g);
  if (m) { const h = m[1]; return ["#" + h, [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]]; }
  const primaries = ["red", "yellow", "blue"];
  for (const [name, rgb] of Object.entries(TARGETS)) if (!primaries.includes(name) && new RegExp(`\\b${name}`).test(g)) return [name, rgb];
  for (const name of primaries) if (new RegExp(`\\b${name}`).test(g)) return [name, TARGETS[name]];
  return null;
}

export const colorDist = (a: number[], b: number[]) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

export interface Step { op: string; [k: string]: any }
interface Container { vol: number; comp: Record<string, number> }
interface Tip extends Container { id: string; sources: Set<string> }
export interface Frame {
  t: number; step: number; label: string; cnc: { x: number; y: number; z: number };
  pipette: { vol_ul: number; tip: string | null; pos_mm: number; stroke_mm: number }; wells: Record<string, { rgb: number[]; vol: number }>;
}

function take(c: Container, ul: number): Record<string, number> {
  if (c.vol <= 0 || ul <= 0) return {};
  const frac = Math.min(1, ul / c.vol);
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(c.comp)) { out[k] = v * frac; c.comp[k] -= out[k]; }
  c.vol = Math.max(0, c.vol - ul);
  return out;
}

function add(c: Container, comp: Record<string, number>, ul: number): void {
  // comp is absolute µL of each dye stock
  for (const [k, v] of Object.entries(comp)) c.comp[k] = (c.comp[k] ?? 0) + v;
  c.vol += ul;
}

const f0 = (x: number) => x.toFixed(0);

class Sim {
  t = 0; pos = [0, 0, 0]; tip: Tip | null = null; usedTips = new Set<string>();
  wells: Record<string, Container> = {};
  res: Record<string, Container> = Object.fromEntries(Object.keys(RESERVOIRS).map((k) => [k, { vol: RES_FILL_UL, comp: { [k]: RES_FILL_UL } }]));
  errors: string[] = []; warnings: string[] = []; frames: Frame[] = []; dist = 0;
  constructor(private record = true) {}

  frame(step: number, label: string, changed: string[] = []): void {
    if (!this.record) return;
    const wells: Frame["wells"] = {};
    for (const w of changed) if (this.wells[w]) wells[w] = { rgb: mixRgb(this.wells[w].comp), vol: rnd(this.wells[w].vol, 1) };
    const vol = this.tip ? this.tip.vol : 0;
    this.frames.push({
      t: rnd(this.t, 2), step, label, cnc: { x: rnd(this.pos[0], 1), y: rnd(this.pos[1], 1), z: rnd(this.pos[2], 1) },
      pipette: { vol_ul: rnd(vol, 1), tip: this.tip?.id ?? null, pos_mm: rnd(SYR_READY_MM + vol / SYR_UL_PER_MM, 3), stroke_mm: SYR_STROKE_MM },
      wells,
    });
  }

  goto(x: number, y: number, z: number, step: number, label: string): void {
    (["x", "y", "z"] as const).forEach((ax, i) => {
      const v = [x, y, z][i], [lo, hi] = BOUNDS[ax];
      if (!(lo - 1e-6 <= v && v <= hi + 1e-6)) this.errors.push(`step ${step}: ${ax}=${v} out of bounds [${lo},${hi}]`);
    });
    // retract to safe Z, travel XY, plunge
    if (this.pos[2] < Z_SAFE && (Math.abs(this.pos[0] - x) > 0.01 || Math.abs(this.pos[1] - y) > 0.01)) {
      const d = Z_SAFE - this.pos[2];
      this.t += moveTime(d, FEED_Z); this.dist += d; this.pos[2] = Z_SAFE;
      this.frame(step, label + ":up");
    }
    const dxy = Math.hypot(x - this.pos[0], y - this.pos[1]);
    if (dxy > 0) {
      if (this.pos[2] < Z_SAFE - 0.5) this.errors.push(`step ${step}: XY travel below safe Z (collision risk)`);
      this.t += moveTime(dxy, FEED_XY); this.dist += dxy; this.pos[0] = x; this.pos[1] = y;
      this.frame(step, label + ":xy");
    }
    const dz = Math.abs(z - this.pos[2]);
    if (dz > 0) {
      this.t += moveTime(dz, FEED_Z); this.dist += dz; this.pos[2] = z;
      this.frame(step, label + ":z");
    }
  }

  container(name: string): Container {
    const [kind] = loc(name);
    if (kind === "reservoir") return this.res[resName(name)];
    const key = name.trim().toUpperCase();
    return (this.wells[key] ??= { vol: 0, comp: {} });
  }

  step(i: number, s: Step): void {
    const op = s.op;
    const need = (k: string) => { if (s[k] === undefined) throw new Error(`'${k}'`); return s[k]; };
    try {
      if (op === "pick_tip") {
        const rw = String(s.rack_well ?? "A1").toUpperCase();
        if (this.tip) this.errors.push(`step ${i}: pick_tip while holding a tip`);
        if (this.usedTips.has(rw)) this.errors.push(`step ${i}: tip ${rw} already used`);
        const [x, y, zt] = wellXyz(rw, TIP_RACK, Z_TIP);
        this.goto(x, y, zt, i, "pick_tip");
        this.t += 0.5;
        this.tip = { id: rw, vol: 0, comp: {}, sources: new Set() };
        this.usedTips.add(rw);
        this.goto(x, y, Z_SAFE, i, "pick_tip");
      } else if (op === "aspirate") {
        let ul = Number(need("ul")); const src = String(need("src"));
        if (!this.tip) { this.errors.push(`step ${i}: aspirate without tip`); return; }
        let [kind, x, y, z] = loc(src);
        const c = this.container(src);
        if (this.tip.sources.size && !this.tip.sources.has(src.toLowerCase()) && kind === "reservoir")
          this.errors.push(`step ${i}: contamination — tip ${this.tip.id} reused for ${src} after ${JSON.stringify([...this.tip.sources].sort())}`);
        if (this.tip.vol + ul > TIP_CAP_UL + 1e-6) this.errors.push(`step ${i}: tip overfill ${f0(this.tip.vol + ul)} > ${f0(TIP_CAP_UL)} µL`);
        if (ul > c.vol + 1e-6) {
          this.errors.push(`step ${i}: aspirate ${ul} µL but ${src} has ${f0(c.vol)} µL`);
          ul = c.vol;
        } else if (c.vol - ul < REACH_UL - 1e-6) {
          this.errors.push(`step ${i}: aspirate ${f0(ul)} µL from ${src} (${f0(c.vol)} µL): the tip only reaches liquid while a beaker holds >= ${f0(REACH_UL)} µL`);
        }
        if (kind === "well") z = dive(z);
        this.goto(x, y, z, i, "aspirate");
        add(this.tip, take(c, ul * VOL_GAIN), ul * VOL_GAIN);
        this.tip.sources.add(src.toLowerCase());
        this.t += ul / PIPETTE_UL_PER_S + 0.3;
        this.frame(i, "aspirate", kind === "well" ? [src.toUpperCase()] : []);
      } else if (op === "dispense") {
        const dst = String(need("dst"));
        if (!this.tip) { this.errors.push(`step ${i}: dispense without tip`); return; }
        let ul = Number(s.ul ?? this.tip.vol);
        if (ul > this.tip.vol + 1e-6) {
          this.errors.push(`step ${i}: dispense ${ul} µL > tip holds ${f0(this.tip.vol)} µL`);
          ul = this.tip.vol;
        }
        const [kind, x, y, z] = loc(dst);
        const c = this.container(dst);
        if (kind === "well" && c.vol + ul > PLATE.cap_ul + 1e-6) this.errors.push(`step ${i}: well ${dst} overflow ${f0(c.vol + ul)} > ${f0(PLATE.cap_ul)} µL`);
        this.goto(x, y, z, i, "dispense");
        add(c, take(this.tip, ul), ul);
        this.tip.sources.add(dst.toLowerCase());
        this.t += ul / PIPETTE_UL_PER_S + 0.3;
        this.frame(i, "dispense", kind === "well" ? [dst.toUpperCase()] : []);
      } else if (op === "mix") {
        const w = String(need("well")), ul = Number(s.ul ?? 50), n = Math.trunc(Number(s.n ?? 3));
        if (!this.tip) { this.errors.push(`step ${i}: mix without tip`); return; }
        const c = this.container(w);
        if (c.vol < REACH_UL) {
          this.warnings.push(`step ${i}: mix skipped: ${w} holds ${f0(c.vol)} µL, the tip reaches liquid from ${f0(REACH_UL)} µL`);
          return;
        }
        if (ul > c.vol) this.warnings.push(`step ${i}: mix volume ${ul} > well volume ${f0(c.vol)}`);
        const [, x, y, z] = loc(w);
        this.goto(x, y, dive(z), i, "mix");
        this.t += n * 2 * (ul / PIPETTE_UL_PER_S + 0.2);
        this.tip.sources.add(w.toLowerCase());
        this.frame(i, "mix", [w.toUpperCase()]);
      } else if (op === "eject_tip") {
        if (!this.tip) { this.warnings.push(`step ${i}: eject without tip`); return; }
        const [ex, ey, ez] = ejectXyz();
        this.goto(ex, ey, ez, i, "eject");
        this.goto(ex, ey + 8, ez, i, "eject");
        this.tip = null;
        this.goto(ex, ey + 8, Z_SAFE, i, "eject");
      } else if (op === "photo") { // lift the pipette out of the way and wait for a fresh frame
        if (this.pos[2] < Z_SAFE) this.goto(this.pos[0], this.pos[1], Z_SAFE, i, "photo");
        this.t += 2.0;
        this.frame(i, "photo");
      } else if (op === "wait") {
        this.t += Number(s.s ?? 1);
        this.frame(i, "wait");
      } else {
        this.errors.push(`step ${i}: unknown op ${op}`);
      }
    } catch (e) {
      this.errors.push(`step ${i}: ${op}: ${(e as Error).message}`);
    }
  }
}

export interface WellResult { rgb: number[]; vol: number; comp: Record<string, number> }
export interface SimResult {
  ok: boolean; errors: string[]; warnings: string[]; duration_s: number; travel_mm: number;
  wells: Record<string, WellResult>; best_well: string | null; predicted_rgb: number[] | null;
  target: { name: string; rgb: number[] } | null; score: number; frames?: Frame[]; tips_used: string[];
}

export function simulate(protocol: Step[], goal = "", recordFrames = true): SimResult {
  const sim = new Sim(recordFrames);
  sim.frame(-1, "start");
  protocol.forEach((s, i) => sim.step(i, s));
  if (sim.tip) sim.warnings.push("protocol ends holding a tip");
  const wells: Record<string, WellResult> = {};
  for (const [w, st] of Object.entries(sim.wells)) {
    if (st.vol > 0) wells[w] = { rgb: mixRgb(st.comp), vol: rnd(st.vol, 1), comp: Object.fromEntries(Object.entries(st.comp).filter(([, v]) => v > 0.01).map(([k, v]) => [k, rnd(v, 1)])) };
  }
  const tgt = goal ? targetRgb(goal) : null;
  const names = Object.keys(wells);
  let score = 0, best: string | null = null;
  if (tgt && names.length) {
    best = names.reduce((a, b) => (colorDist(wells[b].rgb, tgt[1]) < colorDist(wells[a].rgb, tgt[1]) ? b : a));
    score = Math.max(0, 1 - colorDist(wells[best].rgb, tgt[1]) / 441.7);
  } else if (names.length) {
    best = names[0];
    score = 0.5;
  }
  const ok = sim.errors.length === 0;
  if (!ok) score *= 0.1;
  return {
    ok, errors: sim.errors, warnings: sim.warnings, duration_s: rnd(sim.t, 1), travel_mm: rnd(sim.dist, 1),
    wells, best_well: best, predicted_rgb: best ? wells[best].rgb : null,
    target: tgt ? { name: tgt[0], rgb: [...tgt[1]] } : null, score: r4(score), frames: sim.frames,
    tips_used: [...sim.usedTips].sort(),
  };
}

// ---------------- deterministic variant generator ----------------
export function dyesForGoal(goal: string): string[] {
  const g = goal.toLowerCase();
  const named = ["red", "yellow", "blue", "water"].filter((d) => new RegExp(`\\b${d}`).test(g));
  const tname = targetRgb(goal)?.[0] ?? "";
  const def = ({ orange: ["red", "yellow"], green: ["blue", "yellow"], purple: ["red", "blue"], brown: ["red", "yellow", "blue"],
    pink: ["red", "water"], teal: ["blue", "yellow", "water"], lime: ["yellow", "blue"], black: ["red", "yellow", "blue"] } as Record<string, string[]>)[tname];
  if (named.length && (named.includes(tname) || !def || def.every((d) => named.includes(d)))) return named.length > 1 ? named : [...named, "water"];
  if (def) return named.length ? [...new Set([...named, ...def])] : def;
  return named.length ? named : ["red", "yellow"];
}

export function protocolFromRatios(dyes: string[], ratios: number[], totalUl: number, well: string, tipStart = 0, mix = true): Step[] {
  const steps: Step[] = [];
  let tipI = tipStart;
  const s = ratios.reduce((a, b) => a + b, 0) || 1;
  const vols = ratios.map((r) => Math.round((totalUl * r) / s));
  const active = dyes.map((d, j) => [d, vols[j]] as const).filter(([, v]) => v >= 5);
  active.forEach(([d, v], j) => {
    const tip = `${ROWS[tipI % 8]}${Math.floor(tipI / 8) + 1}`;
    tipI++;
    steps.push({ op: "pick_tip", rack_well: tip });
    let remaining = v;
    while (remaining > 0) {
      const chunk = Math.min(remaining, TIP_CAP_UL);
      steps.push({ op: "aspirate", src: d, ul: chunk }, { op: "dispense", dst: well, ul: chunk });
      remaining -= chunk;
    }
    if (mix && j === active.length - 1 && totalUl >= REACH_UL) steps.push({ op: "mix", well, ul: Math.min(100, totalUl / 2), n: 3 });
    steps.push({ op: "eject_tip" });
  });
  steps.push({ op: "photo", target: "plate" });
  return steps;
}

/** Small seeded PRNG (mulberry32), so variant sets are reproducible from a seed. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export interface Variant { id: string; params: { dyes: string[]; ratios: number[]; total_ul: number; well: string }; protocol: Step[]; sim?: SimResult; [k: string]: any }

/** Grid + seeded random over dye ratios and total volume. One well per variant. */
export function generateVariants(goal: string, n = 12, seed = 0): Variant[] {
  const rand = rng(seed);
  const dyes = dyesForGoal(goal);
  const k = dyes.length, gridN = Math.max(1, Math.floor(n / 2));
  const out: Variant[] = [];
  for (let i = 0; i < n; i++) {
    let ratios: number[];
    if (k === 2 && i < gridN) {
      const f = (i + 1) / (gridN + 1);
      ratios = [f, 1 - f];
    } else { // Dirichlet(1,...,1) = normalised exponentials
      const e = dyes.map(() => -Math.log(1 - rand()));
      const s = e.reduce((a, b) => a + b, 0);
      ratios = e.map((x) => x / s);
    }
    const total = [120, 150, 180, 200, 240][Math.floor(rand() * 5)];
    const well = `${ROWS[i % PLATE.rows]}${Math.floor(i / PLATE.rows) + 1}`;
    out.push({
      id: `v${String(i + 1).padStart(2, "0")}`,
      params: { dyes, ratios: ratios.map((r) => rnd(r, 3)), total_ul: total, well },
      protocol: protocolFromRatios(dyes, ratios, total, well, 0),
    });
  }
  return out;
}
