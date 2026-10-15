-- LabRat D1 schema. An experiment is one plate: its samples, steps, dry run, run log and status live in a JSON `data`
-- column next to the columns we filter and sort on. Measurements are rows in `results`, one per beaker reading.

CREATE TABLE experiments (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL,      -- draft | ready | awaiting_approval | running | done | failed | aborted
  ts REAL NOT NULL,          -- last write
  created REAL NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX experiments_created ON experiments (created DESC);
CREATE INDEX experiments_status ON experiments (status);

-- one measured beaker; the newest row per (experiment, well) is the current reading
CREATE TABLE results (
  id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  experiment_id TEXT NOT NULL,
  well TEXT NOT NULL,
  rgb TEXT,                  -- [r,g,b] JSON or NULL
  rating INTEGER,            -- 1..5, optional
  note TEXT,
  source TEXT NOT NULL,      -- operator | camera
  comp TEXT NOT NULL,        -- what went into the beaker, {stock: uL}
  predicted_rgb TEXT         -- the dry run's prediction at the time of the run
);
CREATE INDEX results_experiment ON results (experiment_id, well, ts);

-- lab notebook: free-text notes, full-text searchable, optionally tied to an experiment
CREATE TABLE notes (
  id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  text TEXT NOT NULL,
  experiment_id TEXT,
  source TEXT NOT NULL       -- operator | agent | auto
);
CREATE INDEX notes_ts ON notes (ts DESC);
CREATE VIRTUAL TABLE notes_fts USING fts5 (text, content = 'notes', content_rowid = 'rowid', tokenize = 'porter unicode61');
CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
  INSERT INTO notes_fts (rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
  INSERT INTO notes_fts (notes_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;

-- colour-model versions: refitted from results; the current one is the newest accepted row
CREATE TABLE model (
  v INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  params TEXT NOT NULL,
  error_before REAL,
  error_after REAL,
  n_results INTEGER NOT NULL DEFAULT 0,
  accepted INTEGER NOT NULL,
  experiment_id TEXT
);

-- audit log (chat, tool, experiment_*, deck_teach, calibrate, device_event, ...)
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX events_kind ON events (kind, ts DESC);

-- small JSON documents: deck_cal, camera_cal, cal:pipette, cal:probe, cal:stand
CREATE TABLE config (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  ts REAL NOT NULL
);
