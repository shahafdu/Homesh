-- Per-person access: which folders someone may see, and which rooms they may
-- play in.
--
-- Both are allow-lists, and **no rules means no restriction**. That default
-- matters: it preserves what existing accounts can do, and it keeps the common
-- case — the adults — free of configuration. Restriction is opt-in, applied to
-- the accounts that need it.

CREATE TABLE IF NOT EXISTS user_library_rules (
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- A virtual path prefix, e.g. '/local/library/Music' or '/drive/סלסה'.
    path_prefix TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, path_prefix)
);

CREATE TABLE IF NOT EXISTS user_zone_rules (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    zone_id    UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, zone_id)
);

CREATE INDEX IF NOT EXISTS user_library_rules_user_idx ON user_library_rules(user_id);
CREATE INDEX IF NOT EXISTS user_zone_rules_user_idx ON user_zone_rules(user_id);
