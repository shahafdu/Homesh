-- Where previous goes when the order is random.
--
-- Shuffle decides what comes next, so "the one before this" has no meaning in
-- the queue's own order: the track sitting above the current one is not the
-- track you just heard, and playing it is as arbitrary as picking again. In the
-- web player it *was* picking again -- previous randomised exactly as next did,
-- which made the two buttons the same button.
--
-- What previous means, with shuffle on, is: take me back to what was playing.
-- That needs the room to remember where it has been, because the queue cannot
-- say.
--
-- Indices into the queue rather than item ids: the queue is what the cursor
-- addresses, and an id would have to be searched for on the way back. Trimmed
-- as it grows -- going back is something done a few steps at a time, and a
-- slideshow left running for a week should not accumulate a week of them.

ALTER TABLE play_sessions
    ADD COLUMN IF NOT EXISTS history jsonb NOT NULL DEFAULT '[]'::jsonb;
