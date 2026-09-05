-- Converting video a browser cannot decode.
--
-- MPEG-2 — .m2t from an HDV camcorder, .vob from a DVD — is not playable in any
-- current browser. There is no codec to enable and no container trick: the
-- decoder was never shipped. Five wedding tapes in this library are exactly that,
-- and until now they sat in a folder as files that could be downloaded and
-- nothing else.
--
-- A conversion is long — tens of minutes for an hour of tape on four efficiency
-- cores — so it is a tracked job rather than a request that hangs. The row
-- outlives a restart, which a background task would not.

CREATE TABLE IF NOT EXISTS transcodes (
    item_id      UUID PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    state        TEXT NOT NULL,               -- queued | running | done | failed
    progress     INTEGER NOT NULL DEFAULT 0,  -- per cent, best effort
    output_path  TEXT,
    error        TEXT,
    source_size  BIGINT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS transcodes_state_idx ON transcodes(state);
