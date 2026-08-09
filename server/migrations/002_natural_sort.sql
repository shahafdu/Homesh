-- Natural ordering, so "track2" comes before "track10" and "Episode 9" before
-- "Episode 10" (ARCHITECTURE.md §5.1). Plain byte or lexical ordering gets this
-- wrong, and in a folder-first browser that is immediately visible.
--
-- ICU's numeric-ordering keyword (kn-true) does this properly, including for
-- non-Latin filenames, which a hand-rolled regex split would not.

-- Named `natsort`, not `natural`: NATURAL is a reserved SQL keyword (NATURAL JOIN)
-- and would need quoting at every use site.
CREATE COLLATION IF NOT EXISTS natsort (
    provider = icu,
    locale   = 'en-US-u-kn-true',
    -- Filenames differing only by case or accent are distinct files, so the
    -- collation must stay deterministic.
    deterministic = true
);

-- Index supporting the browse listing's ORDER BY.
CREATE INDEX IF NOT EXISTS replicas_dir_natural_idx
    ON replicas (source_id, dir_path, filename COLLATE natsort);
