// Deck calibration: pipette-tip offset (0,0,0 for the coaxial syringe head) + taught deck positions -> spindle (CNC)
// coordinates. Taught points are CNC machine coords of the PIPETTE TIP; the CNC moves the spindle, so
// spindle = tip - pipette_offset. Untaught targets fall back to the nominal deck (whose numbers are spindle coords).
// If both corners of a grid are taught (A1 + the far corner: tip_rack.H12, plate.F7), pitch and rotation are derived
// from them; otherwise the grid's nominal pitch, axis-aligned, is used from the taught A1.
// This module holds the calibration in memory; the DO loads/saves it as the D1 config row "deck_cal".

import toolsJson from "../../hardware/tool-head/tools.json";

const ROWS = "ABCDEFGH";
export const GRIDS: Record<string, [number, number, number]> = { tip_rack: [8, 12, 9.0], plate: [6, 7, 19.0] };
export const TARGETS = ["tip_rack.A1", "tip_rack.H12", "plate.A1", "plate.F7",
  "reservoir.water", "reservoir.red", "reservoir.blue", "reservoir.yellow", "waste", "eject_bar"];

export interface Offset { dx: number; dy: number; dz: number }
export interface Point { x: number; y: number; z?: number; ts?: number }
export interface DeckCal { pipette_offset: Offset; points: Record<string, Point>; tools: Record<string, Offset>; ts?: number }

// Tool-rail modules (hardware/tool-head/tools.json): tool point relative to the syringe tip, nominal from the CAD.
// Nominal offsets come straight from the CAD (hardware/tool-head/tools.json, written by syrhead.build); teaching overrides them.
export const TOOLS_NOMINAL: Record<string, Offset> = Object.fromEntries(
  Object.entries<{ offset: number[] }>(toolsJson.tools)
    .filter(([name]) => name !== "pipette")
    .map(([name, t]) => [name, { dx: t.offset[0], dy: t.offset[1], dz: t.offset[2] }]),
);
const zero = (): Offset => ({ dx: 0, dy: 0, dz: 0 });
const XYZ = ["x", "y", "z"] as const, DXYZ = ["dx", "dy", "dz"] as const;

let cal: DeckCal = normalize({});

export function normalize(c: any): DeckCal {
  const out: DeckCal = { pipette_offset: zero(), points: {}, tools: structuredClone(TOOLS_NOMINAL) };
  for (const k of DXYZ) if (c?.pipette_offset?.[k] != null) out.pipette_offset[k] = Number(c.pipette_offset[k]);
  for (const [name, t] of Object.entries<any>(c?.tools ?? {})) {
    out.tools[name] ??= zero();
    for (const k of DXYZ) if (t?.[k] != null) out.tools[name][k] = Number(t[k]);
  }
  for (const [name, p] of Object.entries<any>(c?.points ?? {})) {
    const q: any = {};
    for (const k of XYZ) if (p?.[k] != null) q[k] = Number(p[k]);
    if (p?.ts != null) q.ts = p.ts;
    out.points[name] = q;
  }
  if (c?.ts != null) out.ts = c.ts;
  return out;
}

export const get = (): DeckCal => cal;
/** Install a calibration (from D1, or {} in tests). */
export function reset(c: any = {}): DeckCal { cal = normalize(c); return cal; }

export function update(patch: any): DeckCal {
  const c = structuredClone(cal);
  if (patch.pipette_offset) for (const k of DXYZ) if (patch.pipette_offset[k] != null) c.pipette_offset[k] = Number(patch.pipette_offset[k]);
  for (const [name, t] of Object.entries<any>(patch.tools ?? {})) {
    c.tools[name] ??= zero();
    for (const k of DXYZ) if (t?.[k] != null) c.tools[name][k] = Number(t[k]);
  }
  for (const [name, p] of Object.entries<any>(patch.points ?? {})) {
    if (p == null) delete c.points[name];
    else { const q: any = {}; for (const k of XYZ) if (p[k] != null) q[k] = Number(p[k]); c.points[canon(name)] = q; }
  }
  c.ts = Date.now() / 1000;
  return reset(c);
}

export function canon(target: string): string {
  const t = target.trim();
  const m = /^(tip_rack|plate)\.([A-Ha-h])(\d{1,2})$/.exec(t);
  if (m) return `${m[1]}.${m[2].toUpperCase()}${Number(m[3])}`;
  if (["water", "red", "blue", "yellow"].includes(t.toLowerCase())) return "reservoir." + t.toLowerCase();
  return t;
}

const r3 = (x: number) => Math.round(x * 1000) / 1000;

/** Record target's tip position = spindle position + pipette offset. */
export function teach(target: string, spindle: [number, number, number]): Point {
  const o = cal.pipette_offset;
  const tip = { x: r3(spindle[0] + o.dx), y: r3(spindle[1] + o.dy), z: r3(spindle[2] + o.dz), ts: Date.now() / 1000 };
  const c = structuredClone(cal);
  c.points[canon(target)] = tip;
  c.ts = tip.ts;
  reset(c);
  return tip;
}

// ---------------- transforms ----------------
export function tipToSpindle(x: number, y: number, z: number, c = cal): [number, number, number] {
  const o = c.pipette_offset;
  return [x - o.dx, y - o.dy, z - o.dz];
}

export function spindleToTip(x: number, y: number, z: number, c = cal): [number, number, number] {
  const o = c.pipette_offset;
  return [x + o.dx, y + o.dy, z + o.dz];
}

/** A tool's point relative to the spindle reference (pipette offset + the tool's offset from the syringe tip). */
export function toolOffset(tool: string | null | undefined, c = cal): [number, number, number] {
  const p = c.pipette_offset;
  if (!tool || tool === "pipette") return [p.dx, p.dy, p.dz];
  const t = c.tools[tool];
  if (!t) throw new Error(`unknown tool '${tool}' (known: pipette, ${Object.keys(c.tools).join(", ")})`);
  return [p.dx + t.dx, p.dy + t.dy, p.dz + t.dz];
}

/** Spindle position that puts `tool`'s point at deck point (x, y, z) (pipette-tip coordinates). */
export function spindleForTool(tool: string, x: number, y: number, z: number, c = cal): [number, number, number] {
  const o = toolOffset(tool, c);
  return [x - o[0], y - o[1], z - o[2]];
}

export interface GridFrame { a1: [number, number, number]; h12z: number | null; pitch: number; theta: number; theta_deg: number; rows: number; cols: number }

/** Derived grid frame in tip coords, or null if A1 is untaught. */
export function gridFrame(grid: string, c = cal): GridFrame | null {
  const a1 = c.points[`${grid}.A1`];
  if (!a1) return null;
  const [rows, cols, nominal] = GRIDS[grid];
  const h12 = c.points[`${grid}.${ROWS[rows - 1]}${cols}`];
  let pitch = nominal, theta = 0;
  if (h12) {
    const vx = h12.x - a1.x, vy = h12.y - a1.y, nx = (cols - 1) * nominal, ny = (rows - 1) * nominal;
    const m = Math.hypot(vx, vy);
    if (m > 1e-6) {
      pitch = (nominal * m) / Math.hypot(nx, ny);
      theta = Math.atan2(vy, vx) - Math.atan2(ny, nx);
    }
  }
  return { a1: [a1.x, a1.y, a1.z ?? 0], h12z: h12?.z ?? null, pitch, theta, theta_deg: (theta * 180) / Math.PI, rows, cols };
}

export function gridSpindle(grid: string, r: number, col: number, nominalXy: [number, number], nominalZ: number, c = cal): [number, number, number] {
  const f = gridFrame(grid, c);
  if (!f) return [nominalXy[0], nominalXy[1], nominalZ];
  const [ax, ay, az] = f.a1;
  const u = col * f.pitch, v = r * f.pitch, ct = Math.cos(f.theta), st = Math.sin(f.theta);
  let z = az;
  if (f.h12z != null) z = az + 0.5 * (r / (f.rows - 1) + col / (f.cols - 1)) * (f.h12z - az);
  return tipToSpindle(ax + u * ct - v * st, ay + u * st + v * ct, z, c);
}

export function pointSpindle(target: string, nominal: [number, number, number], c = cal): [number, number, number] {
  const p = c.points[canon(target)];
  if (!p) return nominal;
  return tipToSpindle(p.x, p.y, p.z ?? nominal[2], c);
}

export function summary() {
  const fmt = (f: GridFrame | null) => f && { pitch: Math.round(f.pitch * 1e4) / 1e4, theta_deg: Math.round(f.theta_deg * 1e4) / 1e4, a1: f.a1 };
  return {
    ...cal, targets: TARGETS,
    grids: Object.fromEntries(Object.keys(GRIDS).map((g) => [g, fmt(gridFrame(g))])),
    spindle: Object.fromEntries(Object.entries(cal.points).map(([k, p]) => {
      const s = tipToSpindle(p.x, p.y, p.z ?? 0);
      return [k, { x: r3(s[0]), y: r3(s[1]), z: r3(s[2]) }];
    })),
  };
}
