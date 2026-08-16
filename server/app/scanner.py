"""Catalog indexing.

Walks a source and reconciles it into `items` + `replicas`. Filenames and paths are
written as first-class columns, never derived from metadata (ARCHITECTURE.md §2).

Content hashing and metadata extraction are deliberately not done here — they are
separate passes, so a first scan is fast and the folder view becomes usable immediately.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from .db import get_engine
from .sources.base import PLAYLIST_EXTS, classify
from .sources.local import LocalConnector

log = logging.getLogger("homesh.scanner")

BATCH = 500


@dataclass
class ScanResult:
    source_id: UUID
    added: int = 0
    updated: int = 0
    vanished: int = 0
    playlists: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    @property
    def seconds(self) -> float:
        end = self.finished_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds()


def scan_source(source_id: UUID, connector: LocalConnector) -> ScanResult:
    result = ScanResult(source_id=source_id)

    if not connector.available:
        result.errors.append("source unavailable")
        result.finished_at = datetime.now(UTC)
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE sources
                    SET scan_state = 'failed', scan_ended_at = now(),
                        scan_error = 'the source could not be reached'
                    WHERE id = :sid
                    """
                ),
                {"sid": str(source_id)},
            )
        return result

    engine = get_engine()
    seen: set[tuple[str, str]] = set()
    batch: list[dict] = []

    def progress(**fields) -> None:
        """Publish where the scan has got to.

        Its own transaction, committed as it goes: a scan of nine thousand Drive
        files takes minutes, and progress nobody can read until the end is not
        progress. Failures here are swallowed — reporting must never be what
        breaks a scan.
        """
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(f"UPDATE sources SET {sets} WHERE id = :sid"),  # noqa: S608
                    {**fields, "sid": str(source_id)},
                )
        except Exception:  # noqa: BLE001
            log.debug("could not record scan progress", exc_info=True)

    progress(
        scan_state="running",
        scan_started_at=datetime.now(UTC),
        scan_ended_at=None,
        scan_seen=0,
        scan_added=0,
        scan_error=None,
    )

    def flush() -> None:
        if not batch:
            return
        with engine.begin() as conn:
            for row in batch:
                # One statement per file keeps the reconcile logic legible; the volumes
                # here (tens of thousands) do not justify anything cleverer yet.
                existing = conn.execute(
                    text(
                        """
                        SELECT id, item_id FROM replicas
                        WHERE source_id = :sid AND dir_path = :dir AND filename = :name
                        """
                    ),
                    {"sid": str(source_id), "dir": row["dir"], "name": row["name"]},
                ).first()

                if existing is None:
                    item_id = conn.execute(
                        text(
                            """
                            INSERT INTO items (kind, size_bytes, created_at)
                            VALUES (CAST(:kind AS item_kind), :size, :mtime)
                            RETURNING id
                            """
                        ),
                        {"kind": row["kind"], "size": row["size"], "mtime": row["mtime"]},
                    ).scalar_one()

                    conn.execute(
                        text(
                            """
                            INSERT INTO replicas
                                (item_id, source_id, dir_path, filename, ext, mtime, available)
                            VALUES (:iid, :sid, :dir, :name, :ext, :mtime, TRUE)
                            """
                        ),
                        {
                            "iid": str(item_id),
                            "sid": str(source_id),
                            "dir": row["dir"],
                            "name": row["name"],
                            "ext": row["ext"],
                            "mtime": row["mtime"],
                        },
                    )
                    result.added += 1
                else:
                    conn.execute(
                        text(
                            """
                            UPDATE replicas SET mtime = :mtime, available = TRUE
                            WHERE id = :rid
                            """
                        ),
                        {"mtime": row["mtime"], "rid": str(existing[0])},
                    )
                    # Kind as well as size: the classifier improves over time, and
                    # a file catalogued as "other" under an older version should
                    # not stay that way for the life of the library. Five wedding
                    # tapes sat unopenable for exactly this reason.
                    conn.execute(
                        text(
                            """
                            UPDATE items
                            SET size_bytes = :size, kind = CAST(:kind AS item_kind)
                            WHERE id = :iid
                            """
                        ),
                        {"size": row["size"], "kind": row["kind"], "iid": str(existing[1])},
                    )
                    result.updated += 1
        batch.clear()

    for dir_path, entry in connector.walk():
        kind, ext = classify(entry.name)
        if ext in PLAYLIST_EXTS:
            # Catalogued as a file, but imported as a playlist in a later pass (§5.2).
            result.playlists += 1

        seen.add((dir_path, entry.name))
        batch.append(
            {
                "dir": dir_path,
                "name": entry.name,
                "ext": ext,
                "kind": kind,
                "size": entry.size,
                "mtime": entry.mtime,
            }
        )
        if len(batch) >= BATCH:
            flush()
            progress(scan_seen=len(seen), scan_added=result.added)

    flush()
    progress(scan_seen=len(seen), scan_added=result.added)

    # Anything previously indexed but no longer present is marked unavailable rather
    # than deleted: it may be a file the user moved, and the catalog is more useful
    # remembering it existed than silently forgetting (§3.3).
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, dir_path, filename FROM replicas WHERE source_id = :sid"),
            {"sid": str(source_id)},
        ).all()

        gone = [str(r[0]) for r in rows if (r[1], r[2]) not in seen]
        if gone:
            conn.execute(
                text("UPDATE replicas SET available = FALSE WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": gone},
            )
            result.vanished = len(gone)

        conn.execute(
            text("UPDATE sources SET last_seen_at = now() WHERE id = :sid"),
            {"sid": str(source_id)},
        )

    result.finished_at = datetime.now(UTC)
    progress(
        scan_state="done",
        scan_ended_at=result.finished_at,
        scan_seen=len(seen),
        scan_added=result.added,
    )
    log.info(
        "scan complete: +%d ~%d -%d (%d playlists) in %.1fs",
        result.added,
        result.updated,
        result.vanished,
        result.playlists,
        result.seconds,
    )
    return result
