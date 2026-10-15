import type { Hub } from "../hub";
import type { Logical } from "./base";
import { Pipette } from "./pipette";
import { Probe } from "./probe";
import { Stand } from "./stand";

export type { Logical } from "./base";

export function makeLogical(hub: Hub): Record<string, Logical> {
  return { pipette: new Pipette(hub), probe: new Probe(hub), stand: new Stand(hub) };
}
