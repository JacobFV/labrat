// End-to-end through the Worker and the Lab Durable Object: fake provider sockets answer commands (cnc, arms and the
// servo hub's firmware lines for the pipette / probe / stand drivers), a UI socket drives a full experiment.
import { env, evictDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { api, connect, fakeProvider, hubLines } from "./helpers";
import { STAND_DEFAULTS as SD } from "../src/devices/stand";
import { PROBE_DEFAULTS as PD } from "../src/devices/probe";

describe("e2e", () => {
  it("chat -> design -> simulate -> approve -> run -> observe -> learn", async () => {
    await api("/api/cal/pipette", { settle_ms: 0 }); // keep the fake run quick
    await api("/api/cal/stand", { settle_ms: 0 });
    const { p, cmds } = await fakeProvider();
    const ui = await connect("/ws/ui");
    const devs = await ui.next((m) => m.t === "devices" && m.bridge && m.devices.some((d: any) => d.id === "pipette" && d.status === "mock"));
    expect(devs.devices.map((d: any) => d.id)).toEqual(expect.arrayContaining(["cnc", "hub", "pipette", "probe", "stand"]));

    ui.send({ t: "chat", text: "make orange" });
    const exp = (await ui.next((m) => m.t === "experiment" && m.experiment.status === "simulated")).experiment;
    expect(exp.best_variant_id).toBeTruthy();
    expect(exp.sim_summary.passing).toBeGreaterThan(0);
    expect((await ui.next((m) => m.t === "sim")).frames.length).toBeGreaterThan(0);
    await ui.next((m) => m.t === "chat" && m.role === "agent" && m.text.includes("Simulated"));

    const mark = ui.inbox.length;
    ui.send({ t: "chat", text: "run best" });
    const run = (await ui.next((m) => m.t === "run" && m.run.status === "awaiting_approval", 30000, mark)).run;
    ui.send({ t: "approve", run_id: run.id });
    const done = (await ui.next((m) => m.t === "run" && m.run.id === run.id && ["done", "failed", "aborted"].includes(m.run.status), 60000)).run;
    expect(done.error ?? null).toBeNull();
    expect(done.status).toBe("done");

    const actions = new Set(cmds.map((c) => `${c.device}.${c.action}`));
    expect([...actions]).toEqual(expect.arrayContaining(["cnc.move", "hub.line"]));
    const lines = hubLines(cmds);
    expect(lines.some((l) => l.cmd === "pulse" && l.ch === 0 && l.until === "syr_home")).toBe(true); // pipette homed
    expect(lines.some((l) => l.cmd === "pulse" && l.ch === 0 && l.us > 1500)).toBe(true);             // aspirate
    expect(lines.some((l) => l.cmd === "servo" && l.ch === 2) && lines.some((l) => l.cmd === "servo" && l.ch === 3)).toBe(true); // stand look
    // the probe arm is retracted before the first move below safe Z
    const firstLow = cmds.findIndex((c) => c.device === "cnc" && c.action === "move" && c.args.z < 0);
    const retract = cmds.findIndex((c) => c.device === "hub" && c.args.msg?.cmd === "servo" && c.args.msg.ch === 1);
    expect(retract).toBeGreaterThanOrEqual(0);
    expect(retract).toBeLessThan(firstLow);

    expect((await api("/api/experiments")).data.length).toBeGreaterThan(0);
    expect((await api("/healthz")).data.ok).toBe(true);
    expect((await api("/api/memory/search?q=orange")).data.results.length).toBeGreaterThan(0);
    const ob = await api(`/api/runs/${done.id}/observe`, { rating: 4, rgb: [235, 120, 20] });
    expect(ob.status, JSON.stringify(ob.data)).toBe(200);
    expect(ob.data.version.v).toBeGreaterThan(0);
    const lr = (await api("/api/learning")).data;
    expect(lr.versions.length).toBeGreaterThan(1);
    const st = (await api("/api/state")).data;
    expect(st.runs[0].observed_rgb).toEqual([235, 120, 20]);
    expect((await api("/api/events?kind=rsi.update")).data.length).toBe(1);
    ui.close();
    p.close();
  });

  it("rejects a bad token", async () => {
    const ui = await connect("/ws/ui", "nope");
    await new Promise((r) => setTimeout(r, 100));
    expect(ui.closed?.code).toBe(4401);
    expect((await api("/api/state", undefined, "nope")).status).toBe(401);
  });

  it("refuses cnc.probe unless the probe arm is deployed, and retracts it afterwards", async () => {
    const { p, cmds } = await fakeProvider();
    const r = await api("/api/device", { device: "cnc", action: "probe", args: { dz: -20 } });
    expect(r.status).toBe(400);
    expect(r.data.error).toContain("not deployed");
    expect((await api("/api/device", { device: "probe", action: "deploy" })).data.data.deployed).toBe(true);
    const ok = await api("/api/device", { device: "cnc", action: "probe", args: { dz: -20 } });
    expect(ok.data.data.hit).toBe(true);
    const st = (await api("/api/state")).data.devices.find((d: any) => d.id === "probe").state;
    expect(st.deployed).toBe(false);
    const servo1 = hubLines(cmds).filter((l) => l.cmd === "servo" && l.ch === 1).map((l) => l.deg);
    expect(servo1).toEqual([PD.deployed_deg, PD.retracted_deg]); // deploy, then the automatic retract
    p.close();
  });

  it("routes to the most recent provider and fails its pending commands when it disconnects", async () => {
    const a = await fakeProvider();
    const b = await fakeProvider("/ws/phone", [{ id: "cnc", kind: "grbl", status: "ok", info: {} }], false); // never answers
    await b.p.next((m) => m.t === "face");
    await new Promise((r) => setTimeout(r, 50));
    const pending = api("/api/device", { device: "cnc", action: "status" });
    await new Promise((r) => setTimeout(r, 100));
    expect(b.cmds.length).toBe(1);
    b.p.close();
    const r = await pending;
    expect(r.status).toBe(400);
    expect(r.data.error).toContain("disconnected");
    const again = await api("/api/device", { device: "cnc", action: "status" }); // falls back to the bridge
    expect(again.status).toBe(200);
    expect(a.cmds.some((c) => c.action === "status")).toBe(true);
    a.p.close();
  });

  it("drives the stand and the pipette through hub lines", async () => {
    await api("/api/cal/pipette", { settle_ms: 0 });
    await api("/api/cal/stand", { settle_ms: 0 });
    const { p, cmds } = await fakeProvider();
    const look = await api("/api/device", { device: "stand", action: "look", args: { at: "deck" } });
    expect(look.data.data).toMatchObject({ pan: 0, tilt: 0, at: "deck" });
    const servo = hubLines(cmds).filter((l) => l.cmd === "servo");
    expect(servo.map((l) => [l.ch, l.deg])).toEqual([[2, SD.pan_center_deg], [3, SD.tilt_center_deg]]);
    const op = (await api("/api/device", { device: "stand", action: "look", args: { at: "operator" } })).data.data;
    expect(Math.abs(op.pan)).toBeLessThan(5); // the operator is straight ahead; the camera point sits slightly off-centre
    expect(op.tilt).toBeGreaterThan(0);
    // aspirating before priming is refused; prime homes first, then aspirate moves the plunger up
    const bad = await api("/api/device", { device: "pipette", action: "aspirate", args: { ul: 50 } });
    expect(bad.status).toBe(400);
    await api("/api/device", { device: "pipette", action: "prime" });
    const asp = (await api("/api/device", { device: "pipette", action: "aspirate", args: { ul: 50 } })).data.data;
    expect(asp.vol_ul).toBe(50);
    expect(asp.homed).toBe(true);
    expect(asp.pos_mm).toBeCloseTo(2 + 50 / ((Math.PI / 4) * 17.8 ** 2), 3);
    expect(hubLines(cmds).filter((l) => l.cmd === "cfg").map((l) => l.ch)).toEqual(expect.arrayContaining([0, 2, 3]));
    p.close();
  });

  it("aborts a running run: in-flight commands fail, devices get stop", async () => {
    const { p, cmds } = await fakeProvider("/ws/bridge", undefined, (m) => !(m.device === "cnc" && m.action === "move")); // moves hang
    const ui = await connect("/ws/ui");
    ui.send({ t: "chat", text: "make purple" });
    const exp = (await ui.next((m) => m.t === "experiment" && m.experiment.status === "simulated")).experiment;
    const run = (await api(`/api/experiments/${exp.id}/run`, { auto: true })).data;
    expect(run.status).toBe("running");
    for (let i = 0; i < 100 && !cmds.some((c) => c.action === "move"); i++) await new Promise((r) => setTimeout(r, 20));
    expect((await api("/api/abort", {})).data.ok).toBe(true);
    const end = (await ui.next((m) => m.t === "run" && m.run.id === run.id && ["done", "failed", "aborted"].includes(m.run.status), 10000)).run;
    expect(end.status).toBe("aborted");
    expect(cmds.some((c) => c.device === "cnc" && c.action === "stop")).toBe(true);
    expect(hubLines(cmds).some((l) => l.cmd === "stop")).toBe(true);
    ui.close();
    p.close();
  });

  it("keeps providers across hibernation (attachments)", async () => {
    const { p, cmds } = await fakeProvider();
    await new Promise((r) => setTimeout(r, 50));
    await evictDurableObject(env.LAB.get(env.LAB.idFromName("lab")));
    const st = (await api("/api/state")).data;
    expect(st.bridge).toBe(true);
    expect(st.devices.find((d: any) => d.id === "pipette").status).toBe("mock");
    const r = await api("/api/device", { device: "cnc", action: "status" });
    expect(r.status, JSON.stringify(r.data)).toBe(200);
    expect(cmds.some((c) => c.action === "status")).toBe(true);
    p.close();
  });
});
