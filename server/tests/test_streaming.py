"""Signed URLs and range streaming.

These carry the security boundary for media: the stream endpoint has no session,
so the token is the only thing standing between a URL and your files.
"""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import text

from app.scanner import scan_source
from app.signing import TokenError, mint, verify
from app.sources.local import LocalConnector


@pytest.fixture
def scanned(source, monkeypatch):
    sid, prefix, root = source
    # resolve_playable maps a mount prefix back to a real root via MEDIA_ROOTS.
    name = prefix.rsplit("/", 1)[-1]
    monkeypatch.setenv("MEDIA_ROOTS", f"{name}={root}")

    from app.config import get_settings

    get_settings.cache_clear()
    scan_source(sid, LocalConnector(root))
    yield sid, prefix, root
    get_settings.cache_clear()


def _item(db, filename: str):
    with db.connect() as conn:
        return conn.execute(
            text("SELECT item_id FROM replicas WHERE filename = :f"), {"f": filename}
        ).scalar_one()


class TestTokens:
    def test_round_trip(self):
        item, user = uuid.uuid4(), uuid.uuid4()
        claim = verify(mint(item, user), "stream")
        assert claim.item_id == item
        assert claim.user_id == user

    def test_tampered_payload_rejected(self):
        token = mint(uuid.uuid4(), uuid.uuid4())
        payload, sig = token.split(".")
        # Flip a character in the payload; the signature must no longer match.
        forged = ("A" if payload[0] != "A" else "B") + payload[1:]
        with pytest.raises(TokenError, match="signature|payload"):
            verify(f"{forged}.{sig}")

    def test_expired_rejected(self):
        token = mint(uuid.uuid4(), uuid.uuid4(), ttl=-1)
        with pytest.raises(TokenError, match="expired"):
            verify(token)

    def test_purpose_is_enforced(self):
        """A thumbnail token must not unlock the full-resolution original."""
        token = mint(uuid.uuid4(), uuid.uuid4(), purpose="thumb")
        with pytest.raises(TokenError, match="purpose"):
            verify(token, "stream")

    def test_garbage_rejected(self):
        for bad in ["", "nope", "a.b.c", "...."]:
            with pytest.raises(TokenError):
                verify(bad)

    def test_ttl_is_in_the_future(self):
        claim = verify(mint(uuid.uuid4(), uuid.uuid4(), ttl=300))
        assert 290 < claim.expires_at - time.time() <= 300


class TestStreamEndpoint:
    def test_full_body(self, client, db, scanned):
        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]

        r = client.get(url)
        assert r.status_code == 200
        assert r.headers["accept-ranges"] == "bytes"
        assert r.headers["content-type"].startswith("video/x-matroska")
        assert len(r.content) == 16

    def test_range_request_returns_206(self, client, db, scanned):
        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]

        r = client.get(url, headers={"Range": "bytes=4-7"})
        assert r.status_code == 206
        assert r.headers["content-range"] == "bytes 4-7/16"
        assert r.headers["content-length"] == "4"
        assert len(r.content) == 4

    def test_open_ended_range(self, client, db, scanned):
        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]

        r = client.get(url, headers={"Range": "bytes=8-"})
        assert r.status_code == 206
        assert r.headers["content-range"] == "bytes 8-15/16"
        assert len(r.content) == 8

    def test_suffix_range(self, client, db, scanned):
        """'bytes=-4' means the last four bytes, not the first four."""
        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]

        r = client.get(url, headers={"Range": "bytes=-4"})
        assert r.status_code == 206
        assert r.headers["content-range"] == "bytes 12-15/16"

    def test_range_past_end_returns_416(self, client, db, scanned):
        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]

        r = client.get(url, headers={"Range": "bytes=999-"})
        assert r.status_code == 416
        assert r.headers["content-range"] == "bytes */16"

    def test_malformed_range_rejected(self, client, db, scanned):
        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]
        assert client.get(url, headers={"Range": "kilobytes=1-2"}).status_code == 400


class TestStreamAuthorisation:
    def test_no_token_rejected(self, anon_client, db, scanned):
        item = _item(db, "beach.mkv")
        assert anon_client.get(f"/api/stream/{item}").status_code == 422  # missing query param

    def test_bad_token_rejected(self, anon_client, db, scanned):
        item = _item(db, "beach.mkv")
        assert anon_client.get(f"/api/stream/{item}?t=forged").status_code == 403

    def test_token_for_one_item_does_not_unlock_another(self, anon_client, db, scanned, user):
        """The token binds to an item; swapping the path must fail."""
        wanted = _item(db, "beach.mkv")
        other = _item(db, "DSC_0042.MOV")
        token = mint(other, user.id, "stream")

        r = anon_client.get(f"/api/stream/{wanted}?t={token}")
        assert r.status_code == 403

    def test_expired_token_rejected(self, anon_client, db, scanned, user):
        item = _item(db, "beach.mkv")
        token = mint(item, user.id, "stream", ttl=-1)
        assert anon_client.get(f"/api/stream/{item}?t={token}").status_code == 403

    def test_minting_requires_authentication(self, anon_client, db, scanned):
        item = _item(db, "beach.mkv")
        assert anon_client.get(f"/api/items/{item}/url").status_code == 401

    def test_response_is_not_shared_cacheable(self, client, db, scanned):
        """Per-user signed URLs must never be cached by a shared proxy."""
        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]
        r = client.get(url)
        assert "no-store" in r.headers["cache-control"]
        assert "private" in r.headers["cache-control"]


class TestOfflineSources:
    def test_unreachable_source_reports_503(self, client, db, scanned, monkeypatch):
        """The catalog still knows the file; the machine holding it is off."""
        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]

        monkeypatch.setenv("MEDIA_ROOTS", "Nowhere=/does/not/exist")
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            assert client.get(url).status_code == 503
        finally:
            get_settings.cache_clear()

    def test_unknown_item_404s(self, client, scanned):
        assert client.get(f"/api/items/{uuid.uuid4()}/url").status_code == 404


class TestDownload:
    """Saving a copy to the device — the only way to use formats we can't preview."""

    def test_inline_by_default(self, client, db, scanned):
        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]
        r = client.get(url)
        assert r.headers["content-disposition"].startswith("inline")

    def test_download_flag_makes_it_an_attachment(self, client, db, scanned):
        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]
        r = client.get(f"{url}&download=1")
        assert r.headers["content-disposition"].startswith("attachment")

    def test_download_returns_identical_bytes(self, client, db, scanned):
        """Only the header differs, so the flag grants nothing extra."""
        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]
        assert client.get(url).content == client.get(f"{url}&download=1").content

    def test_download_still_requires_a_valid_token(self, anon_client, db, scanned):
        item = _item(db, "beach.mkv")
        assert anon_client.get(f"/api/stream/{item}?t=forged&download=1").status_code == 403

    def test_unicode_filename_uses_rfc6266_encoding(self, client, db, scanned):
        """A bare non-ASCII filename is invalid in an HTTP header."""
        item = _item(db, "שיר בעברית.mp3")
        url = client.get(f"/api/items/{item}/url").json()["url"]
        disposition = client.get(f"{url}&download=1").headers["content-disposition"]

        assert "filename*=UTF-8''" in disposition
        assert "%D7%A9" in disposition          # percent-encoded Hebrew
        assert disposition.isascii(), "header must be ASCII-safe"

    def test_quotes_in_filename_cannot_break_the_header(self):
        """A filename containing a quote would otherwise terminate the parameter."""
        from app.stream import _disposition

        header = _disposition('we"rd\name.mp3', attachment=True)
        assert header.count('"') == 2, f"unbalanced quoting: {header}"


class TestReaderIsClosed:
    """A range read must never be left for the garbage collector to close.

    A regression test for a server-wide freeze, not a tidiness rule.
    `open_range` holds an open HTTP response to Drive while it yields. An
    abandoned generator is finalised by the collector on whatever thread it
    happens to be running on, and when that thread is inside httpx's connection
    pool holding the pool lock, closing the response asks for the same lock on
    the same thread. It is not reentrant: the thread waits for itself forever,
    and every later read of anything queues behind it. Observed in a stack dump
    as twelve worker threads stuck in `open_range`.

    These hold their own reference to the reader, so reference counting cannot
    collect it and quietly make the test pass. Nothing but a deliberate close
    satisfies them.
    """

    @staticmethod
    def _reader(closed: list[bool]):
        def reader():
            try:
                yield b"first"
                yield b"second"
            finally:
                closed.append(True)

        return reader()

    @staticmethod
    def _wait_for(closed: list[bool]) -> bool:
        """The close is scheduled rather than awaited, so give it a moment."""
        for _ in range(200):
            if closed:
                return True
            time.sleep(0.01)
        return False

    async def test_closed_when_the_listener_goes_away(self):
        """A phone locking mid-song, a seek, an ffmpeg killed — all this path."""
        from app.stream import _closing_body

        closed: list[bool] = []
        held = self._reader(closed)          # a reference nothing can drop

        body = _closing_body(held)
        assert await body.__anext__() == b"first"
        await body.aclose()                  # the listener disconnects here

        assert self._wait_for(closed), "the reader was left open mid-file"

    async def test_closed_after_a_complete_read(self):
        from app.stream import _closing_body

        closed: list[bool] = []
        held = self._reader(closed)

        assert [chunk async for chunk in _closing_body(held)] == [b"first", b"second"]
        assert self._wait_for(closed), "the reader was left open after a full read"

    def test_the_endpoint_uses_it(self, client, db, scanned, monkeypatch):
        """Wired in, and not quietly removed later."""
        import app.stream as stream

        seen: list[bool] = []
        original = stream._closing_body

        def spy(chunks):
            seen.append(True)
            return original(chunks)

        monkeypatch.setattr(stream, "_closing_body", spy)

        item = _item(db, "beach.mkv")
        url = client.get(f"/api/items/{item}/url").json()["url"]
        assert client.get(url).status_code == 200
        assert seen, "the stream endpoint no longer closes its reader"
