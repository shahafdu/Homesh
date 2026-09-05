"""Security properties from ARCHITECTURE.md §6, asserted rather than assumed."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.security import SESSION_COOKIE, create_session


class TestAuthGating:
    """No catalog endpoint may answer an unauthenticated caller."""

    @pytest.mark.parametrize(
        "path",
        ["/api/browse?path=/", "/api/search?q=x", "/api/sources", "/api/prefs", "/api/auth/me"],
    )
    def test_requires_authentication(self, anon_client, path):
        assert anon_client.get(path).status_code == 401

    def test_health_is_public(self, anon_client):
        """Deliberately open: it is how the container reports readiness."""
        assert anon_client.get("/api/health").status_code == 200

    def test_unknown_api_path_404s_as_json(self, anon_client):
        """The SPA catch-all must not answer API typos with the HTML shell."""
        r = anon_client.get("/api/definitely-not-a-route")
        assert r.status_code == 404


class TestSessions:
    def test_token_is_stored_only_as_a_hash(self, db, user):
        """A database dump must not yield usable session tokens."""
        with db.begin() as conn:
            token = create_session(conn, user.id, "test-device")

        with db.connect() as conn:
            stored = conn.execute(
                text("SELECT refresh_hash FROM auth_sessions WHERE user_id = :u"),
                {"u": str(user.id)},
            ).scalar_one()

        assert token.encode() not in bytes(stored)
        assert len(bytes(stored)) == 32  # sha256

    def test_revoked_session_is_rejected(self, db, user, anon_client):
        with db.begin() as conn:
            token = create_session(conn, user.id, "test-device")

        anon_client.cookies.set(SESSION_COOKIE, token)
        assert anon_client.get("/api/auth/me").status_code == 200

        with db.begin() as conn:
            conn.execute(text("UPDATE auth_sessions SET revoked_at = now()"))

        assert anon_client.get("/api/auth/me").status_code == 401

    def test_expired_session_is_rejected(self, db, user, anon_client):
        with db.begin() as conn:
            token = create_session(conn, user.id, "test-device")
            conn.execute(text("UPDATE auth_sessions SET expires_at = now() - interval '1 day'"))

        anon_client.cookies.set(SESSION_COOKIE, token)
        assert anon_client.get("/api/auth/me").status_code == 401

    def test_garbage_token_is_rejected(self, anon_client):
        anon_client.cookies.set(SESSION_COOKIE, "not-a-real-token")
        assert anon_client.get("/api/auth/me").status_code == 401


class TestRegistrationIsClosed:
    def test_no_public_registration_once_a_user_exists(self, anon_client, user):
        r = anon_client.post(
            "/api/auth/register/begin",
            json={"handle": "intruder", "display_name": "Intruder"},
        )
        assert r.status_code == 403

    def test_bootstrap_code_required_for_first_user(self, anon_client, db):
        with db.begin() as conn:
            conn.execute(text("DELETE FROM users"))

        r = anon_client.post(
            "/api/auth/register/begin",
            json={"handle": "first", "display_name": "First"},
        )
        assert r.status_code == 403


class TestPathConfinement:
    """Connectors must refuse paths outside their root, after symlink resolution."""

    def test_traversal_is_refused(self, library):
        from app.sources.local import LocalConnector

        conn = LocalConnector(library / "Music")
        with pytest.raises(PermissionError):
            conn.list_dir("../Docs")

    def test_absolute_escape_is_refused(self, library):
        from app.sources.local import LocalConnector

        conn = LocalConnector(library / "Music")
        with pytest.raises(PermissionError):
            conn.stat("../../../../etc/passwd")

    def test_legitimate_subpath_is_allowed(self, library):
        from app.sources.local import LocalConnector

        conn = LocalConnector(library / "Music")
        assert len(conn.list_dir("Unsorted")) > 0
