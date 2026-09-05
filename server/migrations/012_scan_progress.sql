-- What a scan is doing, while it does it.
--
-- Scanning 9,500 Drive files takes minutes. Until now the only signal was the
-- count changing if you happened to reload, so pressing Rescan looked exactly
-- like pressing nothing — and a folder that had never been scanned looked
-- exactly like a folder that was empty.

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS scan_state      TEXT,        -- running | done | failed
    ADD COLUMN IF NOT EXISTS scan_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS scan_ended_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS scan_seen       INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS scan_added      INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS scan_error      TEXT;

-- A source that already holds files has plainly been scanned before, even though
-- nothing recorded it at the time.
UPDATE sources s SET scan_state = 'done'
WHERE scan_state IS NULL
  AND EXISTS (SELECT 1 FROM replicas r WHERE r.source_id = s.id);
