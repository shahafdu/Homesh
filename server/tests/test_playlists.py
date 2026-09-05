"""Playlists, made here and imported from the library.

The import half is the interesting one. These files were written years ago
against paths on a machine that no longer exists, so matching a line to a file is
guesswork constrained by evidence — and the constraint matters more than the
guess: a list quietly pointing at the wrong song is worse than one honestly
missing a track.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.main import app
from app.playlists import _basename, parse_playlist, resolve_entry
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


def _items(db, limit=3) -> list[str]:
    with db.connect() as conn:
        return [
            str(r[0])
            for r in conn.execute(
                text(
                    """
                    SELECT r.item_id FROM replicas r JOIN items i ON i.id = r.item_id
                    WHERE i.kind = 'audio' ORDER BY r.filename LIMIT :n
                    """
                ),
                {"n": limit},
            ).all()
        ]


class TestParsing:
    def test_m3u_keeps_order_and_titles(self):
        body = "\n".join(
            [
                "#EXTM3U",
                "#EXTINF:213,Pink Floyd - In the Flesh",
                "C:\\Music\\Pink Floyd\\01 - In the Flesh.mp3",
                "",
                "#EXTINF:245,The Thin Ice",
                "02 - The Thin Ice.flac",
            ]
        )
        assert parse_playlist(body, "m3u") == [
            ("C:\\Music\\Pink Floyd\\01 - In the Flesh.mp3", "Pink Floyd - In the Flesh"),
            ("02 - The Thin Ice.flac", "The Thin Ice"),
        ]

    def test_a_title_belongs_only_to_the_line_after_it(self):
        """Otherwise one #EXTINF labels every track that follows it."""
        body = "#EXTINF:1,Only mine\na.mp3\nb.mp3"
        assert parse_playlist(body, "m3u") == [("a.mp3", "Only mine"), ("b.mp3", None)]

    def test_pls_is_read_in_its_own_numbering(self):
        body = "[playlist]\nFile2=second.mp3\nTitle2=Second\nFile1=first.mp3\nTitle1=First\n"
        assert parse_playlist(body, "pls") == [("first.mp3", "First"), ("second.mp3", "Second")]

    def test_comments_are_not_tracks(self):
        assert parse_playlist("#EXTM3U\n# a note\nsong.mp3", "m3u") == [("song.mp3", None)]

    def test_windows_paths_yield_a_filename(self):
        assert _basename("C:\\Music\\Rock\\song.mp3") == "song.mp3"
        assert _basename("/mnt/media/song.mp3") == "song.mp3"
        assert _basename("song.mp3") == "song.mp3"


class TestMatching:
    def test_a_moved_file_is_found_by_name(self, db, scanned):
        """The path is from another machine; the filename survived the journey."""
        sid, _prefix, _root = scanned
        with db.connect() as conn:
            found = resolve_entry(conn, "D:\\Old\\Music\\track2.mp3", sid, "")
        assert found is not None

    def test_a_mangled_character_still_matches(self, db, scanned, tmp_path):
        """"Più" written as "Pi?" by something that could not represent it.

        Each ? stands for exactly one lost letter, which is what LIKE's _ means —
        so this is a repair rather than a guess.
        """
        sid, _prefix, root = scanned
        (root / "Music" / "Unsorted" / "Caffè Corretto.mp3").write_bytes(b"x" * 16)
        scan_source(sid, LocalConnector(root))

        with db.connect() as conn:
            found = resolve_entry(conn, "Caff? Corretto.mp3", sid, "")
        assert found is not None, "a mangled filename was not repaired"

    def test_something_absent_matches_nothing(self, db, scanned):
        """Rather than the nearest thing, which would be the wrong song."""
        sid, _prefix, _root = scanned
        with db.connect() as conn:
            assert resolve_entry(conn, "a song nobody owns.mp3", sid, "") is None

    def test_a_web_address_matches_nothing(self, db, scanned):
        """Six of this library's lists are internet radio, not files."""
        sid, _prefix, _root = scanned
        with db.connect() as conn:
            assert resolve_entry(conn, "http://example.com/stream.mp3", sid, "") is None


class TestEditing:
    def _make(self, client, db, name="Evening") -> str:
        r = client.post("/api/playlists", json={"name": name, "item_ids": _items(db)})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_create_and_read_back(self, client, db, scanned):
        playlist_id = self._make(client, db)
        body = client.get(f"/api/playlists/{playlist_id}").json()
        assert body["name"] == "Evening"
        assert len(body["entries"]) == 3
        assert body["missing"] == 0

    def test_rename(self, client, db, scanned):
        playlist_id = self._make(client, db)
        client.put(f"/api/playlists/{playlist_id}", json={"name": "Morning"})
        assert client.get(f"/api/playlists/{playlist_id}").json()["name"] == "Morning"

    def test_add_appends(self, client, db, scanned):
        playlist_id = self._make(client, db)
        extra = _items(db, 5)[3:]
        client.post(f"/api/playlists/{playlist_id}/items", json={"item_ids": extra})

        entries = client.get(f"/api/playlists/{playlist_id}").json()["entries"]
        assert len(entries) == 3 + len(extra)

    def test_remove_closes_the_gap(self, client, db, scanned):
        """Positions left with holes only ever grow more numerous."""
        playlist_id = self._make(client, db)
        entries = client.get(f"/api/playlists/{playlist_id}").json()["entries"]

        client.delete(f"/api/playlists/{playlist_id}/items/{entries[0]['entry_id']}")

        with db.connect() as conn:
            positions = [
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT position FROM playlist_items WHERE playlist_id = :p "
                        "ORDER BY position"
                    ),
                    {"p": playlist_id},
                ).all()
            ]
        assert positions == [0, 1]

    def test_reorder(self, client, db, scanned):
        playlist_id = self._make(client, db)
        entries = client.get(f"/api/playlists/{playlist_id}").json()["entries"]
        reversed_ids = [e["entry_id"] for e in reversed(entries)]

        r = client.put(f"/api/playlists/{playlist_id}/order", json={"entry_ids": reversed_ids})
        assert r.status_code == 200

        after = client.get(f"/api/playlists/{playlist_id}").json()["entries"]
        assert [e["entry_id"] for e in after] == reversed_ids

    def test_a_partial_ordering_is_refused(self, client, db, scanned):
        """A stale client would otherwise drop the rows it had not heard about."""
        playlist_id = self._make(client, db)
        entries = client.get(f"/api/playlists/{playlist_id}").json()["entries"]

        r = client.put(
            f"/api/playlists/{playlist_id}/order",
            json={"entry_ids": [entries[0]["entry_id"]]},
        )
        assert r.status_code == 409

    def test_delete(self, client, db, scanned):
        playlist_id = self._make(client, db)
        assert client.delete(f"/api/playlists/{playlist_id}").status_code == 200
        assert client.get(f"/api/playlists/{playlist_id}").status_code == 404


class TestAccess:
    def test_somebody_elses_list_cannot_be_edited(self, client, db, scanned, user):
        r = client.post("/api/playlists", json={"name": "Mine", "item_ids": _items(db)})
        playlist_id = r.json()["id"]

        with db.begin() as conn:
            uid = conn.execute(
                text(
                    """
                    INSERT INTO users (handle, display_name, is_admin, all_library)
                    VALUES ('kid', 'Kid', FALSE, TRUE) RETURNING id
                    """
                )
            ).scalar_one()

        kid = CurrentUser(id=uid, handle="kid", display_name="Kid", is_admin=False)
        app.dependency_overrides[require_user] = lambda: kid
        app.dependency_overrides[optional_user] = lambda: kid

        assert client.delete(f"/api/playlists/{playlist_id}").status_code == 403
        assert client.put(f"/api/playlists/{playlist_id}", json={"name": "x"}).status_code == 403

    def test_tracks_outside_a_scope_are_not_handed_over(self, client, db, scanned, user):
        """The list still says how long it is; it just cannot play what is out of reach."""
        _sid, prefix, _root = scanned
        r = client.post("/api/playlists", json={"name": "All", "item_ids": _items(db)})
        playlist_id = r.json()["id"]

        with db.begin() as conn:
            uid = conn.execute(
                text(
                    """
                    INSERT INTO users (handle, display_name, is_admin)
                    VALUES ('kid2', 'Kid', FALSE) RETURNING id
                    """
                )
            ).scalar_one()
            conn.execute(
                text("INSERT INTO user_library_rules (user_id, path_prefix) VALUES (:u, :p)"),
                {"u": str(uid), "p": f"{prefix}/Videos"},
            )

        kid = CurrentUser(id=uid, handle="kid2", display_name="Kid", is_admin=False)
        app.dependency_overrides[require_user] = lambda: kid
        app.dependency_overrides[optional_user] = lambda: kid

        body = client.get(f"/api/playlists/{playlist_id}").json()
        assert len(body["entries"]) == 3, "the length of the list changed per viewer"
        assert all(e["item_id"] is None for e in body["entries"]), "a track leaked past a scope"


class TestStorageListsAreReadOnly:
    """A playlist from a .m3u cannot be edited by anybody.

    Not even by whoever imported it. The file is the truth and this server never
    writes to your library, so an edited copy here would immediately disagree
    with the file — and the next import would undo the edit without saying so.
    Copying is the way through, which is why copying is always offered.
    """

    def _imported(self, db, source_id) -> str:
        with db.begin() as conn:
            return str(
                conn.execute(
                    text(
                        """
                        INSERT INTO playlists (name, source_id, source_path, shared)
                        VALUES ('From a file', :s, 'Music/list.m3u', TRUE) RETURNING id
                        """
                    ),
                    {"s": str(source_id)},
                ).scalar_one()
            )

    def test_every_edit_is_refused(self, client, db, scanned):
        sid, _prefix, _root = scanned
        playlist_id = self._imported(db, sid)
        items = _items(db, 1)

        assert client.put(
            f"/api/playlists/{playlist_id}", json={"name": "no"}
        ).status_code == 409
        assert client.post(
            f"/api/playlists/{playlist_id}/items", json={"item_ids": items}
        ).status_code == 409
        assert client.delete(f"/api/playlists/{playlist_id}").status_code == 409

    def test_the_refusal_says_what_to_do(self, client, db, scanned):
        sid, _prefix, _root = scanned
        playlist_id = self._imported(db, sid)

        detail = client.put(f"/api/playlists/{playlist_id}", json={"name": "no"}).json()["detail"]
        assert "copy" in detail.lower(), "the refusal did not point anywhere"

    def test_it_can_still_be_read_and_played(self, client, db, scanned):
        """Read-only is not hidden — the whole point is that it plays."""
        sid, _prefix, _root = scanned
        playlist_id = self._imported(db, sid)

        body = client.get(f"/api/playlists/{playlist_id}").json()
        assert body["read_only"] is True
        assert body["kind"] == "storage"

    def test_a_copy_is_editable_and_yours(self, client, db, scanned, user):
        sid, _prefix, _root = scanned
        playlist_id = self._imported(db, sid)
        client.post(f"/api/playlists/{playlist_id}/copy", json={})

        copy = next(
            p for p in client.get("/api/playlists").json() if p["kind"] == "mine"
        )
        assert copy["read_only"] is False
        assert client.put(
            f"/api/playlists/{copy['id']}", json={"name": "Mine now"}
        ).status_code == 200


class TestSharing:
    """Sharing grants playing, never editing."""

    def _kid(self, db) -> CurrentUser:
        with db.begin() as conn:
            uid = conn.execute(
                text(
                    """
                    INSERT INTO users (handle, display_name, is_admin, all_library)
                    VALUES ('kid', 'Kid', FALSE, TRUE) RETURNING id
                    """
                )
            ).scalar_one()
        return CurrentUser(id=uid, handle="kid", display_name="Kid", is_admin=False)

    def _become(self, person):
        app.dependency_overrides[require_user] = lambda: person
        app.dependency_overrides[optional_user] = lambda: person

    def test_an_unshared_list_is_invisible_to_others(self, client, db, scanned, user):
        client.post("/api/playlists", json={"name": "Private", "item_ids": _items(db)})
        self._become(self._kid(db))

        assert client.get("/api/playlists").json() == []

    def test_a_shared_list_can_be_played_but_not_changed(self, client, db, scanned, user):
        made = client.post("/api/playlists", json={"name": "Ours", "item_ids": _items(db)}).json()
        client.put(f"/api/playlists/{made['id']}/share", json={"shared": True})

        self._become(self._kid(db))
        listed = client.get("/api/playlists").json()
        assert [p["kind"] for p in listed] == ["shared"]
        assert listed[0]["read_only"] is True

        assert client.put(
            f"/api/playlists/{made['id']}", json={"name": "mine now"}
        ).status_code == 403
        # …but it can be taken and changed, which is the point of copying.
        assert client.post(f"/api/playlists/{made['id']}/copy", json={}).status_code == 201

    def test_an_admin_sees_what_was_not_shared_and_it_is_marked(self, client, db, scanned, user):
        kid = self._kid(db)
        self._become(kid)
        client.post("/api/playlists", json={"name": "Kid's own", "item_ids": _items(db)})

        self._become(user)
        listed = {p["name"]: p for p in client.get("/api/playlists").json()}
        assert listed["Kid's own"]["kind"] == "others"
