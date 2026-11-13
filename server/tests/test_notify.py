"""The notification mailbox: who may post, who may read, and what a message can say.

Three credentials meet here -- an app's sending key, a person's session and a
phone's device token -- and most of these tests are about none of them opening
a door meant for another. The rest are about a message's path, which is the one
thing in a notification that decides where a tap takes somebody.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import notify, standby
from app.main import app, notify_senders
from app.security import SESSION_COOKIE, CurrentUser, create_session, optional_user, require_user

KEY_ONE = "KEYMARKER-one-" + "a" * 32
KEY_TWO = "KEYMARKER-two-" + "b" * 32
APPS = "AppOne|https://app-one.example|;AppTwo|https://app-two.example"


@pytest.fixture
def configured(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("PHONE_APPS", APPS)
    monkeypatch.setenv("NOTIFY_SENDERS", f"AppOne|{KEY_ONE};AppTwo|{KEY_TWO}")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_overrides_left():
    yield
    app.dependency_overrides.clear()


def _person(db, handle: str, admin: bool = False) -> CurrentUser:
    with db.begin() as conn:
        uid = conn.execute(
            text(
                "INSERT INTO users (handle, display_name, is_admin) "
                "VALUES (:h, :n, :a) RETURNING id"
            ),
            {"h": handle, "n": handle.title(), "a": admin},
        ).scalar_one()
    return CurrentUser(id=uid, handle=handle, display_name=handle.title(), is_admin=admin)


def _as(person: CurrentUser) -> TestClient:
    app.dependency_overrides[require_user] = lambda: person
    app.dependency_overrides[optional_user] = lambda: person
    return TestClient(app)


def _post(c: TestClient, key: str | None = KEY_ONE, **body):
    body.setdefault("to", "everyone")
    body.setdefault("title", "Two things need a decision")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return c.post("/api/notify/send", json=body, headers=headers)


def _phone(db, person: CurrentUser, label: str = "My phone") -> str:
    """A device token for `person`, made the way a phone makes one."""
    with _as(person) as c:
        claim = c.post("/api/notify/devices", json={"label": label}).json()["claim"]
    app.dependency_overrides.clear()
    with TestClient(app) as c:
        return c.post("/api/notify/devices/claim", json={"claim": claim}).json()["token"]


def _inbox(c: TestClient, token: str, after: int = 0, wait: int = 0):
    return c.get(
        f"/api/notify/inbox?after={after}&wait={wait}",
        headers={"Authorization": f"Bearer {token}"},
    )


def _rows(db) -> list:
    with db.connect() as conn:
        return conn.execute(
            text(
                "SELECT u.handle, m.sender FROM notify_messages m "
                "JOIN users u ON u.id = m.user_id"
            )
        ).all()


# ── The path a message carries ──────────────────────────────────────────────


class TestThePath:
    @pytest.mark.parametrize("path", ["/", "/a/b?c=d#e", "/inbox", "/a-b_c/%20x"])
    def test_a_path_inside_the_app_is_accepted(self, path):
        assert notify.valid_path(path)

    @pytest.mark.parametrize(
        "path",
        [
            "//evil.example",
            "/\\evil",
            "http://x",
            "/a//b",
            "/a\nb",
            "/a\rb",
            "",
            "inbox",
            "/" + "a" * 512,
            "/user@evil.example/x",
            "/javascript:alert(1)",
            "/a‮b",
        ],
    )
    def test_anything_that_could_leave_the_app_is_refused(self, path):
        assert not notify.valid_path(path)

    def test_512_characters_is_the_limit(self):
        assert notify.valid_path("/" + "a" * 511)

    def test_the_route_refuses_a_bad_path(self, db, configured, anon_client):
        assert _post(anon_client, path="//evil.example").status_code == 422
        assert _rows(db) == []

    def test_in_process_senders_are_held_to_it_too(self, db):
        with pytest.raises(ValueError):
            notify.send(sender=notify.HOMESH, to="everyone", title="x", path="//evil.example")


# ── Who may post ────────────────────────────────────────────────────────────


class TestTheSenders:
    def test_a_name_must_be_one_of_the_apps(self):
        apps = [{"name": "AppOne"}]
        assert notify_senders(f"AppOne|{KEY_ONE};Stranger|{KEY_TWO}", apps) == {"AppOne": KEY_ONE}

    def test_homesh_cannot_be_configured(self):
        apps = [{"name": "Homesh"}, {"name": "homesh"}]
        assert notify_senders(f"Homesh|{KEY_ONE};homesh|{KEY_TWO}", apps) == {}

    def test_a_short_key_is_refused(self):
        assert notify_senders("AppOne|short", [{"name": "AppOne"}]) == {}

    def test_a_key_given_twice_says_nothing_about_who_sent(self):
        apps = [{"name": "AppOne"}, {"name": "AppTwo"}]
        assert notify_senders(f"AppOne|{KEY_ONE};AppTwo|{KEY_ONE}", apps) == {"AppOne": KEY_ONE}

    def test_a_skipped_entry_is_logged_by_position_not_content(self, caplog):
        caplog.set_level(logging.DEBUG)
        notify_senders(f"Nobody|{KEY_ONE}-unique-marker", [])
        assert "entry 1" in caplog.text
        assert "Nobody" not in caplog.text and KEY_ONE not in caplog.text

    def test_a_key_for_an_app_not_in_phone_apps_is_not_accepted(self, db, monkeypatch, anon_client):
        from app.config import get_settings

        monkeypatch.setenv("PHONE_APPS", "AppOne|https://app-one.example")
        monkeypatch.setenv("NOTIFY_SENDERS", f"AppTwo|{KEY_TWO}")
        get_settings.cache_clear()
        try:
            assert _post(anon_client, key=KEY_TWO).status_code == 401
        finally:
            get_settings.cache_clear()

    def test_a_wrong_key_is_refused_and_guessing_is_bounded(self, db, configured, anon_client):
        for _ in range(20):
            assert _post(anon_client, key="wrong" * 10).status_code == 401
        assert _post(anon_client, key="wrong" * 10).status_code == 429

    def test_no_key_at_all_is_refused(self, db, configured, anon_client):
        assert _post(anon_client, key=None).status_code == 401

    def test_a_sender_cannot_name_itself(self, db, configured, anon_client):
        _person(db, "alex")
        refused = _post(anon_client, sender="Homesh")
        assert refused.status_code == 422
        assert _rows(db) == []

    def test_the_stored_sender_is_the_keys_name(self, db, configured, anon_client):
        _person(db, "alex")
        assert _post(anon_client, key=KEY_TWO).json() == {"queued": 1}
        assert _rows(db) == [("alex", "AppTwo")]

    def test_a_server_side_caller_is_not_a_cross_origin_write(self, db, configured, anon_client):
        """No Origin header is what a program sends, and webguard lets it by."""
        _person(db, "alex")
        assert _post(anon_client).status_code == 200

    def test_one_sender_is_limited(self, db, configured, anon_client):
        for _ in range(60):
            assert _post(anon_client).status_code == 200
        assert _post(anon_client).status_code == 429
        # Another app's allowance is its own.
        assert _post(anon_client, key=KEY_TWO).status_code == 200


# ── Who it reaches ──────────────────────────────────────────────────────────


class TestTheRecipients:
    def test_everyone_is_one_row_each(self, db, configured, anon_client):
        _person(db, "alex")
        _person(db, "sam", admin=True)
        assert _post(anon_client, to="everyone").json() == {"queued": 2}
        assert sorted(r[0] for r in _rows(db)) == ["alex", "sam"]

    def test_admins_are_administrators_only(self, db, configured, anon_client):
        _person(db, "alex")
        _person(db, "sam", admin=True)
        assert _post(anon_client, to="admins").json() == {"queued": 1}
        assert [r[0] for r in _rows(db)] == ["sam"]

    def test_one_person_by_handle(self, db, configured, anon_client):
        _person(db, "alex")
        _person(db, "sam")
        assert _post(anon_client, to="sam").json() == {"queued": 1}
        assert [r[0] for r in _rows(db)] == ["sam"]

    def test_an_unknown_handle_is_not_an_error(self, db, configured, anon_client):
        """So the route cannot be used to find out which handles exist."""
        _person(db, "alex")
        answer = _post(anon_client, to="nobody")
        assert answer.status_code == 200
        assert answer.json() == {"queued": 0}

    @pytest.mark.parametrize("to", ["", "a b", "x" * 65, "o'neil"])
    def test_a_malformed_recipient_is_refused(self, db, configured, anon_client, to):
        assert _post(anon_client, to=to).status_code == 422

    @pytest.mark.parametrize(
        "field,value",
        [("title", ""), ("title", "x" * 81), ("body", "x" * 301), ("tag", "x" * 65),
         ("ttl_minutes", 0), ("ttl_minutes", 10081)],
    )
    def test_lengths_are_capped(self, db, configured, anon_client, field, value):
        assert _post(anon_client, **{field: value}).status_code == 422


# ── Turning a phone on ──────────────────────────────────────────────────────


class TestTheClaim:
    def test_a_claim_works_once(self, db, user):
        with _as(user) as c:
            made = c.post("/api/notify/devices", json={"label": "My phone"}).json()
        assert made["expires_in"] == 120
        app.dependency_overrides.clear()
        with TestClient(app) as c:
            first = c.post("/api/notify/devices/claim", json={"claim": made["claim"]})
            second = c.post("/api/notify/devices/claim", json={"claim": made["claim"]})
        assert first.status_code == 200
        assert first.json()["who"] == "Tester"
        assert len(first.json()["token"]) >= 32
        assert second.status_code == 404

    def test_an_expired_claim_is_refused(self, db, user):
        with _as(user) as c:
            claim = c.post("/api/notify/devices", json={"label": "My phone"}).json()["claim"]
        with db.begin() as conn:
            conn.execute(text("UPDATE notify_claims SET expires_at = now() - interval '1 second'"))
        app.dependency_overrides.clear()
        with TestClient(app) as c:
            assert c.post("/api/notify/devices/claim", json={"claim": claim}).status_code == 404

    def test_the_cursor_starts_at_now(self, db, user):
        notify.send(sender=notify.HOMESH, to=user.handle, title="Old news")
        token_answer = None
        with _as(user) as c:
            claim = c.post("/api/notify/devices", json={"label": "My phone"}).json()["claim"]
        app.dependency_overrides.clear()
        with TestClient(app) as c:
            token_answer = c.post("/api/notify/devices/claim", json={"claim": claim}).json()
            assert token_answer["cursor"] == notify.newest_id(user.id) > 0
            got = _inbox(c, token_answer["token"], after=token_answer["cursor"]).json()
        assert got["messages"] == []

    def test_the_token_is_shown_only_by_the_claim(self, db, user):
        token = _phone(db, user)
        with _as(user) as c:
            listed = c.get("/api/notify/devices")
        assert listed.status_code == 200
        assert [p["label"] for p in listed.json()] == ["My phone"]
        assert token not in listed.text

    def test_the_claim_is_audited(self, db, user):
        _phone(db, user)
        with db.connect() as conn:
            events = conn.execute(
                text("SELECT event FROM audit_log WHERE user_id = :u"), {"u": str(user.id)}
            ).scalars().all()
        assert "notify.device" in events

    def test_ten_phones_at_most(self, db, user):
        with db.begin() as conn:
            for n in range(10):
                conn.execute(
                    text("INSERT INTO notify_devices (user_id, label, token_hash) "
                         "VALUES (:u, 'p', :h)"),
                    {"u": str(user.id), "h": f"hash-{n}".encode()},
                )
        with _as(user) as c:
            assert c.post("/api/notify/devices", json={"label": "One more"}).status_code == 409

    def test_a_blank_label_is_refused(self, db, user):
        with _as(user) as c:
            assert c.post("/api/notify/devices", json={"label": "   "}).status_code == 422
            assert c.post("/api/notify/devices", json={"label": "x" * 61}).status_code == 422


# ── A phone reading ─────────────────────────────────────────────────────────


class TestTheInbox:
    def test_an_unknown_token_is_refused(self, db, anon_client):
        assert _inbox(anon_client, "not-a-token").status_code == 401
        assert anon_client.get("/api/notify/inbox").status_code == 401

    def test_unknown_tokens_are_rate_limited(self, db, anon_client):
        for _ in range(30):
            assert _inbox(anon_client, "not-a-token").status_code == 401
        assert _inbox(anon_client, "not-a-token").status_code == 429

    def test_only_this_persons_messages_after_the_cursor_and_unexpired(self, db, user):
        token = _phone(db, user)
        other = _person(db, "sam")
        notify.send(sender=notify.HOMESH, to=user.handle, title="First")
        notify.send(sender=notify.HOMESH, to=user.handle, title="Second", tag="t", path="/x")
        notify.send(sender=notify.HOMESH, to=other.handle, title="Not yours")
        notify.send(sender=notify.HOMESH, to=user.handle, title="Gone")
        with db.begin() as conn:
            conn.execute(text("UPDATE notify_messages SET expires_at = now() "
                              "WHERE title = 'Gone'"))

        with TestClient(app) as c:
            everything = _inbox(c, token).json()
            assert everything["role"] == "primary"
            assert [m["title"] for m in everything["messages"]] == ["First", "Second"]
            assert everything["messages"][1]["path"] == "/x"
            assert everything["messages"][1]["tag"] == "t"
            assert everything["messages"][1]["sender"] == "Homesh"

            first_id = everything["messages"][0]["id"]
            later = _inbox(c, token, after=first_id).json()
            assert [m["title"] for m in later["messages"]] == ["Second"]
            assert later["cursor"] == everything["cursor"]

    def test_nothing_new_keeps_the_cursor(self, db, user):
        token = _phone(db, user)
        with TestClient(app) as c:
            assert _inbox(c, token, after=7).json() == {
                "role": "primary", "cursor": 7, "messages": []
            }

    def test_never_more_than_fifty(self, db, user):
        token = _phone(db, user)
        for n in range(55):
            notify.send(sender=notify.HOMESH, to=user.handle, title=f"m{n}")
        with TestClient(app) as c:
            got = _inbox(c, token).json()
        assert len(got["messages"]) == 50
        assert got["cursor"] == got["messages"][-1]["id"]

    def test_a_long_poll_returns_soon_after_a_send(self, db, user):
        token = _phone(db, user)

        def later():
            time.sleep(0.5)
            notify.send(sender=notify.HOMESH, to=user.handle, title="Arrived")

        with TestClient(app) as c:
            threading.Thread(target=later).start()
            started = time.monotonic()
            got = _inbox(c, token, wait=10).json()
            took = time.monotonic() - started
        assert [m["title"] for m in got["messages"]] == ["Arrived"]
        assert took < 1.5

    def test_a_long_poll_with_nothing_returns_empty_after_the_wait(self, db, user):
        token = _phone(db, user)
        with TestClient(app) as c:
            started = time.monotonic()
            got = _inbox(c, token, wait=1).json()
            took = time.monotonic() - started
        assert got["messages"] == []
        assert 0.9 <= took < 3

    def test_too_many_waiters_return_at_once(self, monkeypatch):
        import asyncio

        monkeypatch.setattr(notify, "MAX_WAITERS", 0)
        started = time.monotonic()
        asyncio.run(notify.wait_for(uuid.uuid4(), 5))
        assert time.monotonic() - started < 0.5

    def test_a_send_between_looking_and_waiting_is_not_missed(self):
        import asyncio

        someone = uuid.uuid4()
        mark = notify.generation(someone)
        notify._wake([someone])
        started = time.monotonic()
        asyncio.run(notify.wait_for(someone, 5, since=mark))
        assert time.monotonic() - started < 0.5

    def test_last_seen_is_noted(self, db, user):
        token = _phone(db, user)
        with TestClient(app) as c:
            _inbox(c, token)
        with _as(user) as c:
            assert c.get("/api/notify/devices").json()[0]["last_seen_at"] is not None


# ── Credentials stay in their lanes ─────────────────────────────────────────


class TestOneCredentialOneDoor:
    def test_a_session_alone_cannot_read_the_inbox(self, db, user):
        with db.begin() as conn:
            session = create_session(conn, user.id, "browser")
        with TestClient(app) as c:
            c.cookies.set(SESSION_COOKIE, session)
            assert c.get("/api/notify/inbox").status_code == 401
            # The same cookie is a session everywhere else.
            assert c.get("/api/notify/messages").status_code == 200

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/notify/messages"),
            ("GET", "/api/notify/devices"),
            ("POST", "/api/notify/test"),
            ("GET", "/api/playlists"),
            ("GET", "/api/prefs"),
        ],
    )
    def test_a_device_token_opens_nothing_else(self, db, user, method, path):
        token = _phone(db, user)
        with TestClient(app) as c:
            answer = c.request(method, path, headers={"Authorization": f"Bearer {token}"})
        assert answer.status_code == 401

    def test_a_sending_key_opens_nothing_else(self, db, configured, anon_client):
        headers = {"Authorization": f"Bearer {KEY_ONE}"}
        assert anon_client.get("/api/notify/inbox", headers=headers).status_code == 401
        assert anon_client.get("/api/notify/messages", headers=headers).status_code == 401


# ── A person's own messages and phones ──────────────────────────────────────


class TestForAPerson:
    def test_my_messages_newest_first_and_marked_read(self, db, user):
        notify.send(sender=notify.HOMESH, to=user.handle, title="First")
        notify.send(sender=notify.HOMESH, to=user.handle, title="Second")
        with _as(user) as c:
            listed = c.get("/api/notify/messages").json()
            assert [m["title"] for m in listed] == ["Second", "First"]
            assert not any(m["read"] for m in listed)
            assert set(listed[0]) == {"id", "at", "sender", "title", "body", "path", "read"}

            marked = c.post("/api/notify/messages/read", json={"upto": listed[1]["id"]})
            assert marked.json() == {"marked": 1}
            again = c.get("/api/notify/messages").json()
        assert [m["read"] for m in again] == [False, True]

    def test_nobody_else_sees_them(self, db, user):
        notify.send(sender=notify.HOMESH, to=user.handle, title="Mine")
        other = _person(db, "sam")
        with _as(other) as c:
            assert c.get("/api/notify/messages").json() == []
            assert c.post("/api/notify/messages/read", json={"upto": 10**9}).json() == {
                "marked": 0
            }

    def test_removing_a_phone_stops_its_token(self, db, user):
        token = _phone(db, user)
        with _as(user) as c:
            phone = c.get("/api/notify/devices").json()[0]["id"]
            assert c.delete(f"/api/notify/devices/{phone}").status_code == 204
            assert c.get("/api/notify/devices").json() == []
        app.dependency_overrides.clear()
        with TestClient(app) as c:
            assert _inbox(c, token).status_code == 401

    def test_nobody_else_can_remove_it(self, db, user):
        token = _phone(db, user)
        with _as(user) as c:
            phone = c.get("/api/notify/devices").json()[0]["id"]
        other = _person(db, "sam", admin=True)
        with _as(other) as c:
            assert c.delete(f"/api/notify/devices/{phone}").status_code == 404
        app.dependency_overrides.clear()
        with TestClient(app) as c:
            assert _inbox(c, token).status_code == 200

    def test_the_test_message(self, db, user):
        with _as(user) as c:
            assert c.post("/api/notify/test").json() == {"queued": 1}
            for _ in range(4):
                c.post("/api/notify/test")
            assert c.post("/api/notify/test").status_code == 429
            listed = c.get("/api/notify/messages").json()
        assert listed[0]["sender"] == "Homesh"
        assert listed[0]["title"] == "Notifications are working"
        assert listed[0]["path"] == "/"

    def test_making_claims_is_limited(self, db, user):
        with _as(user) as c:
            for _ in range(10):
                assert c.post("/api/notify/devices", json={"label": "p"}).status_code == 200
            assert c.post("/api/notify/devices", json={"label": "p"}).status_code == 429


# ── Nothing secret leaks ────────────────────────────────────────────────────


def test_no_response_or_log_line_holds_a_key_or_a_token(db, user, configured, caplog):
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []

    token = _phone(db, user)
    with TestClient(app) as c:
        for key in (KEY_ONE, "KEYMARKER-wrong-" + "c" * 32):
            seen.append(_post(c, key=key, to=user.handle).text)
        seen.append(_inbox(c, token).text)
        seen.append(_inbox(c, "KEYMARKER-unknown-token").text)
    with _as(user) as c:
        seen.append(c.get("/api/notify/devices").text)
        seen.append(c.get("/api/notify/messages").text)

    for text_ in [*seen, caplog.text]:
        assert "KEYMARKER" not in text_
        assert token not in text_


# ── The standby ─────────────────────────────────────────────────────────────


class TestOnTheStandby:
    @pytest.mark.parametrize(
        "method,path,kind",
        [
            ("POST", "/api/notify/send", "local"),
            ("POST", "/api/notify/messages/read", "local"),
            ("GET", "/api/notify/inbox", "read"),
            ("GET", "/api/notify/messages", "read"),
            ("GET", "/api/notify/devices", "read"),
            ("POST", "/api/notify/devices", "refused"),
            ("DELETE", f"/api/notify/devices/{uuid.uuid4()}", "refused"),
            ("POST", "/api/notify/devices/claim", "refused"),
            ("POST", "/api/notify/test", "refused"),
        ],
    )
    def test_each_route_is_classified(self, method, path, kind):
        assert standby.classify(method, path) == kind

    def test_the_mailbox_is_kept_across_a_restore(self):
        assert "notify_messages" in standby.STANDBY_LOCAL
        assert "notify_devices" not in standby.STANDBY_LOCAL


# ── Housekeeping ────────────────────────────────────────────────────────────


class TestHousekeeping:
    def test_prune_removes_what_is_expired_or_spent(self, db, user):
        notify.send(sender=notify.HOMESH, to=user.handle, title="Keep")
        notify.send(sender=notify.HOMESH, to=user.handle, title="Gone")
        with db.begin() as conn:
            conn.execute(text("UPDATE notify_messages SET expires_at = now() WHERE title = 'Gone'"))
            now = datetime.now(UTC)
            minute = timedelta(minutes=1)
            # Spent, stale, and live: only the last survives.
            for n, (used, expires) in enumerate(
                [(now, now + minute), (None, now - minute), (None, now + minute)]
            ):
                conn.execute(
                    text(
                        "INSERT INTO notify_claims (code_hash, user_id, label, expires_at, "
                        "used_at) VALUES (:h, :u, 'p', :e, :used)"
                    ),
                    {"h": f"claim-{n}".encode(), "u": str(user.id), "e": expires, "used": used},
                )
        assert notify.prune() == 3
        with db.connect() as conn:
            assert conn.execute(text("SELECT title FROM notify_messages")).scalars().all() == [
                "Keep"
            ]
            assert conn.execute(text("SELECT count(*) FROM notify_claims")).scalar_one() == 1

    def test_ttl_is_capped_at_a_week(self, db, user):
        notify.send(sender=notify.HOMESH, to=user.handle, title="Long", ttl=timedelta(days=30))
        with db.connect() as conn:
            left = conn.execute(text("SELECT expires_at - now() FROM notify_messages")).scalar_one()
        assert left <= notify.MAX_TTL


class TestBackupsHaveStopped:
    def _shelf(self, *ages_hours: float, name: str = "homesh-20260101-000000.sql.gz"):
        from app.backups import Backup

        now = datetime(2026, 11, 13, 12, tzinfo=UTC)
        return now, [Backup(name, now - timedelta(hours=h), 1, 1) for h in ages_hours]

    def test_a_day_and_two_hours_is_stale(self):
        from app.upkeep import backups_stale

        now, shelf = self._shelf(27)
        assert backups_stale(shelf, now)
        now, shelf = self._shelf(25)
        assert not backups_stale(shelf, now)

    def test_a_copy_before_a_restore_does_not_count(self):
        from app.upkeep import backups_stale

        now, shelf = self._shelf(1, name="homesh-20260101-000000-before-restore.sql.gz")
        assert not backups_stale(shelf, now)  # nothing regular at all: says nothing
        _, regular = self._shelf(30)
        assert backups_stale(shelf + regular, now)

    def test_administrators_hear_once_a_day(self, db, user, monkeypatch):
        from app import backups, upkeep

        _person(db, "alex")
        now, shelf = self._shelf(30)
        monkeypatch.setattr(backups, "list_backups", lambda: shelf)
        monkeypatch.setattr(upkeep, "_backup_alert_day", None)

        assert upkeep.check_backups(now)
        assert not upkeep.check_backups(now + timedelta(hours=1))
        assert _rows(db) == [("tester", "Homesh")]
        with db.connect() as conn:
            title, tag = conn.execute(text("SELECT title, tag FROM notify_messages")).one()
        assert (title, tag) == ("Backups have stopped", "backup")
        assert upkeep.check_backups(now + timedelta(days=1))


# ── Handles that are words in `to` ──────────────────────────────────────────


class TestReservedHandles:
    """`to` reads "everyone" and "admins" as groups, so an account named either
    would turn a message for one person into a message for the house."""

    def test_the_test_message_reaches_only_the_caller_whatever_their_handle(self, db, user):
        odd = _person(db, "everyone")
        with _as(odd) as c:
            assert c.post("/api/notify/test").json() == {"queued": 1}
        assert _rows(db) == [("everyone", "Homesh")]

    def test_send_by_id_reaches_that_account_only(self, db, user):
        _person(db, "sam")
        assert notify.send(sender=notify.HOMESH, to=user.id, title="Only you") == 1
        assert _rows(db) == [("tester", "Homesh")]

    @pytest.mark.parametrize("handle", ["everyone", "Admins", "EVERYONE"])
    def test_an_invite_cannot_take_a_reserved_handle(self, db, user, handle):
        with _as(user) as c:
            answer = c.post(
                "/api/people/invites", json={"handle": handle, "display_name": "Odd"}
            )
        assert answer.status_code == 422
        with db.connect() as conn:
            made = conn.execute(
                text("SELECT count(*) FROM invites WHERE lower(handle) = lower(:h)"),
                {"h": handle},
            ).scalar_one()
        assert made == 0

    def test_the_first_account_cannot_take_one_either(self, db, anon_client, monkeypatch):
        from app import auth

        with db.begin() as conn:
            conn.execute(text("DELETE FROM users"))
        monkeypatch.setattr(auth, "_bootstrap_code", "ABCD-EFGH")
        answer = anon_client.post(
            "/api/auth/register/begin",
            json={"handle": "admins", "display_name": "First", "bootstrap_code": "ABCD-EFGH"},
        )
        assert answer.status_code == 422
