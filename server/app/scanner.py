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
        return result

    engine = get_engine()
    seen: set[tuple[str, str]] = set()
    batch: list[dict] = []

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
                    conn.execute(
                        text("UPDATE items SET size_bytes = :size WHERE id = :iid"),
                        {"size": row["size"], "iid": str(existing[1])},
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

    flush()

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
    log.info(
        "scan complete: +%d ~%d -%d (%d playlists) in %.1fs",
        result.added,
        result.updated,
        result.vanished,
        result.playlists,
        result.seconds,
    )
    return result
