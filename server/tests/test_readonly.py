"""Media is read-only. Nothing the server exposes may modify a source file.

This is a product guarantee, not an implementation detail: the owner keeps
irreplaceable photos here and must be able to browse them without any risk that
the server edits or deletes something. The deployment enforces it too — the media
volume is mounted `:ro`, so the kernel refuses writes — but a guarantee that rests
only on deployment configuration is one bad compose edit away from being untrue.
"""

from __future__ import annotations

import pytest

from app.main import app
from app.sources.local import LocalConnector

# Every route that legitimately changes state. None of these touch media files:
# auth manages credentials, prefs writes a JSON column, scan reads the filesystem
# and writes only catalog rows.
ALLOWED_MUTATING_ROUTES = {
    ("POST", "/api/auth/register/begin"),
    ("POST", "/api/auth/register/complete"),
    ("POST", "/api/auth/login/begin"),
    ("POST", "/api/auth/login/complete"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/sources/{source_id}/scan"),
    ("PUT", "/api/prefs"),
    ("PUT", "/api/people/{user_id}/rules"),
    ("DELETE", "/api/people/{user_id}"),
    ("POST", "/api/renderers/pair/begin"),
    ("POST", "/api/renderers/pair/claim"),
    ("POST", "/api/zones"),
    ("POST", "/api/zones/{zone_id}/play"),
    ("POST", "/api/zones/{zone_id}/stop"),
    ("POST", "/api/zones/{zone_id}/volume"),
}


def test_no_unexpected_mutating_routes():
    """Adding a write endpoint must be a deliberate act, not an accident.

    If this fails because you added a route, confirm it cannot modify a source
    file, then add it to the allowlist above.
    """
    found = set()
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        for method in methods - {"GET", "HEAD", "OPTIONS"}:
            found.add((method, route.path))

    unexpected = found - ALLOWED_MUTATING_ROUTES
    assert not unexpected, f"unexpected mutating routes: {sorted(unexpected)}"


class TestConnectorSurface:
    """The connector is the only thing that touches source files."""

    @pytest.mark.parametrize(
        "name",
        ["write", "write_bytes", "write_text", "save", "delete", "remove",
         "unlink", "rename", "move", "mkdir", "rmdir", "chmod", "truncate"],
    )
    def test_has_no_write_operation(self, name, library):
        connector = LocalConnector(library)
        assert not hasattr(connector, name), (
            f"LocalConnector exposes {name}(), which could modify the library"
        )

    def test_files_are_opened_read_only(self, library):
        """open_range must never acquire a writable handle."""
        import inspect

        source = inspect.getsource(LocalConnector.open_range)
        assert '"rb"' in source or "'rb'" in source
        for mode in ['"w"', '"a"', '"r+"', '"wb"', '"ab"', '"rb+"']:
            assert mode not in source, f"open_range uses mode {mode}"

    def test_reading_does_not_alter_the_file(self, library):
        """Belt and braces: read a file and confirm it is untouched."""
        target = library / "Docs" / "notes.md"
        before = target.read_bytes()
        stat_before = target.stat().st_mtime

        connector = LocalConnector(library)
        consumed = b"".join(connector.open_range("Docs/notes.md", 0, None))

        assert consumed == before
        assert target.read_bytes() == before
        assert target.stat().st_mtime == stat_before


class TestStreamingIsReadOnly:
    def test_stream_endpoint_rejects_write_methods(self, client, db):
        """Even with a valid session, the stream path is GET-only."""
        import uuid

        item = uuid.uuid4()
        for method in ("post", "put", "delete", "patch"):
            r = getattr(client, method)(f"/api/stream/{item}?t=x")
            assert r.status_code == 405, f"{method.upper()} was not rejected"

    def test_thumb_endpoint_rejects_write_methods(self, client):
        import uuid

        item = uuid.uuid4()
        for method in ("post", "put", "delete"):
            assert getattr(client, method)(f"/api/thumb/{item}").status_code == 405
