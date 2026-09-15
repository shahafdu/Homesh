"""Keeping the catalog current without being asked.

Scanning was manual, which meant the catalog was only as fresh as the last time
somebody remembered — and a folder that had never been scanned at all looked
exactly like a folder that was empty. A library nobody has to maintain by hand
is the whole point of indexing it.

Daily rather than continuous: Drive charges API quota per listing, the mini PC
has four efficiency cores, and photos added to a shared folder are not urgent.
Anything that *is* urgent has the manual button next to it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
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
# this is how far behind the PC it can be. Pruning keeps a day of hourlies and a
# daily copy after that, which still covers the agreed need -- a change nobody
# noticed at the time is found within a day or two, and the copies go back a
# month.
BACKUP_EVERY = timedelta(hours=1)


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


async def _scan_one(source_id: UUID, name: str) -> None:
    """Run one source's scan off the event loop.

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

        # Re-checked hourly rather than slept for a whole day: a source added at
        # noon should not wait until tomorrow, and a machine that suspends
        # overnight would otherwise drift a full cycle every time it woke.
        await asyncio.sleep(3600)


async def _scheduled_backup() -> None:
    """A backup every hour, and throw away what is no longer worth keeping.

    Hourly because the standby restores from these: it is never further behind
    the PC than the newest one. Pruning keeps every backup from the last day and
    one a day after that, so this costs a day of hourlies rather than a week.

    Hung off the hourly loop rather than given a scheduler of its own: the
    condition is "the newest one is an hour old", which survives the machine
    being asleep at whatever time a scheduler would have chosen.
    """
    from .backups import list_backups, make_backup, prune

    newest = await asyncio.to_thread(list_backups)
    if newest and datetime.now(UTC) - newest[0].taken_at < BACKUP_EVERY:
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


async def sync_forever() -> None:
    """The traffic between the PC and the standby, through the bucket."""
    from . import standby
    from .backups import offsite_ready

    await asyncio.sleep(FIRST_SWEEP_DELAY.total_seconds())
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
                else:
                    await standby.replay_outboxes()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the next pass tries again
                log.exception("sync with the other machine failed")
        await asyncio.sleep(SYNC_EVERY.total_seconds())


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
