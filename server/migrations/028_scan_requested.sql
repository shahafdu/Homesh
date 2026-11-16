-- A folder on the PC changed and the watcher said so (docs/design/06, task L1).
-- Only a request: the upkeep loop decides when to scan.
ALTER TABLE sources ADD COLUMN IF NOT EXISTS scan_requested_at TIMESTAMPTZ;

-- Small facts the upkeep loops need to remember between runs.
CREATE TABLE IF NOT EXISTS upkeep_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
