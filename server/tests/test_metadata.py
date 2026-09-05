"""Metadata extraction.

Tags are additive and carry their origin, so a value from a model can never be
mistaken for one the file itself claimed (ARCHITECTURE.md §2, §9).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.metadata import _clean, extract_for_source, read_audio
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
