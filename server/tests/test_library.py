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




class TestBrowsingThisMachineForAFolder:
    """Picking a folder in the app, which is how every other media server does it.

    The drives are mounted read-only and the server lists them. A browser will
    not hand a web page a real path -- a file picker returns names and bytes and
    never "D:\Media" -- so the alternatives are typing a path from memory or
    running a script on the PC, and neither is any use to somebody holding a
    phone in another room.

    Safety is in the mounts being read-only, the listing being admin-only, and
    nothing being read beyond folder names until a folder is picked.
    """

    def _drives(self, monkeypatch, tmp_path):
        from app import library

        (tmp_path / "c" / "Users" / "Shahaf" / "Music").mkdir(parents=True)
        (tmp_path / "c" / "Users" / "Shahaf" / "Music" / "one.mp3").write_bytes(b"x")
        (tmp_path / "c" / "Windows").mkdir()          # noise
        (tmp_path / "c" / "$Recycle.Bin").mkdir()     # noise
        (tmp_path / "c" / ".hidden").mkdir()          # hidden
        (tmp_path / "d").mkdir()
        monkeypatch.setattr(library, "HOST_FS", tmp_path)
        return tmp_path

    def test_the_top_is_the_drives(self, client, monkeypatch, tmp_path):
        self._drives(monkeypatch, tmp_path)

        body = client.get("/api/sources/local/browse").json()
        assert body["available"] is True
        # Shown as somebody would write them, not as the mount points they are:
        # "C:" is what a person is looking for and "c" is a puzzle.
        assert [f["name"] for f in body["folders"]] == ["C:", "D:"]

    def test_it_descends_and_hides_what_nobody_wants(self, client, monkeypatch, tmp_path):
        self._drives(monkeypatch, tmp_path)

        body = client.get("/api/sources/local/browse", params={"at": "c"}).json()
        assert [f["name"] for f in body["folders"]] == ["Users"], (
            "Windows, $Recycle.Bin and dot-folders are noise, not offerings"
        )
        assert [c["name"] for c in body["crumbs"]] == ["C:"]

    def test_a_folder_deep_inside_can_be_added(self, client, db, monkeypatch, tmp_path):
        """The whole point: the folder somebody wants is not at the top."""
        root = self._drives(monkeypatch, tmp_path)

        here = "c/Users/Shahaf/Music"
        body = client.get("/api/sources/local/browse", params={"at": here}).json()
        assert body["files"] == 1, "how you know you have arrived"

        r = client.post("/api/sources/local", json={"path": here})
        assert r.status_code == 201, r.text
        assert r.json()["mount_prefix"] == "/local/music"

        with db.connect() as conn:
            kind, stored = conn.execute(
                text("SELECT kind::text, remote_id FROM sources WHERE mount_prefix = :p"),
                {"p": "/local/music"},
            ).one()
        assert kind == "local"
        # The path is kept with the source: for a folder on this machine it is
        # the provider's handle for it, and a scan has nothing to open without.
        assert stored == str((root / "c" / "Users" / "Shahaf" / "Music").resolve())

    def test_it_shows_which_are_already_added(self, client, monkeypatch, tmp_path):
        self._drives(monkeypatch, tmp_path)
        client.post("/api/sources/local", json={"path": "c/Users/Shahaf/Music"})

        body = client.get(
            "/api/sources/local/browse", params={"at": "c/Users/Shahaf"}
        ).json()
        assert body["folders"][0]["added"] is True

    def test_two_folders_of_the_same_name_do_not_collide(self, client, db, monkeypatch, tmp_path):
        """Music on two drives is ordinary; the second must not replace the first."""
        root = self._drives(monkeypatch, tmp_path)
        (root / "d" / "Music").mkdir()

        first = client.post("/api/sources/local", json={"path": "c/Users/Shahaf/Music"})
        second = client.post("/api/sources/local", json={"path": "d/Music"})
        assert first.json()["mount_prefix"] == "/local/music"
        assert second.json()["mount_prefix"] == "/local/music-2"

        with db.connect() as conn:
            roots = {
                r[0]
                for r in conn.execute(
                    text("SELECT remote_id FROM sources WHERE kind = 'local'")
                )
            }
        assert len(roots) == 2, "both folders kept their own path"

    def test_adding_the_same_folder_twice_is_not_a_duplicate(
        self, client, db, monkeypatch, tmp_path
    ):
        self._drives(monkeypatch, tmp_path)

        client.post("/api/sources/local", json={"path": "c/Users/Shahaf/Music"})
        client.post("/api/sources/local", json={"path": "c/Users/Shahaf/Music"})

        with db.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM sources WHERE kind = 'local'")
            ).scalar()
        assert count == 1

    def test_the_whole_machine_is_not_a_folder(self, client, monkeypatch, tmp_path):
        """Indexing every drive at once is never what somebody meant to press."""
        self._drives(monkeypatch, tmp_path)
        assert client.post("/api/sources/local", json={"path": ""}).status_code == 422
        assert client.post("/api/sources/local", json={"path": "/"}).status_code == 400

    def test_a_path_that_climbs_out_is_refused(self, client, monkeypatch, tmp_path):
        """Confinement checked after resolution, not before — the project rule."""
        self._drives(monkeypatch, tmp_path)

        for climb in ("..", "../..", "c/../../elsewhere", "/etc"):
            assert client.post(
                "/api/sources/local", json={"path": climb}
            ).status_code in (400, 404), climb
            assert client.get(
                "/api/sources/local/browse", params={"at": climb}
            ).status_code == 404, climb

    def test_a_symlink_out_is_refused(self, client, monkeypatch, tmp_path):
        """Which is the whole reason confinement comes after resolution."""
        root = self._drives(monkeypatch, tmp_path)
        outside = tmp_path.parent / "not-mine"
        outside.mkdir(exist_ok=True)
        try:
            (root / "c" / "Escape").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("this platform will not make symlinks without privileges")

        assert client.post(
            "/api/sources/local", json={"path": "c/Escape"}
        ).status_code == 404
        assert client.get(
            "/api/sources/local/browse", params={"at": "c/Escape"}
        ).status_code == 404

    def test_only_an_administrator_may_browse_or_add(self, client, monkeypatch, tmp_path):
        """Listing names the folders on somebody's computer. Not for everyone."""
        self._drives(monkeypatch, tmp_path)

        ordinary = CurrentUser(
            id=uuid.uuid4(), handle="guest", display_name="Guest", is_admin=False
        )
        app.dependency_overrides[require_user] = lambda: ordinary
        try:
            assert client.get("/api/sources/local/browse").status_code == 403
            assert client.post(
                "/api/sources/local", json={"path": "c"}
            ).status_code == 403
        finally:
            app.dependency_overrides.pop(require_user, None)

    def test_nothing_mounted_says_so_rather_than_crashing(self, client, monkeypatch, tmp_path):
        """A Linux host, or Docker without the drive shared. Not an exception."""
        from app import library

        monkeypatch.setattr(library, "HOST_FS", tmp_path / "absent")
        assert client.get("/api/sources/local/browse").status_code == 503

    def test_a_drive_docker_did_not_attach_says_so(self, client, monkeypatch, tmp_path):
        """An empty drive and an unattached one look identical, and are not.

        Docker attaches the host's drives when it starts, so a disk that was
        powered off then — which the RAID here is, by design — appears as an
        empty directory. Reporting "no folders in here" for a disk full of
        music sends somebody to debug the wrong thing.
        """
        self._drives(monkeypatch, tmp_path)

        empty_drive = client.get("/api/sources/local/browse", params={"at": "d"}).json()
        assert empty_drive["unmounted"] is True

        # A folder that is genuinely empty, deeper in, is just empty — the
        # distinction only means anything at the top, where a drive would be.
        (tmp_path / "c" / "Users" / "Shahaf" / "Empty").mkdir()
        deeper = client.get(
            "/api/sources/local/browse", params={"at": "c/Users/Shahaf/Empty"}
        ).json()
        assert deeper["unmounted"] is False
