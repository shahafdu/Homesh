-- What the room is playing is only half a position; the other half is how long
-- it runs. The control tower can show where a room has got to, but without a
-- length it cannot draw a bar — which is why the bar appeared for music, whose
-- length the catalog knows, and never for video, whose length it does not.
--
-- Reported by the screen rather than read from the catalog: a live transcode
-- has no duration until something is decoding it, and the screen is the only
-- thing that knows.

ALTER TABLE play_sessions
    ADD COLUMN IF NOT EXISTS duration_ms bigint;
