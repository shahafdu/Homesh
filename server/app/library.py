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

from .access import can_read, library_scope, visible
from .config import get_settings
from .db import get_engine
from .metadata import extract_for_source
from .scanner import scan_source
from .security import CurrentUser, require_user
from .sources.local import LocalConnector

log = logging.getLogger("homesh.library")
router = APIRouter(prefix="/api", tags=["library"])


def register_sources() -> None:
    """Upsert every configured source at startup.

    Idempotent, so restarts, added roots and newly shared Drive folders all do
    the right thing.
    """
    _register_local()
    _register_drive()


def _register_local() -> None:
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


def _slug(name: str) -> str:
    """A stable, URL-safe mount name.

    Folder names here are frequently not Latin — Hebrew, in this house — so a
    naive ASCII slug would collapse several folders to the same empty string.
    Non-ASCII names keep their characters; only path separators are replaced.
    """
    cleaned = name.strip().replace("/", "-").replace("\\", "-")
    return "-".join(cleaned.split()).lower() or "folder"


def _register_drive() -> None:
    """Register each Drive folder shared with the service account.

    Discovery rather than configuration: share a folder in Drive and it appears
    here on the next restart, with nothing to edit.
    """
    from pathlib import Path

    from .sources.gdrive import DriveError, shared_folders

    key = Path(get_settings().gdrive_key_file)
    if not key.is_file():
        log.info("no Drive key at %s — Drive not configured", key)
        return

    try:
        folders = shared_folders(key)
    except DriveError as exc:
        log.warning("could not list Drive folders: %s", exc)
        return

    if not folders:
        log.info("Drive key present but no folders are shared with it yet")
        return

    with get_engine().begin() as conn:
        for folder_id, name in folders:
            prefix = f"/drive/{_slug(name)}"
            conn.execute(
                text(
                    """
                    INSERT INTO sources (kind, name, mount_prefix, remote_id)
                    VALUES ('gdrive', :name, :prefix, :rid)
                    ON CONFLICT (mount_prefix) DO UPDATE
                    SET name = EXCLUDED.name, remote_id = EXCLUDED.remote_id
                    """
                ),
                {"name": name, "prefix": prefix, "rid": folder_id},
            )
            log.info("source %s -> Drive folder %r", prefix, name)


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
    user: CurrentUser = Depends(require_user),
) -> dict:
    """List one level of the unified namespace.

    At the root this returns the mounted sources; deeper it returns real directories
    and files, filename first (§2, principles 1 and 2).
    """
    path = "/" + path.strip("/")
    rules = library_scope(user.id)

    with get_engine().connect() as conn:
        sources = conn.execute(
            text("SELECT id, name, mount_prefix FROM sources ORDER BY mount_prefix")
        ).all()

        if path == "/":
            return {
                "path": "/",
                "parent": None,
                # A source the person cannot reach into is not listed at all.
                # Absent rather than greyed out: there is nothing to be done
                # about it, so showing it would only invite the question.
                "dirs": [
                    {"name": s[1], "path": s[2], "source": str(s[0])}
                    for s in sources
                    if visible(s[2], rules)
                ],
                "files": [],
            }

        match = next((s for s in sources if path == s[2] or path.startswith(s[2] + "/")), None)
        if match is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no source mounted at that path")

        if not visible(path, rules):
            # 404 rather than 403: a folder outside your scope should not be
            # confirmed to exist.
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
    # Files are only listed where the folder itself is readable; a folder merely
    # on the way to an allowed one shows its subfolders and nothing else.
    readable_here = can_read(path, rules)
    return {
        "path": path,
        "parent": parent,
        "dirs": [
            {"name": d[0], "path": f"{path}/{d[0]}"}
            for d in dirs
            if visible(f"{path}/{d[0]}", rules)
        ],
        "files": [] if not readable_here else [
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
    user: CurrentUser = Depends(require_user),
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

    # Filtered after the query rather than inside it: a result someone cannot
    # open must not appear, or search becomes a way to learn what exists.
    rules = library_scope(user.id)
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
        if can_read(f"{r[2]}/{r[1]}".rstrip("/"), rules)
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

    connector = connector_for(source_id)
    if connector is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "source has no configured root")

    background.add_task(_scan_then_extract, source_id, connector)
    return {"started": True, "source_id": str(source_id)}


def _scan_then_extract(source_id: UUID, connector) -> None:
    """Index first, read tags second.

    Scanning only touches directory entries, so the folder view is usable almost
    immediately. Reading tags opens every file, which is far slower — running it
    after means a large library is browsable long before it is fully described.
    """
    scan_source(source_id, connector)

    # Tag extraction reads bytes. Over a network that is slow enough to be worth
    # doing separately, so remote sources are indexed now and described later.
    from .sources.gdrive import GoogleDriveConnector

    if not isinstance(connector, GoogleDriveConnector):
        extract_for_source(source_id, connector)


def connector_for(source_id: UUID):
    """Build the right connector for a source, whatever kind it is."""
    from pathlib import Path

    from .sources.gdrive import GoogleDriveConnector

    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT kind::text, mount_prefix, remote_id FROM sources WHERE id = :id"),
            {"id": str(source_id)},
        ).first()
    if row is None:
        return None

    kind, prefix, remote_id = row
    if kind == "gdrive":
        if not remote_id:
            return None
        return GoogleDriveConnector(remote_id, Path(get_settings().gdrive_key_file))

    root = _root_for_source(source_id)
    return LocalConnector(root) if root else None


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
