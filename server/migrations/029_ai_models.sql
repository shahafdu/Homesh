-- What each provider offers, refreshed daily: public information plus this
-- server's own experience of each model. Holds no secret.
CREATE TABLE IF NOT EXISTS ai_models (
    id            TEXT PRIMARY KEY,          -- "<provider>:<model>"
    provider      TEXT NOT NULL,             -- openrouter | gemini
    model         TEXT NOT NULL,
    label         TEXT NOT NULL,
    free          BOOLEAN NOT NULL,
    tools         BOOLEAN NOT NULL,
    context       INT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    gone_at       TIMESTAMPTZ,
    cooling_until TIMESTAMPTZ,
    last_ok_at    TIMESTAMPTZ,
    last_error    TEXT,
    last_error_at TIMESTAMPTZ
);

ALTER TABLE ai_calls ADD COLUMN IF NOT EXISTS task TEXT;      -- chat|bulk|translate|test
ALTER TABLE ai_calls ADD COLUMN IF NOT EXISTS fallback BOOLEAN NOT NULL DEFAULT FALSE;

-- One row: everything an administrator sets on the AI page. In the database so
-- it travels with backups; holds no secret. The worker queue's migration
-- declares the same table; either may run first.
CREATE TABLE IF NOT EXISTS ai_settings (
    only_row   BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (only_row),
    data       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID REFERENCES users(id) ON DELETE SET NULL
);
INSERT INTO ai_settings (only_row) VALUES (TRUE) ON CONFLICT DO NOTHING;
