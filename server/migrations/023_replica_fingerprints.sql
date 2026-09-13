-- One song, two places — rather than two songs.
--
-- `items.content_hash` has been in the schema since 001 and has never been
-- filled, for any of the 141,818 files here. Nothing has ever noticed that a
-- file scanned from two sources is the same file, so `E:\music` and the Drive
-- copy of the same folder produced two complete catalogues side by side: 9,189
-- songs each, 130 videos each, and not one item in common.
--
-- The visible cost is a library listing everything twice. The invisible one is
-- worse, and is the whole availability model (ARCHITECTURE §4): "the catalog is
-- always up, the bytes may not be" works by an item having several replicas and
-- playing whichever answers. With one replica each there is nothing to fall
-- back to, so music that exists on Drive is unplayable the moment the PC is off
-- — and has been all along.
--
-- The fingerprint belongs on the **replica**, not the item. A replica is one
-- copy of a file in one source, so it is the thing that can be hashed; the item
-- is the abstract file that several replicas are copies of. Putting it here is
-- also what makes merging possible at all: `items.content_hash` is unique, so
-- two items could never both hold the hash that proves they are the same.
--
-- MD5 because Drive says MD5. It is a poor choice against an adversary and a
-- fine one here: nothing is trusted on the strength of it, and it is the only
-- digest Drive will hand over without downloading the file — which is the
-- entire point, since the Drive copy of this library is 53 GB of the candidates
-- alone.

ALTER TABLE replicas
    ADD COLUMN IF NOT EXISTS content_md5 bytea;

-- Finding the duplicates means grouping by this, so it has to be indexed.
-- Partial: most replicas will never be hashed, because most files exist in
-- exactly one place and hashing them would be reading the library for nothing.
CREATE INDEX IF NOT EXISTS replicas_content_md5_idx
    ON replicas (content_md5)
    WHERE content_md5 IS NOT NULL;

-- Candidate search: same name, same size, different source. Reading the whole
-- of a 53 GB shortlist is cheap next to reading the whole library, but only if
-- finding the shortlist is cheap too.
CREATE INDEX IF NOT EXISTS replicas_filename_idx
    ON replicas (lower(filename));
