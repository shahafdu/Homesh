"""Playlists: the ones you make, and the ones already in the library.

Forty-one .m3u files sit in this library, written years ago by Winamp against
paths that stopped being true when the music moved. Their contents are the one
thing a scanner cannot recreate — somebody chose that order — so importing them
is worth the trouble of matching lines to files that have since been moved,
renamed and copied to a different machine.

Matching is deliberately conservative. A line that cannot be resolved is kept as
a line rather than dropped, because a playlist that quietly loses four tracks is
worse than one that says four tracks are missing.
"""

from __future__ import annotations

import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from .access import Scope, can_read, library_scope
from .db import get_engine
from .security import CurrentUser, require_user

log = logging.getLogger("homesh.playlists")
router = APIRouter(prefix="/api/playlists", tags=["playlists"])


PLAYLIST_EXTS = {"m3u", "m3u8", "pls"}


# ── Requests ────────────────────────────────────────────────────────────────


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    item_ids: list[UUID] = Field(default_factory=list, max_length=2000)


class PlaylistRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AddItems(BaseModel):
    item_ids: list[UUID] = Field(min_length=1, max_length=2000)


class Reorder(BaseModel):
    """Entry ids in the order they should appear.

    Ids rather than positions: a client that has just dragged a row knows which
    rows it has, and sending the whole order avoids the two of us disagreeing
    about what moved.
    """

    entry_ids: list[UUID] = Field(min_length=1, max_length=5000)


# ── Reading ─────────────────────────────────────────────────────────────────


def _owned_or_admin(playlist_id: UUID, user: CurrentUser) -> None:
    with get_engine().connect() as conn:
        owner = conn.execute(
            text("SELECT owner_id FROM playlists WHERE id = :p"), {"p": str(playlist_id)}
        ).first()
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such playlist")
    if not user.is_admin and owner[0] != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "that playlist belongs to somebody else")


@router.get("")
async def list_playlists(user: CurrentUser = Depends(require_user)) -> list[dict]:
    """Every playlist, with how much of it this person can actually play."""
    scope = library_scope(user.id)

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT p.id, p.name, p.owner_id, u.display_name, p.source_path,
                       p.updated_at,
                       count(e.id) AS entries,
                       count(e.item_id) FILTER (WHERE e.item_id IS NOT NULL) AS found,
                       p.source_id
                FROM playlists p
                LEFT JOIN playlist_items e ON e.playlist_id = p.id
                LEFT JOIN users u ON u.id = p.owner_id
                GROUP BY p.id, u.display_name
                ORDER BY lower(p.name)
                """
            )
        ).all()

    return [
        {
            "id": str(r[0]),
            "name": r[1],
            "owner": r[3],
            "mine": r[2] == user.id,
            "imported_from": r[4],
            # Still following the file, or taken over by an edit. The difference
            # decides what a re-import does, so it is not left to be guessed.
            "linked": r[8] is not None,
            "updated_at": r[5].isoformat(),
            "entries": r[6],
            "missing": r[6] - r[7],
            "playable": _playable_count(r[0], scope),
        }
        for r in rows
    ]


def _playable_count(playlist_id: UUID, scope: Scope) -> int:
    if scope.everything:
        with get_engine().connect() as conn:
            return conn.execute(
                text(
                    "SELECT count(*) FROM playlist_items "
                    "WHERE playlist_id = :p AND item_id IS NOT NULL"
                ),
                {"p": str(playlist_id)},
            ).scalar_one()

    return len(_entries(playlist_id, scope, only_playable=True))


def _entries(playlist_id: UUID, scope: Scope, only_playable: bool = False) -> list[dict]:
    """The tracks in order, with what each one is and whether it can be reached."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT e.id, e.position, e.item_id, e.original_ref, e.raw_title,
                       r.filename, s.mount_prefix, r.dir_path, i.duration_ms,
                       max(CASE WHEN m.key = 'title'  THEN m.value END) AS title,
                       max(CASE WHEN m.key = 'artist' THEN m.value END) AS artist,
                       bool_or(r.available) AS available
                FROM playlist_items e
                LEFT JOIN items i ON i.id = e.item_id
                LEFT JOIN replicas r ON r.item_id = e.item_id
                LEFT JOIN sources s ON s.id = r.source_id
                LEFT JOIN item_metadata m ON m.item_id = e.item_id
                WHERE e.playlist_id = :p
                GROUP BY e.id, e.position, e.item_id, e.original_ref, e.raw_title,
                         r.filename, s.mount_prefix, r.dir_path, i.duration_ms
                ORDER BY e.position
                """
            ),
            {"p": str(playlist_id)},
        ).all()

    out = []
    for r in rows:
        path = f"{r[6]}/{r[7]}".rstrip("/") if r[6] else None
        # A track outside this person's scope is not shown as a track. It is
        # still counted in the total, because the playlist genuinely has that
        # many — pretending otherwise would make two people disagree about the
        # length of the same list.
        reachable = bool(r[2]) and (path is not None and can_read(path, scope))
        if only_playable and not reachable:
            continue

        out.append(
            {
                "entry_id": str(r[0]),
                "item_id": str(r[2]) if r[2] and reachable else None,
                # Always the line from the file when there is no match: the point
                # of keeping it is that somebody can see what is missing.
                "filename": r[5] if reachable else None,
                "raw_path": r[3],
                "raw_title": r[4],
                "title": r[9] if reachable else None,
                "artist": r[10] if reachable else None,
                "duration_ms": r[8] if reachable else None,
                "available": bool(r[11]) if reachable else False,
                "missing": not reachable,
            }
        )
    return out


@router.get("/{playlist_id}")
async def get_playlist(playlist_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT p.id, p.name, p.owner_id, u.display_name, p.source_path
                FROM playlists p LEFT JOIN users u ON u.id = p.owner_id
                WHERE p.id = :p
                """
            ),
            {"p": str(playlist_id)},
        ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such playlist")

    entries = _entries(playlist_id, library_scope(user.id))
    return {
        "id": str(row[0]),
        "name": row[1],
        "owner": row[3],
        "mine": row[2] == user.id,
        "imported_from": row[4],
        "entries": entries,
        "missing": sum(1 for e in entries if e["missing"]),
    }


# ── Writing ─────────────────────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_playlist(
    body: PlaylistCreate, user: CurrentUser = Depends(require_user)
) -> dict:
    with get_engine().begin() as conn:
        playlist_id = conn.execute(
            text("INSERT INTO playlists (name, owner_id) VALUES (:n, :o) RETURNING id"),
            {"n": body.name.strip(), "o": str(user.id)},
        ).scalar_one()

        for position, item_id in enumerate(body.item_ids):
            conn.execute(
                text(
                    """
                    INSERT INTO playlist_items (playlist_id, position, item_id)
                    VALUES (:p, :pos, :i)
                    """
                ),
                {"p": str(playlist_id), "pos": position, "i": str(item_id)},
            )

    return {"id": str(playlist_id), "name": body.name.strip()}


@router.put("/{playlist_id}")
async def rename_playlist(
    playlist_id: UUID, body: PlaylistRename, user: CurrentUser = Depends(require_user)
) -> dict:
    _owned_or_admin(playlist_id, user)
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE playlists SET name = :n, updated_at = now() WHERE id = :p"),
            {"n": body.name.strip(), "p": str(playlist_id)},
        )
        _detach_from_file(conn, playlist_id)
    return {"id": str(playlist_id), "name": body.name.strip()}


@router.delete("/{playlist_id}")
async def delete_playlist(playlist_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    _owned_or_admin(playlist_id, user)
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM playlists WHERE id = :p"), {"p": str(playlist_id)})
    return {"ok": True}


@router.post("/{playlist_id}/items")
async def add_items(
    playlist_id: UUID, body: AddItems, user: CurrentUser = Depends(require_user)
) -> dict:
    """Append tracks. Duplicates are allowed — a list may want a song twice."""
    _owned_or_admin(playlist_id, user)

    with get_engine().begin() as conn:
        next_position = conn.execute(
            text(
                "SELECT coalesce(max(position), -1) + 1 "
                "FROM playlist_items WHERE playlist_id = :p"
            ),
            {"p": str(playlist_id)},
        ).scalar_one()

        for offset, item_id in enumerate(body.item_ids):
            conn.execute(
                text(
                    """
                    INSERT INTO playlist_items (playlist_id, position, item_id)
                    VALUES (:p, :pos, :i)
                    """
                ),
                {"p": str(playlist_id), "pos": next_position + offset, "i": str(item_id)},
            )
        _detach_from_file(conn, playlist_id)

    return {"added": len(body.item_ids)}


@router.delete("/{playlist_id}/items/{entry_id}")
async def remove_item(
    playlist_id: UUID, entry_id: UUID, user: CurrentUser = Depends(require_user)
) -> dict:
    _owned_or_admin(playlist_id, user)
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM playlist_items WHERE id = :e AND playlist_id = :p"),
            {"e": str(entry_id), "p": str(playlist_id)},
        )
        # Positions are closed up rather than left with a hole: a list that has
        # been edited for years would otherwise carry gaps that only ever grow.
        _renumber(conn, playlist_id)
        _detach_from_file(conn, playlist_id)
    return {"ok": True}


@router.put("/{playlist_id}/order")
async def reorder(
    playlist_id: UUID, body: Reorder, user: CurrentUser = Depends(require_user)
) -> dict:
    _owned_or_admin(playlist_id, user)

    with get_engine().begin() as conn:
        known = {
            r[0]
            for r in conn.execute(
                text("SELECT id FROM playlist_items WHERE playlist_id = :p"),
                {"p": str(playlist_id)},
            ).all()
        }
        if {UUID(str(e)) for e in body.entry_ids} != known:
            # Rejecting a partial order rather than applying it: a client working
            # from a stale copy would otherwise silently drop whatever it had not
            # heard about.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "that ordering does not match the playlist — reload and try again",
            )

        for position, entry_id in enumerate(body.entry_ids):
            conn.execute(
                text("UPDATE playlist_items SET position = :pos WHERE id = :e"),
                {"pos": position, "e": str(entry_id)},
            )
        _detach_from_file(conn, playlist_id)

    return {"ok": True}


def _detach_from_file(conn, playlist_id: UUID) -> None:
    """Cut an edited playlist loose from the file it was imported from.

    Two things must not happen. The .m3u must not be rewritten — this server
    reads your library and never writes to it, which is the rule the rest of the
    design rests on. And the edit must not be lost, which is exactly what would
    happen at the next import, since importing replaces a list's contents
    wholesale.

    So the first edit takes ownership. The file stays as it was, the edited list
    becomes yours, and a later import of that file makes a fresh playlist beside
    it rather than overwriting your work.
    """
    conn.execute(
        text(
            """
            UPDATE playlists
            SET source_id = NULL, updated_at = now()
            WHERE id = :p AND source_id IS NOT NULL
            """
        ),
        {"p": str(playlist_id)},
    )


def _renumber(conn, playlist_id: UUID) -> None:
    conn.execute(
        text(
            """
            WITH ordered AS (
                SELECT id, row_number() OVER (ORDER BY position) - 1 AS seat
                FROM playlist_items WHERE playlist_id = :p
            )
            UPDATE playlist_items e SET position = o.seat
            FROM ordered o WHERE o.id = e.id
            """
        ),
        {"p": str(playlist_id)},
    )


@router.post("/import/{source_id}", status_code=status.HTTP_202_ACCEPTED)
async def import_source(source_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    """Turn the playlist files in a source into playlists."""
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")

    from .library import connector_for

    connector = connector_for(source_id)
    if connector is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that source has no connector")

    return import_from_source(source_id, connector, owner_id=user.id)


# ── Importing what is already there ─────────────────────────────────────────


_EXTINF = re.compile(r"^#EXTINF:\s*(-?\d+)\s*,\s*(.*)$", re.IGNORECASE)


def parse_playlist(text_body: str, ext: str) -> list[tuple[str, str | None]]:
    """(path, title) per entry, for m3u/m3u8 and pls alike.

    Both formats are lists of paths with optional labels; the differences are not
    interesting enough to justify two parsers.
    """
    entries: list[tuple[str, str | None]] = []

    if ext == "pls":
        titles: dict[str, str] = {}
        files: dict[str, str] = {}
        for line in text_body.splitlines():
            key, _, value = line.partition("=")
            key = key.strip().lower()
            if key.startswith("file"):
                files[key[4:]] = value.strip()
            elif key.startswith("title"):
                titles[key[5:]] = value.strip()
        for index in sorted(files, key=lambda k: int(k) if k.isdigit() else 0):
            entries.append((files[index], titles.get(index)))
        return entries

    pending_title: str | None = None
    for line in text_body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            found = _EXTINF.match(line)
            if found:
                pending_title = found.group(2).strip() or None
            continue
        entries.append((line, pending_title))
        pending_title = None
    return entries


def _basename(raw: str) -> str:
    """The filename from a path written on some other machine.

    Winamp wrote Windows paths; the library now lives on Drive and in Linux
    containers. Only the last component survives that journey intact.
    """
    return raw.replace("\\", "/").rstrip("/").rpartition("/")[2].strip()


def resolve_entry(conn, raw: str, source_id: UUID, near: str) -> UUID | None:
    """Find the item a playlist line refers to, if it is still in the library.

    Tried in order of confidence: the same folder as the playlist, then anywhere
    in the same source, then anywhere at all. Ambiguity is resolved by preferring
    the closest match rather than by guessing.
    """
    name = _basename(raw)
    if not name:
        return None

    # Literal statements rather than a clause assembled into a string: no SQL
    # here is ever built by interpolation, and three short queries read better
    # than one that has to be reconstructed in the reader's head.
    attempts = (
        # Same folder as the playlist. Winamp lists are usually written beside
        # the music they list, so this is both the likeliest and the safest.
        """
        SELECT r.item_id FROM replicas r
        WHERE lower(r.filename) = lower(:name)
          AND r.source_id = :sid AND r.dir_path = :near
        ORDER BY r.available DESC LIMIT 1
        """,
        # Anywhere in the same source: the folder moved, the library did not.
        """
        SELECT r.item_id FROM replicas r
        WHERE lower(r.filename) = lower(:name) AND r.source_id = :sid
        ORDER BY r.available DESC LIMIT 1
        """,
        # Anywhere at all. Last because a name like "track01.mp3" matches in
        # several places and the nearest match is the better guess.
        """
        SELECT r.item_id FROM replicas r
        WHERE lower(r.filename) = lower(:name)
        ORDER BY r.available DESC LIMIT 1
        """,
    )

    for statement in attempts:
        row = conn.execute(
            text(statement),
            {"name": name, "sid": str(source_id), "near": near},
        ).first()
        if row:
            return row[0]

    # Nothing matched exactly. The commonest reason in this library is not that
    # the file is gone but that its name was mangled on the way into the
    # playlist: "Più che puoi" was written as "Pi? che puoi" by something that
    # could not represent the character. Each ? stands for exactly one lost
    # letter, which is what LIKE's _ means, so the repair is precise rather than
    # a guess.
    if "?" in name:
        # % and _ are LIKE's own wildcards, so a filename containing them has to
        # say it means them literally before ? is turned into one.
        pattern = (
            name.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
            .replace("?", "_")
        )
        row = conn.execute(
            text(
                """
                SELECT r.item_id FROM replicas r
                WHERE r.filename ILIKE :pattern ESCAPE '\\'
                ORDER BY (r.source_id = :sid) DESC, r.available DESC
                LIMIT 1
                """
            ),
            {"pattern": pattern, "sid": str(source_id)},
        ).first()
        if row:
            return row[0]

    # Last resort: trigram similarity, which is why pg_trgm was installed in the
    # first migration. The threshold is high on purpose — a playlist quietly
    # pointing at the wrong song is worse than one honestly missing a track.
    row = conn.execute(
        text(
            """
            SELECT r.item_id FROM replicas r
            WHERE similarity(lower(r.filename), lower(:name)) > 0.8
            ORDER BY similarity(lower(r.filename), lower(:name)) DESC,
                     (r.source_id = :sid) DESC, r.available DESC
            LIMIT 1
            """
        ),
        {"name": name, "sid": str(source_id)},
    ).first()
    return row[0] if row else None


def import_from_source(source_id: UUID, connector, owner_id: UUID | None = None) -> dict:
    """Read every playlist file in a source and turn it into a playlist.

    Re-importing updates in place rather than making a second copy: the unique
    index on (source, path) is what makes that safe, and a nightly scan would
    otherwise breed duplicates.
    """
    engine = get_engine()
    with engine.connect() as conn:
        files = conn.execute(
            text(
                """
                SELECT r.dir_path, r.filename, r.ext
                FROM replicas r
                WHERE r.source_id = :sid AND r.available
                  AND lower(r.ext) IN ('m3u', 'm3u8', 'pls')
                ORDER BY r.dir_path, r.filename
                """
            ),
            {"sid": str(source_id)},
        ).all()

    imported = matched = missing = 0

    for dir_path, filename, ext in files:
        rel = f"{dir_path}/{filename}" if dir_path else filename
        try:
            raw = b"".join(connector.open_range(rel))
        except Exception as exc:  # noqa: BLE001 - one unreadable file is not a failed import
            log.warning("could not read playlist %s: %s", rel, exc)
            continue

        # Winamp wrote these in whatever the machine's code page was. UTF-8
        # first, then Windows-1255 for the Hebrew ones, then bytes we keep rather
        # than lose — a mangled title is still better than a skipped playlist.
        for encoding in ("utf-8-sig", "cp1255", "latin-1"):
            try:
                body = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            body = raw.decode("utf-8", errors="replace")

        entries = parse_playlist(body, (ext or "").lower())
        if not entries:
            continue

        name = filename.rsplit(".", 1)[0]

        with engine.begin() as conn:
            playlist_id = conn.execute(
                text(
                    """
                    INSERT INTO playlists (name, owner_id, source_id, source_path)
                    VALUES (:n, :o, :sid, :path)
                    -- The predicate is repeated because the index is partial:
                    -- Postgres will not use one for a conflict clause unless the
                    -- clause proves it is talking about the same rows.
                    ON CONFLICT (source_id, source_path)
                        WHERE source_id IS NOT NULL AND source_path IS NOT NULL
                    DO UPDATE
                    SET name = EXCLUDED.name, updated_at = now()
                    RETURNING id
                    """
                ),
                {"n": name, "o": str(owner_id) if owner_id else None,
                 "sid": str(source_id), "path": rel},
            ).scalar_one()

            # Replaced wholesale: the file is the truth for an imported list, and
            # merging would mean guessing which side an edit came from.
            conn.execute(
                text("DELETE FROM playlist_items WHERE playlist_id = :p"),
                {"p": str(playlist_id)},
            )

            for position, (raw_path, title) in enumerate(entries):
                item_id = resolve_entry(conn, raw_path, source_id, dir_path)
                if item_id:
                    matched += 1
                else:
                    missing += 1
                conn.execute(
                    text(
                        """
                        INSERT INTO playlist_items
                            (playlist_id, position, item_id, original_ref, raw_title)
                        VALUES (:p, :pos, :i, :raw, :title)
                        """
                    ),
                    {
                        "p": str(playlist_id),
                        "pos": position,
                        "i": str(item_id) if item_id else None,
                        "raw": raw_path,
                        "title": title,
                    },
                )

        imported += 1

    log.info("imported %d playlists: %d tracks matched, %d not found",
             imported, matched, missing)
    return {"playlists": imported, "matched": matched, "missing": missing}
