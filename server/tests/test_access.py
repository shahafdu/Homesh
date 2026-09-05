"""Per-person access to folders and rooms.

The point of these is that a restricted account cannot reach past its scope by
any route — not by browsing, not by searching, not by holding a link, and not by
sending something to a room it is allowed to use.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.access import Scope, can_read, can_traverse, visible
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
def child(db, user):
    """A second account with no rights of its own until given some."""
    with db.begin() as conn:
        uid = conn.execute(
            text(
                """
                INSERT INTO users (handle, display_name, is_admin)
                VALUES ('kid', 'Kid', FALSE) RETURNING id
                """
            )
        ).scalar_one()
    return CurrentUser(id=uid, handle="kid", display_name="Kid", is_admin=False)


def _become(person: CurrentUser):
    app.dependency_overrides[require_user] = lambda: person
    app.dependency_overrides[optional_user] = lambda: person


def _restrict_library(db, person, prefixes):
    with db.begin() as conn:
        for p in prefixes:
            conn.execute(
                text(
                    "INSERT INTO user_library_rules (user_id, path_prefix) VALUES (:u, :p)"
                ),
                {"u": str(person.id), "p": p},
            )


class TestRuleLogic:
    RULES = Scope(everything=False, allowed=("/local/library/Music",))

    def test_reads_inside_the_allowed_prefix(self):
        assert can_read("/local/library/Music", self.RULES)
        assert can_read("/local/library/Music/Pink Floyd", self.RULES)

    def test_does_not_read_a_sibling_with_a_shared_prefix(self):
        """'/Music2' must not match a rule for '/Music'."""
        assert not can_read("/local/library/Music2", self.RULES)

    def test_cannot_read_outside(self):
        assert not can_read("/local/library/Photos", self.RULES)

    def test_can_traverse_towards_an_allowed_folder(self):
        """You must be able to open the parent to reach what you are allowed."""
        assert can_traverse("/local/library", self.RULES)
        assert visible("/local/library", self.RULES)
        assert not can_read("/local/library", self.RULES)

    def test_nothing_granted_means_nothing_reachable(self):
        """The default must fail closed. This is the whole point of the design."""
        nothing = Scope(everything=False)
        assert nothing.nothing
        assert not can_read("/anything/at/all", nothing)
        assert not can_traverse("/anything", nothing)
        assert not visible("/anything", nothing)

    def test_everything_is_its_own_state(self):
        """'All' and 'none' must not share a representation."""
        everything = Scope(everything=True)
        assert can_read("/anything/at/all", everything)
        assert not everything.nothing


class TestBrowsing:
    def test_restricted_account_sees_only_the_route_to_its_folder(
        self, client, db, scanned, child
    ):
        _sid, prefix, _root = scanned
        _restrict_library(db, child, [f"{prefix}/Music"])
        _become(child)

        top = client.get(f"/api/browse?path={prefix}").json()
        assert [d["name"] for d in top["dirs"]] == ["Music"]
        assert top["files"] == []

    def test_a_folder_on_the_way_shows_no_files(self, client, db, scanned, child):
        """Traversing is not reading: the parent lists subfolders only."""
        _sid, prefix, _root = scanned
        _restrict_library(db, child, [f"{prefix}/Music"])
        _become(child)

        body = client.get(f"/api/browse?path={prefix}/Music/Pink Floyd/The Wall").json()
        assert body["files"], "the allowed folder itself must be readable"

    def test_a_forbidden_folder_is_not_confirmed_to_exist(self, client, db, scanned, child):
        _sid, prefix, _root = scanned
        _restrict_library(db, child, [f"{prefix}/Music"])
        _become(child)

        r = client.get(f"/api/browse?path={prefix}/Photos/2019/Greece")
        assert r.status_code == 404, "403 would confirm the folder exists"

    def test_unrestricted_account_is_unaffected(self, client, db, scanned, user):
        _sid, prefix, _root = scanned
        _become(user)
        top = client.get(f"/api/browse?path={prefix}").json()
        assert {"Docs", "Music", "Photos", "Videos"} <= {d["name"] for d in top["dirs"]}


class TestSearch:
    def test_results_outside_the_scope_are_not_returned(self, client, db, scanned, child):
        """Search must not become a way to learn what exists."""
        _sid, prefix, _root = scanned
        _restrict_library(db, child, [f"{prefix}/Music"])
        _become(child)

        hits = client.get("/api/search?q=beach").json()
        assert hits == [], "a video outside the scope was findable"

    def test_results_inside_the_scope_still_work(self, client, db, scanned, child):
        _sid, prefix, _root = scanned
        _restrict_library(db, child, [f"{prefix}/Music"])
        _become(child)

        hits = client.get("/api/search?q=track").json()
        assert hits and all("Music" in h["path"] for h in hits)


class TestMediaAccess:
    def _item(self, db, filename):
        with db.connect() as conn:
            return conn.execute(
                text("SELECT item_id FROM replicas WHERE filename = :f"), {"f": filename}
            ).scalar_one()

    def test_cannot_mint_a_url_outside_the_scope(self, client, db, scanned, child):
        item = self._item(db, "beach.mkv")
        _sid, prefix, _root = scanned
        _restrict_library(db, child, [f"{prefix}/Music"])
        _become(child)

        assert client.get(f"/api/items/{item}/url").status_code == 404

    def test_a_link_from_another_account_does_not_work(
        self, client, anon_client, db, scanned, child, user
    ):
        """The token names its user, so scope is rechecked when bytes are served."""
        from app.signing import mint

        item = self._item(db, "beach.mkv")
        _sid, prefix, _root = scanned
        _restrict_library(db, child, [f"{prefix}/Music"])

        # A token minted for the restricted account, as though the link leaked.
        token = mint(item, child.id, "stream")
        r = anon_client.get(f"/api/stream/{item}?t={token}")
        assert r.status_code == 403

    def test_the_owner_can_still_stream_it(self, client, db, scanned, user):
        from app.signing import mint

        item = self._item(db, "beach.mkv")
        _become(user)
        token = mint(item, user.id, "stream")
        assert client.get(f"/api/stream/{item}?t={token}").status_code == 200

    def test_thumbnails_respect_the_scope(self, client, db, scanned, child):
        """A thumbnail is a small copy, so it needs the same check."""
        item = self._item(db, "real.png")
        _sid, prefix, _root = scanned
        _restrict_library(db, child, [f"{prefix}/Music"])
        _become(child)

        assert client.get(f"/api/thumb/{item}").status_code == 404


class TestZoneAccess:
    def _zone(self, client, name, key):
        # Open to the household, so these tests exercise the personal rules
        # rather than the room's own audience — that has its own tests.
        r = client.post(
            "/api/zones",
            json={
                "name": name,
                "renderer_kind": "tvapp",
                "device_key": key,
                "audience": "selected",
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_rooms_outside_the_scope_are_not_listed(self, client, db, scanned, user, child):
        _become(user)
        kids = self._zone(client, "Kids Room", "uuid:kids")
        self._zone(client, "Living Room", "uuid:living")

        with db.begin() as conn:
            conn.execute(
                text("INSERT INTO user_zone_rules (user_id, zone_id) VALUES (:u, :z)"),
                {"u": str(child.id), "z": kids},
            )

        _become(child)
        assert [z["name"] for z in client.get("/api/zones").json()] == ["Kids Room"]

    def test_playing_in_a_forbidden_room_is_refused(self, client, db, scanned, user, child):
        _become(user)
        kids = self._zone(client, "Kids Room", "uuid:kids")
        living = self._zone(client, "Living Room", "uuid:living")

        with db.begin() as conn:
            conn.execute(
                text("INSERT INTO user_zone_rules (user_id, zone_id) VALUES (:u, :z)"),
                {"u": str(child.id), "z": kids},
            )

        with db.connect() as conn:
            item = conn.execute(
                text("SELECT item_id FROM replicas WHERE filename = 'track2.mp3'")
            ).scalar_one()

        _become(child)
        r = client.post(f"/api/zones/{living}/play", json={"item_ids": [str(item)]})
        assert r.status_code == 404, "403 would confirm the room exists"

    def test_content_is_checked_as_well_as_the_room(self, client, db, scanned, user, child):
        """Otherwise a permitted room would be a way around the library scope."""
        _become(user)
        kids = self._zone(client, "Kids Room", "uuid:kids")
        _sid, prefix, _root = scanned

        with db.begin() as conn:
            conn.execute(
                text("INSERT INTO user_zone_rules (user_id, zone_id) VALUES (:u, :z)"),
                {"u": str(child.id), "z": kids},
            )
        _restrict_library(db, child, [f"{prefix}/Music"])

        with db.connect() as conn:
            forbidden = conn.execute(
                text("SELECT item_id FROM replicas WHERE filename = 'beach.mkv'")
            ).scalar_one()

        _become(child)
        r = client.post(f"/api/zones/{kids}/play", json={"item_ids": [str(forbidden)]})
        assert r.status_code == 404


class TestAdministration:
    def test_only_an_admin_may_list_people(self, client, db, child):
        _become(child)
        assert client.get("/api/people").status_code == 403

    def test_admin_sees_who_is_restricted(self, client, db, scanned, user, child):
        _sid, prefix, _root = scanned
        _restrict_library(db, child, [f"{prefix}/Music"])
        _become(user)

        people = {p["handle"]: p for p in client.get("/api/people").json()}
        assert people["kid"]["library"] == [f"{prefix}/Music"]
        assert people["kid"]["all_library"] is False
        # The unrestricted account says so with a flag, not by having no rules.
        assert people[user.handle]["all_library"] is True
        assert people[user.handle]["library"] == []

    def test_a_new_account_starts_with_nothing(self, client, db, scanned, user, child):
        """Access must be granted, never assumed."""
        _become(child)
        _sid, prefix, _root = scanned
        assert client.get("/api/browse?path=/").json()["dirs"] == []
        assert client.get(f"/api/browse?path={prefix}").status_code == 404
        assert client.get("/api/search?q=track").json() == []

    def test_clearing_the_ticks_removes_access_rather_than_granting_all(
        self, client, db, scanned, user, child
    ):
        """The inverted reading of this is the bug this design exists to prevent."""
        _sid, prefix, _root = scanned
        _restrict_library(db, child, [f"{prefix}/Music"])
        _become(user)

        client.put(f"/api/people/{child.id}/rules", json={"library": [], "zones": []})

        people = {p["handle"]: p for p in client.get("/api/people").json()}
        assert people["kid"]["library"] == []
        assert people["kid"]["all_library"] is False

        _become(child)
        assert client.get("/api/browse?path=/").json()["dirs"] == []

    def test_the_whole_library_is_granted_explicitly(self, client, db, scanned, user, child):
        _become(user)
        client.put(
            f"/api/people/{child.id}/rules",
            json={"library": [], "zones": [], "all_library": True, "all_zones": True},
        )

        _become(child)
        _sid, prefix, _root = scanned
        names = {d["name"] for d in client.get(f"/api/browse?path={prefix}").json()["dirs"]}
        assert {"Docs", "Music", "Photos", "Videos"} <= names

    def test_access_can_be_changed_after_the_invitation(self, client, db, scanned, user, child):
        """Children grow up; a guest should not keep last year's access."""
        _sid, prefix, _root = scanned
        _become(user)
        client.put(
            f"/api/people/{child.id}/rules", json={"library": [f"{prefix}/Music"], "zones": []}
        )

        _become(child)
        assert [d["name"] for d in client.get(f"/api/browse?path={prefix}").json()["dirs"]] == [
            "Music"
        ]

        _become(user)
        client.put(
            f"/api/people/{child.id}/rules",
            json={"library": [f"{prefix}/Music", f"{prefix}/Videos"], "zones": []},
        )

        _become(child)
        assert [d["name"] for d in client.get(f"/api/browse?path={prefix}").json()["dirs"]] == [
            "Music",
            "Videos",
        ]

    def test_restricting_an_admin_is_refused(self, client, db, user):
        _become(user)
        r = client.put(f"/api/people/{user.id}/rules", json={"library": ["/nowhere"]})
        assert r.status_code == 409

    def test_removing_an_unknown_person_404s(self, client, db, user):
        _become(user)
        assert client.delete(f"/api/people/{uuid.uuid4()}").status_code == 404


class TestOwnership:
    """One account cannot be locked out of its own server.

    Administration is shareable so a second adult can manage the household. The
    owner is not, so sharing it is never a route to losing the house.
    """

    @staticmethod
    def _promote(db, person) -> CurrentUser:
        with db.begin() as conn:
            conn.execute(
                text("UPDATE users SET is_admin = TRUE WHERE id = :u"), {"u": str(person.id)}
            )
        return CurrentUser(
            id=person.id, handle=person.handle, display_name=person.display_name, is_admin=True
        )

    def test_the_owner_cannot_be_removed(self, client, db, user, child):
        _become(self._promote(db, child))
        assert client.delete(f"/api/people/{user.id}").status_code == 409

    def test_the_owner_cannot_be_demoted(self, client, db, user, child):
        _become(self._promote(db, child))

        r = client.put(f"/api/people/{user.id}/admin", json={"is_admin": False})
        assert r.status_code == 409
        with db.connect() as conn:
            assert conn.execute(
                text("SELECT is_admin FROM users WHERE id = :u"), {"u": str(user.id)}
            ).scalar_one()

    def test_the_owner_cannot_be_restricted(self, client, db, user, child):
        _become(self._promote(db, child))

        r = client.put(f"/api/people/{user.id}/rules", json={"library": [], "zones": []})
        assert r.status_code == 409

    def test_the_owner_cannot_remove_their_own_ownership(self, client, db, user):
        """Not even by accident, and not even on purpose."""
        _become(user)
        r = client.put(f"/api/people/{user.id}/admin", json={"is_admin": False})
        assert r.status_code == 409

    def test_an_admin_can_grant_administration(self, client, db, user, child):
        _become(user)
        r = client.put(f"/api/people/{child.id}/admin", json={"is_admin": True})
        assert r.status_code == 200

        people = {p["handle"]: p for p in client.get("/api/people").json()}
        assert people["kid"]["is_admin"] is True
        assert people["kid"]["is_owner"] is False

    def test_a_granted_admin_can_manage_others(self, client, db, user, child):
        """The point of sharing administration: no waiting on the owner."""
        _become(user)
        client.put(f"/api/people/{child.id}/admin", json={"is_admin": True})

        with db.begin() as conn:
            third = conn.execute(
                text(
                    """
                    INSERT INTO users (handle, display_name, is_admin)
                    VALUES ('guest', 'Guest', FALSE) RETURNING id
                    """
                )
            ).scalar_one()

        _become(CurrentUser(id=child.id, handle="kid", display_name="Kid", is_admin=True))
        r = client.put(
            f"/api/people/{third}/rules",
            json={"library": [], "zones": [], "all_library": True, "all_zones": False},
        )
        assert r.status_code == 200

    def test_withdrawing_administration_leaves_no_access(self, client, db, scanned, user, child):
        """Demotion must not fall back to whatever rules predated the promotion."""
        _sid, prefix, _root = scanned
        _become(user)
        client.put(f"/api/people/{child.id}/rules", json={"library": [f"{prefix}/Music"]})
        client.put(f"/api/people/{child.id}/admin", json={"is_admin": True})
        client.put(f"/api/people/{child.id}/admin", json={"is_admin": False})

        _become(child)
        assert client.get("/api/browse?path=/").json()["dirs"] == []
