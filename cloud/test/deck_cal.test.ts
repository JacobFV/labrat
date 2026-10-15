import { SELF, evictDurableObject, env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import * as deckCal from "../src/deck_cal";
import * as sim from "../src/sim";

const close = (a: number[], b: number[], eps = 1e-6) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i], -Math.log10(eps)));

describe("deck calibration", () => {
  beforeEach(() => { deckCal.reset(); });

  it("is nominal when uncalibrated", () => {
    expect(sim.wellXy("A1", sim.PLATE)).toEqual([153.25, 32.0]);
    expect(sim.wellXyz("F7", sim.PLATE, sim.Z_WELL)).toEqual([153.25 + 6 * 19, 32.0 + 5 * 19, sim.Z_WELL]);
    expect(sim.loc("red").slice(1)).toEqual([153.25 + 7 * 19, 51.0, sim.Z_RES]); // stock beaker B8
    expect(() => sim.wellXy("G1", sim.PLATE)).toThrow(); // 6 rows
    expect(() => sim.wellXy("A8", sim.PLATE)).toThrow(); // column 8 = stocks
    expect(sim.ejectXyz()).toEqual([10.0, 170.0, -20.0]);
  });

  it("round-trips the pipette offset", () => {
    const s: [number, number, number] = [100, 50, -10];
    expect(deckCal.spindleToTip(...s)).toEqual(s);
    deckCal.update({ pipette_offset: { dy: -45.0 } });
    const t = deckCal.spindleToTip(...s);
    expect(t).toEqual([100, 5, -10]);
    expect(deckCal.tipToSpindle(...t)).toEqual(s);
  });

  it("teaches a single corner with the nominal pitch", () => {
    const tip = deckCal.teach("plate.a1", [150, 70, -30]);
    expect([tip.x, tip.y, tip.z]).toEqual([150, 70, -30]);
    close(sim.wellXyz("A1", sim.PLATE, -99), [150, 70, -30]);
    close(sim.wellXyz("B3", sim.PLATE, -99), [188, 89, -30]);
  });

  it("derives rotation and pitch from two corners", () => {
    const th = (2 * Math.PI) / 180, p = 9.1, a1 = [30, 40], u = 11 * p, v = 7 * p;
    const h12 = [a1[0] + u * Math.cos(th) - v * Math.sin(th), a1[1] + u * Math.sin(th) + v * Math.cos(th)];
    deckCal.update({ points: { "tip_rack.A1": { x: a1[0], y: a1[1], z: -38 }, "tip_rack.H12": { x: h12[0], y: h12[1], z: -38 } } });
    const f = deckCal.gridFrame("tip_rack")!;
    expect(f.pitch).toBeCloseTo(p, 9);
    expect(f.theta_deg).toBeCloseTo(2.0, 9);
    const r = 2, c = 4;
    close(sim.wellXyz("C5", sim.TIP_RACK, 0), [a1[0] + c * p * Math.cos(th) - r * p * Math.sin(th), a1[1] + c * p * Math.sin(th) + r * p * Math.cos(th), -38]);
  });

  it("persists in D1 across a Durable Object restart", async () => {
    const api = (path: string, body?: unknown) => SELF.fetch(`http://lab${path}?token=labrat`, body ? { method: "POST", body: JSON.stringify(body) } : {}).then((r) => r.json<any>());
    await api("/api/deck_cal", { pipette_offset: { dy: -44.5 }, points: { waste: { x: 280, y: 120, z: -5 } } });
    deckCal.reset();
    await evictDurableObject(env.LAB.get(env.LAB.idFromName("lab")));
    const cal = await api("/api/deck_cal");
    expect(cal.pipette_offset.dy).toBe(-44.5);
    close(sim.loc("waste").slice(1) as number[], [280, 164.5, -5]);
    await api("/api/deck_cal", { pipette_offset: { dy: 0 }, points: { waste: null } });
  });

  it("drives the simulator from the calibrated deck", () => {
    deckCal.update({ points: { "plate.A1": { x: 150, y: 10, z: -30 } } });
    const r = sim.simulate(sim.protocolFromRatios(["red", "yellow"], [0.5, 0.5], 100, "A1"), "orange");
    expect(r.frames!.some((f) => f.cnc.x === 150 && f.cnc.y === 10)).toBe(true);
  });
});
