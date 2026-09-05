-- Signing in on a device that cannot use a passkey.
--
-- WebAuthn requires a secure context. Over plain http at a LAN address — which
-- is how every phone in the house reaches this server — navigator.credentials
-- is simply absent, so there is no passkey to present and no way to enrol one.
-- The account is unreachable from the device it is most wanted on.
--
-- A link code is issued from an already-signed-in session and exchanged for a
-- session on the new device. It proves the same thing a passkey proves — that
-- somebody already holding the account authorised this — without needing an API
-- the browser will not expose over http.
--
-- Single use, short-lived, and stored hashed: the row is not a credential, and a
-- database dump grants nobody a session.

CREATE TABLE IF NOT EXISTS device_links (
    code_hash    TEXT PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_label TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    used_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS device_links_user_idx ON device_links(user_id);
CREATE INDEX IF NOT EXISTS device_links_expiry_idx ON device_links(expires_at);
