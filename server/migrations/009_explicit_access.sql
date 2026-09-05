-- Access becomes explicit: nothing granted means nothing reachable.
--
-- The previous default was the opposite — no rules meant no restriction — which
-- reads dangerously in the one place you least want ambiguity. An unticked list
-- looks like "no access" to anyone glancing at it, so that is now what it means.
--
-- "Everything" is therefore its own stored fact rather than the absence of
-- facts, which is what makes the two states tell them apart.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_owner     BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS all_library  BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS all_zones    BOOLEAN NOT NULL DEFAULT FALSE;

-- Exactly one owner: the account that set the system up. Enforced rather than
-- assumed, because "there is always someone who cannot be locked out" is only
-- true if the database says so.
CREATE UNIQUE INDEX IF NOT EXISTS users_single_owner_idx ON users((is_owner)) WHERE is_owner;

-- The first administrator becomes the owner.
UPDATE users SET is_owner = TRUE
WHERE id = (SELECT id FROM users WHERE is_admin ORDER BY created_at LIMIT 1)
  AND NOT EXISTS (SELECT 1 FROM users WHERE is_owner);

-- Nobody loses access to something they could reach a moment ago. Accounts that
-- were unrestricted under the old default are made explicitly unrestricted; any
-- account that already had rules keeps exactly those.
UPDATE users u SET all_library = TRUE
WHERE NOT EXISTS (SELECT 1 FROM user_library_rules r WHERE r.user_id = u.id);

UPDATE users u SET all_zones = TRUE
WHERE NOT EXISTS (SELECT 1 FROM user_zone_rules r WHERE r.user_id = u.id);

-- Invitations carry the same two facts, so an invited account is scoped exactly
-- as intended from its first sign-in rather than briefly holding the run of the
-- house between account creation and rule application.
ALTER TABLE invites
    ADD COLUMN IF NOT EXISTS all_library BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS all_zones   BOOLEAN NOT NULL DEFAULT FALSE;
