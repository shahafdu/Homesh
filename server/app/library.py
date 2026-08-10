"""Browsing, searching and source management.

Everything here reads the *catalog*, not the source. That is deliberate: browsing and
search keep working with the RAID powered off, which is the whole availability
argument (ARCHITECTURE.md §3.3).
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import text

from .config import get_settings
from .db import get_engine
from .metadata import extract_for_source
from .scanner import scan_source
from .security import CurrentUser, require_user
from .sources.local import LocalConnector

log = logging.getLogger("homesh.library")
router = APIRouter(prefix="/api", tags=["library"])


def register_sources() -> None:
    """Upsert the configured local roots into `sources` at startup.

    Idempotent, so restarts and added roots both do the right thing.
    """
    settings = get_settings()
    roots = settings.parsed_media_roots
    if not roots:
        log.info("no MEDIA_ROOTS configured — nothing local to index yet")
        return

    with get_engine().begin() as conn:
        for name, root in roots:
            prefix = f"/local/{name.lower()}"
            conn.execute(
                text(
                    """
                    INSERT INTO sources (kind, name, mount_prefix)
                    VALUES ('local', :name, :prefix)
                    ON CONFLICT (mount_prefix) DO UPDATE SET name = EXCLUDED.name
                    """
                ),
                {"name": name, "prefix": prefix},
            )
            reachable = LocalConnector(root).available
            log.info("source %s -> %s (%s)", prefix, root,
                     "reachable" if reachable else "NOT REACHABLE")


@router.get("/sources")
async def list_sources(_: CurrentUser = Depends(require_user)) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.id, s.kind::text, s.name, s.mount_prefix, s.last_seen_at,
                       count(r.id) FILTER (WHERE r.available) AS files
                FROM sources s
                LEFT JOIN replicas r ON r.source_id = s.id
                GROUP BY s.id
                ORDER BY s.mount_prefix
                """
            )
        ).all()

    return [
        {
            "id": str(r[0]),
            "kind": r[1],
            "name": r[2],
            "mount_prefix": r[3],
            "last_seen_at": r[4].isoformat() if r[4] else None,
            "files": r[5],
        }
        for r in rows
    ]


@router.get("/browse")
async def browse(
    path: str = Query("", description="Virtual path, e.g. /local/raid/Music"),
    _: CurrentUser = Depends(require_user),
) -> dict:
    """List one level of the unified namespace.

    At the root this returns the mounted sources; deeper it returns real directories
    and files, filename first (§2, principles 1 and 2).
    """
    path = "/" + path.strip("/")

    with get_engine().connect() as conn:
        sources = conn.execute(
            text("SELECT id, name, mount_prefix FROM sources ORDER BY mount_prefix")
        ).all()

        if path == "/":
            return {
                "path": "/",
                "parent": None,
                "dirs": [
                    {"name": s[1], "path": s[2], "source": str(s[0])} for s in sources
                ],
                "files": [],
            }

        match = next((s for s in sources if path == s[2] or path.startswith(s[2] + "/")), None)
        if match is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no source mounted at that path")

        source_id, _name, prefix = match
        rel = path[len(prefix) :].strip("/")

        # Immediate child directories, derived from stored paths.
        dirs = conn.execute(
            text(
                """
                SELECT child FROM (
                    SELECT DISTINCT
                        CASE
                            WHEN :rel = '' THEN split_part(dir_path, '/', 1)
                            ELSE split_part(
                                substring(dir_path FROM char_length(:rel) + 2), '/', 1
                            )
                        END AS child
                    FROM replicas
                    WHERE source_id = :sid
                      AND (:rel = '' OR dir_path = :rel OR dir_path LIKE :rel || '/%')
                      AND dir_path <> :rel
                ) t
                WHERE child <> ''
                ORDER BY child COLLATE natsort
                """
            ),
            {"sid": str(source_id), "rel": rel},
        ).all()

        files = conn.execute(
            text(
                """
                SELECT r.filename, r.ext, r.mtime, r.available,
                       i.id, i.kind::text, i.size_bytes, i.duration_ms,
                       md.meta
                FROM replicas r
                JOIN items i ON i.id = r.item_id
                LEFT JOIN LATERAL (
                    -- One value per key, resolving conflicts by origin. A tag the
                    -- user set beats the file's own, which beats a lookup, which
                    -- beats a model's guess — the precedence principle #1 exists
                    -- to make visible.
                    SELECT jsonb_object_agg(key, value) AS meta
                    FROM (
                        SELECT DISTINCT ON (m.key) m.key, m.value
                        FROM item_metadata m
                        WHERE m.item_id = i.id
                          AND m.key IN ('title', 'artist', 'album', 'albumartist')
                        ORDER BY m.key,
                                 CASE m.origin
                                     WHEN 'user' THEN 0
                                     WHEN 'file' THEN 1
                                     WHEN 'musicbrainz' THEN 2
                                     ELSE 3
                                 END
                    ) best
                ) md ON TRUE
                WHERE r.source_id = :sid AND r.dir_path = :rel
                ORDER BY r.filename COLLATE natsort
                """
            ),
            {"sid": str(source_id), "rel": rel},
        ).all()

    # Up from a source root goes to the namespace root, not to a phantom "/local"
    # that nothing is mounted at.
    parent = "/" if path == prefix else (path.rsplit("/", 1)[0] or "/")
    return {
        "path": path,
        "parent": parent,
        "dirs": [{"name": d[0], "path": f"{path}/{d[0]}"} for d in dirs],
        "files": [
            {
                "item_id": str(f[4]),
                # The filename is the primary label, always. Metadata may add to it,
                # never replace it (§2, principle 1).
                "filename": f[0],
                "ext": f[1],
                "kind": f[5],
                "size": f[6],
                "duration_ms": f[7],
                # Additive only: the filename above is never replaced by these.
                "meta": f[8] or {},
                "mtime": f[2].isoformat() if f[2] else None,
                "available": f[3],
            }
            for f in files
        ],
    }


@router.get("/search")
async def search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    _: CurrentUser = Depends(require_user),
) -> list[dict]:
    """Filename search, typo-tolerant via trigram similarity.

    Semantic search over content arrives in phase 6; this is the literal-filename
    search that Plex never gave us.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT r.filename, r.dir_path, s.mount_prefix, r.available,
                       i.id, i.kind::text, i.size_bytes,
                       -- word_similarity scores the best-matching *substring*, so a
                       -- typo'd query still ranks against a long filename. Plain
                       -- similarity() compares whole strings and misses these.
                       greatest(
                           word_similarity(:q, r.filename),
                           word_similarity(:q, r.dir_path)
                       ) AS score,
                       (r.filename ILIKE '%' || :q || '%') AS name_hit
                FROM replicas r
                JOIN items i   ON i.id = r.item_id
                JOIN sources s ON s.id = r.source_id
                -- Folder names are searchable too: "wall" should find the contents of
                -- "Pink Floyd/The Wall" even though no filename contains it (§2).
                WHERE r.filename ILIKE '%' || :q || '%'
                   OR r.dir_path ILIKE '%' || :q || '%'
                   -- 0.3 measured against this corpus: genuine typos score 0.33-0.71
                   -- ("trck"->track2 = 0.40, "beech"->beach = 0.33), unrelated pairs
                   -- top out at 0.20. Retune if the corpus character changes.
                   OR word_similarity(:q, r.filename) > 0.3
                   OR word_similarity(:q, r.dir_path) > 0.3
                ORDER BY name_hit DESC, score DESC, r.filename COLLATE natsort
                LIMIT :lim
                """
            ),
            {"q": q, "lim": limit},
        ).all()

    return [
        {
            "item_id": str(r[4]),
            "filename": r[0],
            "path": f"{r[2]}/{r[1]}".rstrip("/"),
            "kind": r[5],
            "size": r[6],
            "available": r[3],
        }
        for r in rows
    ]


@router.post("/sources/{source_id}/scan", status_code=status.HTTP_202_ACCEPTED)
async def trigger_scan(
    source_id: UUID,
    background: BackgroundTasks,
    user: CurrentUser = Depends(require_user),
) -> dict:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")

    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT id, config_encrypted FROM sources WHERE id = :id"),
            {"id": str(source_id)},
        ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such source")

    root = _root_for_source(source_id)
    if root is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "source has no configured root")

    background.add_task(_scan_then_extract, source_id, root)
    return {"started": True, "source_id": str(source_id)}


def _scan_then_extract(source_id: UUID, root: str) -> None:
    """Index first, read tags second.

    Scanning only touches directory entries, so the folder view is usable almost
    immediately. Reading tags opens every file, which is far slower — running it
    after means a large library is browsable long before it is fully described.
    """
    connector = LocalConnector(root)
    scan_source(source_id, connector)
    extract_for_source(source_id, connector)


def _root_for_source(source_id: UUID) -> str | None:
    """Resolve a source's on-disk root from configuration.

    Roots come from the environment rather than the database for now: they are
    deployment facts, and keeping them out of the DB avoids a stored path that
    silently stops matching the container's mounts.
    """
    settings = get_settings()
    with get_engine().connect() as conn:
        prefix = conn.execute(
            text("SELECT mount_prefix FROM sources WHERE id = :id"), {"id": str(source_id)}
        ).scalar_one_or_none()

    for name, root in settings.parsed_media_roots:
        if f"/local/{name.lower()}" == prefix:
            return root
    return None
