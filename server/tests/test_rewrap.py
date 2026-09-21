"""Containers a television reads wrongly, rewritten rather than re-encoded.

One mp4 in the real library plays from the beginning in a browser and four
seconds in on the box, every time, instantly -- and everything inside it is
ordinary: keyframe at 0.000 s, first sync sample 1, no edit trim, index at the
front, durations in agreement. The only difference from a file that plays
correctly is a non-standard box in front of the index, `ftyp beam moov mdat`.

So these are about recognising that shape, and about being careful with what
follows: the picture must not change, the browser must keep getting the file
itself, and a failure to rewrite must fall back rather than fail.
"""

from __future__ import annotations

import struct
import uuid

import pytest

from app import rewrap


def box(name: str, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + name.encode("latin1") + payload


ORDINARY = box("ftyp", b"isom" * 4) + box("moov", b"\x00" * 64) + box("mdat", b"\x00" * 128)
WITH_BEAM = box("ftyp", b"isom" * 4) + box("beam", b"\x00" * 32) + box("moov", b"\x00" * 64)


class Source:
    """A connector that hands back the head of a file."""

    root = None

    def __init__(self, data: bytes):
        self.data = data
        self.reads = 0

    def open_range(self, _rel, start, end):
        self.reads += 1
        body = self.data[start : end + 1]

        def chunks():
            yield body

        return chunks()


@pytest.fixture
def scanned(source, monkeypatch):
    """The fixture library, indexed and reachable -- as in test_streaming."""
    from app.config import get_settings
    from app.scanner import scan_source
    from app.sources.local import LocalConnector

    sid, prefix, root = source
    monkeypatch.setenv("MEDIA_ROOTS", f"{prefix.rsplit('/', 1)[-1]}={root}")
    get_settings.cache_clear()
    scan_source(sid, LocalConnector(root))
    yield sid, prefix, root
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _forget():
    rewrap.forget()
    yield
    rewrap.forget()


class TestRecognisingIt:
    def test_an_ordinary_container_is_left_alone(self):
        assert rewrap.odd_container(uuid.uuid4(), "mp4", Source(ORDINARY), "a.mp4") is False

    def test_a_box_nobody_standardised_is_noticed(self):
        assert rewrap.odd_container(uuid.uuid4(), "mp4", Source(WITH_BEAM), "a.mp4") is True

    def test_only_what_precedes_the_index_counts(self):
        """Boxes after the index are ordinary and plentiful -- `free`, `udta` and
        the rest -- and none of them is what a player reads first."""
        after = ORDINARY + box("beam", b"\x00" * 16)
        assert rewrap.odd_container(uuid.uuid4(), "mp4", Source(after), "a.mp4") is False

    def test_the_answer_is_remembered(self):
        """It is a property of the file, and reading a header from Drive costs a
        round trip."""
        source = Source(WITH_BEAM)
        item = uuid.uuid4()
        for _ in range(5):
            rewrap.odd_container(item, "mp4", source, "a.mp4")
        assert source.reads == 1

    def test_a_rescan_forgets_it(self):
        source = Source(WITH_BEAM)
        item = uuid.uuid4()
        rewrap.odd_container(item, "mp4", source, "a.mp4")
        rewrap.forget(item)
        rewrap.odd_container(item, "mp4", source, "a.mp4")
        assert source.reads == 2

    def test_formats_that_have_no_such_boxes_are_not_read_at_all(self):
        source = Source(WITH_BEAM)
        assert rewrap.odd_container(uuid.uuid4(), "mkv", source, "a.mkv") is False
        assert source.reads == 0, "a file that cannot have this problem was fetched"

    def test_an_unreadable_header_is_not_treated_as_odd(self):
        """A source that has gone away is a different problem, and the play that
        follows says so properly."""

        class Gone(Source):
            def open_range(self, _rel, _start, _end):
                raise OSError("no such device")

        assert rewrap.odd_container(uuid.uuid4(), "mp4", Gone(b""), "a.mp4") is False


class TestRewriting:
    async def test_a_failure_falls_back_to_the_original(self, monkeypatch, tmp_path):
        """A file that plays four seconds in beats a file that does not play."""
        from app.config import get_settings

        monkeypatch.setenv("CACHE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            # No server to read from in a test, so ffmpeg fails -- which is the
            # case under test.
            assert await rewrap.ensure(uuid.uuid4(), uuid.uuid4()) is None
            assert list(tmp_path.glob("video/*.mp4")) == [], "a partial file was left behind"
        finally:
            get_settings.cache_clear()

    async def test_an_existing_copy_is_used_as_it_is(self, monkeypatch, tmp_path):
        from app.config import get_settings

        monkeypatch.setenv("CACHE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            item = uuid.uuid4()
            made = rewrap.cache_path(item)
            made.parent.mkdir(parents=True, exist_ok=True)
            made.write_bytes(b"already rewritten")

            called = []
            monkeypatch.setattr(rewrap.subprocess, "run", lambda *a, **k: called.append(a))
            assert await rewrap.ensure(item, uuid.uuid4()) == made
            assert called == [], "it was rewritten again"
        finally:
            get_settings.cache_clear()


class TestWhoGetsWhich:
    def test_the_browser_is_given_the_file_itself(self, client, db, scanned):
        """Browsers read these files correctly, so nothing changes for them."""
        from sqlalchemy import text

        with db.connect() as conn:
            item = conn.execute(
                text("SELECT item_id FROM replicas WHERE filename = :f"), {"f": "beach.mkv"}
            ).scalar_one()
        url = client.get(f"/api/items/{item}/url").json()["url"]
        assert "plain=1" not in url

    def test_asking_for_a_rewrite_of_something_ordinary_still_serves_it(
        self, client, db, scanned
    ):
        """`plain=1` on a file that cannot be rewritten here falls back to the
        original rather than failing."""
        from sqlalchemy import text

        with db.connect() as conn:
            item = conn.execute(
                text("SELECT item_id FROM replicas WHERE filename = :f"), {"f": "beach.mkv"}
            ).scalar_one()
        url = client.get(f"/api/items/{item}/url").json()["url"]
        served = client.get(f"{url}&plain=1")
        assert served.status_code == 200 and served.content
