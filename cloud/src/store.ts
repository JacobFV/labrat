// D1 persistence: experiments, runs, memories (FTS5 recall), events, sim_params, observations, config.
export const MEMORY_TYPES = ["semantic", "episodic", "procedural", "taxonomic"] as const;
export type MemoryType = (typeof MEMORY_TYPES)[number];

export const now = () => Date.now() / 1000;
export const hex = (n: number) => [...crypto.getRandomValues(new Uint8Array(Math.ceil(n / 2)))].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, n);
const J = (v: unknown) => JSON.stringify(v ?? null);
const P = <T = any>(s: unknown, d: T = null as T): T => (typeof s === "string" ? JSON.parse(s) : d);

export interface Memory { id: string; type: MemoryType; text: string; tags: string[]; meta: any; source_run_id: string | null; confidence: number | null; ts: number; score?: number }

export class Store {
  constructor(private db: D1Database) {}

  // ---- experiments / runs (JSON documents) ----
  async putExperiment(e: any): Promise<any> {
    e.ts = now();
    e.created ??= e.ts;
    await this.db.prepare("INSERT OR REPLACE INTO experiments (id, goal, status, ts, created, data) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(e.id, e.goal, e.status, e.ts, e.created, J(e)).run();
    return e;
  }
  async getExperiment(id: string): Promise<any | null> {
    return P(await this.db.prepare("SELECT data FROM experiments WHERE id = ?").bind(id).first("data"));
  }
  async listExperiments(limit = 50): Promise<any[]> {
    const r = await this.db.prepare("SELECT data FROM experiments ORDER BY created DESC LIMIT ?").bind(limit).all<{ data: string }>();
    return r.results.map((x) => P(x.data));
  }
  async putRun(run: any): Promise<any> {
    run.ts = now();
    run.created ??= run.ts;
    await this.db.prepare("INSERT OR REPLACE INTO runs (id, experiment_id, status, ts, created, data) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(run.id, run.experiment_id, run.status, run.ts, run.created, J(run)).run();
    return run;
  }
  async getRun(id: string): Promise<any | null> {
    return P(await this.db.prepare("SELECT data FROM runs WHERE id = ?").bind(id).first("data"));
  }
  async listRuns(limit = 10): Promise<any[]> {
    const r = await this.db.prepare("SELECT data FROM runs ORDER BY created DESC LIMIT ?").bind(limit).all<{ data: string }>();
    return r.results.map((x) => P(x.data));
  }
  async pendingRun(): Promise<any | null> {
    return P(await this.db.prepare("SELECT data FROM runs WHERE status = 'awaiting_approval' ORDER BY created DESC LIMIT 1").first("data"));
  }

  // ---- memory ----
  async remember(text: string, o: { tags?: string[]; meta?: any; type?: string; source_run_id?: string | null; confidence?: number } = {}): Promise<Memory> {
    const type = (MEMORY_TYPES as readonly string[]).includes(o.type ?? "") ? (o.type as MemoryType) : "semantic";
    const m: Memory = { id: hex(10), type, text, tags: o.tags ?? [], meta: o.meta ?? {}, source_run_id: o.source_run_id ?? null,
      confidence: Math.round((o.confidence ?? 0.7) * 1000) / 1000, ts: now() };
    await this.db.prepare("INSERT INTO memories (id, type, text, tags, meta, source_run_id, confidence, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
      .bind(m.id, m.type, m.text, J(m.tags), J(m.meta), m.source_run_id, m.confidence, m.ts).run();
    return m;
  }

  /** bm25-ranked full-text recall (any query word matches); score = -bm25, higher is better. */
  async recall(query: string, k = 5, type?: string | null): Promise<Memory[]> {
    const words = (query.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []).slice(0, 32);
    if (!words.length) return [];
    const match = words.map((w) => `"${w}"`).join(" OR ");
    const sql = `SELECT m.*, -bm25(memories_fts) AS score FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid
                 WHERE memories_fts MATCH ?${type ? " AND m.type = ?" : ""} ORDER BY bm25(memories_fts) LIMIT ?`;
    const r = await this.db.prepare(sql).bind(...(type ? [match, type, k] : [match, k])).all<any>();
    return r.results.map((m) => ({ ...m, tags: P(m.tags, []), meta: P(m.meta, {}), score: Math.round(m.score * 1e4) / 1e4 }));
  }
  async memoryTexts(type: string): Promise<Set<string>> {
    const r = await this.db.prepare("SELECT text FROM memories WHERE type = ?").bind(type).all<{ text: string }>();
    return new Set(r.results.map((x) => x.text));
  }

  // ---- events (audit log) ----
  async event(kind: string, data: Record<string, unknown> = {}): Promise<void> {
    await this.db.prepare("INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)").bind(now(), kind, J(data)).run();
  }
  async events(limit = 100, kind?: string): Promise<any[]> {
    const r = await this.db.prepare(`SELECT * FROM events${kind ? " WHERE kind = ?" : ""} ORDER BY id DESC LIMIT ?`)
      .bind(...(kind ? [kind, limit] : [limit])).all<any>();
    return r.results.map((e) => ({ ...e, data: P(e.data, {}) }));
  }
  async trimEvents(keep = 5000): Promise<void> {
    await this.db.prepare("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?").bind(keep).run();
  }

  // ---- sim_params ----
  async simVersions(): Promise<any[]> {
    const r = await this.db.prepare("SELECT * FROM sim_params ORDER BY v").all<any>();
    return r.results.map((d) => ({ ...d, params: P(d.params), proposed_params: P(d.proposed_params), accepted: !!d.accepted }));
  }
  async putSimVersion(d: any): Promise<any> {
    d.ts ??= now();
    await this.db.prepare(`INSERT INTO sim_params (v, ts, params, proposed_params, error_before, error_after, n_runs, accepted, trigger_run_id, parent_v, note)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      .bind(d.v, d.ts, J(d.params), d.proposed_params ? J(d.proposed_params) : null, d.error_before ?? null, d.error_after ?? null,
        d.n_runs ?? 0, d.accepted ? 1 : 0, d.trigger_run_id ?? null, d.parent_v ?? null, d.note ?? null).run();
    return d;
  }

  // ---- observations ----
  async putObservation(o: any): Promise<any> {
    o.id ??= hex(10);
    o.ts ??= now();
    await this.db.prepare(`INSERT INTO observations (id, ts, run_id, experiment_id, variant_id, well, rating, rgb, comp, predicted_rgb, vol_ul, cmd_vol_ul, source)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      .bind(o.id, o.ts, o.run_id, o.experiment_id, o.variant_id, o.well, o.rating ?? null, o.rgb ? J(o.rgb) : null, J(o.comp),
        o.predicted_rgb ? J(o.predicted_rgb) : null, o.vol_ul ?? null, o.cmd_vol_ul ?? null, o.source).run();
    return o;
  }
  async observations(): Promise<any[]> {
    const r = await this.db.prepare("SELECT * FROM observations ORDER BY ts").all<any>();
    return r.results.map((o) => ({ ...o, rgb: P(o.rgb), comp: P(o.comp, {}), predicted_rgb: P(o.predicted_rgb) }));
  }

  // ---- config ----
  async getConfig<T = any>(key: string): Promise<T | null> {
    return P(await this.db.prepare("SELECT value FROM config WHERE key = ?").bind(key).first("value"));
  }
  async setConfig(key: string, value: unknown): Promise<void> {
    await this.db.prepare("INSERT OR REPLACE INTO config (key, value, ts) VALUES (?, ?, ?)").bind(key, J(value), now()).run();
  }
}
