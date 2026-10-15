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
${INVENTORY}
An experiment is one plate: a list of samples, each the µL of each stock (water, red, yellow, blue) going into one mixing
beaker, e.g. [{"red":60,"yellow":140},{"red":100,"yellow":100}] (wells are assigned A1, B1, ... unless given). Creating one
dry-runs it on the digital twin, which checks it and predicts each beaker's colour. To answer a question by experiment:
search_notes for past results, new_experiment with a sensible spread of samples (replicates are just repeated samples),
read the prediction, then run_experiment (a human approves it). After a run the camera measures every beaker and the
colour model refits itself. Never move hardware into unsafe positions. Keep spoken replies short (1-2 sentences) since
they are read aloud on the phone. Use say() only for interim spoken updates; your final text reply is spoken automatically.`;

const obj = (properties: Record<string, unknown>, required: string[] = []) => ({ type: "object", properties, required });
const SAMPLE = { type: "object", description: "µL of each stock into one beaker; well optional", properties: { well: { type: "string" }, label: { type: "string" }, water: { type: "number" }, red: { type: "number" }, yellow: { type: "number" }, blue: { type: "number" } } };
export const TOOLS = [
  { name: "list_devices", description: "List connected hardware devices and their live state.", input_schema: obj({}) },
  { name: "look", description: "Get a fresh camera frame from the phone on the stand.", input_schema: obj({}) },
  { name: "search_notes", description: "Search the lab notebook (past results are written there automatically).", input_schema: obj({ query: { type: "string" } }, ["query"]) },
  { name: "add_note", description: "Write a note in the lab notebook, optionally about an experiment.", input_schema: obj({ text: { type: "string" }, experiment_id: { type: "string" } }, ["text"]) },
  { name: "list_experiments", description: "Recent experiments with their status.", input_schema: obj({}) },
  { name: "get_experiment", description: "One experiment: every beaker's recipe, predicted and measured colour, the dry-run check and the run log.", input_schema: obj({ id: { type: "string" } }, ["id"]) },
  { name: "new_experiment", description: "Create an experiment (one plate) and dry-run it. Give samples; or omit them with a target to get a ratio sweep of n samples.",
    input_schema: obj({ title: { type: "string" }, target: { type: "string", description: "colour name or #rrggbb (optional)" }, samples: { type: "array", items: SAMPLE }, n: { type: "integer" } }, ["title"]) },
  { name: "run_experiment", description: "Run an experiment whose dry run passed on the real hardware (asks a human to approve).", input_schema: obj({ id: { type: "string" } }, ["id"]) },
  { name: "move_arm", description: "Move an SO-101 gripper arm (so_a left of the CNC, so_b right) to a named pose (home, ready, rest) or joint angles (deg).", input_schema: obj({ device: { type: "string" }, pose: { type: "string" }, joints: { type: "object" }, ms: { type: "integer" } }, ["device"]) },
  { name: "cnc_move", description: "Move the CNC syringe head (absolute mm) or home it (home=true).", input_schema: obj({ x: { type: "number" }, y: { type: "number" }, z: { type: "number" }, f: { type: "number" }, home: { type: "boolean" } }) },
  { name: "tool", description: "Head tool rail: put a tool over a deck target (goto), take a USB-microscope picture (snap: optionally over a target first), or probe a beaker's liquid surface with the capacitive probe (probe -> surface height + estimated volume). Tools: pipette (the syringe tip), microscope, probe.",
    input_schema: obj({ action: { type: "string", enum: ["goto", "snap", "probe", "plan"] }, tool: { type: "string", enum: tooling.TOOL_NAMES }, target: { type: "string", description: "beaker A1-F7, a stock (water/red/yellow/blue), waste" }, x: { type: "number" }, y: { type: "number" }, z: { type: "number" } }, ["action"]) },
  { name: "pipette", description: "Syringe pipette action: home (drive plunger to its bottom stop), prime (plunger to the ready air cushion; do it above the liquid), aspirate/dispense (ul), blowout, move (plunger height mm, 0..stroke).",
    input_schema: obj({ action: { type: "string", enum: ["home", "prime", "aspirate", "dispense", "blowout", "move"] }, ul: { type: "number" }, mm: { type: "number" } }, ["action"]) },
  { name: "stand", description: "Aim the phone stand: look at deck, operator, home or a beaker (A1-F7, red/yellow/blue/water), or aim pan/tilt degrees (0,0 = deck centre).",
    input_schema: obj({ at: { type: "string" }, pan: { type: "number" }, tilt: { type: "number" } }) },
  { name: "say", description: "Speak a short sentence through the phone.", input_schema: obj({ text: { type: "string" } }, ["text"]) },
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
      case "search_notes":
        return { notes: (await c.store.notes(String(a.query ?? ""), 8)).map(({ text, experiment_id, ts }) => ({ text, experiment_id, ts })) };
      case "add_note":
        return { note_id: (await c.store.addNote(String(a.text), { experiment_id: a.experiment_id ?? null, source: "agent" })).id };
      case "list_experiments":
        return (await c.store.listExperiments(10)).map((e) => ({ id: e.id, title: e.title, status: e.status, beakers: Object.keys(e.predicted ?? {}).length }));
      case "get_experiment":
        return brief(await lab.view(c, await lab.must(c, a.id)));
      case "new_experiment": {
        const samples = a.samples?.length ? a.samples : sim.sweep(String(a.target ?? a.title), Number(a.n) || 12);
        return brief(await lab.view(c, await lab.create(c, { title: a.title, target: a.target ?? null, samples }, "agent")));
      }
      case "run_experiment": {
        const e = await lab.requestRun(c, a.id);
        return { id: e.id, status: e.status, note: e.status === "awaiting_approval" ? "waiting for a human to approve (console button, or say 'approve')" : "" };
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
    }
    throw new Error(`unknown tool ${name}`);
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
  async rules(text: string): Promise<string> {
    const { c } = this;
    const t = text.toLowerCase().trim();
    if (/\b(approve|go ahead|yes,? (run|go|do it)|confirm)\b/.test(t)) {
      const e = await lab.approve(c).catch(() => null);
      return e ? `Approved. Running "${e.title}" now.` : "Nothing is waiting for approval.";
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
    if (t.startsWith("note") || t.startsWith("remember")) {
      await this.runTool("add_note", { text: text.includes(" ") ? text.slice(text.indexOf(" ") + 1) : text });
      return "Noted.";
    }
    if (/\b(recall|notes?|what do you remember)\b/.test(t)) {
      const ns = (await this.runTool("search_notes", { query: text })).notes ?? [];
      return ns.length ? "From the notebook: " + ns.slice(0, 3).map((n: any) => n.text.slice(0, 140)).join(" | ") : "Nothing relevant in the notebook yet.";
    }
    if (/\b(run|execute|do it)\b/.test(t) && !sim.targetRgb(t)) {
      const e = (await c.store.listExperiments(1, ["ready"]))[0];
      if (!e) return "No experiment is ready to run.";
      const r = await this.runTool("run_experiment", { id: e.id });
      if (r.error) return `Can't run: ${r.error}`;
      return r.status === "awaiting_approval" ? `"${e.title}" is ready. Say approve or press Approve to start.` : `"${e.title}" started.`;
    }
    if (sim.targetRgb(t) || /\b(make|mix|experiment)\b/.test(t)) {
      const m = /(\d+)\s*(samples?|beakers?|variants?)/.exec(t);
      const tgt = sim.targetRgb(t)?.[0] ?? null;
      const r = await this.runTool("new_experiment", { title: text, target: tgt, samples: sim.sweep(text, m ? Number(m[1]) : 12) });
      if (r.error) return `I couldn't set that up: ${r.error}`;
      return expSentence(r) + " Say 'run' to run it for real.";
    }
    return "I can: make a colour (e.g. 'make orange'), run, approve, abort, home arms, look, status, note ..., recall ... (" +
      (llmAvailable(c.env) ? "LLM call failed, so I'm using simple commands this turn" : "LLM key not configured, using simple commands") + ").";
  }
}

/** What the agent needs to know about an experiment (no frames, no full step list). */
function brief(e: any) {
  return {
    id: e.id, title: e.title, status: e.status, target: e.target, closest_predicted: e.closest_predicted ?? null, closest_measured: e.closest_measured ?? null,
    check: { ok: e.check.ok, errors: e.check.errors.slice(0, 5), warnings: e.check.warnings.slice(0, 3), duration_s: e.check.duration_s },
    beakers: e.beakers, n_steps: e.steps.length, error: e.error ?? null, log_tail: (e.log ?? []).slice(-3),
  };
}

function expSentence(r: any): string {
  if (!r.check.ok) return `I set up ${r.beakers.length} beakers but the dry run found problems: ${r.check.errors.slice(0, 2).join("; ")}.`;
  const best = r.beakers.find((b: any) => b.well === r.closest_predicted);
  return `Dry-ran ${r.beakers.length} beakers (${Math.round(r.check.duration_s / 60)} min of robot time), all checks passed.` +
    (best ? ` Closest to ${r.target.name} should be ${best.well}: ${sim.STOCKS.filter((d) => best[d]).map((d) => `${best[d]} µL ${d}`).join(" + ")}.` : "");
}
