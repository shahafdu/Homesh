-- A slideshow that does not end.
--
-- The queue holds up to five thousand photographs, which at five seconds each is
-- seven hours before it wraps and starts them again. That is already "forever"
-- in one sense, but with 105,000 photographs in this house it is forever over
-- the same seven hours of them — and the point of shuffle on a library that
-- size is that you do not know what is coming.
--
-- So the room needs to know where the photographs came from, in order to go back
-- for more. Repeats are fine and expected: an endless slideshow that refused to
-- show a photograph twice would have to remember everything it had ever shown.

ALTER TABLE play_sessions
    -- The folder the photographs were gathered from, so the queue can be
    -- refilled from it. NULL for every queue that is not an endless slideshow,
    -- which is all of them until somebody asks for one.
    ADD COLUMN IF NOT EXISTS photo_under text,
    ADD COLUMN IF NOT EXISTS photo_endless boolean NOT NULL DEFAULT false,
    -- How far through the folder an in-order endless slideshow has walked.
    -- Without it, refilling would fetch the first five thousand again and a
    -- library of a hundred thousand would circle its first seven hours for
    -- ever -- the same fault as shuffling only the slice that had been sent.
    -- Reset to zero when a page comes back empty, which is the wrap-around.
    ADD COLUMN IF NOT EXISTS photo_offset integer NOT NULL DEFAULT 0;

-- Refilling means gathering again as the person who started it, which
-- 019 already recorded and this now depends on. Stated here because the two
-- columns look independent and are not: without started_by there is no account
-- to gather as, and an endless slideshow would quietly stop at the end of its
-- first queue.
