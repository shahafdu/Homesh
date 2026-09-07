"""Browsing and search — the behaviours that answer the Plex complaints."""

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


def test_root_lists_sources(client, scanned):
    _sid, prefix, _root = scanned
    body = client.get("/api/browse?path=/").json()

    assert body["parent"] is None
    assert prefix in [d["path"] for d in body["dirs"]]
    assert body["files"] == []


def test_folder_tree_navigation(client, scanned):
    _sid, prefix, _root = scanned

    top = client.get(f"/api/browse?path={prefix}").json()
    assert {d["name"] for d in top["dirs"]} == {"Docs", "Music", "Photos", "Videos"}

    music = client.get(f"/api/browse?path={prefix}/Music").json()
    assert {d["name"] for d in music["dirs"]} == {"Pink Floyd", "Unsorted"}


def test_parent_of_source_root_is_namespace_root(client, scanned):
    """Up from a source must not land on a phantom path nothing is mounted at."""
    _sid, prefix, _root = scanned
    body = client.get(f"/api/browse?path={prefix}").json()
    assert body["parent"] == "/"


def test_natural_sort_puts_track2_before_track10(client, scanned):
    _sid, prefix, _root = scanned
    files = client.get(f"/api/browse?path={prefix}/Music/Unsorted").json()["files"]
    names = [f["filename"] for f in files]
    assert names.index("track2.mp3") < names.index("track10.mp3")


def test_filename_and_kind_are_preserved(client, scanned):
    _sid, prefix, _root = scanned
    files = client.get(f"/api/browse?path={prefix}/Photos/2019/Greece").json()["files"]
    by_name = {f["filename"]: f for f in files}

    # Unicode survives the round trip.
    assert by_name["IMG_1234.jpg"]["kind"] == "photo"
    # An unknown extension is listed, not hidden.
    assert by_name["weird.xyz"]["kind"] == "other"


def test_unicode_filename_round_trips(client, scanned):
    _sid, prefix, _root = scanned
    files = client.get(f"/api/browse?path={prefix}/Music/Unsorted").json()["files"]
    assert "שיר בעברית.mp3" in [f["filename"] for f in files]


def test_browse_unknown_path_404s(client):
    assert client.get("/api/browse?path=/nowhere").status_code == 404


class TestSearch:
    def test_matches_filename(self, client, scanned):
        hits = client.get("/api/search?q=beach").json()
        assert "beach.mkv" in [h["filename"] for h in hits]

    def test_matches_folder_name(self, client, scanned):
        """'wall' appears in no filename — only in the folder 'The Wall'."""
        _sid, _prefix, root = scanned
        expected = sum(
            1 for p in (root / "Music" / "Pink Floyd" / "The Wall").iterdir() if p.is_file()
        )

        hits = client.get("/api/search?q=wall").json()
        # Derived, not hardcoded: the fixture grows as tests need new cases.
        assert len(hits) == expected
        assert all("The Wall" in h["path"] for h in hits)

    @pytest.mark.parametrize(
        "typo,expected",
        [("trck", "track2.mp3"), ("beech", "beach.mkv"), ("denonn", "Denon AVR-X1600H manual.pdf")],
    )
    def test_tolerates_typos(self, client, scanned, typo, expected):
        hits = client.get(f"/api/search?q={typo}").json()
        assert expected in [h["filename"] for h in hits]

    def test_nonsense_returns_nothing(self, client, scanned):
        """The fuzzy threshold must not turn every query into a match."""
        assert client.get("/api/search?q=zzzzqqqxnomatch").json() == []

    def test_unicode_query(self, client, scanned):
        hits = client.get("/api/search?q=בעברית").json()
        assert "שיר בעברית.mp3" in [h["filename"] for h in hits]

    def test_empty_query_rejected(self, client):
        assert client.get("/api/search?q=").status_code == 422


class TestSearchInOneFolder:
    """Search narrowed to where you are standing.

    Everywhere is the right default — usually you do not know where a thing is.
    But inside a folder of 1,500 tracks, "everywhere" is the wrong answer to
    "which of these is the live one".
    """

    @staticmethod
    def _paths(client, q, under=None):
        params = {"q": q}
        if under is not None:
            params["under"] = under
        return [h["path"] for h in client.get("/api/search", params=params).json()]

    def test_it_keeps_only_what_is_under_the_folder(self, client, scanned):
        _sid, prefix, _root = scanned
        wall = f"{prefix}/Music/Pink Floyd/The Wall"

        wide = self._paths(client, "track")
        narrow = self._paths(client, "track", wall)

        assert narrow, "the folder does contain matches"
        assert len(narrow) < len(wide), "narrowing should narrow"
        assert all(p.startswith(wall) for p in narrow)

    def test_descendants_count_as_inside(self, client, scanned):
        """A folder means the folder and everything below it."""
        _sid, prefix, _root = scanned

        deep = self._paths(client, "track", f"{prefix}/Music/Pink Floyd/The Wall")
        above = self._paths(client, "track", f"{prefix}/Music")

        assert deep, "the deeper folder has matches"
        assert set(deep) <= set(above), "everything deeper is inside the parent"

    def test_a_trailing_slash_means_the_same_folder(self, client, scanned):
        _sid, prefix, _root = scanned
        assert self._paths(client, "track", f"{prefix}/Music") == self._paths(
            client, "track", f"{prefix}/Music/"
        )

    def test_no_folder_given_searches_everything(self, client, scanned):
        assert self._paths(client, "track", None) == self._paths(client, "track", "")

    def test_a_sibling_folder_is_excluded(self, client, scanned):
        """The point of the feature: a name that appears in more than one place."""
        _sid, prefix, _root = scanned

        everywhere = self._paths(client, "track")
        one = self._paths(client, "track", f"{prefix}/Music/Pink Floyd/The Wall")

        elsewhere = [p for p in everywhere if p not in one]
        assert elsewhere, "there are matches outside this folder"
        assert not any(p in one for p in elsewhere)


class TestFoldersGrantedFromThePC:
    """A folder reaches Homesh by being granted on the machine that holds it.

    The same bargain as a folder shared with the Homesh account in Drive: you
    grant it where it lives, and what you did not grant is unreachable rather
    than merely unlisted. Adding a folder is a one-time act performed at the
    machine, so the server never needs — and must not have — a view of the rest
    of the disk.

    An earlier version mounted whole drives and browsed them in the app, which
    is what most media servers do. It bought convenience once, on a job done
    once, and paid for it with permanent read access to everything.
    """

    def _granted(self, monkeypatch, tmp_path, *names):
        from app import library

        for name in names:
            (tmp_path / name).mkdir(parents=True)
        monkeypatch.setattr(library, "LIBRARY_MOUNTS", tmp_path)
        return tmp_path

    def test_a_granted_folder_becomes_a_source(self, db, monkeypatch, tmp_path):
        root = self._granted(monkeypatch, tmp_path, "music")

        from app.library import _register_mounted

        _register_mounted()

        with db.connect() as conn:
            kind, name, stored = conn.execute(
                text(
                    "SELECT kind::text, name, remote_id FROM sources "
                    "WHERE mount_prefix = :p"
                ),
                {"p": "/local/music"},
            ).one()
        assert (kind, name) == ("local", "music")
        # The path is kept with the source: for a folder on this machine it is
        # the provider's handle for it, and a scan has nothing to open without.
        assert stored == str(root / "music")

    def test_registering_twice_does_not_duplicate(self, db, monkeypatch, tmp_path):
        """Every restart runs this. A restart must not add the library again."""
        self._granted(monkeypatch, tmp_path, "music")

        from app.library import _register_mounted

        _register_mounted()
        _register_mounted()

        with db.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM sources WHERE mount_prefix = :p"),
                {"p": "/local/music"},
            ).scalar()
        assert count == 1

    def test_files_beside_the_folders_are_ignored(self, db, monkeypatch, tmp_path):
        """Only directories are grants. Anything else is somebody's mistake."""
        self._granted(monkeypatch, tmp_path, "videos")
        (tmp_path / "stray.txt").write_bytes(b"x")
        (tmp_path / ".hidden").mkdir()

        from app.library import _register_mounted

        _register_mounted()

        with db.connect() as conn:
            names = {
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM sources WHERE kind = 'local'")
                )
            }
        assert "stray.txt" not in names and ".hidden" not in names
        assert "videos" in names

    def test_nothing_granted_is_not_an_error(self, monkeypatch, tmp_path):
        """The ordinary state of a fresh install, and of every CI run."""
        from app import library

        monkeypatch.setattr(library, "LIBRARY_MOUNTS", tmp_path / "absent")
        library._register_mounted()  # must not raise

    def test_the_server_cannot_list_this_machine(self, client):
        """The point of the design, kept from drifting back.

        Nothing the server exposes may enumerate the host or take a path to
        add. A folder arrives by being mounted, and by no other route.
        """
        assert client.get("/api/sources/local/browse").status_code == 404
        assert client.get("/api/sources/local/browse?at=c").status_code == 404

        # 405 rather than 404, because "local" matches the {source_id} of the
        # delete route and so the *path* exists. Either answer means the same
        # thing and is the thing being asserted: no operation adds a folder by
        # path, so no request can widen what the server reaches.
        assert client.post(
            "/api/sources/local", json={"path": "C:/Users"}
        ).status_code in (404, 405)


class TestRemovingASource:
    """Forgetting a folder, which needs no access to it and so can happen here."""

    def _source(self, db):
        with db.begin() as conn:
            return conn.execute(
                text(
                    "INSERT INTO sources (kind, name, mount_prefix, remote_id) "
                    "VALUES ('local', 'gone', '/local/gone', '/library/gone') "
                    "RETURNING id"
                )
            ).scalar_one()

    def test_an_administrator_can_remove_one(self, client, db):
        sid = self._source(db)
        assert client.delete(f"/api/sources/{sid}").status_code == 204

        with db.connect() as conn:
            left = conn.execute(
                text("SELECT count(*) FROM sources WHERE id = :id"), {"id": str(sid)}
            ).scalar()
        assert left == 0

    def test_removing_what_is_not_there_is_a_404(self, client):
        assert client.delete(f"/api/sources/{uuid.uuid4()}").status_code == 404

    def test_an_ordinary_account_may_not(self, client, db):
        sid = self._source(db)
        ordinary = CurrentUser(
            id=uuid.uuid4(), handle="guest", display_name="Guest", is_admin=False
        )
        app.dependency_overrides[require_user] = lambda: ordinary
        try:
            assert client.delete(f"/api/sources/{sid}").status_code == 403
        finally:
            app.dependency_overrides.pop(require_user, None)
