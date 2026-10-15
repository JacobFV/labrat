-- LabRat D1 schema. Documents whose shape evolves (experiments with their variants, runs with their step log) live
-- in a JSON `data` column next to the columns we filter and sort on.

CREATE TABLE experiments (
  id TEXT PRIMARY KEY,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  ts REAL NOT NULL,          -- last write
  created REAL NOT NULL,
  data TEXT NOT NULL         -- full experiment JSON incl. variants (protocol, params, sim result without frames)
);
CREATE INDEX experiments_created ON experiments (created DESC);

CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL,
  status TEXT NOT NULL,      -- awaiting_approval -> running -> done | failed | aborted
  ts REAL NOT NULL,
  created REAL NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX runs_created ON runs (created DESC);
CREATE INDEX runs_status ON runs (status);

-- typed long-term memory; recall = FTS5 bm25 over text + tags with an optional type filter
CREATE TABLE memories (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('semantic', 'episodic', 'procedural', 'taxonomic')),
  text TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '[]',
  meta TEXT NOT NULL DEFAULT '{}',
  source_run_id TEXT,
  confidence REAL,
  ts REAL NOT NULL
);
CREATE INDEX memories_type ON memories (type, ts DESC);
CREATE VIRTUAL TABLE memories_fts USING fts5 (text, tags, content = 'memories', content_rowid = 'rowid', tokenize = 'porter unicode61');
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts (rowid, text, tags) VALUES (new.rowid, new.text, new.tags);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts (memories_fts, rowid, text, tags) VALUES ('delete', old.rowid, old.text, old.tags);
END;
CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts (memories_fts, rowid, text, tags) VALUES ('delete', old.rowid, old.text, old.tags);
  INSERT INTO memories_fts (rowid, text, tags) VALUES (new.rowid, new.text, new.tags);
END;

-- audit log (chat, tool, run_*, rsi.update, vision.observe, deck_teach, provider_connected, ...)
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX events_kind ON events (kind, ts DESC);

-- versioned simulator parameters; the current one is the newest accepted row
CREATE TABLE sim_params (
  v INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  params TEXT NOT NULL,
  proposed_params TEXT,
  error_before REAL,
  error_after REAL,
  n_runs INTEGER NOT NULL DEFAULT 0,
  accepted INTEGER NOT NULL,
  trigger_run_id TEXT,
  parent_v INTEGER,
  note TEXT
);

CREATE TABLE observations (
  id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  run_id TEXT NOT NULL,
  experiment_id TEXT,
  variant_id TEXT,
  well TEXT,
  rating INTEGER,
  rgb TEXT,                  -- [r,g,b] JSON or NULL
  comp TEXT NOT NULL,        -- {dye: uL} JSON
  predicted_rgb TEXT,
  vol_ul REAL,
  cmd_vol_ul REAL,
  source TEXT NOT NULL       -- operator | vision
);
CREATE INDEX observations_run ON observations (run_id);

-- small JSON documents: deck_cal, camera_cal, cal:pipette, cal:probe, cal:stand
CREATE TABLE config (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  ts REAL NOT NULL
);
