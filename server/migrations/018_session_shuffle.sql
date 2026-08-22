-- Shuffle belongs to the room, not to the phone that pressed it.
--
-- It was an action — reorder what is left — which works but says nothing
-- afterwards: the button looked identical whether or not it had been pressed,
-- so the only way to know was to listen. A room is driven from several phones
-- and walked away from, so the state has to live where the queue lives.

ALTER TABLE play_sessions
    ADD COLUMN IF NOT EXISTS shuffle boolean NOT NULL DEFAULT false;
