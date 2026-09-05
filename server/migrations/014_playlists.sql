-- Playlists become usable.
--
-- The tables were sketched in 001 and never filled in. They are extended rather
-- than replaced: the shape was right, and the comment in 001 already recorded the
-- important decision — an entry that cannot be resolved keeps its original line
-- rather than vanishing.
--
-- Forty-one .m3u files sit in this library, written years ago by Winamp against
-- paths that stopped being true when the music moved. The ordering in them is
-- somebody's work, and it is the one thing a scanner cannot recreate.

ALTER TABLE playlists
    ADD COLUMN IF NOT EXISTS owner_id    UUID REFERENCES users(id) ON DELETE SET NULL,
    -- Where it was imported from, so re-importing updates in place. Without it a
    -- nightly scan would breed a fresh copy of every list every night.
    ADD COLUMN IF NOT EXISTS source_id   UUID REFERENCES sources(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS source_path TEXT,
    ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS playlists_import_idx
    ON playlists(source_id, source_path)
    WHERE source_id IS NOT NULL AND source_path IS NOT NULL;

-- A stable identity per row. Position was the primary key, which makes reordering
-- a puzzle of colliding updates rather than a rewrite of a column.
ALTER TABLE playlist_items
    ADD COLUMN IF NOT EXISTS id UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The title from #EXTINF, which is often the only readable thing about a
    -- line whose file has moved.
    ADD COLUMN IF NOT EXISTS raw_title TEXT;

-- Nullable now: a track added from the browser was never in a file and has no
-- original reference to keep.
ALTER TABLE playlist_items ALTER COLUMN original_ref DROP NOT NULL;

ALTER TABLE playlist_items DROP CONSTRAINT IF EXISTS playlist_items_pkey;
ALTER TABLE playlist_items ADD PRIMARY KEY (id);

CREATE INDEX IF NOT EXISTS playlist_items_order_idx ON playlist_items(playlist_id, position);
