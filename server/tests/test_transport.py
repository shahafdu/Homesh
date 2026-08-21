"""Pause, resume and skip in a room.

Skipping is the interesting half. The receiver is handed one URL at a time and
has no idea a queue exists, so "next" cannot be a command to the hardware — the
server holds the queue and pushes the next track exactly as it pushed the first.
These check that the cursor moves, that the ends of the queue behave, and that a
room somebody may not use cannot be driven anyway.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.main import app
from app.scanner import scan_source
from app.security import CurrentUser, optional_user, require_user
from app.sources.local import LocalConnector


@pytest.fixture
def scanned(source, monkeypatch):
    sid, prefix, root = source
    name = prefix.rsplit("/", 1)[-1]
    monkeypatch.setenv("MEDIA_ROOTS", f"{name}={root}")

    from app.config import get_settings

    get_settings.cache_clear()
    scan_source(sid, LocalConnector(root))
    yield sid, prefix, root
    get_settings.cache_clear()


@pytest.fixture
def screen(client):
    """A zone whose renderer needs no hardware.

    These tests are about the queue, which the server owns, so they use the one
    renderer kind that has nothing to push to — a screen would answer 503 for
    want of a live socket and a receiver would want a receiver.
    """
    r = client.post(
        "/api/zones",
        json={
            "name": "Test Room",
            "renderer_kind": "browser",
            "device_key": "uuid:test-room",
            "audience": "everyone",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _tracks(db, limit=3) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT r.item_id FROM replicas r
                JOIN items i ON i.id = r.item_id
                WHERE i.kind = 'audio'
                ORDER BY r.filename
                LIMIT :n
                """
            ),
            {"n": limit},
        ).all()
    return [str(r[0]) for r in rows]


def _cursor(db, zone_id) -> int:
    with db.connect() as conn:
        return conn.execute(
            text("SELECT cursor FROM play_sessions WHERE zone_id = :z"), {"z": str(zone_id)}
        ).scalar_one()


def _state(db, zone_id) -> str:
    with db.connect() as conn:
        return conn.execute(
            text("SELECT state FROM play_sessions WHERE zone_id = :z"), {"z": str(zone_id)}
        ).scalar_one()


class TestSkipping:
    def test_next_advances_the_queue(self, client, db, scanned, screen):
        queue = _tracks(db)
        assert len(queue) >= 3
        client.post(f"/api/zones/{screen}/play", json={"item_ids": queue})
        assert _cursor(db, screen) == 0

        assert client.post(f"/api/zones/{screen}/next").status_code == 200
        assert _cursor(db, screen) == 1

    def test_previous_goes_back(self, client, db, scanned, screen):
        queue = _tracks(db)
        client.post(f"/api/zones/{screen}/play", json={"item_ids": queue, "start_index": 2})
        assert _cursor(db, screen) == 2

        client.post(f"/api/zones/{screen}/previous")
        assert _cursor(db, screen) == 1

    def test_previous_on_the_first_track_restarts_it(self, client, db, scanned, screen):
        """What every music player does, and so what the button is reached for."""
        queue = _tracks(db)
        client.post(f"/api/zones/{screen}/play", json={"item_ids": queue})

        assert client.post(f"/api/zones/{screen}/previous").status_code == 200
        assert _cursor(db, screen) == 0

    def test_next_past_the_end_stops(self, client, db, scanned, screen):
        """Rather than erroring, or wrapping round to the start uninvited."""
        queue = _tracks(db, 2)
        client.post(f"/api/zones/{screen}/play", json={"item_ids": queue, "start_index": 1})

        r = client.post(f"/api/zones/{screen}/next")
        assert r.status_code == 200
        assert _state(db, screen) == "idle"

    def test_skipping_an_idle_room_is_refused(self, client, db, scanned, screen):
        r = client.post(f"/api/zones/{screen}/next")
        assert r.status_code == 409


class TestPausing:
    def test_pause_and_resume_move_the_state(self, client, db, scanned, screen):
        client.post(f"/api/zones/{screen}/play", json={"item_ids": _tracks(db)})

        assert client.post(f"/api/zones/{screen}/pause").status_code == 200
        assert _state(db, screen) == "paused"

        assert client.post(f"/api/zones/{screen}/resume").status_code == 200
        assert _state(db, screen) == "playing"


class TestAccess:
    def test_a_room_outside_the_scope_cannot_be_driven(self, client, db, scanned, screen, user):
        """404 rather than 403: the room should not be confirmed to exist."""
        with db.begin() as conn:
            uid = conn.execute(
                text(
                    """
                    INSERT INTO users (handle, display_name, is_admin)
                    VALUES ('kid', 'Kid', FALSE) RETURNING id
                    """
                )
            ).scalar_one()
        client.post(f"/api/zones/{screen}/play", json={"item_ids": _tracks(db)})

        kid = CurrentUser(id=uid, handle="kid", display_name="Kid", is_admin=False)
        app.dependency_overrides[require_user] = lambda: kid
        app.dependency_overrides[optional_user] = lambda: kid

        for action in ("next", "previous", "pause", "resume"):
            r = client.post(f"/api/zones/{screen}/{action}")
            assert r.status_code == 404, f"{action} leaked a room the account cannot use"


class TestNowPlaying:
    """The tower has to say what is on, not merely that something is."""

    def test_a_room_reports_the_track_by_name(self, client, db, scanned, screen):
        client.post(f"/api/zones/{screen}/play", json={"item_ids": _tracks(db)})

        room = next(z for z in client.get("/api/zones").json() if z["id"] == screen)
        now = room["session"]["now"]
        assert now is not None, "a playing room said nothing about what it was playing"
        # The filename is the guarantee. Tags may be missing on any given file —
        # plenty of these fixtures have none — but the name always exists.
        assert now["filename"]
        # duration_ms joined these so the tower can draw a position bar: a bar
        # needs something to be a fraction of.
        assert set(now) == {"filename", "title", "artist", "duration_ms"}

    def test_the_name_follows_a_skip(self, client, db, scanned, screen):
        queue = _tracks(db)
        client.post(f"/api/zones/{screen}/play", json={"item_ids": queue})
        first = next(
            z for z in client.get("/api/zones").json() if z["id"] == screen
        )["session"]["now"]["filename"]

        client.post(f"/api/zones/{screen}/next")
        second = next(
            z for z in client.get("/api/zones").json() if z["id"] == screen
        )["session"]["now"]["filename"]

        assert first != second, "the tower kept naming the track that had been skipped"

    def test_an_idle_room_reports_nothing(self, client, db, scanned, screen):
        room = next(z for z in client.get("/api/zones").json() if z["id"] == screen)
        assert room["session"] is None


class TestSeekingInARoom:
    """Moving through what another room is playing.

    The control tower could start and stop a room but never move through it, so
    getting past a slow passage on the bedroom screen meant walking to the
    bedroom — on an hour-long video, which is exactly when nobody is in that
    room.
    """

    def test_a_receiver_says_it_cannot_rather_than_doing_nothing(
        self, client, db, screen, scanned
    ):
        """HEOS is fed a stream, and a live transcode has no index to seek in."""
        with db.connect() as conn:
            conn.execute(
                text("UPDATE renderers SET kind = 'heos' WHERE id = "
                     "(SELECT renderer_id FROM zones WHERE id = :z)"),
                {"z": screen},
            )
            conn.commit()

        r = client.post(f"/api/zones/{screen}/seek", json={"position_ms": 60_000})
        assert r.status_code == 409
        assert "cannot be moved through" in r.json()["detail"]

    def test_a_screen_that_is_not_connected_says_so(self, client, db, screen, scanned):
        with db.connect() as conn:
            conn.execute(
                text("UPDATE renderers SET kind = 'tvapp' WHERE id = "
                     "(SELECT renderer_id FROM zones WHERE id = :z)"),
                {"z": screen},
            )
            conn.commit()

        r = client.post(f"/api/zones/{screen}/seek", json={"position_ms": 60_000})
        assert r.status_code == 503
        assert "not connected" in r.json()["detail"]

    def test_a_negative_position_is_refused(self, client, screen):
        assert client.post(
            f"/api/zones/{screen}/seek", json={"position_ms": -1}
        ).status_code == 422


class TestAScreenThatGoesAway:
    """Closing the app on the television is not "still playing".

    It used to leave the session marked playing, so the tower showed a room
    happily playing to a screen that no longer existed — and pressing play there
    did nothing, because resuming asks the screen to carry on and there was no
    screen left to ask.
    """

    def test_the_session_goes_idle_but_keeps_its_place(self, client, db, screen, scanned):
        tracks = _tracks(db)
        assert client.post(
            f"/api/zones/{screen}/play", json={"item_ids": tracks}
        ).status_code in (200, 503)

        with db.connect() as conn:
            renderer = conn.execute(
                text("SELECT renderer_id FROM zones WHERE id = :z"), {"z": screen}
            ).scalar_one()
            conn.execute(
                text("UPDATE play_sessions SET state = 'playing', position_ms = 90000 "
                     "WHERE zone_id = :z"),
                {"z": screen},
            )
            conn.commit()

        from app.renderers import _end_session

        _end_session(renderer)

        with db.connect() as conn:
            state, position = conn.execute(
                text("SELECT state::text, position_ms FROM play_sessions WHERE zone_id = :z"),
                {"z": screen},
            ).one()

        assert state == "idle"
        # Where it got to is exactly what somebody wants when the television
        # comes back on. It is the claim that it is *playing* that was false.
        assert position == 90000

    def test_an_idle_session_is_left_alone(self, client, db, screen, scanned):
        with db.connect() as conn:
            renderer = conn.execute(
                text("SELECT renderer_id FROM zones WHERE id = :z"), {"z": screen}
            ).scalar_one()
            conn.execute(
                text("INSERT INTO play_sessions (zone_id, state, queue, cursor) "
                     "VALUES (:z, 'idle', '{}', 0) ON CONFLICT (zone_id) "
                     "DO UPDATE SET state = 'idle'"),
                {"z": screen},
            )
            conn.commit()

        from app.renderers import _end_session

        _end_session(renderer)

        with db.connect() as conn:
            state = conn.execute(
                text("SELECT state::text FROM play_sessions WHERE zone_id = :z"),
                {"z": screen},
            ).scalar_one()
        assert state == "idle"
