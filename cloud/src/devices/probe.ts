// Probe arm on hub channel 1 (SG90 positional servo) that swings the capacitive liquid-level probe down into its
// working position. The hub enforces the rules around it: cnc.probe is refused unless the arm is deployed, the arm is
// retracted after every probe cycle and before any CNC motion that can take the pipette below safe Z.
import { Logical, sleep } from "./base";
import toolsJson from "../../../hardware/tool-head/tools.json";

const CAD = toolsJson.tools.probe.servo; // hardware/tool-head/tools.json (syrhead.build): the arm's real angles

export const PROBE_DEFAULTS = {
  ch: CAD.ch,
  deployed_deg: CAD.deployed_deg,   // servo angle with the probe down in its working position (just off the hard stop)
  retracted_deg: CAD.retracted_deg, // servo angle with the probe folded up clear of the pipette tip
  ms: 700,            // eased swing time (the swing is ~150 deg; an SG90 needs ~0.4 s at 5 V)
  settle_ms: 150,
};

export class Probe extends Logical {
  readonly id = "probe";
  readonly kind = "servo_arm";
  readonly defaults = PROBE_DEFAULTS;
  state = { deployed: false, deg: null as number | null, moving: false };

  protected channels() { return [{ ch: this.cal.ch, kind: "pos" }]; }

  private async swing(deg: number): Promise<void> {
    deg = Math.max(0, Math.min(180, Number(deg)));
    this.state.moving = true;
    try {
      await this.line({ cmd: "servo", ch: this.cal.ch, deg, ms: this.cal.ms }, this.cal.ms / 1000 + 10);
      this.state.deg = deg;
    } finally {
      this.state.moving = false;
    }
    this.state.deployed = Math.abs(deg - this.cal.deployed_deg) < 2;
    await sleep(this.cal.settle_ms);
  }

  async a_deploy() { await this.swing(this.cal.deployed_deg); }
  async a_retract() { await this.swing(this.cal.retracted_deg); }
  async a_set({ deg }: { deg: number }) { await this.swing(deg); }
  async a_stop() { await this.line({ cmd: "off", ch: this.cal.ch }); this.state.moving = false; }

  /** Retract unless known retracted (no-op without a hub: then the arm cannot have been deployed either). */
  async ensureRetracted(): Promise<void> {
    if (!this.hub.online("hub")) return;
    if (this.state.deployed || this.state.deg == null || this.state.moving) await this.run("retract", {});
  }
}
