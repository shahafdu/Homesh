"""Change notices from the PC's folder watcher (docs/design/06, L1)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from app import scanrequest, standby, upkeep


def _requested(db, sid):
    with db.connect() as conn:
        return conn.execute(
            text("SELECT scan_requested_at FROM sources WHERE id = :i"), {"i": str(sid)}
        ).scalar_one()


def _name_of(db, sid):
    with db.connect() as conn:
        return conn.execute(
            text("SELECT mount_prefix FROM sources WHERE id = :i"), {"i": str(sid)}
        ).scalar_one().removeprefix("/local/")


def test_valid_name_sets_the_flag(db, source):
    sid, _prefix, _root = source
    assert _requested(db, sid) is None
    assert scanrequest.main(["x", _name_of(db, sid)]) == 0
    assert _requested(db, sid) is not None


def test_unknown_name_exits_3(db, source):
    assert scanrequest.main(["x", "NoSuchFolder"]) == 3


@pytest.mark.parametrize("bad", ["../x", "", "a" * 100, "a/b", "a\\b", "a..b", "a\nb"])
def test_bad_names_exit_2(db, source, bad):
    assert scanrequest.main(["x", bad]) == 2


def test_wrong_argument_count_exits_2(db):
    assert scanrequest.main(["x"]) == 2


def _age(db, sid, requested, ended):
    with db.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE sources SET scan_state = :st,
                  scan_requested_at = now() - CAST(:r AS interval),
                  scan_ended_at = now() - CAST(:e AS interval)
                WHERE id = :i
                """
            ),
            {"i": str(sid), "r": requested, "e": ended,
             "st": "done"},
        )


def test_loop_query_debounce(db, source):
    sid, _p, _r = source
    _age(db, sid, "1 minute", None)
    assert upkeep._next_requested() is None
    _age(db, sid, "3 minutes", None)
    assert upkeep._next_requested()[0] == sid
    _age(db, sid, "3 minutes", "5 minutes")
    assert upkeep._next_requested() is None
    _age(db, sid, "3 minutes", "20 minutes")
    assert upkeep._next_requested()[0] == sid


def test_running_scan_is_not_picked(db, source):
    sid, _p, _r = source
    _age(db, sid, "3 minutes", None)
    with db.begin() as conn:
        conn.execute(text("UPDATE sources SET scan_state = 'running' WHERE id = :i"),
                     {"i": str(sid)})
    assert upkeep._next_requested() is None


def test_scan_clears_the_request(db, source, monkeypatch):
    sid, _p, _r = source
    _age(db, sid, "3 minutes", None)
    scanned = []

    async def fake(source_id, name):
        # Cleared before the scan, so a change during it asks again.
        assert _requested(db, source_id) is None
        scanned.append(source_id)

    monkeypatch.setattr(upkeep, "_scan_one", fake)
    assert asyncio.run(upkeep.scan_requested_once()) is True
    assert scanned == [sid]
    assert _requested(db, sid) is None
    assert asyncio.run(upkeep.scan_requested_once()) is False


def test_standby_does_nothing(db, source, monkeypatch):
    sid, _p, _r = source
    _age(db, sid, "3 minutes", None)
    monkeypatch.setattr(standby, "is_standby", lambda: True)
    assert asyncio.run(upkeep.scan_requested_once()) is False
    assert _requested(db, sid) is not None


def test_a_requested_scan_waits_for_one_already_running(db, source, monkeypatch):
    sid, _p, _r = source
    _age(db, sid, "3 minutes", None)
    order = []

    async def fake(source_id, name):
        order.append("requested")

    monkeypatch.setattr(upkeep, "_scan_one", fake)

    async def both():
        async with upkeep._SCAN_LOCK:
            task = asyncio.create_task(upkeep.scan_requested_once())
            await asyncio.sleep(0.05)
            order.append("sweep done")
        return await task

    assert asyncio.run(both()) is True
    assert order == ["sweep done", "requested"]


def test_sweep_skips_a_folder_scanned_since_it_made_its_list(db, source, monkeypatch):
    from datetime import timedelta

    sid, _p, _r = source
    _age(db, sid, "3 minutes", "2 days")
    scanned = []

    async def fake(source_id, name):
        scanned.append(source_id)

    real_due = upkeep._due
    calls = []

    def due_then_scanned(interval):
        found = real_due(interval)
        if not calls:
            # Between the sweep making its list and reaching this folder, the
            # change-notice loop scanned it.
            with db.begin() as conn:
                conn.execute(
                    text("UPDATE sources SET scan_ended_at = now() WHERE id = :i"),
                    {"i": str(sid)},
                )
        calls.append(1)
        return found

    monkeypatch.setattr(upkeep, "_scan_one", fake)
    monkeypatch.setattr(upkeep, "_due", due_then_scanned)
    monkeypatch.setattr(upkeep, "_join_duplicates", _nothing)
    monkeypatch.setattr("app.library.register_sources", lambda: None)
    asyncio.run(upkeep.sweep(timedelta(days=1)))
    assert sid not in scanned


async def _nothing():
    return None
