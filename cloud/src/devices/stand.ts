// Phone stand pan/tilt on hub channels 2 (pan, about Z) and 3 (tilt, about the panned X axis). aim {pan, tilt} is in
// degrees relative to the reference view: (0, 0) = the phone camera looking at the deck centre. look {at} computes
// the angles from the stand geometry in cal:stand (deck coordinates, mm; z up from the bed):
//   pivot        the pan/tilt axes' intersection (≈ the camera)       default front-centre of the CNC, above the bed
//   deck_center  what (0, 0) looks at
//   operator     where the operator's face is; `look operator` turns the phone's SCREEN (its face) toward it, i.e. the
//                camera points directly away from the operator
//   poses        named fixed aims, e.g. home
// Wells (A1..F7) and stocks (water/red/yellow/blue) resolve through the deck model to their rim centre.
import { CommandError } from "../hub";
import * as sim from "../sim";
import * as deckCal from "../deck_cal";
import { Logical, sleep } from "./base";

type V3 = [number, number, number];

export const STAND_DEFAULTS = {
  pan_ch: 2, tilt_ch: 3,
  // geometry: hardware/phone-stand/stand.json `cloud_cal_stand` (best-fit camera point, <= 2.3 deg pointing error)
  pan_center_deg: 84.4, tilt_center_deg: 63.9, // servo angle at aim (0, 0)
  pan_dir: -1, tilt_dir: 1,                  // flip if a servo turns the wrong way
  pan_limits: [-90.6, 79.4], tilt_limits: [-28.9, 106.1],
  pivot: [157.5, -131, 369.3] as V3,
  deck_center: [150, 90, 0] as V3,
  operator: [150, -560, 628] as V3,
  poses: { home: { pan: 0, tilt: 0 } } as Record<string, { pan: number; tilt: number }>,
  rim_h: 51.2,                               // beaker rim height above the bed
  ms: 600,                                   // eased move time
  settle_ms: 300,                            // wait after a move before a photo
};

const deg = (r: number) => (r * 180) / Math.PI;
const wrap = (a: number) => ((((a + 180) % 360) + 360) % 360) - 180;
const az = (d: V3) => deg(Math.atan2(d[1], d[0]));
const el = (d: V3) => deg(Math.atan2(d[2], Math.hypot(d[0], d[1])));
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];

/** (pan, tilt) in degrees that point the camera along `dir`, relative to the deck-centre reference view. */
export function anglesFor(cal: typeof STAND_DEFAULTS, dir: V3): { pan: number; tilt: number } {
  const ref = sub(cal.deck_center, cal.pivot);
  return { pan: wrap(az(dir) - az(ref)), tilt: el(dir) - el(ref) };
}

/** Deck point (bed coordinates) a look target names, or null for named poses / unknown names. */
export function targetPoint(cal: typeof STAND_DEFAULTS, at: string): V3 | null {
  const a = at.trim();
  if (a === "deck") return cal.deck_center;
  try {
    const [, x, y, z] = sim.loc(a); // spindle coords of the work height
    const [tx, ty] = deckCal.spindleToTip(x, y, z);
    return [tx, ty, cal.rim_h];
  } catch {
    return null;
  }
}

export class Stand extends Logical {
  readonly id = "stand";
  readonly kind = "pan_tilt";
  readonly defaults = STAND_DEFAULTS;
  state = { pan: null as number | null, tilt: null as number | null, moving: false, at: null as string | null };

  protected channels() { return [{ ch: this.cal.pan_ch, kind: "pos" }, { ch: this.cal.tilt_ch, kind: "pos" }]; }

  private async aim(pan: number, tilt: number, ms = this.cal.ms): Promise<void> {
    const c = this.cal;
    const clamp = (v: number, [lo, hi]: number[]) => Math.max(lo, Math.min(hi, v));
    pan = clamp(Number(pan), c.pan_limits);
    tilt = clamp(Number(tilt), c.tilt_limits);
    const servo = (center: number, dir: number, a: number) => Math.round(Math.max(0, Math.min(180, center + dir * a)) * 10) / 10;
    this.state.moving = true;
    try {
      await Promise.all([
        this.line({ cmd: "servo", ch: c.pan_ch, deg: servo(c.pan_center_deg, c.pan_dir, pan), ms }, ms / 1000 + 10),
        this.line({ cmd: "servo", ch: c.tilt_ch, deg: servo(c.tilt_center_deg, c.tilt_dir, tilt), ms }, ms / 1000 + 10),
      ]);
      Object.assign(this.state, { pan: Math.round(pan * 10) / 10, tilt: Math.round(tilt * 10) / 10 });
    } finally {
      this.state.moving = false;
    }
    await sleep(c.settle_ms);
  }

  async a_aim({ pan = 0, tilt = 0, ms }: { pan?: number; tilt?: number; ms?: number }) {
    await this.aim(pan, tilt, ms ?? this.cal.ms);
    this.state.at = null;
  }

  async a_look({ at = "deck" }: { at?: string }) {
    const c = this.cal as typeof STAND_DEFAULTS;
    let a: { pan: number; tilt: number };
    if (c.poses[at]) a = c.poses[at];
    else if (at === "operator") a = anglesFor(c, sub(c.pivot, c.operator)); // screen toward the operator
    else {
      const p = targetPoint(c, at);
      if (!p) throw new CommandError(`stand: unknown look target '${at}' (deck, operator, ${Object.keys(c.poses).join(", ")}, or a beaker)`);
      a = anglesFor(c, sub(p, c.pivot));
    }
    await this.aim(a.pan, a.tilt);
    this.state.at = at;
  }

  async a_relax() {
    await Promise.all([this.line({ cmd: "off", ch: this.cal.pan_ch }), this.line({ cmd: "off", ch: this.cal.tilt_ch })]);
    this.state.moving = false;
  }

  async a_stop() { await this.a_relax(); }
}
