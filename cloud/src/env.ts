import type { Hub } from "./hub";
import type { Store } from "./store";

export interface Env {
  DB: D1Database;
  LAB: DurableObjectNamespace;
  ASSETS: Fetcher;
  LABRAT_TOKEN?: string;
  ANTHROPIC_API_KEY?: string;
  ANTHROPIC_MODEL?: string;
  ANTHROPIC_BASE_URL?: string;
  LABRAT_AUTO_APPROVE?: string;
  LABRAT_AUTO_OBSERVE?: string;
  LABRAT_PHOTO_WAIT_S?: string;
}

/** Everything the lab modules share inside the Durable Object. */
export interface Ctx {
  env: Env;
  store: Store;
  hub: Hub;
  /** Run a background task; errors are logged to the UIs, never thrown. */
  bg(p: Promise<unknown>, what?: string): void;
  /** Experiments running on the hardware, by id; `aborted` is checked between steps. */
  running: Map<string, { aborted: boolean }>;
  /** Last commanded CNC position (spindle, mm). */
  pos: { x: number; y: number; z: number };
}

export const flag = (v: string | undefined, dflt = false) => (v == null || v === "" ? dflt : ["1", "true", "yes", "on"].includes(v.toLowerCase()));
