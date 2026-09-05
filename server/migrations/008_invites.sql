-- Invitations.
--
-- An account has to be created on the device it will be used from: a passkey is
-- bound to the authenticator that made it, so an admin registering someone else
-- from their own phone would enrol the wrong fingerprint.
--
-- So the admin creates an invite carrying the name and the intended access, and
-- the person completes it on their own device. The account is correctly scoped
-- from its first sign-in rather than briefly unrestricted.

CREATE TABLE IF NOT EXISTS invites (
    code          TEXT PRIMARY KEY,
    handle        TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    -- The access this account should have, decided when inviting.
    library_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
    zone_rules    JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    used_at       TIMESTAMPTZ,
    user_id       UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS invites_expiry_idx ON invites(expires_at) WHERE used_at IS NULL;
