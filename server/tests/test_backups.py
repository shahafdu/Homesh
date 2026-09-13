"""Backing up the database, and putting one back.

The only test that matters here is the round trip: take a backup, change
everything, restore, and find what was there before. A backup that has never
been restored is a belief, not a backup — and this one is about to become the
safety net under an AI that can write to the catalog.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app import backups as module
from app.backups import (
    DAILY_DAYS,
    Backup,
    list_backups,
    make_backup,
    prune,
    resolve,
    restore,
)


@pytest.fixture(autouse=True)
def shelf(tmp_path, monkeypatch):
    """A backup directory per test, so one test's shelf is not another's."""
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path / "backups"


def _playlists(db) -> list[str]:
    with db.connect() as conn:
        return sorted(conn.execute(text("SELECT name FROM playlists")).scalars())


def _make_playlist(db, user, name: str) -> None:
    with db.begin() as conn:
        conn.execute(
            text("INSERT INTO playlists (owner_id, name) VALUES (:u, :n)"),
            {"u": str(user.id), "n": name},
        )


class TestTheRoundTrip:
    def test_what_was_there_comes_back(self, db, user):
        _make_playlist(db, user, "Before")
        taken = make_backup()

        _make_playlist(db, user, "After")
        with db.begin() as conn:
            conn.execute(text("DELETE FROM playlists WHERE name = 'Before'"))
        assert _playlists(db) == ["After"]

        restore(taken.name)
        assert _playlists(db) == ["Before"], "the backup did not come back"

    def test_an_empty_table_restores_empty(self, db, user):
        """A restore replaces, rather than merging into what is there."""
        taken = make_backup()
        _make_playlist(db, user, "Added afterwards")

        restore(taken.name)
        assert _playlists(db) == []

    def test_accounts_and_their_passkeys_survive(self, db, user):
        """The part that exists nowhere else. A lost credential cannot be
        re-derived from anything — it is on somebody's telephone."""
        with db.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO credentials (user_id, credential_id, public_key,
                                             sign_count, transports)
                    VALUES (:u, :c, :k, 0, ARRAY['internal'])
                    """
                ),
                {"u": str(user.id), "c": b"credential-bytes", "k": b"public-key-bytes"},
            )
        taken = make_backup()

        with db.begin() as conn:
            conn.execute(text("DELETE FROM credentials"))
        restore(taken.name)

        with db.connect() as conn:
            back = conn.execute(text("SELECT credential_id FROM credentials")).scalar_one()
        assert bytes(back) == b"credential-bytes"

    def test_a_restore_reports_what_it_moved(self, db, user):
        _make_playlist(db, user, "One")
        taken = make_backup()
        done = restore(taken.name)

        assert done["restored"] == taken.name
        assert done["rows"] >= 2, "at least the account and its playlist"
        assert "playlists" in done["tables"]

    def test_every_table_is_in_the_file(self, db):
        """Written out from the foreign keys rather than from a list somebody
        maintains, because a list goes stale and takes a table with it."""
        taken = make_backup()
        with gzip.open(resolve(taken.name), "rt", encoding="utf-8") as f:
            body = f.read()

        with db.connect() as conn:
            expected = set(
                conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                ).scalars()
            ) - module.SKIP

        for table in expected:
            assert f"-- table: {table}\n" in body, f"{table} is not in the backup"

    def test_parents_are_written_before_their_children(self, db):
        """Which is what lets a restore put the rows back at all: a replica
        cannot be inserted before the item it belongs to."""
        taken = make_backup()
        with gzip.open(resolve(taken.name), "rt", encoding="utf-8") as f:
            order = [
                line[len("-- table: "):].strip()
                for line in f
                if line.startswith("-- table: ")
            ]
        assert order.index("items") < order.index("replicas")
        assert order.index("users") < order.index("credentials")


class TestRefusals:
    def test_a_name_that_is_a_path_is_refused(self):
        """The name comes from a request. It is matched against a shape rather
        than cleaned up, because cleaning up is where traversal gets in."""
        for attempt in ("../../etc/passwd", "homesh-20260101-000000.sql.gz/../x", "x.sql.gz"):
            with pytest.raises(ValueError):
                resolve(attempt)

    def test_a_missing_backup_is_not_found(self):
        with pytest.raises(FileNotFoundError):
            resolve("homesh-20260101-000000.sql.gz")

    def test_a_backup_from_a_newer_schema_is_refused(self, db, shelf):
        """Restoring forwards is fine; restoring into an older server is not,
        and finding that out half way through is the worst time."""
        taken = make_backup()
        path = resolve(taken.name)
        with gzip.open(path, "rt", encoding="utf-8") as f:
            lines = f.readlines()
        lines[1] = lines[1].replace(
            lines[1][lines[1].index("{"):],
            '{"taken_at": "2099-01-01T00:00:00+00:00", '
            '"schema_version": "999_from_the_future.sql", "tables": []}\n',
        )
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.writelines(lines)

        with pytest.raises(ValueError, match="schema"):
            restore(taken.name)

    def test_something_that_is_not_a_backup_is_not_listed(self, shelf):
        shelf.mkdir(parents=True, exist_ok=True)
        with gzip.open(shelf / "homesh-20260101-000000.sql.gz", "wt") as f:
            f.write("this is not a backup\n")
        assert list_backups() == []


class TestKeeping:
    """Every day for a week, then a fortnight back, then a month back. The
    older two are what catch a change nobody noticed at the time, which a
    daily-only shelf quietly loses."""

    def _shelf(self, shelf, ages_in_days):
        shelf.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        for age in ages_in_days:
            when = now - timedelta(days=age)
            path = shelf / f"homesh-{when:%Y%m%d-%H%M%S}.sql.gz"
            with gzip.open(path, "wt", encoding="utf-8") as f:
                f.write(f"-- {module.MAGIC} {module.FORMAT}\n")
                f.write(
                    "-- "
                    + json.dumps(
                        {"taken_at": when.isoformat(), "schema_version": "x", "tables": []}
                    )
                    + "\n"
                )
        return now

    def test_a_week_of_dailies_is_kept(self, shelf):
        self._shelf(shelf, range(0, DAILY_DAYS))
        assert prune() == []
        assert len(list_backups()) == DAILY_DAYS

    def test_the_middle_is_thrown_away(self, shelf):
        """Between the week and the fortnight there is nothing worth keeping."""
        self._shelf(shelf, [0, 9, 10, 11, 14])
        gone = prune()
        left = {b.name for b in list_backups()}
        assert len(gone) == 3
        assert len(left) == 2, "today and the fortnight mark"

    def test_the_landmarks_are_kept(self, shelf):
        self._shelf(shelf, [0, 14, 30])
        assert prune() == []
        assert len(list_backups()) == 3

    def test_it_never_empties_the_shelf(self, shelf):
        """Everything is ancient. Keeping a stale backup beats keeping none."""
        self._shelf(shelf, [400, 500])
        prune()
        assert len(list_backups()) == 1


class TestWhoMayDoThis:
    """Administrators only, all of it — which is also what stops the AI: it
    reaches the server as whoever asked it, through this same API."""

    def _ordinary(self, client, db):
        from app.security import CurrentUser, optional_user, require_user

        with db.begin() as conn:
            uid = conn.execute(
                text(
                    """
                    INSERT INTO users (handle, display_name, is_admin)
                    VALUES ('guest', 'Guest', FALSE) RETURNING id
                    """
                )
            ).scalar_one()
        guest = CurrentUser(id=uid, handle="guest", display_name="Guest", is_admin=False)
        client.app.dependency_overrides[require_user] = lambda: guest
        client.app.dependency_overrides[optional_user] = lambda: guest

    def test_an_administrator_can_see_the_shelf(self, client):
        assert client.get("/api/backups").status_code == 200

    def test_everybody_else_cannot(self, client, db):
        self._ordinary(client, db)
        assert client.get("/api/backups").status_code == 403
        assert client.post("/api/backups").status_code == 403
        assert client.post("/api/backups/x/restore").status_code == 403
        assert client.delete("/api/backups/x").status_code == 403

    def test_taking_one_through_the_api_puts_it_on_the_shelf(self, client):
        made = client.post("/api/backups")
        assert made.status_code == 201
        listed = client.get("/api/backups").json()["backups"]
        assert [b["name"] for b in listed] == [made.json()["name"]]

    def test_restoring_saves_what_it_is_about_to_replace(self, client, db, user):
        """Restoring the wrong one is a mistake made at exactly the moment
        somebody can least afford another."""
        _make_playlist(db, user, "Current")
        taken = make_backup()

        done = client.post(f"/api/backups/{taken.name}/restore")
        assert done.status_code == 200
        saved = done.json()["previous_state_saved_as"]
        assert saved != taken.name
        assert saved in {b.name for b in list_backups()}


def test_the_list_is_newest_first(shelf):
    shelf.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    for age in (3, 1, 2):
        when = now - timedelta(days=age)
        with gzip.open(shelf / f"homesh-{when:%Y%m%d-%H%M%S}.sql.gz", "wt") as f:
            f.write(f"-- {module.MAGIC} {module.FORMAT}\n")
            f.write(
                "-- "
                + json.dumps(
                    {"taken_at": when.isoformat(), "schema_version": "x", "tables": []}
                )
                + "\n"
            )
    ages = [b.taken_at for b in list_backups()]
    assert ages == sorted(ages, reverse=True)


def test_two_in_the_same_second_do_not_overwrite_each_other(db, user):
    """Which is not a curiosity: restoring takes a safety copy first, and a
    colliding name overwrote the file it was about to restore from."""
    first = make_backup()
    second = make_backup()
    assert first.name != second.name
    assert {b.name for b in list_backups()} == {first.name, second.name}


def test_a_backup_is_a_dataclass_not_a_dict():
    """Guards the shape the endpoints serialise."""
    one = Backup(name="x", taken_at=datetime.now(UTC), size_bytes=1, rows=2)
    assert one.name == "x"
