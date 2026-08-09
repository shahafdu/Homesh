"""Thumbnail generation, caching and access control."""

from __future__ import annotations

import io
import shutil

import pytest
from sqlalchemy import text

from app.scanner import scan_source
from app.signing import mint
from app.sources.local import LocalConnector
from app.thumbs import CACHE_ROOT, cache_path


@pytest.fixture
def scanned(source, monkeypatch):
    sid, prefix, root = source
    name = prefix.rsplit("/", 1)[-1]
    monkeypatch.setenv("MEDIA_ROOTS", f"{name}={root}")

    from app.config import get_settings

    get_settings.cache_clear()
    scan_source(sid, LocalConnector(root))
    yield sid, prefix, root
    get_settings.cache_clear()
    # The cache is shared state on disk; leaving entries behind would let one test
    # satisfy another's request.
    shutil.rmtree(CACHE_ROOT, ignore_errors=True)


def _item(db, filename: str):
    with db.connect() as conn:
        return conn.execute(
            text("SELECT item_id FROM replicas WHERE filename = :f"), {"f": filename}
        ).scalar_one()


class TestGeneration:
    def test_real_image_produces_a_webp(self, client, db, scanned):
        item = _item(db, "real.png")
        r = client.get(f"/api/thumb/{item}")

        assert r.status_code == 200
        assert r.headers["content-type"] == "image/webp"
        assert r.content[:4] == b"RIFF" and r.content[8:12] == b"WEBP"

    def test_thumbnail_is_bounded_by_the_requested_size(self, client, db, scanned):
        from PIL import Image

        item = _item(db, "real.png")
        small = client.get(f"/api/thumb/{item}?size=small").content
        large = client.get(f"/api/thumb/{item}?size=large").content

        with Image.open(io.BytesIO(small)) as img:
            assert max(img.size) <= 160
        with Image.open(io.BytesIO(large)) as img:
            # The source is 320x240, so "large" (480) must not upscale it.
            assert max(img.size) == 320

    def test_result_is_cached_on_disk(self, client, db, scanned):
        item = _item(db, "real.png")
        assert not cache_path(item, "small").exists()

        client.get(f"/api/thumb/{item}?size=small")
        assert cache_path(item, "small").exists()

    def test_undecodable_file_404s_without_crashing(self, client, db, scanned):
        """A corrupt or placeholder file must not break the folder it lives in."""
        item = _item(db, "IMG_1234.jpg")  # 16 bytes of 'x'
        assert client.get(f"/api/thumb/{item}").status_code == 404

    def test_absence_is_remembered(self, client, db, scanned):
        """Having failed once, we must not re-run the work on every page view."""
        item = _item(db, "IMG_1234.jpg")
        client.get(f"/api/thumb/{item}")

        marker = cache_path(item, "small")
        assert marker.exists(), "no marker written, so the failure would repeat"
        assert marker.stat().st_size == 1

        assert client.get(f"/api/thumb/{item}").status_code == 404

    def test_document_has_no_thumbnail_strategy(self, client, db, scanned):
        item = _item(db, "notes.md")
        assert client.get(f"/api/thumb/{item}").status_code == 404

    def test_unknown_item_404s(self, client, scanned):
        import uuid

        assert client.get(f"/api/thumb/{uuid.uuid4()}").status_code == 404


class TestAccessControl:
    def test_requires_authentication(self, anon_client, db, scanned):
        item = _item(db, "real.png")
        assert anon_client.get(f"/api/thumb/{item}").status_code == 401

    def test_thumb_token_grants_access(self, anon_client, db, scanned, user):
        """A TV app or Cast receiver has no session, so it presents a token."""
        item = _item(db, "real.png")
        token = mint(item, user.id, "thumb")
        assert anon_client.get(f"/api/thumb/{item}?t={token}").status_code == 200

    def test_stream_token_does_not_grant_thumbnail_access(self, anon_client, db, scanned, user):
        """Purposes are not interchangeable in either direction."""
        item = _item(db, "real.png")
        token = mint(item, user.id, "stream")
        assert anon_client.get(f"/api/thumb/{item}?t={token}").status_code == 401

    def test_token_for_another_item_rejected(self, anon_client, db, scanned, user):
        wanted = _item(db, "real.png")
        other = _item(db, "IMG_1235.heic")
        token = mint(other, user.id, "thumb")
        assert anon_client.get(f"/api/thumb/{wanted}?t={token}").status_code == 401

    def test_expired_token_rejected(self, anon_client, db, scanned, user):
        item = _item(db, "real.png")
        token = mint(item, user.id, "thumb", ttl=-1)
        assert anon_client.get(f"/api/thumb/{item}?t={token}").status_code == 401

    def test_response_is_privately_cacheable(self, client, db, scanned):
        item = _item(db, "real.png")
        r = client.get(f"/api/thumb/{item}")
        assert "private" in r.headers["cache-control"]


class TestOfflineSource:
    def test_offline_source_is_not_cached_as_absent(self, client, db, scanned, monkeypatch):
        """A source being off is temporary; recording 'no artwork' would be wrong."""
        import os

        from app.config import get_settings

        item = _item(db, "real.png")
        working_roots = os.environ["MEDIA_ROOTS"]

        monkeypatch.setenv("MEDIA_ROOTS", "Nowhere=/does/not/exist")
        get_settings.cache_clear()

        assert client.get(f"/api/thumb/{item}").status_code == 503
        assert not cache_path(item, "small").exists(), "offline was cached as 'no artwork'"

        # Restore explicitly: monkeypatch does not undo until teardown, so clearing
        # the settings cache alone would just reload the broken value.
        monkeypatch.setenv("MEDIA_ROOTS", working_roots)
        get_settings.cache_clear()

        assert client.get(f"/api/thumb/{item}").status_code == 200
