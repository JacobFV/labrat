// LabRat agent: Anthropic tool-use loop (Messages API over fetch) + a rule-based fallback when no key is set or the
// call fails. One turn at a time; the last 12 text turns are kept as history (in DO storage).
import * as sim from "./sim";
import * as lab from "./lab";
import * as tooling from "./tooling";
import type { Ctx } from "./env";
import { sleep } from "./hub";
import { completeText, llmAvailable, LlmRefusal, messages, model, type ContentBlock } from "./llm";
import { now } from "./store";

const INVENTORY = "water; food colouring (any colour); white vinegar; baking soda; borax; ammonium chloride; pink Himalayan " +
  "salt; PLA crumbs; food crumbs; the operator's own saliva, mucus and cheek-cell scrapings. Safety: never " +
  "cap a beaker that is fizzing (vinegar + baking soda makes CO2); ammonium chloride with borax or baking " +
  "soda gives off ammonia, so keep those mixes small and ventilated; biological samples are the operator's " +
  "own only, get their own tips and are bleached afterwards; mucus is too viscous for a 200 uL tip.";

export const SYSTEM = `You are LabRat, a friendly voice-operated desk-lab robot agent. Physical setup: a 3018 CNC (cnc) carries a syringe
pipette (pipette: a disposable syringe with a 200 µL tip on a luer adapter, plunger driven by a 12:1 gear train and rack on a
continuous-rotation servo on channel 0 of the servo hub; the tip is on the spindle axis). Two SO-101 arms with stock grippers,
so_a and so_b, stand on either side of the CNC for pick-and-place (named poses: home, ready, rest). A phone on a pan/tilt stand
(stand) at the front of the CNC is your eye, ear and mouth: look() returns its latest frame, and the stand can aim it at the
deck, a beaker or the operator. Deck (mm): FT-200L-RS tip_rack 8x12 (A1 at 17,19.25, pitch 9); a printed beaker matrix of
15.3 x 50 mm beakers, 6 rows A-F x 8 columns on a 19 mm pitch with A1 at (153.25,32): columns 1-7 are the mixing beakers
("wells" A1-F7, up to 5 mL each), column 8 holds the stocks (A8 water, B8 red, C8 yellow, D8 blue, 4.5 mL each); waste
(280,170), eject bar (10,170). The tip only reaches liquid while a beaker holds about 1.2 mL or more. The head carries a USB
microscope and a capacitive liquid-level probe on a tool rail (tool(): goto / snap / probe; each tool has its own offset);
the probe swings down on a servo arm (probe) only while probing.
Reagents on hand (only the food dyes and water are modelled by the simulator; anything else needs the operator):
${INVENTORY} Work is SIMULATION-FIRST: for any wet-lab goal, recall relevant memories, design_experiment (many variants),
simulate, select the best variant with a reason, then run_variant (this requests human approval). Never move hardware into
unsafe positions. Keep spoken replies short (1-2 sentences) since they are read aloud on the phone. Use say() only for
interim spoken updates; your final text reply is spoken automatically.`;

const obj = (properties: Record<string, unknown>, required: string[] = []) => ({ type: "object", properties, required });
export const TOOLS = [
  { name: "list_devices", description: "List connected hardware devices and their live state.", input_schema: obj({}) },
  { name: "look", description: "Get a fresh camera frame from the phone on the stand.", input_schema: obj({}) },
  { name: "recall", description: "Search long-term memory (past experiments, observations, notes).", input_schema: obj({ query: { type: "string" }, type: { type: "string", enum: ["semantic", "episodic", "procedural", "taxonomic"] } }, ["query"]) },
  { name: "design_experiment", description: "Create an experiment for a goal with N protocol variants (e.g. dye ratios/volumes).", input_schema: obj({ goal: { type: "string" }, n_variants: { type: "integer" } }, ["goal"]) },
  { name: "simulate", description: "Run the digital twin on all variants of an experiment; returns checks and scores.", input_schema: obj({ experiment_id: { type: "string" } }, ["experiment_id"]) },
  { name: "select_variant", description: "Select a variant to run, with a reason.", input_schema: obj({ experiment_id: { type: "string" }, variant_id: { type: "string" }, reason: { type: "string" } }, ["experiment_id", "variant_id"]) },
  { name: "run_variant", description: "Request a real hardware run of a simulated, passing variant (needs human approval).", input_schema: obj({ experiment_id: { type: "string" }, variant_id: { type: "string" } }, ["experiment_id"]) },
  { name: "move_arm", description: "Move an SO-101 gripper arm (so_a left of the CNC, so_b right) to a named pose (home, ready, rest) or joint angles (deg).", input_schema: obj({ device: { type: "string" }, pose: { type: "string" }, joints: { type: "object" }, ms: { type: "integer" } }, ["device"]) },
  { name: "cnc_move", description: "Move the CNC syringe head (absolute mm) or home it (home=true).", input_schema: obj({ x: { type: "number" }, y: { type: "number" }, z: { type: "number" }, f: { type: "number" }, home: { type: "boolean" } }) },
  { name: "tool", description: "Head tool rail: put a tool over a deck target (goto), take a USB-microscope picture (snap: optionally over a target first), or probe a beaker's liquid surface with the capacitive probe (probe -> surface height + estimated volume). Tools: pipette (the syringe tip), microscope, probe.",
    input_schema: obj({ action: { type: "string", enum: ["goto", "snap", "probe", "plan"] }, tool: { type: "string", enum: tooling.TOOL_NAMES }, target: { type: "string", description: "beaker A1-F7, a stock (water/red/yellow/blue), waste" }, x: { type: "number" }, y: { type: "number" }, z: { type: "number" } }, ["action"]) },
  { name: "pipette", description: "Syringe pipette action: home (drive plunger to its bottom stop), prime (plunger to the ready air cushion; do it above the liquid), aspirate/dispense (ul), blowout, move (plunger height mm, 0..stroke).",
    input_schema: obj({ action: { type: "string", enum: ["home", "prime", "aspirate", "dispense", "blowout", "move"] }, ul: { type: "number" }, mm: { type: "number" } }, ["action"]) },
  { name: "stand", description: "Aim the phone stand: look at deck, operator, home or a beaker (A1-F7, red/yellow/blue/water), or aim pan/tilt degrees (0,0 = deck centre).",
    input_schema: obj({ at: { type: "string" }, pan: { type: "number" }, tilt: { type: "number" } }) },
  { name: "say", description: "Speak a short sentence through the phone.", input_schema: obj({ text: { type: "string" } }, ["text"]) },
  { name: "remember", description: "Store a note in long-term memory.", input_schema: obj({ text: { type: "string" }, tags: { type: "array", items: { type: "string" } }, type: { type: "string", enum: ["semantic", "episodic", "procedural", "taxonomic"] } }, ["text"]) },
];

type Turn = { role: "user" | "assistant"; content: string };

export class Agent {
  private history: Turn[] = [];
  private chain: Promise<unknown> = Promise.resolve();

  constructor(private c: Ctx, private storage: DurableObjectStorage) {}

  async load() { this.history = (await this.storage.get<Turn[]>("agent:history")) ?? []; }

  face(mood: string) { this.c.hub.phone({ t: "face", mood, gaze: [0, 0] }); }
  speak(text: string) { this.face("speaking"); this.c.hub.phone({ t: "say", text }); }
  chatUi(role: string, text: string, meta: Record<string, unknown> = {}) { this.c.hub.ui({ t: "chat", role, text, meta }); }

  /** One agent turn (serialised). Returns the reply text. */
  handle(text: string, source = "ui"): Promise<string> {
    text = (text ?? "").trim();
    if (!text) return Promise.resolve("");
    this.chatUi("user", text, { source });
    this.c.hub.phone({ t: "transcript", role: "user", text });
    const p = this.chain.then(() => this.turn(text, source));
    this.chain = p.catch(() => {});
    return p;
  }

  private async turn(text: string, source: string): Promise<string> {
    this.face("thinking");
    const t0 = Date.now(), useLlm = llmAvailable(this.c.env);
    let reply: string;
    try {
      reply = useLlm ? await this.llmLoop(text) : await this.rules(text);
    } catch (e) {
      if (e instanceof LlmRefusal) reply = "Sorry, I can't help with that one.";
      else {
        this.c.hub.log("warn", `agent: ${useLlm ? "LLM failed, rules this turn" : "rules failed"}: ${(e as Error).message}`);
        try { reply = await this.rules(text); } catch (e2) { reply = `Sorry, something went wrong: ${(e2 as Error).message}`; }
      }
    }
    this.history.push({ role: "user", content: text }, { role: "assistant", content: reply });
    this.history = this.history.slice(-24);
    await this.storage.put("agent:history", this.history);
    await this.c.store.event("chat", { source, text, reply, model: useLlm ? model(this.c.env) : "rules", ms: Date.now() - t0 });
    this.chatUi("agent", reply);
    this.c.hub.phone({ t: "transcript", role: "agent", text: reply });
    this.speak(reply.slice(0, 600));
    this.face("idle");
    return reply;
  }

  // ---------------- tools ----------------
  async runTool(name: string, args: Record<string, any>): Promise<any> {
    let out: any, ok = true;
    try { out = await this.tool(name, args ?? {}); } catch (e) { out = { error: (e as Error).message }; ok = false; }
    await this.c.store.event("tool", { name, args, ok });
    const short = out?._image ? "<image>" : JSON.stringify(out ?? null).slice(0, 600);
    this.chatUi("tool", `${name}(${JSON.stringify(args).slice(0, 200)}) → ${short.slice(0, 300)}`, { tool: name, ok });
    return out;
  }

  private async tool(name: string, a: Record<string, any>): Promise<any> {
    const { c } = this;
    switch (name) {
      case "list_devices":
        return { bridge: c.hub.devicesMsg().bridge, devices: c.hub.allDevices().map(({ id, kind, status, state }) => ({ id, kind, status, state })) };
      case "look": {
        const t = now();
        c.hub.phone({ t: "snap" });
        const f = (await c.hub.waitFrame(t, 2)) ?? c.hub.latestFrame;
        if (!f) return { error: "no camera frame yet (phone not connected?)" };
        return { _image: f.jpeg, w: f.w, h: f.h, age_s: Math.round((now() - f.ts) * 10) / 10 };
      }
      case "recall":
        return { memories: (await c.store.recall(String(a.query ?? ""), 5, a.type)).map(({ type, text, tags, score, confidence }) => ({ type, text, tags, score, confidence })) };
      case "design_experiment": {
        const n = Math.trunc(Number(a.n_variants) || 12);
        let vs = await this.llmVariants(String(a.goal), n);
        const source = vs ? "llm" : "deterministic";
        if (vs && vs.length < n) {
          const extra = sim.generateVariants(a.goal, n).slice(vs.length);
          extra.forEach((v, i) => (v.id = `v${String(vs!.length + i + 1).padStart(2, "0")}`));
          vs = [...vs, ...extra];
        }
        const e = await lab.createExperiment(c, String(a.goal), n, vs, source);
        return { experiment_id: e.id, n_variants: e.variants.length, source, dyes: e.variants[0].params.dyes };
      }
      case "simulate":
        return summary(await lab.simulateExperiment(c, a.experiment_id));
      case "select_variant":
        return { selected: (await lab.selectVariant(c, a.experiment_id, a.variant_id, a.reason ?? "")).selected_variant_id };
      case "run_variant": {
        const run = await lab.requestRun(c, a.experiment_id, a.variant_id);
        return { run_id: run.id, status: run.status, note: run.status === "awaiting_approval" ? "waiting for human approval in the web UI or by voice ('approve')" : "" };
      }
      case "move_arm":
        if (a.pose) return c.hub.cmd(a.device, "pose", { name: a.pose });
        return c.hub.cmd(a.device, "joints", { q: a.joints ?? {}, ms: a.ms ?? 1500 });
      case "cnc_move": {
        if (a.home) return c.hub.cmd("cnc", "home", {}, 90);
        const p = { x: Number(a.x ?? 0), y: Number(a.y ?? 0), z: Number(a.z ?? 0) };
        for (const ax of ["x", "y", "z"] as const) {
          const [lo, hi] = sim.BOUNDS[ax];
          if (!(lo <= p[ax] && p[ax] <= hi)) throw new Error(`${ax}=${p[ax]} outside [${lo},${hi}]`);
        }
        Object.assign(c.pos, p);
        return c.hub.cmd("cnc", "move", { ...p, f: Number(a.f ?? 2000) }, 60);
      }
      case "tool": {
        const act = a.action, tool = a.tool || ({ snap: "microscope", probe: "probe" } as Record<string, string>)[act] || "pipette";
        if (act === "plan") return tooling.plan(tool, a.target, a.x, a.y, a.z);
        if (act === "goto") return tooling.goto(c, tool, a.target, a.x, a.y, a.z);
        if (act === "snap") return tooling.snap(c, a.target, a.x, a.y, a.z);
        if (act === "probe") return tooling.probeLevel(c, a.target);
        throw new Error(`unknown tool action ${act}`);
      }
      case "pipette": {
        const act = a.action;
        const args = act === "aspirate" || act === "dispense" ? { ul: a.ul } : act === "move" ? { mm: Number(a.mm ?? 0) } : {};
        return c.hub.cmd("pipette", act, args);
      }
      case "stand":
        return a.at ? c.hub.cmd("stand", "look", { at: a.at }) : c.hub.cmd("stand", "aim", { pan: a.pan ?? 0, tilt: a.tilt ?? 0 });
      case "say":
        this.speak(a.text);
        this.chatUi("agent", a.text, { spoken: true });
        return { ok: true };
      case "remember":
        return { memory_id: (await c.store.remember(a.text, { tags: a.tags, type: a.type ?? "semantic" })).id };
    }
    throw new Error(`unknown tool ${name}`);
  }

  /** Ask the LLM for variant parameters; protocols are built deterministically. null on any failure. */
  private async llmVariants(goal: string, n: number): Promise<sim.Variant[] | null> {
    if (!llmAvailable(this.c.env)) return null;
    const prompt = `Goal: ${goal}\nReservoir dyes available: water, red, yellow, blue. Propose ${n} diverse variants as JSON only: ` +
      '[{"dyes":["red","yellow"],"ratios":[0.2,0.8],"total_ul":200}] . total_ul between 60 and 280.';
    try {
      const txt = await Promise.race([completeText(this.c.env, prompt), sleep(25000).then(() => { throw new Error("timeout"); })]);
      const m = /\[[\s\S]*\]/.exec(txt);
      const items: any[] = m ? JSON.parse(m[0]) : [];
      const out: sim.Variant[] = [];
      for (const it of items.slice(0, n)) {
        const dyes = (it.dyes ?? []).filter((d: string) => d in sim.DYES);
        const ratios = (it.ratios ?? []).map(Number).slice(0, dyes.length);
        if (!dyes.length || ratios.length !== dyes.length) continue;
        const i = out.length, total = Math.max(60, Math.min(280, Number(it.total_ul ?? 200)));
        const well = `${sim.ROWS[i % sim.PLATE.rows]}${Math.floor(i / sim.PLATE.rows) + 1}`;
        out.push({ id: `v${String(i + 1).padStart(2, "0")}`, params: { dyes, ratios, total_ul: total, well }, protocol: sim.protocolFromRatios(dyes, ratios, total, well) });
      }
      return out.length ? out : null;
    } catch (e) {
      this.c.hub.log("warn", `LLM variant design failed (${(e as Error).message}); deterministic generator used`);
      return null;
    }
  }

  // ---------------- LLM loop ----------------
  private async llmLoop(text: string): Promise<string> {
    const msgs: any[] = [...this.history.slice(-12), { role: "user", content: text }];
    let final = "";
    for (let i = 0; i < 10; i++) {
      const resp = await messages(this.c.env, { max_tokens: 16000, system: SYSTEM, tools: TOOLS, messages: msgs });
      msgs.push({ role: "assistant", content: resp.content }); // unchanged, thinking blocks included
      const texts = resp.content.filter((b) => b.type === "text" && b.text?.trim()).map((b) => b.text!);
      if (texts.length) final = texts.join("\n");
      if (resp.stop_reason !== "tool_use") break;
      if (texts.length) this.chatUi("agent", final, { interim: true });
      const results: any[] = [];
      for (const b of resp.content.filter((x) => x.type === "tool_use")) {
        const out = await this.runTool(b.name!, b.input ?? {});
        const content: string | ContentBlock[] = out?._image
          ? [{ type: "image", source: { type: "base64", media_type: out._mime ?? "image/jpeg", data: out._image } }, { type: "text", text: `camera frame ${out.w}x${out.h}, ${out.age_s ?? 0} s old` }]
          : JSON.stringify(out ?? null).slice(0, 8000);
        results.push({ type: "tool_result", tool_use_id: b.id, content, ...(out?.error ? { is_error: true } : {}) });
      }
      msgs.push({ role: "user", content: results });
    }
    return final || "Done.";
  }

  // ---------------- rule-based fallback ----------------
  private async latestExp() { return (await this.c.store.listExperiments(1))[0] ?? null; }

  async rules(text: string): Promise<string> {
    const { c } = this;
    const t = text.toLowerCase().trim();
    if (/\b(approve|go ahead|yes,? (run|go|do it)|confirm)\b/.test(t)) {
      const r = await c.store.pendingRun();
      if (!r) return "There is no run waiting for approval.";
      await lab.approve(c, r.id);
      return `Approved. Running variant ${r.variant_id} now.`;
    }
    if (/\b(abort|stop|halt|emergency)\b/.test(t)) {
      await lab.abort(c);
      return "Stopping everything.";
    }
    if (/\bhome\b/.test(t)) {
      const msgs: string[] = [];
      for (const dev of ["so_a", "so_b"]) if (c.hub.online(dev)) {
        const r = await this.runTool("move_arm", { device: dev, pose: "home" });
        msgs.push(`${dev} ${r.error ?? "ok"}`);
      }
      if (c.hub.online("cnc") && /\b(cnc|all|everything)\b/.test(t)) {
        const r = await this.runTool("cnc_move", { home: true });
        msgs.push(`cnc ${r.error ?? "ok"}`);
      }
      return `Homing: ${msgs.join(", ") || "no devices connected"}.`;
    }
    if (/\b(look|see|camera)\b/.test(t)) {
      const r = await this.runTool("look", {});
      if (r.error) return "I can't see anything yet. Is the phone connected?";
      return `I grabbed a ${r.w}x${r.h} camera frame. Vision needs an LLM key to describe it.`;
    }
    if (/\b(devices?|status)\b/.test(t)) {
      const r = await this.runTool("list_devices", {});
      return `Bridge ${r.bridge ? "connected" : "offline"}; devices: ${r.devices.map((d: any) => `${d.id} (${d.status})`).join(", ") || "none"}.`;
    }
    if (t.startsWith("remember")) {
      await this.runTool("remember", { text: text.includes(" ") ? text.slice(text.indexOf(" ") + 1) : text });
      return "Got it, I'll remember that.";
    }
    if (/\b(recall|what do you remember|memory|memories)\b/.test(t)) {
      const ms = (await this.runTool("recall", { query: text })).memories ?? [];
      return ms.length ? "I remember: " + ms.slice(0, 3).map((m: any) => m.text.slice(0, 120)).join(" | ") : "I don't remember anything relevant yet.";
    }
    if (/\b(simulate|sim)\b/.test(t) && !sim.targetRgb(t)) {
      const e = await this.latestExp();
      if (!e) return "No experiment yet. Tell me a goal, like 'make orange'.";
      return simSentence(await this.runTool("simulate", { experiment_id: e.id }));
    }
    if (/\b(run|execute|do it)\b/.test(t) && !sim.targetRgb(t)) {
      const e = await this.latestExp();
      if (!e) return "No experiment to run yet.";
      if (e.status !== "simulated") await this.runTool("simulate", { experiment_id: e.id });
      const r = await this.runTool("run_variant", { experiment_id: e.id });
      if (r.error) return `Can't run: ${r.error}`;
      const run = await c.store.getRun(r.run_id);
      return run.status === "awaiting_approval" ? `Run ${r.run_id} ready for variant ${run.variant_id}. Say approve or press approve to start.` : `Run ${r.run_id} started for variant ${run.variant_id}.`;
    }
    if (sim.targetRgb(t) || /\b(make|mix|experiment)\b/.test(t)) {
      const m = /(\d+)\s*variants?/.exec(t);
      const mem = await c.store.recall(text, 2);
      const r = await this.runTool("design_experiment", { goal: text, n_variants: m ? Number(m[1]) : 12 });
      if (r.error) return `I couldn't design that: ${r.error}`;
      const s = await this.runTool("simulate", { experiment_id: r.experiment_id });
      return simSentence(s) + (mem.length ? ` I recalled ${mem.length} related memories.` : "") + " Say 'run best' to run it for real.";
    }
    return "I can: make a color (e.g. 'make orange'), simulate, run best, approve, abort, home arms, look, status, remember ..., recall ... (" +
      (llmAvailable(c.env) ? "LLM call failed, so I'm using simple commands this turn" : "LLM key not configured, using simple commands") + ").";
  }
}

function summary(e: any, top = 5) {
  const vs = (e.variants as sim.Variant[]).filter((v) => v.sim).sort((a, b) => b.sim!.score - a.sim!.score);
  return {
    experiment_id: e.id, goal: e.goal, status: e.status, n_variants: e.variants.length, sim_summary: e.sim_summary, best_variant_id: e.best_variant_id,
    top: vs.slice(0, top).map((v) => ({ variant_id: v.id, params: v.params, score: v.sim!.score, ok: v.sim!.ok, predicted_rgb: v.sim!.predicted_rgb, duration_s: v.sim!.duration_s, errors: v.sim!.errors.slice(0, 2) })),
  };
}

function simSentence(r: any): string {
  if (r.error) return `Simulation failed: ${r.error}`;
  const ss = r.sim_summary ?? {};
  const b = r.top.find((x: any) => x.ok);
  if (!b) return "No variant passed the safety checks.";
  return `Simulated ${ss.n} variants in ${ss.sim_ms} ms, ${ss.passing} passed checks. Best is ${b.variant_id} with ratios ${JSON.stringify(b.params.ratios)} of ${b.params.dyes.join("/")}, predicted color ${JSON.stringify(b.predicted_rgb)}, score ${b.score.toFixed(2)}.`;
}

