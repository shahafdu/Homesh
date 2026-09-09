-- A slideshow is a queue whose items have no length of their own.
--
-- Everything else in a queue ends by itself: a track finishes, a film finishes,
-- and the screen says so. A photograph never finishes, so how long it stays is
-- a property of the room rather than of the file, and it has to live beside the
-- queue for the same reason shuffle does — a slideshow is started from a phone
-- that then leaves the house.

ALTER TABLE play_sessions
    -- How long each photograph is held. NULL for a queue of things that end on
    -- their own, which is what distinguishes a slideshow from every other queue.
    ADD COLUMN IF NOT EXISTS photo_ms integer,
    -- How one photograph gives way to the next. 'random' is decided per photo by
    -- whatever is drawing it, so a long slideshow does not repeat itself.
    ADD COLUMN IF NOT EXISTS transition text NOT NULL DEFAULT 'fade';

ALTER TABLE play_sessions
    DROP CONSTRAINT IF EXISTS play_sessions_photo_ms_check;
ALTER TABLE play_sessions
    ADD CONSTRAINT play_sessions_photo_ms_check
    -- One second is the fastest anybody can look at a photograph; ten minutes is
    -- long enough that the bound is not the thing stopping you.
    CHECK (photo_ms IS NULL OR (photo_ms >= 1000 AND photo_ms <= 600000));

-- Who started what is playing, so the room can carry on without them.
--
-- Advancing a queue means checking that the listener may reach the next item,
-- and that check needs an account. Until now the only account to hand was
-- whoever pressed the button in that instant, so a queue could not advance on
-- its own: the screen reported that a track had ended and the server, having
-- nobody to act as, did nothing. The room stopped after every item.
--
-- ON DELETE SET NULL rather than CASCADE: removing an account must not delete
-- the record of what a room is doing. A session with no starter simply stops
-- advancing, which is the safe direction.
ALTER TABLE play_sessions
    ADD COLUMN IF NOT EXISTS started_by uuid REFERENCES users(id) ON DELETE SET NULL;
