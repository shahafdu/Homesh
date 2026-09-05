-- Where a file is, as the provider names it.
--
-- Drive addresses files by id, not by path, so playing one meant walking the
-- folder tree from the root to translate a path into an id — listing every
-- folder on the way, on every byte-range request. Measured at nearly seven
-- seconds for a track in a nested folder, which is why playing anything on a
-- phone felt broken.
--
-- The scanner already learns the id as it walks. Keeping it turns that walk into
-- a lookup.

ALTER TABLE replicas ADD COLUMN IF NOT EXISTS remote_id TEXT;

CREATE INDEX IF NOT EXISTS replicas_remote_idx
    ON replicas(source_id, remote_id) WHERE remote_id IS NOT NULL;
