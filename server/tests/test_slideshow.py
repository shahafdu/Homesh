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


class TestASlideshowWithNoEnd:
    """Forever, with repeats, rather than forever over the same seven hours.

    Five thousand photographs at five seconds each is seven hours before a queue
    wraps. In a house with 105,000 that is not "forever" in any sense worth
    having, so an endless slideshow goes back to the folder for more.
    """

    def _paged(self, client, prefix, **params):
        r = client.get("/api/slideshow", params={"under": prefix, **params})
        assert r.status_code == 200, r.text
        return r.json()

    def test_paging_walks_past_the_first_page(self, client, scanned):
        """Without an offset an endless slideshow would circle its first page."""
        _sid, prefix, _root = scanned
        everything = self._paged(client, prefix)["item_ids"]
        assert len(everything) >= 2, "needs more than one photo to page through"

        second = self._paged(client, prefix, offset=1)["item_ids"]
        assert second == everything[1:], "offset skips, and skips in the same order"

    def test_walking_off_the_end_returns_nothing(self, client, scanned):
        """Which is how the wrap-around is detected without ever counting."""
        _sid, prefix, _root = scanned
        total = self._paged(client, prefix)["total"]
        assert self._paged(client, prefix, offset=total)["item_ids"] == []

    def test_counting_can_be_skipped(self, client, scanned):
        """An endless slideshow never reaches an end, so the size tells it nothing."""
        _sid, prefix, _root = scanned
        uncounted = self._paged(client, prefix, count=0)
        assert uncounted["total"] is None
        assert uncounted["truncated"] is False
        assert uncounted["item_ids"], "it still returns photographs"

    def test_shuffled_draws_are_independent(self, client, scanned):
        """Repeats are the point: refusing them would mean remembering everything."""
        _sid, prefix, _root = scanned
        once = self._paged(client, prefix, shuffle=1, count=0)["item_ids"]
        twice = self._paged(client, prefix, shuffle=1, count=0)["item_ids"]
        assert set(once) == set(twice), (
            "the fixture is small enough that both draws take all of it — "
            "what matters is that neither refused to repeat what the other had"
        )

    def test_a_room_records_where_to_get_more(self, client, db, scanned):
        """Without the folder there is nothing to go back to."""
        _sid, prefix, _root = scanned
        r = client.post(
            "/api/zones",
            json={
                "name": f"Hall {uuid.uuid4().hex[:6]}",
                "renderer_kind": "tvapp",
                "device_key": f"uuid:test::hall::{uuid.uuid4()}",
                "preroll": [],
            },
        )
        zone_id = r.json()["id"]
        photos = client.get("/api/slideshow", params={"under": prefix}).json()["item_ids"]

        client.post(
            f"/api/zones/{zone_id}/play",
            json={
                "item_ids": photos,
                "photo_ms": 5000,
                "under": prefix,
                "endless": True,
            },
        )

        with db.connect() as conn:
            under, endless, offset = conn.execute(
                text(
                    "SELECT photo_under, photo_endless, photo_offset "
                    "FROM play_sessions WHERE zone_id = :z"
                ),
                {"z": zone_id},
            ).one()
        assert (under, endless) == (prefix, True)
        # The first queue is page one, so refilling continues rather than
        # repeating what has just been sent.
        assert offset == len(photos)

    def test_an_ordinary_queue_is_not_endless(self, client, db, scanned):
        zone_id = client.post(
            "/api/zones",
            json={
                "name": f"Porch {uuid.uuid4().hex[:6]}",
                "renderer_kind": "tvapp",
                "device_key": f"uuid:test::porch::{uuid.uuid4()}",
                "preroll": [],
            },
        ).json()["id"]
        photos = client.get(
            "/api/slideshow", params={"under": scanned[1]}
        ).json()["item_ids"]

        client.post(f"/api/zones/{zone_id}/play", json={"item_ids": photos})

        with db.connect() as conn:
            under, endless = conn.execute(
                text("SELECT photo_under, photo_endless FROM play_sessions WHERE zone_id = :z"),
                {"z": zone_id},
            ).one()
        assert (under, endless) == (None, False)

    def test_a_room_refills_when_the_queue_runs_out(self, client, db, scanned):
        """The whole point, and the thing that cannot be checked by reading."""
        from app.zones import _refill_endless

        _sid, prefix, _root = scanned
        zone_id = client.post(
            "/api/zones",
            json={
                "name": f"Loft {uuid.uuid4().hex[:6]}",
                "renderer_kind": "tvapp",
                "device_key": f"uuid:test::loft::{uuid.uuid4()}",
                "preroll": [],
            },
        ).json()["id"]
        photos = client.get("/api/slideshow", params={"under": prefix}).json()["item_ids"]

        client.post(
            f"/api/zones/{zone_id}/play",
            json={"item_ids": photos, "photo_ms": 5000, "under": prefix, "endless": True},
        )

        with db.connect() as conn:
            starter = conn.execute(
                text("SELECT started_by FROM play_sessions WHERE zone_id = :z"),
                {"z": zone_id},
            ).scalar_one()
            row = conn.execute(
                text("SELECT handle, display_name, is_admin FROM users WHERE id = :id"),
                {"id": starter},
            ).one()

        who = CurrentUser(id=starter, handle=row[0], display_name=row[1], is_admin=row[2])

        # The first refill walks off the end of the folder, so it wraps to the
        # beginning -- which is the behaviour asked for, and needs no count.
        fresh = _refill_endless(uuid.UUID(zone_id), who)
        assert fresh, "the room went back to the folder rather than stopping"
        assert set(fresh) <= set(photos)

    def test_a_queue_that_is_not_endless_refuses_to_refill(self, client, db, scanned):
        from app.zones import _refill_endless

        zone_id = client.post(
            "/api/zones",
            json={
                "name": f"Shed {uuid.uuid4().hex[:6]}",
                "renderer_kind": "tvapp",
                "device_key": f"uuid:test::shed::{uuid.uuid4()}",
                "preroll": [],
            },
        ).json()["id"]
        photos = client.get(
            "/api/slideshow", params={"under": scanned[1]}
        ).json()["item_ids"]
        client.post(f"/api/zones/{zone_id}/play", json={"item_ids": photos})

        with db.connect() as conn:
            starter = conn.execute(
                text("SELECT started_by FROM play_sessions WHERE zone_id = :z"),
                {"z": zone_id},
            ).scalar_one()
        who = CurrentUser(id=starter, handle="x", display_name="X", is_admin=True)

        assert _refill_endless(uuid.UUID(zone_id), who) is None


class TestSendingPaperToARoom:
    """A document on the television in the room where it is needed.

    It was impossible, and for two reasons that both looked like design. The
    room picker had a list of kinds that had simply never been extended, and the
    endpoint that renders a document to PDF took a session -- which a television
    does not have, because it authenticates as a renderer and fetches media by
    signed URL, exactly as it does for every other kind.
    """

    def _a_document(self, db):
        with db.connect() as conn:
            return conn.execute(
                text(
                    "SELECT i.id FROM items i JOIN replicas r ON r.item_id = i.id "
                    "WHERE i.kind = 'doc' ORDER BY r.filename LIMIT 1"
                )
            ).scalar_one_or_none()

    def test_a_signed_url_is_accepted(self, anon_client, db, scanned, user):
        """The television's only way in, and it was refused outright.

        Asserted as "not 401" rather than "200" deliberately: what was broken
        was authentication, and everything past it -- reaching the file,
        running LibreOffice -- belongs to the conversion tests and needs a
        mounted source this one does not have. A 503 here is the endpoint
        saying "I know who you are and the file is elsewhere", which is exactly
        the behaviour being checked.
        """
        from app.signing import mint

        item = self._a_document(db)
        if item is None:
            pytest.skip("the fixture has no documents")

        token = mint(item, user.id, "stream", ttl=600)
        r = anon_client.get(f"/api/documents/{item}", params={"t": token})
        assert r.status_code != 401, "a valid signed URL was turned away"

    def test_no_token_and_no_session_is_refused(self, anon_client, db, scanned):
        item = self._a_document(db)
        if item is None:
            pytest.skip("the fixture has no documents")
        assert anon_client.get(f"/api/documents/{item}").status_code == 401

    def test_a_token_for_another_item_does_not_open_this_one(
        self, anon_client, db, scanned, user
    ):
        """A signed URL is bound to one file. It has to stay bound to it."""
        from app.signing import mint

        item = self._a_document(db)
        if item is None:
            pytest.skip("the fixture has no documents")

        with db.connect() as conn:
            other = conn.execute(
                text("SELECT id FROM items WHERE id <> :id LIMIT 1"), {"id": str(item)}
            ).scalar_one()

        token = mint(other, user.id, "stream", ttl=600)
        assert anon_client.get(
            f"/api/documents/{item}", params={"t": token}
        ).status_code == 401

    def test_a_thumb_token_does_not_open_a_document(self, anon_client, db, scanned, user):
        """Purposes are separate for a reason; a thumbnail grant is not a read."""
        from app.signing import mint

        item = self._a_document(db)
        if item is None:
            pytest.skip("the fixture has no documents")

        token = mint(item, user.id, "thumb", ttl=600)
        assert anon_client.get(
            f"/api/documents/{item}", params={"t": token}
        ).status_code == 401


class TestChangingASlideshowWhileItRuns:
    """Second thoughts, without starting again from the first photograph.

    The settings lived only in the dialogue that started it, so changing one
    meant stopping the room and beginning again -- which for a folder of a
    hundred thousand photographs is not a small thing to ask of somebody who
    only wanted them to go past more slowly.
    """

    def _showing(self, client, db, **extra):
        zone_id = client.post(
            "/api/zones",
            json={
                "name": f"Gallery {uuid.uuid4().hex[:6]}",
                "renderer_kind": "tvapp",
                "device_key": f"uuid:test::gallery::{uuid.uuid4()}",
                "preroll": [],
            },
        ).json()["id"]

        with db.connect() as conn:
            photos = [
                str(r[0])
                for r in conn.execute(
                    text(
                        "SELECT i.id FROM items i JOIN replicas r ON r.item_id = i.id "
                        "WHERE i.kind = 'photo' ORDER BY r.filename LIMIT 3"
                    )
                ).all()
            ]
        client.post(
            f"/api/zones/{zone_id}/play",
            json={"item_ids": photos, "photo_ms": 5000, "transition": "fade", **extra},
        )
        return zone_id

    def _settings(self, db, zone_id):
        with db.connect() as conn:
            return conn.execute(
                text("SELECT photo_ms, transition FROM play_sessions WHERE zone_id = :z"),
                {"z": zone_id},
            ).one()

    def test_the_hold_can_be_changed(self, client, db, scanned):
        zone_id = self._showing(client, db)
        r = client.post(f"/api/zones/{zone_id}/slideshow", json={"photo_ms": 15000})
        assert r.status_code == 200, r.text
        assert self._settings(db, zone_id) == (15000, "fade")

    def test_the_transition_can_be_changed(self, client, db, scanned):
        zone_id = self._showing(client, db)
        client.post(f"/api/zones/{zone_id}/slideshow", json={"transition": "zoom"})
        assert self._settings(db, zone_id) == (5000, "zoom")

    def test_one_does_not_reset_the_other(self, client, db, scanned):
        """They are two separate decisions, made at two separate moments."""
        zone_id = self._showing(client, db)
        client.post(f"/api/zones/{zone_id}/slideshow", json={"transition": "slide"})
        client.post(f"/api/zones/{zone_id}/slideshow", json={"photo_ms": 30000})
        assert self._settings(db, zone_id) == (30000, "slide")

    @pytest.mark.parametrize("bad", [{"photo_ms": 10}, {"transition": "barrel-roll"}])
    def test_nonsense_is_refused(self, client, db, scanned, bad):
        zone_id = self._showing(client, db)
        assert client.post(f"/api/zones/{zone_id}/slideshow", json=bad).status_code == 422

    def test_a_room_that_is_not_showing_one_says_so(self, client, db, scanned):
        """Rather than quietly writing settings nothing will ever read."""
        zone_id = client.post(
            "/api/zones",
            json={
                "name": f"Quiet {uuid.uuid4().hex[:6]}",
                "renderer_kind": "tvapp",
                "device_key": f"uuid:test::quiet::{uuid.uuid4()}",
                "preroll": [],
            },
        ).json()["id"]

        with db.connect() as conn:
            song = conn.execute(
                text(
                    "SELECT i.id FROM items i JOIN replicas r ON r.item_id = i.id "
                    "WHERE i.kind = 'audio' LIMIT 1"
                )
            ).scalar_one()
        client.post(f"/api/zones/{zone_id}/play", json={"item_ids": [str(song)]})

        r = client.post(f"/api/zones/{zone_id}/slideshow", json={"photo_ms": 8000})
        assert r.status_code == 409

    def test_the_tower_reports_both(self, client, db, scanned):
        """The card draws its dropdowns from these, so they have to come back."""
        zone_id = self._showing(client, db)
        client.post(f"/api/zones/{zone_id}/slideshow", json={"photo_ms": 8000,
                                                            "transition": "random"})

        zone = next(z for z in client.get("/api/zones").json() if z["id"] == zone_id)
        assert zone["session"]["photo_ms"] == 8000
        assert zone["session"]["transition"] == "random"
