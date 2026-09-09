"""Showing a folder of photographs, here or in a room.

Two things are being checked. That gathering a folder means everything beneath
it, because that is how photographs are kept — a year, with months inside it,
with a weekend inside that — and that a queue moves on by itself, which a
slideshow forces because a photograph never ends on its own.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.main import app
from app.scanner import scan_source
from app.security import CurrentUser, require_user
from app.sources.local import LocalConnector


@pytest.fixture
def scanned(source):
    sid, prefix, root = source
    scan_source(sid, LocalConnector(root))
    return sid, prefix, root


class TestGatheringAFolder:
    def test_it_reaches_into_subfolders(self, client, scanned):
        """The whole point: a year of photographs is not stored flat.

        Asking for "Photos" and being given only the loose files at the top of
        it, while the ones inside 2019/Greece are ignored, would be a slideshow
        of the wrong thing.
        """
        _sid, prefix, _root = scanned

        deep = client.get("/api/slideshow", params={"under": f"{prefix}/Photos/2019/Greece"})
        wide = client.get("/api/slideshow", params={"under": f"{prefix}/Photos"})
        assert deep.status_code == 200, deep.text
        assert wide.status_code == 200, wide.text

        assert deep.json()["count"] > 0, "the fixture has photographs down there"
        assert set(deep.json()["item_ids"]) <= set(wide.json()["item_ids"])
        assert wide.json()["count"] >= deep.json()["count"]

    def test_only_photographs(self, client, db, scanned):
        """A folder holds films and documents too, and neither is a slide."""
        _sid, prefix, _root = scanned
        found = client.get("/api/slideshow", params={"under": prefix}).json()["item_ids"]
        assert found, "the fixture has photographs somewhere"

        with db.connect() as conn:
            kinds = {
                r[0]
                for r in conn.execute(
                    text("SELECT DISTINCT kind::text FROM items WHERE id = ANY(:ids)"),
                    {"ids": [uuid.UUID(i) for i in found]},
                )
            }
        assert kinds == {"photo"}

    def test_order_is_stable_and_by_folder(self, client, scanned):
        """A run through a year should arrive in the order it happened.

        Shuffle is applied by whatever is playing, not here, so this has to be
        the same list every time or "in order" would mean nothing.
        """
        _sid, prefix, _root = scanned
        once = client.get("/api/slideshow", params={"under": prefix}).json()["item_ids"]
        twice = client.get("/api/slideshow", params={"under": prefix}).json()["item_ids"]
        assert once == twice

    def test_a_folder_outside_your_scope_is_not_confirmed(self, client):
        assert client.get("/api/slideshow", params={"under": "/local/nowhere"}).status_code == 404

    def test_it_needs_an_account(self, anon_client, scanned):
        _sid, prefix, _root = scanned
        assert anon_client.get("/api/slideshow", params={"under": prefix}).status_code == 401


class TestSlideshowInARoom:
    """The settings belong to the room, because the phone that started it leaves."""

    def _photo_zone(self, client, db):
        r = client.post(
            "/api/zones",
            json={
                "name": "Snug",
                "renderer_kind": "tvapp",
                "device_key": f"uuid:test::snug::{uuid.uuid4()}",
                "preroll": [],
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def _photos(self, db, limit=3):
        with db.connect() as conn:
            return [
                str(r[0])
                for r in conn.execute(
                    text(
                        """
                        SELECT i.id FROM items i JOIN replicas r ON r.item_id = i.id
                        WHERE i.kind = 'photo' ORDER BY r.filename LIMIT :n
                        """
                    ),
                    {"n": limit},
                ).all()
            ]

    def test_the_hold_and_transition_are_recorded(self, client, db, scanned):
        """A skip mid-slideshow has to keep them, so they cannot live in the request."""
        zone_id = self._photo_zone(client, db)
        photos = self._photos(db)

        # The screen is not connected, so the push fails — but the session is
        # written first and independently, which is the behaviour being used.
        client.post(
            f"/api/zones/{zone_id}/play",
            json={"item_ids": photos, "photo_ms": 8000, "transition": "zoom"},
        )

        with db.connect() as conn:
            hold, transition = conn.execute(
                text("SELECT photo_ms, transition FROM play_sessions WHERE zone_id = :z"),
                {"z": zone_id},
            ).one()
        assert (hold, transition) == (8000, "zoom")

    def test_an_ordinary_queue_has_no_hold(self, client, db, scanned):
        """Its absence is the definition: everything else ends by itself."""
        zone_id = self._photo_zone(client, db)
        client.post(f"/api/zones/{zone_id}/play", json={"item_ids": self._photos(db)})

        with db.connect() as conn:
            hold = conn.execute(
                text("SELECT photo_ms FROM play_sessions WHERE zone_id = :z"),
                {"z": zone_id},
            ).scalar_one()
        assert hold is None

    @pytest.mark.parametrize("bad", [999, 600_001])
    def test_absurd_holds_are_refused(self, client, db, scanned, bad):
        """A second is as fast as anybody can look; ten minutes is not a limit."""
        zone_id = self._photo_zone(client, db)
        r = client.post(
            f"/api/zones/{zone_id}/play",
            json={"item_ids": self._photos(db), "photo_ms": bad},
        )
        assert r.status_code == 422

    def test_an_invented_transition_is_refused(self, client, db, scanned):
        zone_id = self._photo_zone(client, db)
        r = client.post(
            f"/api/zones/{zone_id}/play",
            json={"item_ids": self._photos(db), "transition": "barrel-roll"},
        )
        assert r.status_code == 422

    def test_who_started_it_is_kept(self, client, db, scanned, user):
        """Without this a queue cannot advance: there is no account to act as."""
        zone_id = self._photo_zone(client, db)
        client.post(f"/api/zones/{zone_id}/play", json={"item_ids": self._photos(db)})

        with db.connect() as conn:
            started_by = conn.execute(
                text("SELECT started_by FROM play_sessions WHERE zone_id = :z"),
                {"z": zone_id},
            ).scalar_one()
        assert started_by is not None


class TestAQueueMovesOnByItself:
    """The screen says an item ended; the room plays the next one.

    This was missing entirely. The screen reported "ended", the server broadcast
    it and did nothing else, so a folder of songs sent to a television was one
    song. It went unnoticed because the phone is usually still in the room and
    pressing next looks like ordinary use.
    """

    def _zone_with_queue(self, client, db):
        r = client.post(
            "/api/zones",
            json={
                "name": f"Study {uuid.uuid4().hex[:6]}",
                "renderer_kind": "tvapp",
                "device_key": f"uuid:test::study::{uuid.uuid4()}",
                "preroll": [],
            },
        )
        zone_id = r.json()["id"]

        with db.connect() as conn:
            items = [
                str(x[0])
                for x in conn.execute(
                    text(
                        "SELECT i.id FROM items i JOIN replicas r ON r.item_id = i.id "
                        "WHERE i.kind = 'photo' ORDER BY r.filename LIMIT 3"
                    )
                ).all()
            ]
        client.post(
            f"/api/zones/{zone_id}/play",
            json={"item_ids": items, "photo_ms": 5000},
        )

        # No screen is connected in a test, so the push fails and the session is
        # marked idle -- correctly, since nothing is playing. A real slideshow is
        # reporting "ended" precisely because a real screen is showing it, so the
        # state is put back to what that screen would have set.
        with db.begin() as conn:
            conn.execute(
                text("UPDATE play_sessions SET state = 'playing' WHERE zone_id = :z"),
                {"z": zone_id},
            )
        return zone_id, items

    async def test_ended_moves_the_cursor_on(self, client, db, scanned):
        from app.zones import advance_when_finished

        zone_id, items = self._zone_with_queue(client, db)
        assert len(items) >= 2, "needs a queue to advance through"

        await advance_when_finished(uuid.UUID(zone_id))

        with db.connect() as conn:
            cursor = conn.execute(
                text("SELECT cursor FROM play_sessions WHERE zone_id = :z"),
                {"z": zone_id},
            ).scalar_one()
        assert cursor == 1, "the room moved on without anybody pressing next"

    async def test_a_session_with_no_starter_stops(self, client, db, scanned):
        """The safe direction. An account removed must not become a way in."""
        from app.zones import advance_when_finished

        zone_id, _items = self._zone_with_queue(client, db)
        with db.begin() as conn:
            conn.execute(
                text("UPDATE play_sessions SET started_by = NULL WHERE zone_id = :z"),
                {"z": zone_id},
            )

        await advance_when_finished(uuid.UUID(zone_id))

        with db.connect() as conn:
            cursor = conn.execute(
                text("SELECT cursor FROM play_sessions WHERE zone_id = :z"),
                {"z": zone_id},
            ).scalar_one()
        assert cursor == 0

    async def test_an_idle_room_is_left_alone(self, client, db, scanned):
        """A stopped room reporting a stale "ended" must not start playing again."""
        from app.zones import advance_when_finished

        zone_id, _items = self._zone_with_queue(client, db)
        with db.begin() as conn:
            conn.execute(
                text("UPDATE play_sessions SET state = 'idle' WHERE zone_id = :z"),
                {"z": zone_id},
            )

        await advance_when_finished(uuid.UUID(zone_id))

        with db.connect() as conn:
            cursor = conn.execute(
                text("SELECT cursor FROM play_sessions WHERE zone_id = :z"),
                {"z": zone_id},
            ).scalar_one()
        assert cursor == 0


class TestOnlyWhatYouCanReach:
    def test_a_slideshow_is_not_a_way_round_access(self, client, scanned, db):
        """Gathering is recursive, so it is exactly the shape that could be one."""
        _sid, prefix, _root = scanned

        ordinary = CurrentUser(
            id=uuid.uuid4(), handle="guest", display_name="Guest", is_admin=False
        )
        app.dependency_overrides[require_user] = lambda: ordinary
        try:
            # An account with no rules reaches nothing: access is granted, never
            # assumed, so an empty rule list means empty.
            r = client.get("/api/slideshow", params={"under": prefix})
        finally:
            app.dependency_overrides.pop(require_user, None)

        assert r.status_code == 404 or r.json()["count"] == 0
