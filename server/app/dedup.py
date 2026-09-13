"""One file in two places, recognised as one file.

The catalog has always been able to hold several copies of an item — that is
what `replicas` is, and `resolve_playable` picks whichever copy answers, which
is how the library is meant to keep playing with the PC switched off
(ARCHITECTURE §4). Nothing ever put two copies under one item, though, because
nothing ever compared file contents. `items.content_hash` sat empty for all
141,818 files.

So `E:\\music` and the Drive copy of the same folder produced two complete
catalogues side by side: 9,189 songs on each side and not one item in common.
Visibly, everything appears twice. Invisibly, the availability model has never
worked at all.

Two passes, in order:

1. **fingerprint** — give local copies an MD5, but only those that might be
   duplicates. Drive states its own MD5 in the listing, free; local disk has to
   be read. Hashing the whole library means reading every byte of it, so this
   reads only files that have a same-name, same-size counterpart in another
   source: 53 GB rather than everything.

2. **merge** — where two replicas have the same fingerprint and belong to
   different items, make them replicas of one item.

Merging is the destructive half and is written to be boring: everything
pointing at the losing item is moved first, and only then is the item removed.
Nothing about a file on disk is touched — this is a catalog operation, and the
catalog is the only thing it can damage.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import closing
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text

from .db import get_engine

log = logging.getLogger("homesh.dedup")

# Read size when fingerprinting. Large enough that the syscalls disappear,
# small enough that a 13 GB tape is not held in memory.
CHUNK = 1024 * 1024


@dataclass
class DedupResult:
    fingerprinted: int = 0
    merged: int = 0
    bytes_read: int = 0


def _candidates(limit: int) -> list[tuple[UUID, UUID, str, str]]:
    """Local copies worth reading, because something elsewhere looks like them.

    Same name and same size in a different source is not proof — that is what
    the fingerprint is for — but it is a shortlist, and the shortlist is the
    difference between reading 53 GB and reading the whole library.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT r.id, r.source_id, r.dir_path, r.filename
                FROM replicas r
                JOIN items i   ON i.id = r.item_id
                JOIN sources s ON s.id = r.source_id
                WHERE r.content_md5 IS NULL
                  AND r.available
                  AND s.kind = 'local'
                  AND i.size_bytes > 0
                  AND EXISTS (
                      SELECT 1
                      FROM replicas other
                      JOIN items oi   ON oi.id = other.item_id
                      JOIN sources os ON os.id = other.source_id
                      WHERE lower(other.filename) = lower(r.filename)
                        AND oi.size_bytes = i.size_bytes
                        AND other.source_id <> r.source_id
                        AND os.kind <> 'local'
                        AND other.available
                  )
                ORDER BY i.size_bytes
                LIMIT :lim
                """
            ),
            {"lim": limit},
        ).all()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def fingerprint_local(limit: int = 2000, connectors=None) -> DedupResult:
    """Read the shortlisted local files and record what they are.

    Smallest first, deliberately. A run that is cut short should have got
    through as many files as possible rather than as many bytes, and the long
    tail here is a handful of wedding tapes.

    `connectors` is how a source is turned into something that can be read, and
    is a parameter only so a test can hand over a library that is not mounted
    where the catalog says it is.
    """
    from .library import connector_for

    open_source = connectors or connector_for
    result = DedupResult()
    engine = get_engine()

    for replica_id, source_id, dir_path, filename in _candidates(limit):
        connector = open_source(source_id)
        if connector is None:
            continue
        rel = f"{dir_path}/{filename}" if dir_path else filename

        digest = hashlib.md5()  # noqa: S324 - matching Drive, not guarding anything
        read = 0
        try:
            with closing(connector.open_range(rel, 0, None)) as chunks:
                for chunk in chunks:
                    digest.update(chunk)
                    read += len(chunk)
        except Exception as exc:  # noqa: BLE001 - one unreadable file is not the pass failing
            log.debug("could not fingerprint %s: %s", filename, exc)
            continue

        with engine.begin() as conn:
            conn.execute(
                text("UPDATE replicas SET content_md5 = :md5 WHERE id = :rid"),
                {"md5": digest.digest(), "rid": str(replica_id)},
            )
        result.fingerprinted += 1
        result.bytes_read += read

    if result.fingerprinted:
        log.info(
            "fingerprinted %d files (%.1f GB)",
            result.fingerprinted,
            result.bytes_read / 1e9,
        )
    return result


def _duplicate_groups() -> list[tuple[bytes, list[UUID]]]:
    """Fingerprints held by more than one item."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT r.content_md5, array_agg(DISTINCT r.item_id) AS items
                FROM replicas r
                WHERE r.content_md5 IS NOT NULL
                GROUP BY r.content_md5
                HAVING count(DISTINCT r.item_id) > 1
                """
            )
        ).all()
    return [(bytes(r[0]), list(r[1])) for r in rows]


def _merge_into(conn, keep: UUID, drop: UUID) -> None:
    """Move everything that points at one item onto another, then remove it.

    Order matters and the order is: move, then delete. Deleting first would take
    the rest with it — `progress`, `item_metadata`, `embeddings` and
    `transcodes` all cascade from `items`, and a playlist entry would be set to
    NULL, which is how a merge could silently empty somebody's playlist.

    Where both items hold a row for the same key — the same person's position in
    the same track, the same tag — the kept item's row stands and the other is
    discarded. They describe the same file, so there is nothing to reconcile.
    """
    args = {"keep": str(keep), "drop": str(drop)}

    # The copies themselves. This is the point of the whole exercise.
    conn.execute(text("UPDATE replicas SET item_id = :keep WHERE item_id = :drop"), args)

    # A playlist entry must survive: it is somebody's list, and the file it
    # names has not gone anywhere.
    conn.execute(text("UPDATE playlist_items SET item_id = :keep WHERE item_id = :drop"), args)

    # The rest describe the item rather than being it, and each has a key that
    # two rows cannot share. Where both items have a row for the same key, the
    # kept item's stands: they describe the same file, so there is nothing to
    # reconcile, and a rule that always picks the same side is what makes
    # re-running this safe.
    for table, key in (
        ("progress", "user_id"),
        ("item_metadata", "key, origin"),
        ("embeddings", "chunk_idx, model"),
        ("transcodes", None),
    ):
        matching = (
            " AND ".join(f"kept.{column.strip()} = moving.{column.strip()}"
                         for column in key.split(","))
            if key
            else "TRUE"
        )
        conn.execute(
            text(
                f"""
                DELETE FROM {table} moving
                WHERE moving.item_id = :drop
                  AND EXISTS (
                      SELECT 1 FROM {table} kept
                      WHERE kept.item_id = :keep AND {matching}
                  )
                """  # noqa: S608 - names come from the tuple above, never from input
            ),
            args,
        )
        conn.execute(
            text(f"UPDATE {table} SET item_id = :keep WHERE item_id = :drop"),  # noqa: S608
            args,
        )

    conn.execute(text("DELETE FROM items WHERE id = :drop"), args)


def merge_duplicates(limit: int = 5000) -> DedupResult:
    """Bring copies of the same file under one item.

    The survivor is the oldest item, which is arbitrary but stable: re-running
    this must not shuffle ids about, because ids are what playlists, progress
    and shared links are written in terms of.
    """
    result = DedupResult()
    engine = get_engine()

    for md5, items in _duplicate_groups()[:limit]:
        with engine.begin() as conn:
            ordered = conn.execute(
                text(
                    """
                    SELECT id FROM items
                    WHERE id = ANY(CAST(:ids AS uuid[]))
                    ORDER BY indexed_at, id
                    """
                ),
                {"ids": [str(i) for i in items]},
            ).scalars().all()
            if len(ordered) < 2:
                continue

            keep = UUID(str(ordered[0]))
            for other in ordered[1:]:
                _merge_into(conn, keep, UUID(str(other)))
                result.merged += 1

            # Now that it is the only item holding this fingerprint, it can
            # carry it: `items.content_hash` is unique, which is exactly why the
            # fingerprint had to live on the replica until this moment.
            #
            # Any *other* item still claiming this fingerprint has no copy left
            # that carries it -- the group above was built from the copies, so
            # anything holding it outside the group is a leftover. Released
            # first, because the unique index would otherwise abort the merge
            # rather than the stale claim.
            conn.execute(
                text(
                    "UPDATE items SET content_hash = NULL "
                    "WHERE content_hash = :h AND id <> :id"
                ),
                {"h": md5, "id": str(keep)},
            )
            conn.execute(
                text("UPDATE items SET content_hash = :h WHERE id = :id"),
                {"h": md5, "id": str(keep)},
            )

    if result.merged:
        log.info("merged %d duplicate items", result.merged)
    return result
