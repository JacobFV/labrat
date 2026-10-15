// D1 persistence: experiments, results, notes (full-text search), model versions, events, config.
export const now = () => Date.now() / 1000;
export const hex = (n: number) => [...crypto.getRandomValues(new Uint8Array(Math.ceil(n / 2)))].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, n);
const J = (v: unknown) => JSON.stringify(v ?? null);
const P = <T = any>(s: unknown, d: T = null as T): T => (typeof s === "string" ? JSON.parse(s) : d);

export interface Result { id: string; ts: number; experiment_id: string; well: string; rgb: number[] | null; rating: number | null; note: string | null; source: string; comp: Record<string, number>; predicted_rgb: number[] | null }
export interface Note { id: string; ts: number; text: string; experiment_id: string | null; source: string; score?: number }

export class Store {
  constructor(private db: D1Database) {}

  // ---- experiments (JSON documents) ----
  async putExperiment(e: any): Promise<any> {
    e.ts = now();
    e.created ??= e.ts;
    await this.db.prepare("INSERT OR REPLACE INTO experiments (id, title, status, ts, created, data) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(e.id, e.title, e.status, e.ts, e.created, J(e)).run();
    return e;
  }
  async getExperiment(id: string): Promise<any | null> {
    return P(await this.db.prepare("SELECT data FROM experiments WHERE id = ?").bind(id).first("data"));
  }
  async listExperiments(limit = 100, status?: string[]): Promise<any[]> {
    const where = status?.length ? ` WHERE status IN (${status.map(() => "?").join(",")})` : "";
    const r = await this.db.prepare(`SELECT data FROM experiments${where} ORDER BY created DESC LIMIT ?`).bind(...(status ?? []), limit).all<{ data: string }>();
    return r.results.map((x) => P(x.data));
  }

  // ---- results ----
  async addResult(o: Omit<Result, "id" | "ts"> & { ts?: number }): Promise<Result> {
    const r: Result = { id: hex(10), ts: o.ts ?? now(), ...o } as Result;
    await this.db.prepare("INSERT INTO results (id, ts, experiment_id, well, rgb, rating, note, source, comp, predicted_rgb) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .bind(r.id, r.ts, r.experiment_id, r.well, r.rgb ? J(r.rgb) : null, r.rating ?? null, r.note ?? null, r.source, J(r.comp), r.predicted_rgb ? J(r.predicted_rgb) : null).run();
    return r;
  }
  /** Every reading, oldest first (optionally of one experiment). */
  async results(experimentId?: string): Promise<Result[]> {
    const r = await this.db.prepare(`SELECT * FROM results${experimentId ? " WHERE experiment_id = ?" : ""} ORDER BY ts`).bind(...(experimentId ? [experimentId] : [])).all<any>();
    return r.results.map((o) => ({ ...o, rgb: P(o.rgb), comp: P(o.comp, {}), predicted_rgb: P(o.predicted_rgb) }));
  }

  // ---- notes ----
  async addNote(text: string, o: { experiment_id?: string | null; source?: string } = {}): Promise<Note> {
    const n: Note = { id: hex(10), ts: now(), text, experiment_id: o.experiment_id ?? null, source: o.source ?? "operator" };
    await this.db.prepare("INSERT INTO notes (id, ts, text, experiment_id, source) VALUES (?, ?, ?, ?, ?)").bind(n.id, n.ts, n.text, n.experiment_id, n.source).run();
    return n;
  }
  /** Newest notes, or a bm25-ranked full-text search (any word matches) when `query` is given. */
  async notes(query = "", k = 20, experimentId?: string): Promise<Note[]> {
    const words = (query.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []).slice(0, 32);
    const ex = experimentId ? " AND n.experiment_id = ?" : "";
    const exArgs = experimentId ? [experimentId] : [];
    if (!words.length) {
      const r = await this.db.prepare(`SELECT * FROM notes n WHERE 1=1${ex} ORDER BY ts DESC LIMIT ?`).bind(...exArgs, k).all<Note>();
      return r.results;
    }
    const sql = `SELECT n.*, -bm25(notes_fts) AS score FROM notes_fts JOIN notes n ON n.rowid = notes_fts.rowid
                 WHERE notes_fts MATCH ?${ex} ORDER BY bm25(notes_fts) LIMIT ?`;
    const r = await this.db.prepare(sql).bind(words.map((w) => `"${w}"`).join(" OR "), ...exArgs, k).all<any>();
    return r.results.map((n) => ({ ...n, score: Math.round(n.score * 1e4) / 1e4 }));
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

  // ---- colour-model versions ----
  async modelVersions(): Promise<any[]> {
    const r = await this.db.prepare("SELECT * FROM model ORDER BY v").all<any>();
    return r.results.map((d) => ({ ...d, params: P(d.params), accepted: !!d.accepted }));
  }
  async putModelVersion(d: any): Promise<any> {
    d.ts ??= now();
    await this.db.prepare("INSERT INTO model (v, ts, params, error_before, error_after, n_results, accepted, experiment_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
      .bind(d.v, d.ts, J(d.params), d.error_before ?? null, d.error_after ?? null, d.n_results ?? 0, d.accepted ? 1 : 0, d.experiment_id ?? null).run();
    return d;
  }

  // ---- config ----
  async getConfig<T = any>(key: string): Promise<T | null> {
    return P(await this.db.prepare("SELECT value FROM config WHERE key = ?").bind(key).first("value"));
  }
  async setConfig(key: string, value: unknown): Promise<void> {
    await this.db.prepare("INSERT OR REPLACE INTO config (key, value, ts) VALUES (?, ?, ?)").bind(key, J(value), now()).run();
  }
}
