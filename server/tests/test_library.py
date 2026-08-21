"""Browsing and search — the behaviours that answer the Plex complaints."""

from __future__ import annotations

import pytest

from app.scanner import scan_source
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
