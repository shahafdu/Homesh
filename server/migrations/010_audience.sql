-- Every folder and room decides its own audience.
--
-- Without this, "admins only" could not be honoured: an account holding
-- whole-library access would pick up a newly shared folder the moment it
-- appeared, which is precisely the case where you most want to decide first.
-- The audience is therefore a property of the folder, applied before any
-- personal grant is consulted.
--
-- NULL means nobody has decided yet, which is treated as admins-only. Sources
-- arrive by discovery — a Drive folder shared with the service account shows up
-- on its own — so the safe state on arrival is the restrictive one. The
-- alternative publishes a folder to the household before its owner has looked
-- at it.

CREATE TYPE audience AS ENUM ('everyone', 'admins', 'selected');

ALTER TABLE sources ADD COLUMN IF NOT EXISTS audience audience;
ALTER TABLE zones   ADD COLUMN IF NOT EXISTS audience audience;

-- Everything that exists today keeps the reach it already had; only what
-- arrives from here on has to be decided.
UPDATE sources SET audience = 'everyone' WHERE audience IS NULL;
UPDATE zones   SET audience = 'everyone' WHERE audience IS NULL;
