"""Alerts to administrators: what is told, to whom, and what is never said.

The notification mailbox is read straight from the table, because what matters
here is who got a row and what it says, not how a phone fetches it.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import alerts, notify, throttle
from app.main import app
from app.security import SESSION_COOKIE, CurrentUser, audit, create_session

MARKER = "MARKERNAME-xyzzy"
NOBODY = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _quick_bursts(monkeypatch):
    monkeypatch.setattr(alerts, "BURST_SECONDS", 1.0)


def _person(db, handle: str, admin: bool = False, **prefs) -> CurrentUser:
    with db.begin() as conn:
        uid = conn.execute(
            text(
                "INSERT INTO users (handle, display_name, is_admin, prefs) "
                "VALUES (:h, :n, :a, CAST(:p AS jsonb)) RETURNING id"
            ),
            {"h": handle, "n": handle.title(), "a": admin, "p": json.dumps(prefs)},
        ).scalar_one()
    return CurrentUser(id=uid, handle=handle, display_name=handle.title(), is_admin=admin)


def _messages(db, handle: str | None = None) -> list:
    sql = (
        "SELECT u.handle, m.sender, m.title, m.body, m.tag FROM notify_messages m "
        "JOIN users u ON u.id = m.user_id ORDER BY m.id"
    )
    with db.connect() as conn:
        rows = conn.execute(text(sql)).all()
    return [r for r in rows if handle is None or r.handle == handle]


def _session(db, person: CurrentUser) -> str:
    with db.begin() as conn:
        return create_session(conn, person.id, None)


def _wait_for(db, count: int, seconds: float = 3.0) -> list:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        rows = _messages(db)
        if len(rows) >= count:
            return rows
        time.sleep(0.05)
    return _messages(db)


class TestSecurityEvents:
    def test_a_sign_in_tells_each_administrator_who_wants_to_know(self, db):
        _person(db, "boss", admin=True)
        _person(db, "quiet", admin=True, alert_signins="off")
        sam = _person(db, "sam")
        with db.begin() as conn:
            audit(conn, "auth.login", sam.id, {"label": "Tablet"}, None)
        rows = _messages(db)
        assert [r.handle for r in rows] == ["boss"]
        assert rows[0].title == "Sam signed in"
        assert rows[0].body == "Tablet"
        assert rows[0].sender == "Homesh"

    def test_nothing_goes_to_someone_who_is_not_an_administrator(self, db):
        _person(db, "boss", admin=True)
        sam = _person(db, "sam")
        alerts.on_audit("auth.login", sam.id, {})
        assert [r.handle for r in _messages(db)] == ["boss"]

    def test_failed_sign_ins_speak_from_the_fifth_and_then_once_an_hour(self, db):
        _person(db, "boss", admin=True)
        for _ in range(4):
            alerts.on_audit("auth.login.failed", None, {})
        assert _messages(db) == []
        alerts.on_audit("auth.login.failed", None, {})
        rows = _messages(db)
        assert len(rows) == 1 and rows[0].title == "Failed sign-in attempts"
        assert rows[0].body == "5 in the last 10 minutes"
        for _ in range(10):
            alerts.on_audit("auth.login.failed", None, {})
        assert len(_messages(db)) == 1

    def test_a_copied_passkey_reaches_an_administrator_who_switched_alerts_off(self, db):
        _person(db, "quiet", admin=True, alert_signins="off", alert_changes="off")
        sam = _person(db, "sam")
        alerts.on_audit("auth.login", sam.id, {})
        assert _messages(db) == []
        alerts.on_audit("auth.signcount.anomaly", sam.id, {})
        rows = _messages(db)
        assert [r.title for r in rows] == ["A passkey may have been copied"]
        assert "Sam" in rows[0].body

    @pytest.mark.parametrize(
        ("event", "title"),
        [
            ("auth.passkey.added", "Sam added a passkey"),
            ("auth.passkey.removed", "Sam removed a passkey"),
            ("auth.device_link.claimed", "Sam signed in a new device with a code"),
        ],
    )
    def test_new_passkeys_and_devices_are_reported(self, db, event, title):
        _person(db, "boss", admin=True)
        sam = _person(db, "sam")
        alerts.on_audit(event, sam.id, {})
        assert [r.title for r in _messages(db)] == [title]

    def test_an_invited_person_joining_is_reported(self, db):
        _person(db, "boss", admin=True)
        # Not yet committed when the audit runs, so the handle is the fallback.
        alerts.on_audit("auth.register", None, {"invited": True, "handle": "newcomer"})
        alerts.on_audit("auth.register", None, {"invited": False, "handle": "first"})
        assert [r.title for r in _messages(db)] == ["newcomer joined Homesh"]

    def test_a_failing_alert_does_not_break_the_sign_in(self, db, monkeypatch):
        _person(db, "boss", admin=True)
        sam = _person(db, "sam")

        def boom(**_):
            raise RuntimeError("mailbox down")

        monkeypatch.setattr(notify, "send", boom)
        with db.begin() as conn:
            audit(conn, "auth.login", sam.id, {}, None)  # must not raise
        with db.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM audit_log WHERE user_id = :u"), {"u": str(sam.id)}
            ).scalar() == 1

    def test_a_pairing_request_is_reported_once_an_hour(self, db):
        _person(db, "boss", admin=True)
        alerts.on_pairing_started()
        alerts.on_pairing_started()
        assert [r.title for r in _messages(db)] == ["A screen is asking to be paired"]

    def test_a_tripped_limit_is_reported_once_per_door_per_window(self, db):
        _person(db, "boss", admin=True)
        limit = throttle.Limit(1, timedelta(minutes=5))
        for caller in ("a", "b", "c"):
            throttle.hit("codes", caller, limit)
            with pytest.raises(HTTPException):
                throttle.hit("codes", caller, limit)
        rows = _messages(db)
        assert [r.title for r in rows] == ["Too many attempts"]
        assert "sign-in codes" in rows[0].body


class TestChanges:
    def _post(self, db, person, path, **body):
        with TestClient(app) as c:
            c.cookies.set(SESSION_COOKIE, _session(db, person))
            return c.post(path, json=body)

    def test_a_burst_of_changes_is_one_message_that_counts_them(self, db, monkeypatch):
        # Long enough that a slow machine posts all three inside one burst.
        monkeypatch.setattr(alerts, "BURST_SECONDS", 4.0)
        _person(db, "boss", admin=True)
        sam = _person(db, "sam")
        for i in range(3):
            r = self._post(db, sam, "/api/playlists", name=f"{MARKER}-{i}")
            assert r.status_code == 201, r.text
        _wait_for(db, 1, seconds=8.0)
        time.sleep(0.4)
        rows = _messages(db)
        assert [(r.handle, r.title, r.body) for r in rows] == [
            ("boss", "Sam made changes", "3 playlists")
        ]
        assert rows[0].tag.startswith("changes-")

    def test_names_never_appear_in_an_alert(self, db):
        _person(db, "boss", admin=True)
        sam = _person(db, "sam")
        self._post(db, sam, "/api/playlists", name=MARKER)
        rows = _wait_for(db, 1)
        assert rows
        assert all(MARKER not in f"{r.title} {r.body}" for r in rows)

    def test_the_person_who_made_the_changes_is_not_told(self, db):
        boss = _person(db, "boss", admin=True)
        _person(db, "other", admin=True)
        assert self._post(db, boss, "/api/playlists", name="mine").status_code == 201
        rows = _wait_for(db, 1)
        assert [r.handle for r in rows] == ["other"]

    def test_an_administrator_changing_things_alone_tells_no_one(self, db):
        boss = _person(db, "boss", admin=True)
        self._post(db, boss, "/api/playlists", name="mine")
        time.sleep(1.6)
        assert _messages(db) == []

    def test_an_administrator_can_switch_change_alerts_off(self, db):
        _person(db, "boss", admin=True, alert_changes="off")
        sam = _person(db, "sam")
        self._post(db, sam, "/api/playlists", name="x")
        time.sleep(1.6)
        assert _messages(db) == []

    def test_using_the_house_is_not_a_change(self, db):
        _person(db, "boss", admin=True)
        sam = _person(db, "sam")
        with TestClient(app) as c:
            c.cookies.set(SESSION_COOKIE, _session(db, sam))
            c.put("/api/prefs", json={"palette": "studio"})
            c.post(f"/api/zones/{NOBODY}/pause")
            c.post(f"/api/zones/{NOBODY}/volume", json={"level": 5})
        time.sleep(1.6)
        assert _messages(db) == []

    def test_a_refused_change_is_not_reported(self, db):
        _person(db, "boss", admin=True)
        sam = _person(db, "sam")
        assert self._post(db, sam, "/api/playlists").status_code == 422
        assert self._post(
            db, sam, f"/api/playlists/{NOBODY}/items", item_ids=[NOBODY]
        ).status_code >= 400
        time.sleep(1.6)
        assert _messages(db) == []

    def test_a_change_replayed_from_the_standby_is_not_counted_twice(self, db):
        _person(db, "boss", admin=True)
        sam = _person(db, "sam")

        async def replayed(scope, receive, send):
            scope = dict(scope)
            scope["state"] = {"replay_user": sam, "from_standby": True}
            await app(scope, receive, send)

        async def go():
            transport = httpx.ASGITransport(app=replayed)
            async with httpx.AsyncClient(transport=transport, base_url="http://replay") as c:
                return await c.post("/api/playlists", json={"name": "x"})

        assert asyncio.run(go()).status_code == 201
        time.sleep(1.6)
        assert _messages(db) == []

    def test_a_replay_that_is_not_the_standbys_is_counted(self, db):
        _person(db, "boss", admin=True)
        sam = _person(db, "sam")

        async def assistant(scope, receive, send):
            scope = dict(scope)
            scope["state"] = {"replay_user": sam}
            await app(scope, receive, send)

        async def go():
            transport = httpx.ASGITransport(app=assistant)
            async with httpx.AsyncClient(transport=transport, base_url="http://replay") as c:
                return await c.post("/api/playlists", json={"name": "x"})

        assert asyncio.run(go()).status_code == 201
        assert len(_wait_for(db, 1)) == 1


class TestWatchedTable:
    @pytest.mark.parametrize(
        ("method", "path", "kind"),
        [
            ("POST", "/api/playlists", "playlists"),
            ("PUT", "/api/library/items/abc/tags", "song and file details"),
            ("POST", "/api/zones", "rooms"),
            ("DELETE", "/api/zones/abc", "rooms"),
            ("DELETE", "/api/notify/devices/abc", "notification phones"),
            ("POST", "/api/backups/restore", "backups"),
        ],
    )
    def test_watched(self, method, path, kind):
        assert alerts.watched_kind(method, path) == kind

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/api/zones/abc/pause"),
            ("POST", "/api/zones/abc/volume"),
            ("POST", "/api/zones/abc"),
            ("PUT", "/api/prefs"),
            ("GET", "/api/playlists"),
            ("POST", "/api/search"),
        ],
    )
    def test_not_watched(self, method, path):
        assert alerts.watched_kind(method, path) is None
