import { afterEach, describe, expect, it } from "vitest";
import deck from "../../hardware/deck/deck.json";
import * as sim from "../src/sim";

describe("sim", () => {
  it("simulates 20 variants fast and sanely", () => {
    const goal = "make the most orange-looking well using red & yellow";
    const vs = sim.generateVariants(goal, 20);
    const t0 = performance.now();
    const rs = vs.map((v) => sim.simulate(v.protocol, goal));
    expect(performance.now() - t0).toBeLessThan(500);
    expect(rs.filter((r) => !r.ok).map((r) => r.errors)).toEqual([]);
    const best = rs.reduce((a, b) => (b.score > a.score ? b : a));
    expect(best.score).toBeGreaterThan(0.85);
    const [r, g, b] = best.predicted_rgb!;
    expect(r > 180 && g > 60 && g < 200 && b < 60).toBe(true); // orange-ish
    const last = best.frames!.at(-1)!;
    for (const k of ["t", "cnc", "pipette", "wells"]) expect(last).toHaveProperty(k);
  });

  const cap = sim.PLATE.cap_ul;
  afterEach(() => { sim.PLATE.cap_ul = cap; });

  it("catches protocol errors", () => {
    sim.PLATE.cap_ul = 400; // a small beaker, so 450 uL overflows
    const bad: sim.Step[] = [
      { op: "aspirate", src: "red", ul: 50 }, // no tip
      { op: "pick_tip", rack_well: "A1" },
      { op: "aspirate", src: "red", ul: 150 },
      { op: "dispense", dst: "A1", ul: 150 },
      { op: "aspirate", src: "yellow", ul: 100 }, // contamination
      { op: "dispense", dst: "A1", ul: 100 },
      { op: "eject_tip" },
      { op: "pick_tip", rack_well: "A2" },
      { op: "aspirate", src: "blue", ul: 200 },
      { op: "dispense", dst: "A1", ul: 200 }, // overflow
      { op: "eject_tip" },
      { op: "pick_tip", rack_well: "A1" }, // reused tip
    ];
    const r = sim.simulate(bad, "make orange");
    expect(r.ok).toBe(false);
    const errs = r.errors.join(" ");
    for (const frag of ["without tip", "contamination", "overflow", "already used"]) expect(errs).toContain(frag);
  });

  it("matches the deck CAD (hardware/deck/deck.json)", () => {
    const b = deck.beakers, t = deck.tip_rack;
    expect(sim.PLATE.origin).toEqual(b.a1);
    expect(sim.PLATE.pitch).toBe(b.pitch);
    expect([sim.PLATE.rows, sim.PLATE.cols]).toEqual([b.rows, b.mix_cols]);
    expect(sim.TIP_RACK.origin).toEqual(t.a1);
    expect(sim.TIP_RACK.pitch).toBe(t.pitch);
    for (const [name, well] of Object.entries(b.stocks)) {
      const r = "ABCDEF".indexOf(well[0]), c = Number(well.slice(1)) - 1;
      expect(sim.RESERVOIRS[name]).toEqual([b.a1[0] + c * b.pitch, b.a1[1] + r * b.pitch]);
    }
    expect(sim.SAFE_TIP_H).toBe(deck.head.tip_end_at_z0);
    expect(-sim.BOUNDS.z[0]).toBe(deck.head.z_travel);
    expect(sim.WASTE).toEqual(deck.waste);
    expect(sim.EJECT_BAR).toEqual(deck.eject_bar);
  });

  it("enforces the tip's reach", () => {
    // aspirating a stock below the reachable level is an error; small mixes skip the mix step
    let r = sim.simulate([{ op: "pick_tip", rack_well: "A1" }, { op: "aspirate", src: "red", ul: 200 },
      { op: "dispense", dst: "A1", ul: 200 }, { op: "mix", well: "A1" }, { op: "eject_tip" }]);
    expect(r.ok).toBe(true);
    expect(r.warnings.some((w) => w.includes("mix skipped"))).toBe(true);
    const proto: sim.Step[] = [{ op: "pick_tip", rack_well: "A1" }];
    for (let i = 0; i < 18; i++) proto.push({ op: "aspirate", src: "blue", ul: 200 }, { op: "dispense", dst: "B2", ul: 200 });
    r = sim.simulate([...proto, { op: "eject_tip" }]);
    expect(r.ok).toBe(false);
    expect(r.errors.some((e) => e.includes("only reaches liquid"))).toBe(true);
  });

  it("refits absorbance toward observations (learn.fit)", async () => {
    const { fit } = await import("../src/learn");
    const comp = { red: 100, yellow: 100 };
    const p = fit([{ comp, rgb: [225, 95, 25] }], sim.factoryParams());
    sim.setParams(p);
    const after = sim.colorDist(sim.mixRgb(comp), [225, 95, 25]);
    sim.setParams(sim.factoryParams());
    expect(after).toBeLessThan(sim.colorDist(sim.mixRgb(comp), [225, 95, 25]));
    expect(p.absorbance.water).toEqual([0, 0, 0]);
  });
});
