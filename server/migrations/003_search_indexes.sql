-- Folder names are searchable alongside filenames, so dir_path needs the same
-- trigram index the filename already has. Without it, fuzzy search degrades to a
-- sequential scan — fine for a fixture, not for a real library.
--
-- gin_trgm_ops backs both ILIKE '%…%' and the word_similarity operator.

CREATE INDEX IF NOT EXISTS replicas_dirpath_trgm_idx
    ON replicas USING gin (dir_path gin_trgm_ops);
