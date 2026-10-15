// Tool-rail tools on the CNC head: put any tool over any deck target, snap the USB microscope, probe a liquid surface
// with the capacitive probe. Every tool has an offset from the syringe tip (deck_cal tools); the CNC moves the spindle
// so the tool's point lands on the target: spindle = target(tip coords) - tool offset.
import * as deckCal from "./deck_cal";
import * as sim from "./sim";
import type { Ctx } from "./env";

export const TOOL_NAMES = ["pipette", "microscope", "probe"];
const FOCUS_Z: Record<string, number> = { microscope: -20.0 }; // default work z for the lens over a target

/** Deck point (pipette-tip coordinates) of a named target (beaker / stock / waste ...) or explicit x, y. */
export function targetXy(target?: string | null, x?: number | null, y?: number | null): [number, number] {
  if (target) {
    const [, sx, sy, sz] = sim.loc(target);
    const [tx, ty] = deckCal.spindleToTip(sx, sy, sz);
    return [tx, ty];
  }
  if (x == null || y == null) throw new Error("give a target (e.g. 'B3', 'red') or x and y");
  return [Number(x), Number(y)];
}

const r2 = (v: number) => Math.round(v * 100) / 100;

/** Spindle move that puts `tool` over the target, checked against the CNC's bounds. */
export function plan(tool: string, target?: string | null, x?: number | null, y?: number | null, z?: number | null) {
  const [tx, ty] = targetXy(target, x, y);
  const tz = z != null ? Number(z) : (FOCUS_Z[tool] ?? sim.Z_SAFE);
  let [sx, sy, sz] = deckCal.spindleForTool(tool, tx, ty, tz);
  sz = Math.min(sz, sim.Z_SAFE);
  const bad = (["x", "y", "z"] as const).flatMap((ax, i) => {
    const v = [sx, sy, sz][i], [lo, hi] = sim.BOUNDS[ax];
    return lo - 1e-6 <= v && v <= hi + 1e-6 ? [] : [`${ax}=${v.toFixed(1)} outside [${lo},${hi}]`];
  });
  return { tool, target: target || [tx, ty], deck_xy: [r2(tx), r2(ty)], spindle: [r2(sx), r2(sy), r2(sz)], reachable: !bad.length, why: bad.join("; ") };
}

export async function goto(c: Ctx, tool: string, target?: string | null, x?: number | null, y?: number | null, z?: number | null) {
  const p = plan(tool, target, x, y, z);
  if (!p.reachable) throw new Error(`${tool} cannot reach ${JSON.stringify(p.target)}: ${p.why}`);
  const [sx, sy, sz] = p.spindle;
  const mv = async (q: Partial<typeof c.pos>, f: number) => { Object.assign(c.pos, q); await c.hub.cmd("cnc", "move", { ...c.pos, f }, 60); };
  await mv({ z: sim.Z_SAFE }, 600); // up, across, down
  await mv({ x: sx, y: sy }, 2000);
  await mv({ z: sz }, 600);
  return p;
}

export async function snap(c: Ctx, target?: string | null, x?: number | null, y?: number | null, z?: number | null) {
  const p = target || x != null ? await goto(c, "microscope", target, x, y, z) : null;
  const img = await c.hub.cmd("microscope", "snap", {}, 20);
  return { _image: img.image_b64, _mime: img.mime ?? "image/jpeg", w: img.w, h: img.h, at: p };
}

/** Deploy the probe arm over a beaker, probe down to the liquid surface (the hub retracts the arm afterwards) and
 *  return the surface height and the volume it implies (beaker geometry from the deck). */
export async function probeLevel(c: Ctx, target: string, depth = 45.0) {
  const p = await goto(c, "probe", target, null, null, sim.Z_SAFE);
  await c.hub.cmd("probe", "deploy");
  const r = await c.hub.cmd("cnc", "probe", { dz: -Math.abs(depth), f: 60 }, 120);
  const out: any = { at: p, hit: r.hit ?? null };
  if (r.hit) {
    const h = sim.SAFE_TIP_H + deckCal.toolOffset("probe")[2] + Number(r.z); // electrode tip above the bed at the trigger
    Object.assign(out, { surface_h_mm: r2(h), est_ul: Math.round(Math.max(0, h - sim.BEAKER_FLOOR_H) * sim.BEAKER_UL_PER_MM) });
  }
  return out;
}
