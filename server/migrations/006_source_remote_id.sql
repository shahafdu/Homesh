-- Remote sources are keyed by an opaque id rather than a path.
--
-- A Drive folder has an id that survives renaming and moving, which is the same
-- identity-not-location principle the rest of the system uses (ARCHITECTURE.md
-- §5.7). Not a secret — the credential is the key file, which never goes in the
-- database.

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS remote_id TEXT;

CREATE INDEX IF NOT EXISTS sources_remote_idx ON sources(remote_id) WHERE remote_id IS NOT NULL;
