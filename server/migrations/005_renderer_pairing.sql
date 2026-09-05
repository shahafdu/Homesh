-- Pairing and credentials for renderers (TV apps).
--
-- A screen joins by showing a short code that you type on your phone. No password
-- is ever entered on a TV remote, and no address or MAC is configured anywhere —
-- the device dials out and identifies itself (ARCHITECTURE.md §5.7, §5.8).

-- Long-lived device credential. Stored as a hash, like session tokens: a database
-- dump must not yield anything that can drive a screen in someone's house.
ALTER TABLE renderers
    ADD COLUMN IF NOT EXISTS token_hash BYTEA,
    ADD COLUMN IF NOT EXISTS paired_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS paired_by  UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS renderers_token_idx
    ON renderers(token_hash) WHERE token_hash IS NOT NULL;

-- A pairing attempt in flight. Short-lived by design: the code is only six
-- characters, so it must stop being useful quickly.
CREATE TABLE IF NOT EXISTS pairing_codes (
    code         TEXT PRIMARY KEY,
    -- Identifies the physical device across re-pairings, so re-pairing a screen
    -- updates it rather than creating a duplicate.
    device_key   TEXT NOT NULL,
    device_name  TEXT,
    -- The device polls with this; it is not the code, so watching someone type
    -- the code on their phone does not let you collect the result.
    poll_hash    BYTEA NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    claimed_at   TIMESTAMPTZ,
    renderer_id  UUID REFERENCES renderers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS pairing_codes_expiry_idx ON pairing_codes(expires_at);

-- The device credential is minted when the phone claims the code, but has to be
-- collected by the screen on its next poll. It rests here in between, and is
-- deleted the moment it is handed over — so a replayed poll gets nothing, and a
-- credential is never left lying in the pairing row.
CREATE TABLE IF NOT EXISTS pairing_handoff (
    poll_hash    BYTEA PRIMARY KEY,
    device_token TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
