"""Backups of the database, and putting one back.

The media is not backed up and must not be: it is the user's own files on the
user's own disks, it is terabytes, and copying it somewhere else is a different
job with a different answer. What is backed up is everything the server knows
*about* those files, which is the part that exists nowhere else — accounts and
their passkeys, who may see which folder, playlists somebody built by hand,
where each person had got to in each film, and a catalog of 141,818 files whose
tags and durations took hours of reading to work out.

It exists now because the AI work needs it (see CLAUDE.md): a model that can
write tags and edit playlists is a model that can be wrong at scale, and
"restore from within the app" is the thing that makes giving it those powers
reasonable rather than reckless.

**Why not pg_dump.** It is the obvious answer and it was rejected for one
reason: this image ships for amd64 and arm64, Debian's own client is a major
version behind the server, and closing that gap means adding PostgreSQL's apt
repository to the build — a third-party key and repository in the supply chain
of a server that holds the household's passkeys. What is dumped here instead is
data only, in Postgres's own COPY text format, because the *schema* already has
a source of truth that is tracked, reviewed and idempotent: the migrations. A
restore runs those and then puts the rows back.

The trade is that this is our format rather than the one every DBA knows, so it
is kept plain: a header anyone can read, then unmodified COPY text, table by
table. Recovering one by hand needs psql and nothing else.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import text

from . import crypt
from .config import get_settings
from .db import get_engine
from .security import CurrentUser, require_user

log = logging.getLogger("homesh.backups")

# The format's own version, not the schema's. Bumped only if the framing below
# changes, so a future reader can tell what it is holding.
FORMAT = 1

MAGIC = "homesh-backup"

# What COPY writes at the end of a table's rows.
TERMINATOR = chr(92) + '.'

# Tracked by the migration runner and restored by running the migrations, not by
# copying rows: a backup taken at schema 023 and restored into a server running
# 025 must end up at 025, and re-inserting the old row would say otherwise.
SKIP = {"schema_migrations"}

# What a backup file is allowed to be called. Anything reaching the filesystem
# from a request is matched against this first — a name is a name, never a path.
NAME = re.compile(r"^homesh-\d{8}-\d{6}(-\d+)?\.sql\.gz$")


@dataclass
class Backup:
    name: str
    taken_at: datetime
    size_bytes: int
    rows: int


def backup_dir() -> Path:
    """Where backups live.

    Beside the cache rather than inside it: the cache is disposable by
    definition and something will eventually be written that empties it.
    """
    settings = get_settings()
    root = Path(os.environ.get("BACKUP_DIR") or Path(settings.cache_dir).parent / "backups")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ordered_tables(conn) -> list[str]:
    """Every table, parents before children.

    Worked out from the foreign keys rather than written down, because a list
    written down is a list that goes stale the next time somebody adds a table —
    and the failure mode of a stale list is a backup silently missing something.
    """
    tables = set(
        conn.execute(
            text(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                """
            )
        ).scalars()
    ) - SKIP

    edges = conn.execute(
        text(
            """
            SELECT c.conrelid::regclass::text AS child,
                   c.confrelid::regclass::text AS parent
            FROM pg_constraint c
            WHERE c.contype = 'f'
              AND c.connamespace = 'public'::regnamespace
            """
        )
    ).all()

    parents: dict[str, set[str]] = {t: set() for t in tables}
    for child, parent in edges:
        # A table referring to itself cannot be ordered against itself, and does
        # not need to be: Postgres checks a self-reference per row, so the rows
        # come back in the order they were written.
        if child in parents and parent in tables and child != parent:
            parents[child].add(parent)

    ordered: list[str] = []
    remaining = dict(parents)
    while remaining:
        free = sorted(t for t, needs in remaining.items() if not (needs - set(ordered)))
        if not free:
            # A cycle. Nothing here has one, and if something grows one it is
            # better to say so than to write a backup that cannot be restored.
            raise RuntimeError(
                f"cannot order these tables by their references: {sorted(remaining)}"
            )
        ordered.extend(free)
        for t in free:
            remaining.pop(t)
    return ordered


def make_backup() -> Backup:
    """Write every row of every table to a new file.

    One connection and one snapshot: a backup taken across several transactions
    could hold a playlist whose tracks were written after it, which is the sort
    of thing that only shows up on the day it matters.
    """
    taken = datetime.now(UTC)
    # Two in the same second is not hypothetical: restoring takes one first, and
    # a name that collided would overwrite the very file being restored. Found
    # by the test that checks the safety copy exists afterwards -- it did not,
    # because it had replaced its own source.
    name, target = _free_name(taken)
    rows = 0

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        tables = _ordered_tables(conn)
        raw = conn.connection.driver_connection

        # Written beside the real name and moved into place at the end, so a
        # backup interrupted half way is never mistaken for one that finished.
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".partial-", delete=False
        ) as handle:
            partial = Path(handle.name)
        try:
            with gzip.open(partial, "wt", encoding="utf-8", newline="\n") as out:
                out.write(f"-- {MAGIC} {FORMAT}\n")
                out.write(
                    "-- "
                    + json.dumps(
                        {
                            "taken_at": taken.isoformat(),
                            "schema_version": _schema_version(conn),
                            "tables": tables,
                        }
                    )
                    + "\n"
                )
                for table in tables:
                    out.write(f"\n-- table: {table}\n")
                    with raw.cursor().copy(
                        f'COPY public."{table}" TO STDOUT'  # noqa: S608 - names from pg_tables
                    ) as copy:
                        for chunk in copy:
                            block = bytes(chunk).decode("utf-8")
                            rows += block.count("\n")
                            out.write(block)
                    out.write(TERMINATOR + "\n")
            partial.replace(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    log.info("backup %s written: %d rows, %.1f MB", name, rows, target.stat().st_size / 1e6)
    return Backup(
        name=name, taken_at=taken, size_bytes=target.stat().st_size, rows=rows
    )


def _free_name(taken: datetime) -> tuple[str, Path]:
    """A name nothing else is using, to the second and then some."""
    root = backup_dir()
    stem = f"homesh-{taken:%Y%m%d-%H%M%S}"
    for suffix in ("", *(f"-{n}" for n in range(2, 100))):
        candidate = root / f"{stem}{suffix}.sql.gz"
        if not candidate.exists():
            return candidate.name, candidate
    raise RuntimeError("a hundred backups in one second is not a backup problem")


def _schema_version(conn) -> str:
    return conn.execute(text("SELECT max(name) FROM schema_migrations")).scalar() or ""


def _read_header(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        magic = source.readline().strip()
        if not magic.startswith(f"-- {MAGIC} "):
            raise ValueError(f"{path.name} is not a Homesh backup")
        header = json.loads(source.readline().lstrip("- ").strip())
    return header


def list_backups() -> list[Backup]:
    """Newest first. A file that cannot be read is left out rather than raising:
    the list is how somebody finds a good backup, and one bad file must not be
    what stops them."""
    found: list[Backup] = []
    for path in backup_dir().glob("homesh-*.sql.gz"):
        try:
            header = _read_header(path)
            found.append(
                Backup(
                    name=path.name,
                    taken_at=datetime.fromisoformat(header["taken_at"]),
                    size_bytes=path.stat().st_size,
                    rows=0,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ignoring unreadable backup %s: %s", path.name, exc)
    return sorted(found, key=lambda b: b.taken_at, reverse=True)


def resolve(name: str) -> Path:
    """The file a request is asking for, or an error.

    The name is matched against a pattern rather than sanitised. Sanitising is
    where path traversal gets in; a whitelist of shapes cannot express `..` at
    all.
    """
    if not NAME.match(name):
        raise ValueError("not a backup name")
    path = backup_dir() / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


# Kept: every day for a week, then a fortnight back, then a month back.
#
# The shape matters more than the numbers. A week of dailies catches "something
# went wrong yesterday"; the older two catch "something went wrong a while ago
# and nobody noticed", which is the failure a daily-only scheme quietly loses.
DAILY_DAYS = 7
LANDMARKS = (timedelta(days=14), timedelta(days=30))
# How far from a landmark a backup may be and still count as that landmark.
NEAR = timedelta(days=3)


def prune(now: datetime | None = None) -> list[str]:
    """Delete what is no longer worth keeping. Returns what went."""
    now = now or datetime.now(UTC)
    kept: set[str] = set()
    backups = list_backups()

    for backup in backups:
        if now - backup.taken_at <= timedelta(days=DAILY_DAYS):
            kept.add(backup.name)

    for landmark in LANDMARKS:
        near = [b for b in backups if abs((now - b.taken_at) - landmark) <= NEAR]
        if near:
            # The one closest to the mark.
            kept.add(min(near, key=lambda b: abs((now - b.taken_at) - landmark)).name)

    # Never leave nothing behind. If everything is old enough to go, the newest
    # stays: a pruning that empties the shelf is worse than a stale backup.
    if backups and not kept:
        kept.add(backups[0].name)

    removed = []
    for backup in backups:
        if backup.name not in kept:
            (backup_dir() / backup.name).unlink(missing_ok=True)
            removed.append(backup.name)
    if removed:
        log.info("pruned %d old backup(s)", len(removed))
    return removed


def restore(name: str) -> dict:
    """Put a backup back, replacing what is there now.

    Everything happens in one transaction: either the whole catalog is the
    backup's or it is untouched. A half-restored database is the one outcome
    worse than the problem being restored from.

    The schema is *not* restored. It is whatever the migrations have made it,
    which is the version the running code expects — so a backup can be put into
    a newer server, but not into an older one, and the check below refuses the
    latter rather than failing part way through with a missing column.
    """
    path = resolve(name)
    header = _read_header(path)

    engine = get_engine()
    with engine.connect() as conn:
        here = _schema_version(conn)
    there = str(header.get("schema_version") or "")
    if there > here:
        raise ValueError(
            f"this backup was taken at schema {there} and this server is at {here}. "
            "Update the server before restoring it."
        )

    tables: list[str] = list(header["tables"])
    counts: dict[str, int] = {}

    with engine.begin() as conn:
        raw = conn.connection.driver_connection
        # Children first, so nothing is deleted out from under a reference.
        for table in reversed(tables):
            conn.execute(text(f'DELETE FROM public."{table}"'))  # noqa: S608 - from the header

        # One pass, one line iterator, shared between the two loops: the outer
        # one finds a table's header and the inner one feeds that table's rows
        # until the terminator. Driving psycopg's copy context by hand instead
        # -- entering it on one line and leaving it on another -- is what it
        # looked like at first and it does not work: the copy is closed by the
        # generator being dropped, and the next write lands with no COPY in
        # progress.
        with gzip.open(path, "rt", encoding="utf-8") as source, raw.cursor() as cursor:
            source.readline()
            source.readline()
            lines = iter(source)
            for line in lines:
                if not line.startswith("-- table: "):
                    continue
                table = line[len("-- table: "):].strip()
                if table not in tables:
                    raise ValueError(f"{name} contains an unexpected table: {table}")
                counts[table] = 0
                with cursor.copy(
                    f'COPY public."{table}" FROM STDIN'  # noqa: S608 - from the header
                ) as copy:
                    for row in lines:
                        if row.startswith(TERMINATOR):
                            break
                        copy.write(row)
                        counts[table] += 1

    total = sum(counts.values())
    log.warning("restored %s: %d rows across %d tables", name, total, len(counts))
    return {"restored": name, "rows": total, "tables": counts}


# ── A copy somewhere this house is not ──────────────────────────────────────
#
# A backup on the same disk as the database it was taken from survives a
# mistake and nothing else. The fire, the theft, the drive failure — the whole
# category this is supposed to cover — take both copies together.
#
# The direction of travel is the security argument, and it is the same one the
# rest of the system is built on: **the house pushes, and nothing out there
# pulls.** There is no server of ours online to break into. There is a folder in
# Drive with files in it, and the files are encrypted here, before they leave,
# with a key that never goes with them. Whoever holds that folder holds
# ciphertext and a filename, and no route back to anything.
#
# Encryption is not decoration on this one. A Homesh backup is not your media —
# it is the catalog, and the catalog names the rooms in the house, the screens
# in them, the folders on the disks and the accounts that reach them. In the
# clear, off-site, that is a map of somebody's home.
#
# What this does **not** defend against, stated rather than implied: the key
# that writes to that folder lives on the machine being backed up, so whoever
# takes the machine can also delete what was written from it. Drive's own trash
# is all that stands behind that. Off-site copies protect against losing the
# machine; they do not protect against somebody who already has it.


def offsite_ready() -> tuple[bool, str]:
    """Whether a copy can be sent, and why not when it cannot."""
    settings = get_settings()
    try:
        crypt.key_bytes(settings.backup_key)
    except crypt.KeyError_ as exc:
        return False, str(exc)
    if not Path(settings.gdrive_key_file).is_file():
        return False, "no Drive credential, so there is nowhere to send a copy"
    return True, ""


def _encrypted(path: Path) -> Path:
    """Encrypt a backup beside itself, ready to be sent."""
    key = crypt.key_bytes(get_settings().backup_key)
    sealed = path.with_suffix(path.suffix + ".enc")
    crypt.encrypt(path, sealed, key)
    return sealed


def send_offsite(name: str) -> dict:
    """Encrypt one backup and push it to Drive."""
    from .sources import gdrive

    settings = get_settings()
    path = resolve(name)
    key_file = Path(settings.gdrive_key_file)

    folder = gdrive.backup_folder_id(key_file, settings.backup_folder)
    sealed = _encrypted(path)
    try:
        gdrive.upload(key_file, folder, sealed.name, sealed)
        size = sealed.stat().st_size
    finally:
        # The encrypted copy is a courier, not a second backup. Keeping it would
        # double what the disk holds for no gain: it can be made again from the
        # plain one in seconds.
        sealed.unlink(missing_ok=True)

    log.info("sent %s off-site (%.1f MB encrypted)", sealed.name, size / 1e6)
    return {"sent": sealed.name, "size_bytes": size}


def offsite_index() -> list[dict]:
    """What is being kept off-site."""
    from .sources import gdrive

    settings = get_settings()
    key_file = Path(settings.gdrive_key_file)
    folder = gdrive.backup_folder_id(key_file, settings.backup_folder)
    return [
        {
            "name": f["name"],
            "id": f["id"],
            "size_bytes": int(f.get("size") or 0),
            "taken_at": f.get("modifiedTime"),
        }
        for f in gdrive.list_backups(key_file, folder)
    ]


def bring_back(file_id: str, name: str) -> str:
    """Fetch one from Drive, decrypt it, and put it on the local shelf.

    Deliberately two steps: this brings the file back and stops. Restoring it is
    the same button as for any other backup, with the same confirmation, because
    "restore" should mean one thing regardless of where the file came from.
    """
    from .sources import gdrive

    settings = get_settings()
    key = crypt.key_bytes(settings.backup_key)
    plain_name = name[: -len(".enc")] if name.endswith(".enc") else name
    if not NAME.match(plain_name):
        raise ValueError("that is not a Homesh backup")

    with tempfile.NamedTemporaryFile(
        dir=backup_dir(), prefix=".incoming-", delete=False
    ) as handle:
        sealed = Path(handle.name)
    try:
        gdrive.fetch(Path(settings.gdrive_key_file), file_id, sealed)
        crypt.decrypt(sealed, backup_dir() / plain_name, key)
    finally:
        sealed.unlink(missing_ok=True)

    log.info("brought %s back from off-site storage", plain_name)
    return plain_name


# ── The interface ───────────────────────────────────────────────────────────
#
# Administrators only, all of it. Listing says what exists; taking one is
# something anybody prudent does before a big change; restoring replaces the
# catalog and is the reason this is not open to everybody in the house.
#
# The AI reaches the server through this same API as whoever is asking it
# (CLAUDE.md), so these endpoints are what stops a model restoring a database:
# it cannot act beyond the asking user's own access, and a request from someone
# who is not an administrator is refused here rather than by a prompt.

router = APIRouter(prefix="/api/backups", tags=["backups"])


def _require_admin(user: CurrentUser) -> None:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")


def _as_json(backup: Backup) -> dict:
    return {
        "name": backup.name,
        "taken_at": backup.taken_at.isoformat(),
        "size_bytes": backup.size_bytes,
    }


@router.get("")
async def index(user: CurrentUser = Depends(require_user)) -> dict:
    """What is on the shelf, newest first."""
    _require_admin(user)
    return {"backups": [_as_json(b) for b in list_backups()]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def take_one(user: CurrentUser = Depends(require_user)) -> dict:
    """Back up now, and send a copy away if there is anywhere to send it.

    On a thread: it reads every row in the database, and the event loop is what
    is feeding whatever is playing in the house.
    """
    _require_admin(user)
    made = await asyncio.to_thread(make_backup)
    await asyncio.to_thread(prune)

    sent, why = offsite_ready()
    body = _as_json(made)
    if not sent:
        # Not an error. A backup that exists only here is worth having; it is
        # just worth less, and saying so is better than failing the request.
        body["offsite"] = None
        body["offsite_note"] = why
        return body

    try:
        await asyncio.to_thread(send_offsite, made.name)
        body["offsite"] = "sent"
    except Exception as exc:  # noqa: BLE001 - the local backup succeeded regardless
        log.warning("could not send %s off-site: %s", made.name, exc)
        body["offsite"] = None
        body["offsite_note"] = str(exc)
    return body


@router.get("/offsite")
async def offsite(user: CurrentUser = Depends(require_user)) -> dict:
    """What is being kept somewhere this house is not."""
    _require_admin(user)
    ready, why = offsite_ready()
    if not ready:
        return {"ready": False, "why": why, "backups": []}
    try:
        return {"ready": True, "why": "", "backups": await asyncio.to_thread(offsite_index)}
    except Exception as exc:  # noqa: BLE001 - a folder not shared yet is the common case
        return {"ready": False, "why": str(exc), "backups": []}


@router.post("/offsite/{file_id}")
async def retrieve(
    file_id: str, name: str, user: CurrentUser = Depends(require_user)
) -> dict:
    """Bring one back down and decrypt it onto the shelf.

    It stops there. Restoring is the same button as for any other backup, with
    the same confirmation — "restore" should mean one thing regardless of where
    the file came from.
    """
    _require_admin(user)
    try:
        landed = await asyncio.to_thread(bring_back, file_id, name)
    except (ValueError, crypt.KeyError_) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"name": landed}


@router.get("/{name}")
async def download(name: str, user: CurrentUser = Depends(require_user)) -> FileResponse:
    """Take a copy off the machine.

    The one thing that makes a backup worth having is a copy somewhere the
    original disk is not, and nothing here can put one there — so at least it
    can be fetched.
    """
    _require_admin(user)
    try:
        path = resolve(name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such backup") from exc
    return FileResponse(path, media_type="application/gzip", filename=name)


@router.post("/{name}/restore")
async def put_back(name: str, user: CurrentUser = Depends(require_user)) -> dict:
    """Replace the catalog with this backup.

    A backup is taken first, always. Restoring the wrong one is a mistake
    somebody makes at exactly the moment they are least able to absorb another
    one, and the state being replaced is the only copy of itself.
    """
    _require_admin(user)
    try:
        resolve(name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such backup") from exc

    safety = await asyncio.to_thread(make_backup)
    try:
        done = await asyncio.to_thread(restore, name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {**done, "previous_state_saved_as": safety.name}


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(name: str, user: CurrentUser = Depends(require_user)) -> Response:
    _require_admin(user)
    try:
        resolve(name).unlink()
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such backup") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
