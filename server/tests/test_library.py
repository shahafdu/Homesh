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
        hits = client.get("/api/search?q=wall").json()
        assert len(hits) == 3
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
