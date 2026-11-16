"""Keeping the catalog current without being asked.

Scanning was manual, which meant the catalog was only as fresh as the last time
somebody remembered — and a folder that had never been scanned at all looked
exactly like a folder that was empty. A library nobody has to maintain by hand
is the whole point of indexing it.

Daily rather than continuous: Drive charges API quota per listing, the mini PC
has a low-power CPU, and photos added to a shared folder are not urgent.
Anything that *is* urgent has the manual button next to it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from .config import get_settings
from .db import get_engine

log = logging.getLogger("homesh.upkeep")

# How long after startup before the first sweep. Long enough that a restart
# during an evening of use does not immediately spend the machine on scanning.
FIRST_SWEEP_DELAY = timedelta(minutes=5)

# Between sources within one sweep, so nine thousand Drive files do not arrive
# as one burst of API calls.
BETWEEN_SOURCES = timedelta(seconds=30)

# Files fingerprinted per sweep.
#
# Each one is read from end to end, so this is a disk-time budget rather than a
# count: two thousand of the shortlist here is a few gigabytes, which a scheduled
# sweep can afford and a house watching a film will not notice.
FINGERPRINTS_PER_SWEEP = 2000

# How often the database is copied. Hourly: the standby restores from these, so
# this is how far behind the PC it can be. Pruning keeps one a day for a week
# and one a week for five, so taking them hourly costs nothing on the shelf.
BACKUP_EVERY = timedelta(hours=1)


def next_backup_in(backups: list, now: datetime) -> float:
    """Seconds until the next backup is due, between a minute and an hour.

    The loop used to sleep a flat hour between looks. After a restart that put
    its looks out of step with the backups: the first look, five minutes in,
    found one twenty minutes old and rightly skipped -- and the next was an
    hour later, so the gap was 83 minutes the first time it was measured and
    could approach two hours. That is the standby falling that far behind.
    Waking when the next one is due keeps it hourly whenever the server was
    restarted.

    The copy taken before a restore does not count as the latest backup. It is
    the state being thrown away, so the state that replaced it would otherwise
    wait an hour for its first backup.
    """
    from .backups import BEFORE_RESTORE

    regular = [b for b in backups if BEFORE_RESTORE not in b.name]
    if not regular:
        return 60.0
    due = (regular[0].taken_at + BACKUP_EVERY - now).total_seconds()
    return min(3600.0, max(60.0, due))


def _due(interval: timedelta) -> list[tuple[UUID, str]]:
    """Sources that have not been scanned within the interval.

    A source that has never been scanned is always due — that is the case that
    went unnoticed for weeks.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, name FROM sources
                WHERE scan_state IS DISTINCT FROM 'running'
                  AND (
                    scan_ended_at IS NULL
                    OR scan_ended_at < now() - CAST(:window AS interval)
                  )
                ORDER BY scan_ended_at NULLS FIRST
                """
            ),
            {"window": f"{int(interval.total_seconds())} seconds"},
        ).all()
    return [(r[0], r[1]) for r in rows]


# One scheduled scan at a time. The daily sweep and the loop that answers the
# PC's change notices both scan, and without this the sweep -- which picks its
# list at the start and then works through it for minutes -- could reach a
# folder the other loop was still scanning, and run a second scan of it beside
# the first.
_SCAN_LOCK = asyncio.Lock()


async def _scan_one(source_id: UUID, name: str) -> None:
    """Run one source's scan off the event loop. Callers hold ``_SCAN_LOCK``.

    The scanner is synchronous and network-bound; on a thread it cannot block
    playback, which is the one thing on this server nobody will forgive.
    """
    # The same two passes the manual button runs: index first so the folder is
    # browsable, then read tags. Calling the scanner alone would leave every new
    # file with no artist or album — which is precisely the state this is meant
    # to stop the library drifting into.
    from .library import _scan_then_extract, connector_for

    connector = connector_for(source_id)
    if connector is None:
        log.warning("skipping %s: no connector", name)
        return

    log.info("scheduled scan starting: %s", name)
    try:
        await asyncio.to_thread(_scan_then_extract, source_id, connector)
        log.info("scheduled scan of %s finished", name)
    except Exception:  # noqa: BLE001
        # One unreachable source must never stop the loop that scans the others,
        # nor take the server down with it.
        log.exception("scheduled scan of %s failed", name)


_READY = """
    SELECT id, name FROM sources
    WHERE scan_requested_at IS NOT NULL
      AND scan_state IS DISTINCT FROM 'running'
      AND scan_requested_at < now() - interval '2 minutes'
      AND (scan_ended_at IS NULL
           OR scan_ended_at < now() - interval '15 minutes')
    ORDER BY scan_requested_at
    LIMIT 1
    FOR UPDATE SKIP LOCKED
"""


def _next_requested() -> tuple[UUID, str] | None:
    """The folder whose change notice has settled and which may be scanned now.

    Two intervals are the debounce: a big copy raises events for minutes and
    must make one scan, and a folder that changes all day is scanned at most
    every 15 minutes.
    """
    with get_engine().connect() as conn:
        row = conn.execute(text(_READY)).first()
    return (row[0], row[1]) if row else None


def _claim_requested() -> tuple[UUID, str] | None:
    """Take the next settled request, clearing it in the same transaction.

    Cleared before the scan rather than after: a change made while the scan
    runs asks again and is not lost.
    """
    with get_engine().begin() as conn:
        row = conn.execute(text(_READY)).first()
        if row is None:
            return None
        conn.execute(
            text("UPDATE sources SET scan_requested_at = NULL WHERE id = :i"),
            {"i": str(row[0])},
        )
    return (row[0], row[1])


async def scan_requested_once() -> bool:
    """Scan one requested folder if any is ready. Returns whether it did."""
    from .standby import is_standby

    if is_standby():
        return False
    async with _SCAN_LOCK:
        # Looked for under the lock, so a folder the sweep has just scanned is
        # seen as just scanned and waits out its fifteen minutes.
        found = await asyncio.to_thread(_claim_requested)
        if found is None:
            return False
        await _scan_one(*found)
    return True


async def requested_scans_forever() -> None:
    """Every minute, scan a folder the PC's watcher reported as changed."""
    while True:
        try:
            await scan_requested_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the next minute tries again
            log.exception("requested scan failed")
        await asyncio.sleep(60)


# -- Drive changes ---------------------------------------------------------------

DRIVE_CHANGES_EVERY = timedelta(minutes=15)
_STATE_KEY = "drive.page_token"


def _state_get(key: str) -> str | None:
    with get_engine().connect() as conn:
        return conn.execute(
            text("SELECT value FROM upkeep_state WHERE key = :k"), {"k": key}
        ).scalar()


def _state_set(key: str, value: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO upkeep_state (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = now()"
            ),
            {"k": key, "v": value},
        )


def _state_clear(key: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM upkeep_state WHERE key = :k"), {"k": key})


def _mark_drive_sources(changes: list[dict]) -> int:
    """Flag the Drive sources the changes belong to. Returns how many."""
    # A change with no file id is about a shared drive itself (a rename, say),
    # not a file in one; it says nothing about what to scan.
    changes = [c for c in changes if c.get("fileId")]
    with get_engine().begin() as conn:
        roots = {
            r[0]: r[1]
            for r in conn.execute(
                text("SELECT remote_id, id FROM sources "
                     "WHERE kind = 'gdrive' AND remote_id IS NOT NULL")
            )
        }
        if not roots or not changes:
            return 0
        drive_ids = list(roots.values())
        # One query for the whole batch, led by source_id so the
        # (source_id, remote_id) index serves it: a query per change by
        # remote_id alone was a walk of every replica for every change.
        owners: dict[str, set] = {}
        for remote_id, source_id in conn.execute(
            text("SELECT DISTINCT remote_id, source_id FROM replicas "
                 "WHERE source_id = ANY(:s) AND remote_id = ANY(:f)"),
            {"s": drive_ids, "f": [c["fileId"] for c in changes]},
        ):
            owners.setdefault(remote_id, set()).add(source_id)
        found: set = set()
        everything = False
        for change in changes:
            file_id = change["fileId"]
            ids = set(owners.get(file_id, ()))
            if file_id in roots:
                ids.add(roots[file_id])
            for parent in (change.get("file") or {}).get("parents") or []:
                if parent in roots:
                    ids.add(roots[parent])
            if not ids:
                # A new file deep in a sub-folder: nothing here knows whose it is.
                everything = True
                break
            found |= ids
        marked = set(drive_ids) if everything else found
        conn.execute(
            text("UPDATE sources SET scan_requested_at = now() WHERE id = ANY(:i)"),
            {"i": list(marked)},
        )
    return len(marked)


def drive_changes_once() -> int:
    """One look at what Drive says changed. Returns how many sources it flagged."""
    from .sources import gdrive

    key = Path(get_settings().gdrive_key_file)
    if not key.is_file():
        return 0
    token = _state_get(_STATE_KEY)
    try:
        if token is None:
            # The daily sweep covers everything before now.
            _state_set(_STATE_KEY, gdrive.start_token(key))
            return 0
        changes, new_token = gdrive.changes_since(key, token)
    except gdrive.TokenRejected:
        _state_clear(_STATE_KEY)
        log.info("drive change token was refused; starting afresh next time")
        return 0
    marked = _mark_drive_sources(changes) if changes else 0
    # After the marking, so a failure above repeats these changes next time.
    _state_set(_STATE_KEY, new_token)
    return marked


async def drive_changes_forever() -> None:
    """Every 15 minutes, ask Drive what changed and flag the folders affected."""
    from .standby import is_standby

    if is_standby():
        return
    await asyncio.sleep(FIRST_SWEEP_DELAY.total_seconds())
    while True:
        try:
            marked = await asyncio.to_thread(drive_changes_once)
            if marked:
                log.info("drive changes: %d folder(s) flagged for a scan", marked)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the old token stays; try again
            log.warning("could not read Drive changes: %s", exc)
        await asyncio.sleep(DRIVE_CHANGES_EVERY.total_seconds())


async def sweep(interval: timedelta) -> int:
    """Scan everything that is due. Returns how many were scanned."""
    # A folder shared with this server since the last sweep is a new source, and
    # nobody should have to restart anything for it to appear.
    from .library import register_sources

    try:
        await asyncio.to_thread(register_sources)
    except Exception:  # noqa: BLE001 - discovery failing must not stop scanning
        log.exception("could not look for new sources")

    due = _due(interval)
    if not due:
        return 0

    log.info("scheduled sweep: %d source(s) due", len(due))
    for index, (source_id, name) in enumerate(due):
        if index:
            await asyncio.sleep(BETWEEN_SOURCES.total_seconds())
        async with _SCAN_LOCK:
            # The list was made minutes ago; a change notice may have had this
            # folder scanned since, and scanning it again at once is waste.
            if source_id not in {sid for sid, _ in _due(interval)}:
                continue
            await _scan_one(source_id, name)

    await _join_duplicates()
    return len(due)


async def _join_duplicates() -> None:
    """Notice that a file scanned from two places is one file.

    After the scans rather than inside one, because it is about what two sources
    have in common and neither of them can see that alone.

    Bounded on purpose. Fingerprinting reads whole files off the disk, and this
    shares a machine with whatever is playing in the house -- so it takes a bite
    each sweep and converges over a few of them rather than reading fifty
    gigabytes in one go.
    """
    from .dedup import fingerprint_local, merge_duplicates

    try:
        read = await asyncio.to_thread(fingerprint_local, FINGERPRINTS_PER_SWEEP)
        joined = await asyncio.to_thread(merge_duplicates)
        if read.fingerprinted or joined.merged:
            log.info(
                "fingerprinted %d file(s), merged %d duplicate item(s)",
                read.fingerprinted,
                joined.merged,
            )
    except Exception:  # noqa: BLE001 - housekeeping must never break the sweep
        log.exception("could not join duplicates")


async def run_forever() -> None:
    """The background loop. Cancelled on shutdown."""
    settings = get_settings()
    hours = settings.scan_interval_hours
    if hours <= 0:
        log.info("automatic scanning disabled (SCAN_INTERVAL_HOURS=%s)", hours)
        return

    interval = timedelta(hours=hours)
    log.info("automatic scanning every %s hours", hours)

    from .standby import is_standby

    if is_standby():
        # The standby scans nothing and backs nothing up of its own: its catalog
        # is the PC's, restored, and a backup of it pushed to the bucket would
        # sit beside the PC's and could be restored in their place.
        log.info("standby: no scanning or backups here; see sync_forever")
        return

    await asyncio.sleep(FIRST_SWEEP_DELAY.total_seconds())
    while True:
        try:
            await sweep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("scheduled sweep failed")

        try:
            await _scheduled_backup()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("scheduled backup failed")

        # Re-checked at least hourly rather than slept for a whole day: a source
        # added at noon should not wait until tomorrow, and a machine that
        # suspends overnight would otherwise drift a full cycle every time it
        # woke. Sooner when the next backup is due sooner -- see next_backup_in.
        try:
            from .backups import list_backups

            shelf = await asyncio.to_thread(list_backups)
            pause = next_backup_in(shelf, datetime.now(UTC))
        except Exception:  # noqa: BLE001 - an unreadable shelf still waits
            pause = 3600.0
        await asyncio.sleep(pause)


async def _scheduled_backup() -> None:
    """A backup every hour, and throw away what is no longer worth keeping.

    Hourly because the standby restores from these: it is never further behind
    the PC than the newest one. Pruning after each keeps only the newest of the
    day, so hourly costs nothing on the shelf: the one before is gone as soon
    as the next is written.

    Hung off the hourly loop rather than given a scheduler of its own: the
    condition is "the newest one is an hour old", which survives the machine
    being asleep at whatever time a scheduler would have chosen.
    """
    from .backups import list_backups, make_backup, prune

    shelf = await asyncio.to_thread(list_backups)
    # Due is the same question the loop's sleep asks, answered the same way,
    # so the two cannot disagree about whether this is the moment.
    if next_backup_in(shelf, datetime.now(UTC)) > 60.0:
        return

    made = await asyncio.to_thread(make_backup)
    gone = await asyncio.to_thread(prune)
    log.info("backup %s written, %d old one(s) removed", made.name, len(gone))

    # And a copy somewhere this house is not, which is the only kind that
    # survives the house. Failing to send one is worth a line in the log and
    # nothing more: the backup itself succeeded, and a folder that has not been
    # shared yet is the ordinary case rather than a fault.
    from .backups import offsite_ready, send_offsite

    ready, why = offsite_ready()
    if not ready:
        log.info("no off-site copy: %s", why)
        return
    try:
        await asyncio.to_thread(send_offsite, made.name)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not send %s off-site: %s", made.name, exc)
        return

    from .backups import prune_offsite

    try:
        await asyncio.to_thread(prune_offsite)
    except Exception as exc:  # noqa: BLE001 - a key that may not delete is a fine setup
        log.warning("could not prune off-site backups: %s", exc)

    # And the thumbnails made since last time, for the standby -- which cannot
    # make them for files that live only here. Hourly, with the backup, rather
    # than on the ten-minute sync: a request budget of 50,000 a month is spent
    # better on the changes a person made than on pictures of them.
    from . import thumbsync

    try:
        await asyncio.to_thread(thumbsync.push)
    except Exception as exc:  # noqa: BLE001 - the next hour tries again
        log.warning("could not send thumbnails to the standby: %s", exc)


# How often the two machines check the bucket for each other.
#
# Ten minutes on both sides. On the standby it is how soon a change made there
# is on its way; on the PC it is how soon after waking it carries those changes
# out. Each check is a bucket listing, and the listings are what count against
# Oracle's free allowance of 50,000 requests a month: at ten minutes both
# machines together use about 10,000 of them, and at five they would use about
# 20,000 -- which would leave the hourly backups less room than they deserve.
# Past the allowance a never-upgraded account refuses requests rather than
# billing for them, so the failure is safe, but it is still a failure.
SYNC_EVERY = timedelta(minutes=10)

# The standby looks for new thumbnail packs every sixth pass: hourly, which is
# how often the PC sends them. About 720 listings a month.
THUMBS_EVERY_PASSES = 6


async def sync_forever() -> None:
    """The traffic between the PC and the standby, through the bucket."""
    from . import standby
    from .backups import offsite_ready

    await asyncio.sleep(FIRST_SWEEP_DELAY.total_seconds())
    passes = 0
    while True:
        ready, why = offsite_ready()
        if not ready:
            log.debug("no sync with the other machine: %s", why)
        else:
            try:
                if standby.is_standby():
                    await asyncio.to_thread(standby.push_outbox)
                    outcome = await asyncio.to_thread(standby.refresh_from_primary)
                    log.debug("standby refresh: %s", outcome)
                    # Thumbnails hourly -- the PC only sends them hourly, so
                    # looking more often would spend requests finding nothing.
                    if passes % THUMBS_EVERY_PASSES == 0:
                        from . import thumbsync

                        await asyncio.to_thread(thumbsync.pull)
                else:
                    await standby.replay_outboxes()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the next pass tries again
                log.exception("sync with the other machine failed")
        passes += 1
        await asyncio.sleep(SYNC_EVERY.total_seconds())


# -- The mailbox ---------------------------------------------------------------

# Hourly backups, so a day without one is a day and two hours of nothing: room
# for a machine that slept through a night, not for one that has stopped.
BACKUPS_STALE = timedelta(hours=26)
MAILBOX_EVERY = timedelta(hours=1)

# The day the "backups have stopped" message last went out. In memory: losing
# it on a restart costs one extra message, and the restart itself is often the
# thing an administrator wants to hear about.
_backup_alert_day: str | None = None
_pruned_day: str | None = None


def backups_stale(backups: list, now: datetime) -> bool:
    """True when the newest regular backup is older than BACKUPS_STALE.

    A shelf with none at all says nothing: a server that has never taken one
    is a fresh install, and the first is minutes away.
    """
    from .backups import BEFORE_RESTORE

    regular = [b for b in backups if BEFORE_RESTORE not in b.name]
    if not regular:
        return False
    return now - max(b.taken_at for b in regular) > BACKUPS_STALE


def check_backups(now: datetime | None = None) -> bool:
    """Tell the administrators, at most once a day, that backups have stopped."""
    global _backup_alert_day
    from . import notify
    from .backups import list_backups

    now = now or datetime.now(UTC)
    day = now.date().isoformat()
    if _backup_alert_day == day or not backups_stale(list_backups(), now):
        return False
    notify.send(sender=notify.HOMESH, to="admins", title="Backups have stopped",
                path="/", tag="backup")
    _backup_alert_day = day
    return True


async def mailbox_forever() -> None:
    """Hourly: say so when backups stop; daily: empty out what has expired.

    On both machines -- the standby's mailbox is its own -- except the backup
    check, which belongs to the machine that takes them.
    """
    global _pruned_day
    from . import notify
    from .standby import is_standby

    await asyncio.sleep(FIRST_SWEEP_DELAY.total_seconds())
    while True:
        try:
            if not is_standby():
                await asyncio.to_thread(check_backups)
            today = datetime.now(UTC).date().isoformat()
            if _pruned_day != today:
                gone = await asyncio.to_thread(notify.prune)
                _pruned_day = today
                if gone:
                    log.info("mailbox: %d expired message(s) and claim(s) removed", gone)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the next hour tries again
            log.exception("mailbox upkeep failed")
        await asyncio.sleep(MAILBOX_EVERY.total_seconds())


def next_due(interval_hours: int) -> datetime | None:
    """When the earliest source becomes due, for showing in the UI."""
    if interval_hours <= 0:
        return None
    with get_engine().connect() as conn:
        earliest = conn.execute(
            text("SELECT min(scan_ended_at) FROM sources WHERE scan_ended_at IS NOT NULL")
        ).scalar()
    if earliest is None:
        return datetime.now(UTC)
    return earliest + timedelta(hours=interval_hours)
