"""Metadata extraction.

Tags are additive and carry their origin, so a value from a model can never be
mistaken for one the file itself claimed (ARCHITECTURE.md §2, §9).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.metadata import (
    _clean,
    _timeable_copy,
    extract_for_source,
    plausible_length,
    read_audio,
)
from app.scanner import scan_source
from app.sources.local import LocalConnector

TAGGED = "03 - Tagged Track.wav"


@pytest.fixture
def scanned(source):
    sid, prefix, root = source
    scan_source(sid, LocalConnector(root))
    return sid, prefix, root


def _item(db, filename: str):
    with db.connect() as conn:
        return conn.execute(
            text("SELECT item_id FROM replicas WHERE filename = :f"), {"f": filename}
        ).scalar_one()


class TestReadAudio:
    def test_reads_tags_and_duration(self, library):
        path = library / "Music" / "Pink Floyd" / "The Wall" / TAGGED
        tags, duration_ms = read_audio(path)

        assert tags["title"] == "Another Brick in the Wall"
        assert tags["artist"] == "Pink Floyd"
        assert tags["album"] == "The Wall"
        assert duration_ms == pytest.approx(1000, abs=150)

    def test_track_position_strips_the_total(self, library):
        """"3/26" is a position within an album, not a number."""
        path = library / "Music" / "Pink Floyd" / "The Wall" / TAGGED
        tags, _ = read_audio(path)
        assert tags["track"] == "3"

    def test_corrupt_file_returns_empty_rather_than_raising(self, library):
        """A placeholder byte string must not break the pass."""
        tags, duration = read_audio(library / "Music" / "Unsorted" / "track2.mp3")
        assert tags == {}
        assert duration is None


class TestClean:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (["Pink Floyd"], "Pink Floyd"),
            ("  spaced  ", "spaced"),
            ("1/12", "1"),
            ((3, 26), "3"),
            ("", None),
            (None, None),
            ([], None),
            ("AC/DC", "AC/DC"),  # a slash that is part of the text must survive
        ],
    )
    def test_normalisation(self, raw, expected):
        assert _clean(raw) == expected


class TestExtraction:
    def test_writes_tags_with_file_origin(self, db, scanned):
        sid, _prefix, root = scanned
        result = extract_for_source(sid, LocalConnector(root))
        assert result.tagged >= 1

        item = _item(db, TAGGED)
        with db.connect() as conn:
            rows = dict(
                conn.execute(
                    text(
                        """
                        SELECT key, value FROM item_metadata
                        WHERE item_id = :id AND origin = 'file'
                        """
                    ),
                    {"id": str(item)},
                ).all()
            )
        assert rows["artist"] == "Pink Floyd"
        assert rows["album"] == "The Wall"

    def test_duration_lands_on_the_item(self, db, scanned):
        sid, _prefix, root = scanned
        extract_for_source(sid, LocalConnector(root))

        item = _item(db, TAGGED)
        with db.connect() as conn:
            duration = conn.execute(
                text("SELECT duration_ms FROM items WHERE id = :id"), {"id": str(item)}
            ).scalar_one()
        assert duration and duration > 0

    def test_second_pass_does_no_work(self, db, scanned):
        """Untagged files are marked, so a rerun is cheap rather than repeating."""
        sid, _prefix, root = scanned
        first = extract_for_source(sid, LocalConnector(root))
        assert first.processed > 0

        second = extract_for_source(sid, LocalConnector(root))
        assert second.processed == 0, "already-inspected files were processed again"

    def test_unavailable_source_reports_rather_than_raising(self, db, scanned, tmp_path):
        sid, _prefix, _root = scanned
        result = extract_for_source(sid, LocalConnector(tmp_path / "gone"))
        assert result.errors == ["source unavailable"]


class TestListingIncludesMetadata:
    def _wall(self, client, prefix):
        return client.get(f"/api/browse?path={prefix}/Music/Pink Floyd/The Wall").json()["files"]

    def test_browse_returns_tags_and_duration(self, client, db, scanned):
        sid, prefix, root = scanned
        extract_for_source(sid, LocalConnector(root))

        tagged = next(f for f in self._wall(client, prefix) if f["filename"] == TAGGED)
        assert tagged["meta"]["artist"] == "Pink Floyd"
        assert tagged["meta"]["title"] == "Another Brick in the Wall"
        assert tagged["duration_ms"] > 0

    def test_filename_is_still_the_primary_field(self, client, db, scanned):
        """Metadata is additive; it must never replace the filename."""
        sid, prefix, root = scanned
        extract_for_source(sid, LocalConnector(root))

        tagged = next(f for f in self._wall(client, prefix) if f["filename"] == TAGGED)
        assert tagged["filename"] == TAGGED

    def test_untagged_file_has_empty_meta_not_a_guess(self, client, db, scanned):
        sid, prefix, root = scanned
        extract_for_source(sid, LocalConnector(root))

        files = client.get(f"/api/browse?path={prefix}/Music/Unsorted").json()["files"]
        untagged = next(f for f in files if f["filename"] == "track2.mp3")
        assert untagged["meta"] == {}

    def test_user_origin_wins_over_file(self, client, db, scanned):
        """Precedence exists so a correction is not undone by the next rescan."""
        sid, prefix, root = scanned
        extract_for_source(sid, LocalConnector(root))
        item = _item(db, TAGGED)

        with db.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO item_metadata (item_id, key, value, origin, confidence)
                    VALUES (:id, 'artist', 'Corrected Artist', 'user', 1.0)
                    """
                ),
                {"id": str(item)},
            )

        tagged = next(f for f in self._wall(client, prefix) if f["filename"] == TAGGED)
        assert tagged["meta"]["artist"] == "Corrected Artist"

    def test_ai_origin_loses_to_file(self, client, db, scanned):
        """A model's guess must not displace what the file itself claimed."""
        sid, prefix, root = scanned
        extract_for_source(sid, LocalConnector(root))
        item = _item(db, TAGGED)

        with db.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO item_metadata (item_id, key, value, origin, confidence)
                    VALUES (:id, 'artist', 'Hallucinated Band', 'ai', 0.6)
                    """
                ),
                {"id": str(item)},
            )

        tagged = next(f for f in self._wall(client, prefix) if f["filename"] == TAGGED)
        assert tagged["meta"]["artist"] == "Pink Floyd"


class TestALengthMustAccountForTheFile:
    """A film's length and its size have to agree.

    The check exists because the wrong numbers were not absurd-looking. A Drive
    video is read as a prefix, and ffprobe handed a quarter-megabyte of a film
    reports how long that quarter-megabyte lasts: 0.64 seconds for a 1.6 GB
    conga lesson. That is a number, so it was stored as the length, and the
    control tower drew it -- a bar the film ran off the end of in the first
    second.
    """

    def test_a_prefix_length_is_refused(self):
        # The real one: 1.6 GB recorded as 0.64 seconds.
        assert not plausible_length(640, 1_661_159_424)

    def test_the_true_length_of_the_same_file_is_accepted(self):
        # Ten minutes and forty seconds, which is 20 Mbit/s -- high, and real.
        assert plausible_length(640_000, 1_661_159_424)

    def test_a_short_clip_is_not_refused_for_being_short(self):
        """Nothing here is about short files; it is about impossible ones."""
        assert plausible_length(2_000, 500_000)

    def test_no_length_is_not_a_length(self):
        assert not plausible_length(None, 1_000_000)
        assert not plausible_length(0, 1_000_000)

    def test_nothing_is_asserted_without_a_size(self):
        """Half the question cannot answer it, and refusing everything then
        would lose more than the fault costs."""
        assert plausible_length(640, None)


class TestTimingARemoteVideo:
    """A prefix cannot be timed, so the ends of the file are fetched instead.

    The temporary file is the true size with a hole in the middle: ffprobe finds
    the container's index where it expects it, and where it has to estimate
    instead it estimates against the real size rather than against a quarter of
    a megabyte.
    """

    class Remote:
        """A connector with no _resolve, which is what makes it remote."""

        def __init__(self, data: bytes):
            self.data = data
            self.reads: list[tuple[int, int]] = []

        def open_range(self, rel_path: str, start: int, end: int):
            self.reads.append((start, end))
            yield self.data[start : end + 1]

    def test_the_copy_has_the_shape_of_the_whole_file(self):
        size = 4 * 1024 * 1024
        body = bytes(range(256)) * (size // 256)
        connector = self.Remote(body)

        with _timeable_copy(connector, "a/film.avi", "film.avi", size) as (path, partial):
            assert partial
            assert path.stat().st_size == size
            with path.open("rb") as f:
                assert f.read(16) == body[:16]
                f.seek(size - 16)
                assert f.read(16) == body[-16:]

    def test_it_reads_two_slices_and_not_the_film(self):
        size = 200 * 1024 * 1024
        connector = self.Remote(b"\0" * size)

        with _timeable_copy(connector, "a/film.avi", "film.avi", size):
            pass

        fetched = sum(end - start + 1 for start, end in connector.reads)
        assert fetched < 2 * 1024 * 1024, "a length should not cost a download"

    def test_a_local_file_is_handed_straight_through(self, library):
        path = library / "Music" / "Pink Floyd" / "The Wall" / TAGGED
        connector = LocalConnector(library)
        rel = str(path.relative_to(library)).replace("\\", "/")

        with _timeable_copy(connector, rel, TAGGED, path.stat().st_size) as (got, partial):
            assert not partial
            assert got == path

    def test_it_refuses_rather_than_guessing_when_the_size_is_unknown(self):
        """A prefix on its own is worse than no answer at all."""
        connector = self.Remote(b"x" * 1024)
        with pytest.raises(OSError):
            with _timeable_copy(connector, "a/film.avi", "film.avi", None):
                pass
