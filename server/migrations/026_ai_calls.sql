-- Every question put to a model, and what it cost.
--
-- One table, written for three jobs at once:
--
-- 1. The history that is readable in the app. Software that calls a paid API on
--    a schedule must be answerable for what it did; "check the logs on the PC"
--    is not an answer.
-- 2. The spending cap. The platform refuses the spend rather than the code
--    promising not to, and refusing needs to know what has been spent -- so the
--    month's cost is summed from here before every call.
-- 3. The record of refusals as well as calls. A refusal is the interesting
--    event: it says the cap worked, or that somebody asked for something they
--    are not allowed.
--
-- Costs are in millionths of a unit of currency: a request to a free model is 0
-- and a paid one is a fraction of a cent, so nothing here is representable in
-- whole cents, and floating point has no business counting money.
CREATE TABLE IF NOT EXISTS ai_calls (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Who asked. NULL where the server asked on nobody's behalf -- a scheduled
    -- tagging pass -- which is deliberately visible as such.
    user_id       UUID REFERENCES users(id) ON DELETE SET NULL,
    -- What it was for, in the app's own words: "tag-track", "search", "ask".
    purpose       TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    outcome       TEXT NOT NULL,          -- ok | refused | error
    -- Why, when it was refused or failed. Never the question or the answer:
    -- this table is a record of what happened, not a copy of the content.
    detail        TEXT,
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_micros        BIGINT  NOT NULL DEFAULT 0,
    duration_ms        INTEGER NOT NULL DEFAULT 0
);

-- The month's spend, summed on every call, and the history read newest first.
CREATE INDEX IF NOT EXISTS ai_calls_at_idx ON ai_calls (at DESC);

-- Who may spend money. Nobody, until the owner says otherwise: the free tier is
-- open to the household, and a paid provider is granted per account rather than
-- to everyone. An account that cannot spend can still use a free model.
ALTER TABLE users ADD COLUMN IF NOT EXISTS may_spend_on_ai BOOLEAN NOT NULL DEFAULT FALSE;
