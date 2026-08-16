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
    due = _due(interval)
    if not due:
        return 0

    log.info("scheduled sweep: %d source(s) due", len(due))
    for index, (source_id, name) in enumerate(due):
        if index:
            await asyncio.sleep(BETWEEN_SOURCES.total_seconds())
        await _scan_one(source_id, name)
    return len(due)


async def run_forever() -> None:
    """The background loop. Cancelled on shutdown."""
    settings = get_settings()
    hours = settings.scan_interval_hours
    if hours <= 0:
        log.info("automatic scanning disabled (SCAN_INTERVAL_HOURS=%s)", hours)
        return

    interval = timedelta(hours=hours)
    log.info("automatic scanning every %s hours", hours)

    await asyncio.sleep(FIRST_SWEEP_DELAY.total_seconds())
    while True:
        try:
            await sweep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("scheduled sweep failed")

        # Re-checked hourly rather than slept for a whole day: a source added at
        # noon should not wait until tomorrow, and a machine that suspends
        # overnight would otherwise drift a full cycle every time it woke.
        await asyncio.sleep(3600)


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
