-- Throw away the lengths that are the length of the first 256 KB of a film.
--
-- Videos in a Drive folder are read as a prefix, the same as everything else
-- remote. For tags that is fine. For duration it was not, and it failed
-- silently: ffprobe handed a quarter-megabyte of a film reports how long that
-- quarter-megabyte lasts, which is a number. A 1.6 GB conga lesson was recorded
-- as 0.64 seconds long, a 988 MB one as 1.5 seconds, and the control tower drew
-- exactly that -- a progress bar the film ran off the end of within a second.
--
-- Measured, not assumed: every one of the twelve videos in that folder was
-- timed as its own prefix, and the pattern holds across the Drive sources.
--
-- The rule below is the one the extraction now applies as it writes: a length
-- that implies more than 60 Mbit/s sustained is not a length. UHD from a
-- telephone peaks near 100 and a Blu-ray remux at 40, so nothing real is
-- anywhere near this; a prefix-timed film is a thousand times over it.
--
-- Clearing is the whole repair. Nothing is lost by nulling a length that is
-- also correct, because the backfill pass only looks at items that have none
-- and now measures them properly -- so anything cleared here that was in fact
-- right is simply measured again and written back the same.

UPDATE items
SET duration_ms = NULL
WHERE kind = 'video'
  AND duration_ms IS NOT NULL
  AND duration_ms > 0
  AND size_bytes IS NOT NULL
  AND size_bytes > 0
  AND size_bytes * 8000.0 / duration_ms > 60000000;

-- A room may be holding one of these right now, having been seeded from the
-- catalog when the track started. Cleared where the item it is playing no
-- longer claims a length: a bar drawn from nothing is better than one drawn
-- from a wrong number, and the next track re-seeds from the catalog anyway.
UPDATE play_sessions s
SET duration_ms = NULL
WHERE s.duration_ms IS NOT NULL
  AND jsonb_typeof(s.queue) = 'array'
  AND s.cursor >= 0
  AND s.cursor < jsonb_array_length(s.queue)
  AND EXISTS (
      SELECT 1 FROM items i
      WHERE i.id = (s.queue ->> s.cursor)::uuid
        AND i.duration_ms IS NULL
  );
