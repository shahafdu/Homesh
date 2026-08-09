"""Catalog indexing."""

from __future__ import annotations

from sqlalchemy import text

from app.scanner import scan_source
from app.sources.base import classify
from app.sources.local import LocalConnector


def test_scan_indexes_every_file(db, source):
    sid, _prefix, root = source
    result = scan_source(sid, LocalConnector(root))

    assert result.added == 14
    assert result.errors == []
    assert result.playlists == 1  # oldies.m3u

    with db.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM replicas WHERE source_id = :s"), {"s": str(sid)}
        ).scalar_one()
    assert count == 14


def test_rescan_creates_no_duplicates(db, source):
    """The scanner reconciles; running it twice must not double the catalog."""
    sid, _prefix, root = source
    scan_source(sid, LocalConnector(root))
    again = scan_source(sid, LocalConnector(root))

    assert again.added == 0
    assert again.updated == 14

    with db.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM replicas WHERE source_id = :s"), {"s": str(sid)}
        ).scalar_one()
    assert count == 14


def test_vanished_files_are_marked_not_deleted(db, source):
    """A file that disappears stays in the catalog, flagged unavailable.

    Deleting the row would lose the fact that it ever existed, which is exactly
    what makes the catalog useful when the RAID is powered off.
    """
    sid, _prefix, root = source
    scan_source(sid, LocalConnector(root))

    (root / "Docs" / "notes.md").unlink()
    result = scan_source(sid, LocalConnector(root))

    assert result.vanished == 1
    with db.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT available FROM replicas
                WHERE source_id = :s AND filename = 'notes.md'
                """
            ),
            {"s": str(sid)},
        ).first()
    assert row is not None, "row was deleted instead of marked unavailable"
    assert row[0] is False


def test_unavailable_source_reports_rather_than_raising(db, source, tmp_path):
    sid, _prefix, _root = source
    result = scan_source(sid, LocalConnector(tmp_path / "does-not-exist"))
    assert result.errors == ["source unavailable"]
    assert result.added == 0


class TestClassify:
    """Extension handling. Unknown extensions are kept, never dropped."""

    def test_known_kinds(self):
        assert classify("song.mp3") == ("audio", "mp3")
        assert classify("clip.mkv") == ("video", "mkv")
        assert classify("snap.HEIC") == ("photo", "heic")
        assert classify("manual.pdf") == ("doc", "pdf")

    def test_uppercase_extension_normalises(self):
        assert classify("DSC_0042.MOV") == ("video", "mov")

    def test_unknown_extension_is_kept_as_other(self):
        assert classify("weird.xyz") == ("other", "xyz")

    def test_no_extension(self):
        assert classify("README") == ("other", "")

    def test_unicode_filename(self):
        kind, ext = classify("שיר בעברית.mp3")
        assert (kind, ext) == ("audio", "mp3")
