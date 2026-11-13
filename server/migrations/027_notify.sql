-- The mailbox: short messages for a person, posted by Homesh or by an app
-- beside it, and fetched by that person's phones (docs/design/07-notifications.md).

-- A phone that may read one person's messages. Only the hash of its token is
-- kept, as with sessions.
CREATE TABLE IF NOT EXISTS notify_devices (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label        TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 60),
    token_hash   BYTEA NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS notify_devices_user ON notify_devices (user_id)
    WHERE revoked_at IS NULL;

-- A single-use code that turns into a device token. Lives two minutes.
CREATE TABLE IF NOT EXISTS notify_claims (
    code_hash  BYTEA PRIMARY KEY,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label      TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ
);

-- The mailbox: one row per message per person.
CREATE TABLE IF NOT EXISTS notify_messages (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sender     TEXT NOT NULL,
    title      TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 80),
    body       TEXT NOT NULL DEFAULT '' CHECK (length(body) <= 300),
    -- A path inside the sending app. Never an address.
    path       TEXT NOT NULL DEFAULT '/' CHECK (length(path) <= 512),
    -- A later message with the same sender and tag replaces this one on the
    -- phone ("3 items waiting" becoming "4 items waiting").
    tag        TEXT CHECK (tag IS NULL OR length(tag) <= 64),
    expires_at TIMESTAMPTZ NOT NULL,
    read_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS notify_messages_inbox ON notify_messages (user_id, id);
