-- Who a playlist belongs to, and who else may see it.
--
-- Three kinds now exist and they behave differently:
--
--   yours      — you made it, you can change it
--   shared     — somebody else made it and let the house see it; play only
--   storage    — imported from a .m3u in the library, read only for everyone
--
-- Storage lists are read only because the file they came from is. This server
-- reads your library and never writes to it, so a playlist that could be edited
-- here would immediately disagree with the file it claims to be — and the next
-- import would silently undo the edit. Copying is the way to change one.

ALTER TABLE playlists
    ADD COLUMN IF NOT EXISTS shared BOOLEAN NOT NULL DEFAULT FALSE;

-- An imported list is everybody's to play: it describes music in the shared
-- library, and nobody owns it in the sense that matters here.
UPDATE playlists SET shared = TRUE WHERE source_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS playlists_owner_idx ON playlists(owner_id);
