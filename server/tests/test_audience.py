"""Who a folder or room is for.

The load-bearing case is the interaction between an audience and a blanket
grant: an account holding whole-library access must *not* pick up a folder
restricted to administrators. Without that, "admins only" would be a label
rather than a rule, and it would fail exactly when a new folder appears —
which is the moment the setting exists for.
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
def household(db, user):
    """Two non-admin accounts: one with the whole library, one with nothing."""
    with db.begin() as conn:
        everyone = conn.execute(
            text(
                """
                INSERT INTO users (handle, display_name, is_admin, all_library, all_zones)
                VALUES ('wife', 'Wife', FALSE, TRUE, TRUE) RETURNING id
                """
            )
        ).scalar_one()
        kid = conn.execute(
            text(
                """
                INSERT INTO users (handle, display_name, is_admin)
                VALUES ('kid', 'Kid', FALSE) RETURNING id
                """
            )
        ).scalar_one()
    return (
        CurrentUser(id=everyone, handle="wife", display_name="Wife", is_admin=False),
        CurrentUser(id=kid, handle="kid", display_name="Kid", is_admin=False),
    )


def _become(person: CurrentUser):
    app.dependency_overrides[require_user] = lambda: person
    app.dependency_overrides[optional_user] = lambda: person


def _roots(client) -> list[str]:
    return [d["name"] for d in client.get("/api/browse?path=/").json()["dirs"]]


def _set_audience(client, source_id, audience, users=()):
    return client.put(
        f"/api/people/audiences/folders/{source_id}",
        json={"audience": audience, "users": [str(u) for u in users]},
    )


class TestFolderAudience:
    def test_admins_only_beats_whole_library_access(self, client, db, scanned, user, household):
        """The case the whole feature turns on."""
        sid, _prefix, _root = scanned
        wife, _kid = household

        _become(user)
        assert _set_audience(client, sid, "admins").status_code == 200

        _become(wife)
        assert _roots(client) == [], "whole-library access must not defeat an audience"

        _become(user)
        assert _roots(client), "an administrator still sees it"

    def test_everyone_reaches_the_scoped_account_too(self, client, db, scanned, user, household):
        """'Everyone' has to mean everyone, not only the unrestricted."""
        sid, _prefix, _root = scanned
        wife, kid = household

        _become(user)
        assert _set_audience(client, sid, "everyone").status_code == 200

        for person in (wife, kid):
            _become(person)
            assert _roots(client), f"{person.handle} was left out of a folder shared with all"

    def test_selected_reaches_only_the_chosen(self, client, db, scanned, user, household):
        sid, _prefix, _root = scanned
        wife, kid = household

        _become(user)
        assert _set_audience(client, sid, "selected", [kid.id]).status_code == 200

        _become(kid)
        assert _roots(client)

        _become(wife)
        assert _roots(client) == [], "whole-library access must not include a chosen-few folder"

    def test_an_undecided_folder_is_admins_only(self, client, db, scanned, user, household):
        """A Drive folder appears on its own; it must arrive closed."""
        sid, _prefix, _root = scanned
        wife, _kid = household

        with db.begin() as conn:
            conn.execute(
                text("UPDATE sources SET audience = NULL WHERE id = :s"), {"s": str(sid)}
            )

        _become(wife)
        assert _roots(client) == []

        _become(user)
        listed = client.get("/api/people/audiences").json()["folders"]
        assert listed[0]["audience"] is None, "undecided folders are listed first"

    def test_restricting_a_folder_drops_the_grants_under_it(
        self, client, db, scanned, user, household
    ):
        """Reopening later must not silently revive last year's list."""
        sid, _prefix, _root = scanned
        _wife, kid = household

        _become(user)
        _set_audience(client, sid, "selected", [kid.id])
        _set_audience(client, sid, "admins")

        with db.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM user_library_rules")).scalar_one() == 0

        _set_audience(client, sid, "selected")
        _become(kid)
        assert _roots(client) == []

    def test_search_and_streaming_honour_the_audience(self, client, db, scanned, user, household):
        """A ceiling that only applied to browsing would not be a ceiling."""
        sid, _prefix, _root = scanned
        wife, _kid = household

        _become(user)
        _set_audience(client, sid, "admins")

        with db.connect() as conn:
            item = conn.execute(
                text("SELECT item_id FROM replicas WHERE filename = 'track2.mp3'")
            ).scalar_one()

        _become(wife)
        assert client.get("/api/search?q=track").json() == []
        assert client.get(f"/api/items/{item}/url").status_code == 404

    def test_only_an_admin_may_set_an_audience(self, client, db, scanned, user, household):
        sid, _prefix, _root = scanned
        _wife, kid = household
        _become(kid)
        assert _set_audience(client, sid, "everyone").status_code == 403

    def test_an_unknown_folder_404s(self, client, db, user):
        import uuid

        _become(user)
        assert _set_audience(client, uuid.uuid4(), "everyone").status_code == 404


class TestRoomAudience:
    def _zone(self, client, name, key, audience=None, grant_to=()):
        body = {"name": name, "renderer_kind": "tvapp", "device_key": key}
        if audience is not None:
            body |= {"audience": audience, "grant_to": [str(u) for u in grant_to]}
        r = client.post("/api/zones", json=body)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def _rooms(self, client) -> list[str]:
        return [z["name"] for z in client.get("/api/zones").json()]

    def test_a_room_created_for_admins_stays_with_admins(self, client, db, user, household):
        wife, _kid = household
        _become(user)
        self._zone(client, "Study", "uuid:study", audience="admins")

        _become(wife)
        assert self._rooms(client) == [], "any-room access must not defeat an audience"

    def test_a_room_can_be_created_for_chosen_people(self, client, db, user, household):
        wife, kid = household
        _become(user)
        self._zone(client, "Kids Room", "uuid:kids", audience="selected", grant_to=[kid.id])

        _become(kid)
        assert self._rooms(client) == ["Kids Room"]

        _become(wife)
        assert self._rooms(client) == []

    def test_a_room_created_without_an_answer_is_admins_only(self, client, db, user, household):
        """Pairing a screen must not publish it to the household by default."""
        wife, _kid = household
        _become(user)
        self._zone(client, "Balcony", "uuid:balcony")

        _become(wife)
        assert self._rooms(client) == []

    def test_playing_in_a_room_outside_the_audience_is_refused(
        self, client, db, scanned, user, household
    ):
        sid, _prefix, _root = scanned
        wife, _kid = household

        _become(user)
        _set_audience(client, sid, "everyone")
        room = self._zone(client, "Study", "uuid:study", audience="admins")

        with db.connect() as conn:
            item = conn.execute(
                text("SELECT item_id FROM replicas WHERE filename = 'track2.mp3'")
            ).scalar_one()

        _become(wife)
        r = client.post(f"/api/zones/{room}/play", json={"item_ids": [str(item)]})
        assert r.status_code == 404, "403 would confirm the room exists"

    def test_the_audience_can_be_changed_afterwards(self, client, db, user, household):
        wife, _kid = household
        _become(user)
        room = self._zone(client, "Study", "uuid:study", audience="admins")

        client.put(
            f"/api/people/audiences/rooms/{room}", json={"audience": "everyone", "users": []}
        )

        _become(wife)
        assert self._rooms(client) == ["Study"]
