"""Signing in on a device that cannot use a passkey.

This exists because WebAuthn needs a secure context, and a phone reaching the
server over plain http at a LAN address does not have one. The code route is
therefore the only way onto the device the app is most wanted on, which makes it
worth being strict about: issued only from a signed-in session, usable once, and
short-lived.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.main import app
from app.security import CurrentUser, optional_user, require_user


@pytest.fixture
def other(db, user):
    with db.begin() as conn:
        uid = conn.execute(
            text(
                """
                INSERT INTO users (handle, display_name, is_admin)
                VALUES ('kid', 'Kid', FALSE) RETURNING id
                """
            )
        ).scalar_one()
    return CurrentUser(id=uid, handle="kid", display_name="Kid", is_admin=False)


def _become(person: CurrentUser):
    app.dependency_overrides[require_user] = lambda: person
    app.dependency_overrides[optional_user] = lambda: person


class TestIssuing:
    def test_a_signed_in_session_can_issue_a_code(self, client, db, user):
        r = client.post("/api/auth/devices/link")
        assert r.status_code == 200
        body = r.json()
        assert len(body["code"]) == 8
        assert body["expires_in"] > 0

    def test_the_code_avoids_characters_that_are_misread(self, client, db, user):
        """It is read off one screen and typed into another."""
        for _ in range(20):
            code = client.post("/api/auth/devices/link").json()["code"]
            assert not (set(code) & set("O0I1S5"))

    def test_anonymous_callers_cannot_issue_one(self, anon_client, db):
        """Otherwise it would be a way in rather than a way across."""
        assert anon_client.post("/api/auth/devices/link").status_code == 401

    def test_only_the_hash_is_stored(self, client, db, user):
        """A database dump must not hand anybody a session."""
        code = client.post("/api/auth/devices/link").json()["code"]
        with db.connect() as conn:
            stored = conn.execute(text("SELECT code_hash FROM device_links")).scalar_one()
        assert code not in stored


class TestClaiming:
    def _issue(self, client) -> str:
        return client.post("/api/auth/devices/link").json()["code"]

    def test_a_code_signs_the_same_account_in(self, client, anon_client, db, user):
        code = self._issue(client)

        r = anon_client.post("/api/auth/devices/claim", json={"code": code})
        assert r.status_code == 200
        assert r.json()["handle"] == user.handle
        assert "homesh_session" in r.cookies

    def test_it_works_exactly_once(self, client, anon_client, db, user):
        code = self._issue(client)
        assert anon_client.post("/api/auth/devices/claim", json={"code": code}).status_code == 200

        again = anon_client.post("/api/auth/devices/claim", json={"code": code})
        assert again.status_code == 403, "a reused code must not sign a second device in"

    def test_case_and_spacing_are_forgiven(self, client, anon_client, db, user):
        """It is typed on a phone keyboard, often in a hurry."""
        code = self._issue(client)
        messy = f" {code[:4].lower()} {code[4:].lower()} "
        assert anon_client.post("/api/auth/devices/claim", json={"code": messy}).status_code == 200

    def test_a_wrong_code_is_refused(self, anon_client, db, user):
        r = anon_client.post("/api/auth/devices/claim", json={"code": "ABCD2345"})
        assert r.status_code == 403

    def test_an_expired_code_is_refused(self, client, anon_client, db, user):
        code = self._issue(client)
        with db.begin() as conn:
            conn.execute(text("UPDATE device_links SET expires_at = now() - interval '1 minute'"))

        assert anon_client.post("/api/auth/devices/claim", json={"code": code}).status_code == 403

    def test_the_session_belongs_to_the_issuer_not_the_claimer(
        self, client, anon_client, db, user, other
    ):
        """A code issued by one account must never sign in as another."""
        _become(other)
        kid_code = client.post("/api/auth/devices/link").json()["code"]

        body = anon_client.post("/api/auth/devices/claim", json={"code": kid_code}).json()
        assert body["handle"] == "kid"

    def test_guessing_is_throttled(self, anon_client, db, user):
        """Hopeless already at 39 bits; this makes it loud as well."""
        codes = [f"ABCD234{c}" for c in "ABCDEFGHJKLMNPQ"]
        statuses = [
            anon_client.post("/api/auth/devices/claim", json={"code": c}).status_code
            for c in codes
        ]
        assert 429 in statuses, "brute force was not throttled"


class TestAddingAPasskey:
    """Enrolling a passkey on an account that already exists.

    A passkey belongs to the device that made it *and* to the address it was made
    against. Without this, a household could have one device per account, and
    moving the server to a real hostname would lock its owner out — every
    credential invalidated by the act of securing it, with no way to enrol a
    replacement.
    """

    def test_it_offers_a_challenge_to_a_signed_in_account(self, client, db, user):
        r = client.post("/api/auth/passkeys/begin")
        assert r.status_code == 200
        assert r.json()["options"]["challenge"]

    def test_credentials_already_held_are_excluded(self, client, db, user):
        """So an authenticator says "you already have one" rather than making a second."""
        with db.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO credentials (user_id, credential_id, public_key, sign_count)
                    VALUES (:u, :c, :p, 0)
                    """
                ),
                {"u": str(user.id), "c": b"already-here", "p": b"key"},
            )

        options = client.post("/api/auth/passkeys/begin").json()["options"]
        assert options.get("excludeCredentials"), "an existing passkey was not excluded"

    def test_anonymous_callers_are_refused(self, anon_client, db, user):
        """It adds a key to whoever is asking, so who is asking must be known."""
        assert anon_client.post("/api/auth/passkeys/begin").status_code == 401

    def test_the_last_passkey_cannot_be_removed(self, client, db, user):
        """That would leave an account reachable only by a link code."""
        with db.begin() as conn:
            cred = conn.execute(
                text(
                    """
                    INSERT INTO credentials (user_id, credential_id, public_key, sign_count)
                    VALUES (:u, :c, :p, 0) RETURNING id
                    """
                ),
                {"u": str(user.id), "c": b"only-one", "p": b"key"},
            ).scalar_one()

        r = client.delete(f"/api/auth/passkeys/{cred}")
        assert r.status_code == 409

    def test_one_of_several_can_be_removed(self, client, db, user):
        """A lost phone should not stay enrolled."""
        with db.begin() as conn:
            first = conn.execute(
                text(
                    """
                    INSERT INTO credentials (user_id, credential_id, public_key, sign_count)
                    VALUES (:u, :c, :p, 0) RETURNING id
                    """
                ),
                {"u": str(user.id), "c": b"phone", "p": b"key"},
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO credentials (user_id, credential_id, public_key, sign_count)
                    VALUES (:u, :c, :p, 0)
                    """
                ),
                {"u": str(user.id), "c": b"laptop", "p": b"key"},
            )

        assert client.delete(f"/api/auth/passkeys/{first}").status_code == 200
        assert len(client.get("/api/auth/passkeys").json()) == 1
