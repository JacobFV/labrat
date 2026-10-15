import { beforeEach, describe, expect, it } from "vitest";
import tools from "../../hardware/tool-head/tools.json";
import * as deckCal from "../src/deck_cal";
import * as sim from "../src/sim";
import * as tooling from "../src/tooling";

describe("tool rail", () => {
  beforeEach(() => { deckCal.reset(); });

  it("matches the CAD tool offsets (hardware/tool-head/tools.json)", () => {
    const cad: Record<string, { offset: number[] }> = tools.tools;
    for (const [name, nom] of Object.entries(deckCal.TOOLS_NOMINAL)) {
      [nom.dx, nom.dy, nom.dz].forEach((v, i) => expect(v).toBeCloseTo(cad[name].offset[i], 3));
    }
    expect(cad.pipette.offset).toEqual([0, 0, 0]);
  });

  it("moves the spindle by the tool offset", () => {
    const [x, y] = sim.wellXy("C3", sim.PLATE);
    expect(tooling.plan("pipette", "C3").spindle.slice(0, 2)).toEqual([x, y]);
    const m = tooling.plan("microscope", "C3", null, null, -10);
    const o = deckCal.toolOffset("microscope");
    const r2 = (v: number) => Math.round(v * 100) / 100;
    expect(m.spindle).toEqual([r2(x - o[0]), r2(y - o[1]), r2(-10 - o[2])]);
    deckCal.update({ tools: { microscope: { dx: -10.0 } } }); // a taught tool offset moves the plan
    expect(tooling.plan("microscope", "C3", null, null, -10).spindle[0]).toBeCloseTo(r2(x + 10), 6);
  });

  it("reports reach", () => {
    // the microscope hangs 63 mm in front of the spindle: the back rows are out of its reach
    const far = tooling.plan("microscope", null, 150, 170);
    expect(far.reachable).toBe(false);
    expect(far.why).toContain("y=");
    expect(() => tooling.plan("laser", "A1")).toThrow();
  });
});
