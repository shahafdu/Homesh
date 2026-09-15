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
    prune_offsite,
    resolve,
    restore,
    worth_keeping,
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
    """Everything from the last day, a day for a week, a week for five. The
    weeklies are what catch a change nobody noticed at the time, which a
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

    def test_every_hour_of_the_last_day_is_kept(self):
        now = datetime(2026, 9, 15, 12, tzinfo=UTC)
        taken = [(now - timedelta(hours=h), f"h{h}") for h in range(24)]
        assert worth_keeping(taken, now) == {name for _, name in taken}

    def test_an_older_day_keeps_only_its_newest(self):
        now = datetime(2026, 9, 15, 12, tzinfo=UTC)
        day = datetime(2026, 9, 12, tzinfo=UTC)
        taken = [(day + timedelta(hours=h), f"h{h}") for h in (1, 9, 23)]
        assert worth_keeping(taken, now) == {"h23"}

    def test_an_older_week_keeps_only_its_newest(self):
        """Wednesday 19 August to Sunday 23 August 2026 are one ISO week."""
        now = datetime(2026, 9, 15, 12, tzinfo=UTC)
        taken = [
            (datetime(2026, 8, 19, tzinfo=UTC), "wed"),
            (datetime(2026, 8, 21, tzinfo=UTC), "fri"),
            (datetime(2026, 8, 23, 22, tzinfo=UTC), "sun"),
        ]
        assert worth_keeping(taken, now) == {"sun"}

    def test_beyond_five_weeks_nothing_is_kept_but_the_rest(self):
        now = datetime(2026, 9, 15, 12, tzinfo=UTC)
        taken = [(now, "today"), (now - timedelta(days=60), "old")]
        assert worth_keeping(taken, now) == {"today"}

    def test_a_fortnight_and_a_month_back_always_exist(self):
        """Hourly backups for three months, pruned after every one, the way the
        server does it -- then checked on every day of a whole week, so that
        whichever weekday it is, there is a point about two weeks old and one
        about a month old.

        The rule before this aimed at the fourteen- and thirty-day marks
        directly, and was wrong in a way no test of a hand-picked shelf could
        show: a daily was deleted at eight days old, so by the fourteenth day
        there was never anything left to keep.
        """
        start = datetime(2026, 6, 1, tzinfo=UTC)
        shelf: list = []
        checked = 0
        for hour in range(24 * 92):
            now = start + timedelta(hours=hour)
            shelf.append((now, now.isoformat()))
            kept = worth_keeping(shelf, now)
            shelf = [entry for entry in shelf if entry[1] in kept]

            if hour >= 24 * 85 and hour % 24 == 13:
                ages = [(now - when).total_seconds() / 86400 for when, _ in shelf]
                assert any(10 <= a <= 18 for a in ages), ages
                assert any(26 <= a <= 35 for a in ages), ages
                # And it stays small: a day of hourlies, a week, five weeks.
                assert len(shelf) <= 40, len(shelf)
                checked += 1
        assert checked == 7

    def test_it_never_empties_the_shelf(self, shelf):
        """Everything is ancient. Keeping a stale backup beats keeping none."""
        self._shelf(shelf, [400, 500])
        prune()
        assert len(list_backups()) == 1

    def test_the_bucket_is_kept_by_the_same_rule(self, monkeypatch):
        now = datetime(2026, 9, 15, 12, tzinfo=UTC)
        names = {
            "homesh-20260915-110000.sql.gz.enc": True,  # within the day
            "homesh-20260912-010000.sql.gz.enc": False,  # not its day's newest
            "homesh-20260912-230000.sql.gz.enc": True,
            "homesh-20260819-000000.sql.gz.enc": False,  # not its week's newest
            "homesh-20260823-220000.sql.gz.enc": True,
            "homesh-20260601-000000.sql.gz.enc": False,  # beyond five weeks
            "outbox/20260601-000000-x.json.enc": True,  # not a backup at all
        }
        stored = [module.offsite.Stored(n, 1, None) for n in names]
        removed: list[str] = []
        monkeypatch.setattr(module, "_store", lambda: object())
        monkeypatch.setattr(module.offsite, "index", lambda store: stored)
        monkeypatch.setattr(module.offsite, "remove", lambda store, n: removed.append(n))

        prune_offsite(now)
        assert sorted(removed) == sorted(n for n, keep in names.items() if not keep)


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


class TestTheCopyThatLeavesTheHouse:
    """A backup on the same disk as the database survives a mistake and nothing
    else. The fire, the theft and the failed drive take both copies together.

    These tests stand in for Drive. What they are about is everything that
    happens *before* the upload, because that is where the security lives: the
    file is encrypted here, with a key that does not go with it.
    """

    @pytest.fixture
    def keyed(self, monkeypatch, shelf):
        from app.config import get_settings
        from app.crypt import new_key

        get_settings.cache_clear()
        monkeypatch.setenv("BACKUP_KEY", new_key())
        monkeypatch.setenv("OFFSITE_PROVIDER", "oracle")
        monkeypatch.setenv("OFFSITE_REGION", "somewhere")
        monkeypatch.setenv("OFFSITE_BUCKET", "homesh-backups")
        monkeypatch.setenv("OFFSITE_ACCESS_KEY", "key")
        monkeypatch.setenv("OFFSITE_SECRET_KEY", "secret")
        monkeypatch.setenv("OFFSITE_NAMESPACE", "namespace")
        yield
        get_settings.cache_clear()

    class Elsewhere:
        """Somewhere to put a file, and a record of what arrived."""

        def __init__(self):
            self.held: dict[str, bytes] = {}

        def configured(self, _settings):
            from app.offsite import Store

            return Store(
                provider="oracle",
                region="somewhere",
                bucket="homesh-backups",
                access_key="key",
                secret_key="secret",  # noqa: S106 - a stand-in, not a secret
                namespace="namespace",
            )

        def put(self, _store, name, path):
            self.held[name] = path.read_bytes()
            return len(self.held[name])

        def index(self, _store):
            from app.offsite import Stored

            return [
                Stored(name=n, size_bytes=len(b), written_at="2026-01-01T00:00:00Z")
                for n, b in self.held.items()
            ]

        def get(self, _store, name, target):
            target.write_bytes(self.held[name])
            return len(self.held[name])

    @pytest.fixture
    def store(self, monkeypatch):
        from app import offsite as real

        fake = self.Elsewhere()
        for name in ("configured", "put", "index", "get"):
            monkeypatch.setattr(real, name, getattr(fake, name))
        return fake

    def test_what_leaves_is_encrypted(self, db, user, keyed, store):
        """The test that matters. It is the catalog, and the catalog names the
        rooms in the house — in the clear, off-site, that is a map."""
        _make_playlist(db, user, "The bedroom television")
        made = make_backup()

        module.send_offsite(made.name)

        sent = next(iter(store.held.values()))
        assert sent.startswith(b"homesh-enc")
        assert b"bedroom" not in sent, "the catalog went up in the clear"

    def test_the_plain_copy_stays_here(self, db, user, keyed, store):
        made = make_backup()
        module.send_offsite(made.name)
        assert resolve(made.name).is_file()

    def test_the_courier_copy_is_not_kept(self, db, user, keyed, store):
        """Encrypting makes a second file. Keeping it would double what the disk
        holds for nothing — it can be made again in seconds."""
        made = make_backup()
        module.send_offsite(made.name)
        assert list(shelf_files(module.backup_dir())) == [made.name]

    def test_it_comes_back_and_can_be_restored(self, db, user, keyed, store):
        """The round trip that matters when the house has gone."""
        _make_playlist(db, user, "Before the fire")
        made = make_backup()
        module.send_offsite(made.name)

        # The local shelf is gone, as it would be with the machine.
        resolve(made.name).unlink()
        assert list_backups() == []

        [entry] = module.offsite_index()
        landed = module.bring_back(entry["name"])

        with db.begin() as conn:
            conn.execute(text("DELETE FROM playlists"))
        restore(landed)
        assert _playlists(db) == ["Before the fire"]

    def test_without_a_key_nothing_is_sent(self, db, monkeypatch, shelf):
        """Rather than sending it in the clear, which is not a thing anybody
        wants quietly done for them."""
        from app.config import get_settings

        monkeypatch.setenv("BACKUP_KEY", "")
        get_settings.cache_clear()
        try:
            ready, why = module.offsite_ready()
            assert ready is False
            assert "BACKUP_KEY" in why
        finally:
            get_settings.cache_clear()

    def test_a_backup_taken_through_the_api_says_whether_it_left(self, client, keyed, store):
        made = client.post("/api/backups")
        assert made.status_code == 201
        assert made.json()["offsite"] == "sent"

    def test_it_still_succeeds_when_there_is_nowhere_to_send_it(self, client, monkeypatch, shelf):
        """A backup that exists only here is worth having. It is just worth
        less, and saying so beats failing the request."""
        from app.config import get_settings

        monkeypatch.setenv("BACKUP_KEY", "")
        get_settings.cache_clear()
        try:
            made = client.post("/api/backups")
            assert made.status_code == 201
            assert made.json()["offsite"] is None
            assert made.json()["offsite_note"]
        finally:
            get_settings.cache_clear()


def shelf_files(directory):
    return sorted(p.name for p in directory.glob("homesh-*"))


def test_the_suite_cannot_reach_the_real_bucket():
    """It did. The suite runs in a container that reads the real `.env`, so
    every backup taken through the API went to the real bucket -- copies of the
    *test* database, one Bring back and one Restore away from replacing the
    household's accounts with a fixture account called "tester".

    conftest.py blanks the credentials before anything is imported; this is the
    check that it still does."""
    from app import offsite
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        assert offsite.configured(get_settings()) is None
        assert get_settings().backup_key == ""
    finally:
        get_settings.cache_clear()
