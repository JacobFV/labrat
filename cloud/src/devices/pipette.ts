// Syringe pipette on hub channel 0 (FS90R continuous servo -> 12:1 gear train -> rack -> plunger), ported from the
// bridge's dead-reckoning Syringe. A continuous servo has no position feedback: the pulse width sets SPEED. Position
// is dead-reckoned from calibrated plunger speeds, re-zeroed by `home` (drive into the bottom stop; the firmware ends
// the drive early when the `syr_home` switch closes) and corrected by `observe`. Timed drives use the firmware's
// `pulse {ch, us, ms}`, so their length does not depend on network latency; the firmware returns the channel to its
// rest_us (neutral) when the hold ends. pos_mm = plunger height above its lowest point.
import { CommandError } from "../hub";
import { Logical, sleep } from "./base";

// defaults for cal:pipette (formerly the bridge's calib/syringe.json)
export const PIPETTE_DEFAULTS = {
  ch: 0,
  neutral_us: 1500,         // servo stops here
  deadband_us: 40,          // |us - neutral| below this does not turn the servo
  span_us: 400,             // us above the deadband at full speed
  dir: 1,                   // +1: us > neutral raises the plunger (aspirates)
  move_speed: 0.25,         // fraction of span for normal moves
  mm_per_s_up: 1.4,         // measured plunger speed at move_speed, raising
  mm_per_s_down: 1.4,       // ... lowering
  backlash_mm: 0.08,        // extra travel on direction reversal
  stroke_mm: 30.0,          // usable plunger travel
  syringe_id_mm: 17.8,      // barrel bore -> ul_per_mm = pi/4 * id^2
  ul_gain: 1.0,             // actual/geometric volume
  ready_mm: 2.0,            // air cushion kept below the liquid
  max_ul: 200.0,            // tip capacity
  home_speed: 0.12,         // slow, low-torque final drive into the bottom stop
  home_fast_above_mm: 1.5,  // with a trusted position, move fast to this height first
  home_overtravel_mm: 1.0,  // extra distance driven into the stop
  home_input: "syr_home",   // firmware input that ends the homing drive ("" if no switch is fitted)
  settle_ms: 120,
  min_pulse_ms: 25,
};

export class Pipette extends Logical {
  readonly id = "pipette";
  readonly kind = "servo_syringe";
  readonly defaults = PIPETTE_DEFAULTS;
  state = { pos_mm: 0, stroke_mm: PIPETTE_DEFAULTS.stroke_mm, vol_ul: 0, ul_per_mm: 0, homed: false, moving: false, last_dir: 0, obs: null as any };
  unlocked = new Set(["stop", "observe"]);

  protected channels() { return [{ ch: this.cal.ch, kind: "cont", rest_us: this.cal.neutral_us }]; }

  snapshot() {
    const { last_dir: _ld, ...s } = this.state;
    return { ...s, pos_mm: Math.round(s.pos_mm * 1000) / 1000, stroke_mm: this.cal.stroke_mm, ul_per_mm: Math.round(this.ulPerMm * 100) / 100 };
  }

  get ulPerMm(): number { const d = Number(this.cal.syringe_id_mm); return (Math.PI / 4) * d * d * Number(this.cal.ul_gain); }

  /** speed -1..1 (+ = plunger up) -> pulse width. */
  usFor(speed: number): number {
    const c = this.cal;
    speed = Math.max(-1, Math.min(1, Number(speed)));
    if (Math.abs(speed) < 1e-6) return Math.trunc(c.neutral_us);
    const mag = c.deadband_us + c.span_us * Math.abs(speed);
    return Math.round(c.neutral_us + c.dir * Math.sign(speed) * mag);
  }

  /** Drive at `speed` for `seconds` (firmware-timed), then neutral; always settle afterwards. */
  private async drive(speed: number, seconds: number, until?: string): Promise<any> {
    const ms = Math.max(1, Math.round(seconds * 1000));
    this.state.moving = true;
    try {
      return await this.line({ cmd: "pulse", ch: this.cal.ch, us: this.usFor(speed), ms, ...(until ? { until } : {}) }, ms / 1000 + 10);
    } catch (e) {
      await this.line({ cmd: "off", ch: this.cal.ch }).catch(() => {}); // no pulses also stops a continuous servo
      throw e;
    } finally {
      this.state.moving = false;
      await sleep(this.cal.settle_ms);
    }
  }

  private rate(up: boolean, speed: number): number {
    const c = this.cal;
    return Math.max(1e-3, ((up ? c.mm_per_s_up : c.mm_per_s_down) * speed) / c.move_speed); // ~linear above the deadband
  }

  private async moveTo(target: number, speed?: number): Promise<void> {
    const c = this.cal;
    if (!(target >= -1e-6 && target <= c.stroke_mm + 1e-6)) throw new CommandError(`plunger target ${target.toFixed(2)} mm outside 0..${c.stroke_mm}`);
    const d = target - this.state.pos_mm;
    if (Math.abs(d) < 1e-4) return;
    const up = d > 0, sign = up ? 1 : -1;
    let sp = Number(speed || c.move_speed), dist = Math.abs(d);
    if (this.state.last_dir && this.state.last_dir !== sign) dist += c.backlash_mm;
    let t = dist / this.rate(up, sp);
    if (t * 1000 < c.min_pulse_ms) { // too short to resolve: slow down instead
      sp = Math.max(0.02, (sp * t * 1000) / c.min_pulse_ms);
      t = dist / this.rate(up, sp);
    }
    await this.drive(sign * sp, t);
    this.state.pos_mm = target;
    this.state.last_dir = sign;
  }

  /** Fast to just above the stop if the position is trusted, then slow and low-torque into the bottom stop. */
  private async home(): Promise<void> {
    const c = this.cal;
    let dist: number;
    if (this.state.homed && this.state.pos_mm > c.home_fast_above_mm) {
      await this.moveTo(c.home_fast_above_mm);
      dist = c.home_fast_above_mm + c.home_overtravel_mm;
    } else {
      dist = c.stroke_mm + c.home_overtravel_mm;
    }
    await this.drive(-c.home_speed, dist / this.rate(false, c.home_speed), c.home_input || undefined);
    Object.assign(this.state, { pos_mm: 0, homed: true, last_dir: -1 });
  }

  // ---- actions ----
  async a_home() { await this.home(); this.state.vol_ul = 0; }

  async a_zero({ mm = 0 }) { Object.assign(this.state, { pos_mm: Number(mm), homed: true }); }

  /** External position estimate (camera / NN reading the rack), blended into the dead-reckoned position. */
  async a_observe({ mm, conf = 1 }: { mm: number; conf?: number }) {
    const m = Number(mm), k = Math.max(0, Math.min(1, Number(conf)));
    const err = m - this.state.pos_mm;
    this.state.pos_mm = Math.max(0, Math.min(this.cal.stroke_mm, this.state.pos_mm + k * err));
    this.state.obs = { mm: Math.round(m * 1000) / 1000, conf: k, err_mm: Math.round(err * 1000) / 1000, ts: Date.now() / 1000 };
  }

  async a_move({ mm, speed }: { mm: number; speed?: number }) { await this.moveTo(Number(mm), speed); }

  async a_jog({ mm, speed }: { mm: number; speed?: number }) { await this.moveTo(this.state.pos_mm + Number(mm), speed); }

  /** Raw open-loop drive for calibration (position is NOT tracked: home/zero/observe afterwards). */
  async a_drive({ speed, ms }: { speed: number; ms: number }) {
    if (!(ms > 0 && ms <= 5000)) throw new CommandError("ms must be in (0, 5000]");
    await this.drive(Number(speed), Number(ms) / 1000);
    this.state.homed = false;
  }

  /** Plunger to ready_mm (do this ABOVE the liquid): the air cushion the aspirate starts from. */
  async a_prime() {
    if (!this.state.homed) await this.home();
    await this.moveTo(this.cal.ready_mm);
  }

  async a_aspirate({ ul }: { ul: number }) {
    ul = Number(ul);
    if (!(ul > 0) || this.state.vol_ul + ul > this.cal.max_ul + 1e-6) throw new CommandError(`aspirate ${ul} uL exceeds capacity (${this.state.vol_ul}/${this.cal.max_ul})`);
    if (this.state.vol_ul === 0 && Math.abs(this.state.pos_mm - this.cal.ready_mm) > 1e-3) throw new CommandError("prime first (plunger not at ready_mm above the liquid)");
    await this.moveTo(this.state.pos_mm + ul / this.ulPerMm);
    this.state.vol_ul = Math.round((this.state.vol_ul + ul) * 100) / 100;
  }

  async a_dispense({ ul }: { ul?: number | null }) {
    if (ul == null || Number(ul) >= this.state.vol_ul - 1e-6) {
      await this.moveTo(this.cal.ready_mm);
      this.state.vol_ul = 0;
    } else {
      await this.moveTo(Math.max(this.cal.ready_mm, this.state.pos_mm - Number(ul) / this.ulPerMm));
      this.state.vol_ul = Math.round((this.state.vol_ul - Number(ul)) * 100) / 100;
    }
  }

  /** Push the air cushion out too (plunger to 0) to clear the last drop. */
  async a_blowout() { await this.moveTo(0); this.state.vol_ul = 0; }

  /** Stop the servo (channel off). The rack holds through the servo gearbox. */
  async a_rest() { await this.line({ cmd: "off", ch: this.cal.ch }); }

  async a_stop() {
    if (this.state.moving) this.state.homed = false; // interrupted mid-move: dead reckoning no longer trustworthy
    await this.line({ cmd: "off", ch: this.cal.ch }); // a new command on the channel preempts the running pulse
    this.state.moving = false;
  }
}
