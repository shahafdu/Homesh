-- What a person asked. Theirs: only they read it, and they can clear it.
CREATE TABLE IF NOT EXISTS ai_questions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation UUID NOT NULL,
    asked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    text         TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 1000),
    spoken       BOOLEAN NOT NULL DEFAULT FALSE,
    state        TEXT NOT NULL DEFAULT 'thinking'
                 CHECK (state IN ('thinking','done','failed','refused')),
    steps        JSONB NOT NULL DEFAULT '[]'::jsonb,   -- progress lines
    answer       TEXT,
    results      JSONB NOT NULL DEFAULT '[]'::jsonb,   -- item ids shown, in order
    finished_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ai_questions_mine ON ai_questions (user_id, asked_at DESC);
CREATE INDEX IF NOT EXISTS ai_questions_text_trgm
    ON ai_questions USING gin (text gin_trgm_ops);

-- What the assistant changed, or was stopped from changing. The household's:
-- administrators read it, and clearing a question does not erase it.
CREATE TABLE IF NOT EXISTS ai_actions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    question_id UUID REFERENCES ai_questions(id) ON DELETE SET NULL,
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    tool        TEXT NOT NULL,
    -- The exact request that was, or would be, made.
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    body        JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome     TEXT NOT NULL
                CHECK (outcome IN ('done','failed','refused','pending','cancelled','expired')),
    status      INT,
    -- One sentence a person can read: "Created playlist X, 23 tracks".
    summary     TEXT NOT NULL,
    -- What to open to see the result: ("playlist", id) or ("room", id).
    target_kind TEXT,
    target_id   TEXT,
    expires_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ai_actions_at ON ai_actions (at DESC);
CREATE INDEX IF NOT EXISTS ai_actions_summary_trgm
    ON ai_actions USING gin (summary gin_trgm_ops);
-- Reading a person's history looks up each question's actions, and clearing a
-- question has to find the actions that pointed at it.
CREATE INDEX IF NOT EXISTS ai_actions_question ON ai_actions (question_id);
