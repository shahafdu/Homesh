"""Tracks from Drive, kept on disk after the first play.

Drive charges about 1.4 seconds before the first byte of every read. For music
that is the wait before it starts, on every track, every time. These tests are
about that wait being paid once -- and about what must not be kept: films,
local files, half a download, or a copy of a file that has since changed.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app import audiocache


class Remote:
    """A Drive-like connector: no root on disk, and it counts its reads."""

    root = None

    def __init__(self, body: bytes):
        self.body = body
        self.reads: list[tuple[int, int]] = []
        self.closed = 0

    def open_range(self, _rel: str, start: int, end: int):
        self.reads.append((start, end))
        connector = self

        def chunks():
            try:
                yield connector.body[start : end + 1]
            finally:
                connector.closed += 1

        return chunks()


class Local(Remote):
    """A folder on this machine. Already fast; never cached."""

    root = "/library/music"


@pytest.fixture
def scanned(source, monkeypatch):
    """The fixture library, indexed -- and reachable, so the endpoint can serve
    from it. As in test_streaming: the mount prefix maps back through MEDIA_ROOTS."""
    from app.config import get_settings
    from app.scanner import scan_source
    from app.sources.local import LocalConnector

    sid, prefix, root = source
    monkeypatch.setenv("MEDIA_ROOTS", f"{prefix.rsplit('/', 1)[-1]}={root}")
    get_settings.cache_clear()
    scan_source(sid, LocalConnector(root))
    yield sid, prefix, root
    get_settings.cache_clear()


@pytest.fixture
def cache(monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("AUDIO_CACHE_MB", "1")
    get_settings.cache_clear()
    yield audiocache.root()
    get_settings.cache_clear()


TRACK = b"ID3" + bytes(range(256)) * 40  # ~10 KB, and recognisable at any offset


class TestWhatIsWorthKeeping:
    def test_a_track_from_drive_is(self, cache):
        assert audiocache.wanted("mp3", len(TRACK), Remote(TRACK)) is True

    def test_a_film_is_not(self, cache):
        """A library of films would fill any disk, and the wait is paid once."""
        assert audiocache.wanted("mkv", 900_000_000, Remote(b"")) is False

    def test_a_local_file_is_not(self, cache):
        """Already fast: there is no network in front of it."""
        assert audiocache.wanted("mp3", len(TRACK), Local(TRACK)) is False

    def test_an_enormous_one_is_not(self, cache, monkeypatch):
        monkeypatch.setenv("AUDIO_CACHE_FILE_MB", "1")
        from app.config import get_settings

        get_settings.cache_clear()
        assert audiocache.wanted("wav", 50 * 1024 * 1024, Remote(b"")) is False

    def test_nothing_is_when_it_is_switched_off(self, cache, monkeypatch):
        monkeypatch.setenv("AUDIO_CACHE_MB", "0")
        from app.config import get_settings

        get_settings.cache_clear()
        assert audiocache.wanted("mp3", len(TRACK), Remote(TRACK)) is False


class TestTheSecondPlay:
    def test_it_comes_from_disk(self, cache):
        item, drive = uuid.uuid4(), Remote(TRACK)
        assert audiocache.hit(item, len(TRACK)) is None

        assert audiocache.fill(item, len(TRACK), drive, "Music/song.mp3") is True
        assert drive.reads == [(0, len(TRACK) - 1)], "the whole file, once"

        path = audiocache.hit(item, len(TRACK))
        assert path is not None
        assert path.read_bytes() == TRACK

    def test_a_range_is_served_from_the_copy(self, cache):
        item = uuid.uuid4()
        audiocache.fill(item, len(TRACK), Remote(TRACK), "Music/song.mp3")
        path = audiocache.hit(item, len(TRACK))

        served = b"".join(audiocache.read_range(path, 100, 199))
        assert served == TRACK[100:200]
        assert b"".join(audiocache.read_range(path, len(TRACK) - 3, len(TRACK) - 1)) == TRACK[-3:]

    def test_the_reader_is_closed_even_on_a_short_read(self, cache):
        """The connector holds an HTTP response open while it yields; leaving one
        suspended is what once wedged every later Drive request."""
        item, drive = uuid.uuid4(), Remote(TRACK)
        audiocache.fill(item, len(TRACK), drive, "Music/song.mp3")
        assert drive.closed == 1

    def test_filling_twice_does_nothing_twice(self, cache):
        item, drive = uuid.uuid4(), Remote(TRACK)
        audiocache.fill(item, len(TRACK), drive, "Music/song.mp3")
        audiocache.fill(item, len(TRACK), drive, "Music/song.mp3")
        assert len(drive.reads) == 1


class TestWhatIsNotServed:
    def test_a_file_that_changed_is_not_a_hit(self, cache):
        """The name carries the size, so a different file cannot be served from
        an old copy -- it is simply not there."""
        item = uuid.uuid4()
        audiocache.fill(item, len(TRACK), Remote(TRACK), "Music/song.mp3")
        assert audiocache.hit(item, len(TRACK) + 1) is None

    def test_half_a_download_is_not_kept(self, cache):
        """Half a track served as if whole is worse than no cache at all."""
        item = uuid.uuid4()
        truncated = Remote(TRACK[: len(TRACK) // 2])
        assert audiocache.fill(item, len(TRACK), truncated, "Music/song.mp3") is False
        assert audiocache.hit(item, len(TRACK)) is None
        assert list(cache.glob("*/*")) == [], "a partial file was left behind"

    def test_a_failure_leaves_nothing(self, cache):
        class Broken(Remote):
            def open_range(self, _rel, _start, _end):
                raise OSError("Drive said no")

        item = uuid.uuid4()
        assert audiocache.fill(item, len(TRACK), Broken(b""), "Music/song.mp3") is False
        assert list(cache.glob("*/*")) == []


class TestTheBudget:
    def _fill(self, size: int) -> uuid.UUID:
        item = uuid.uuid4()
        audiocache.fill(item, size, Remote(bytes(size)), "Music/song.mp3")
        return item

    def test_the_longest_unplayed_goes_first(self, cache, monkeypatch):
        import os

        # A budget of one megabyte, and three files of 400 KB: the third arrival
        # must not fit alongside the other two.
        sizes = 400 * 1024
        first, second = self._fill(sizes), self._fill(sizes)

        # First played long ago, second just now -- the timestamps are the record.
        old = Path(audiocache.path_for(first, sizes))
        os.utime(old, (1_600_000_000, 1_600_000_000))
        audiocache.hit(second, sizes)

        third = self._fill(sizes)

        assert audiocache.hit(first, sizes) is None, "the stalest was not the one removed"
        assert audiocache.hit(second, sizes) is not None
        assert audiocache.hit(third, sizes) is not None

    def test_it_stays_inside_the_budget(self, cache):
        for _ in range(6):
            self._fill(300 * 1024)
        total = sum(size for _p, _m, size in audiocache.kept())
        assert total <= audiocache.budget()

    def test_a_hit_counts_as_played(self, cache):
        """Otherwise a much-loved album would be evicted for being old."""
        import os

        item = self._fill(1024)
        path = audiocache.path_for(item, 1024)
        os.utime(path, (1_600_000_000, 1_600_000_000))
        before = path.stat().st_mtime
        audiocache.hit(item, 1024)
        assert path.stat().st_mtime > before


class TestThroughTheEndpoint:
    """What the player actually does: ask for a URL, then fetch it."""

    def _play(self, client, db, filename: str) -> tuple[int, bytes]:
        from sqlalchemy import text

        with db.connect() as conn:
            item = conn.execute(
                text("SELECT item_id FROM replicas WHERE filename = :f"), {"f": filename}
            ).scalar_one()
        url = client.get(f"/api/items/{item}/url").json()["url"]
        r = client.get(url)
        return r.status_code, r.content

    def test_a_local_track_is_served_and_not_cached(self, client, db, scanned, cache):
        """The fixture library is on disk, so there is nothing to save."""
        code, body = self._play(client, db, "track2.mp3")
        assert code == 200 and body
        assert list(cache.glob("*/*")) == []
