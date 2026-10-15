import { afterEach, describe, expect, it } from "vitest";
import deck from "../../hardware/deck/deck.json";
import * as sim from "../src/sim";

describe("sim", () => {
  it("dry-runs a 20-beaker sweep fast and sanely", () => {
    const samples = sim.placeSamples(sim.sweep("orange", 20));
    expect(samples.map((x) => x.well).slice(0, 7)).toEqual(["A1", "B1", "C1", "D1", "E1", "F1", "A2"]);
    const steps = sim.stepsFor(samples);
    const t0 = performance.now();
    const r = sim.simulate(steps);
    expect(performance.now() - t0).toBeLessThan(500);
    expect(r.errors).toEqual([]);
    expect(r.tips_used).toEqual(["A1", "B1"]); // one tip per stock
    expect(Object.keys(r.wells).length).toBe(20);
    const orange = sim.parseTarget("orange")!.rgb;
    const best = Object.values(r.wells).reduce((a, b) => (sim.colorDist(b.rgb, orange) < sim.colorDist(a.rgb, orange) ? b : a));
    const [R, G, B] = best.rgb;
    expect(R > 180 && G > 60 && G < 200 && B < 60).toBe(true); // orange-ish
    for (const k of ["t", "cnc", "pipette", "wells"]) expect(r.frames!.at(-1)).toHaveProperty(k);
  });

  it("validates samples", () => {
    expect(() => sim.placeSamples([{ green: 10 }])).toThrow(/unknown key 'green'/);
    expect(() => sim.placeSamples([{ well: "A8", red: 10 }])).toThrow(/not a mixing beaker/);
    expect(() => sim.placeSamples([{ well: "A1", red: 10 }, { well: "a1", blue: 5 }])).toThrow(/used twice/);
    expect(() => sim.placeSamples([{ red: 0 }])).toThrow(/no liquid/);
    expect(sim.placeSamples([{ red: 10 }, { well: "A1", blue: 5 }]).map((x) => x.well)).toEqual(["B1", "A1"]);
    expect(sim.parseTarget("#ff8800")!.rgb).toEqual([255, 136, 0]);
    expect(sim.parseTarget("rgb(1, 2, 3)")!.rgb).toEqual([1, 2, 3]);
    expect(sim.simulate([{ op: "dispense", dst: "F1", ul: 5 }]).errors[0]).toMatch(/^step 0 \(dispense F1\):/);
    expect(() => sim.parseTarget("chartreuse-ish")).toThrow(/unknown target/);
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
    const r = sim.simulate(bad);
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
