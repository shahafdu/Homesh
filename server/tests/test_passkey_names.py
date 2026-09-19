"""One passkey for the PC and the standby.

A passkey belongs to a name -- the relying party id. The PC's used to belong to
its full host name, so the standby, under another, could use none of them and
every device had to be set up twice. New passkeys belong to the tailnet's domain,
which both machines sit under. Old ones still sign in to the PC, and each device
swaps its own with one tap.

The signature check itself is the webauthn library's and is not re-tested here.
What is tested is which name each ceremony is checked against, because that is
the whole of the change -- a sign-in verified against the wrong name is either a
lock-out or a hole.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text

SHARED = "example.ts.net"
OLD = "pc.example.ts.net"


@pytest.fixture
def names(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("RP_ID", SHARED)
    monkeypatch.setenv("RP_ID_LEGACY", OLD)
    monkeypatch.setenv("PUBLIC_ORIGIN", f"https://{OLD}")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def checked(monkeypatch):
    """Stand-ins for the signature checks, recording what they were asked."""
    from app import auth

    seen: list[dict] = []

    def authentication(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(new_sign_count=0)

    def registration(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(
            credential_id=b"new-passkey", credential_public_key=b"k", sign_count=0
        )

    monkeypatch.setattr(auth, "verify_authentication_response", authentication)
    monkeypatch.setattr(auth, "verify_registration_response", registration)
    return seen


def _passkey(db, user, cid: bytes, rp: str | None) -> str:
    with db.begin() as conn:
        return str(
            conn.execute(
                text(
                    "INSERT INTO credentials (user_id, credential_id, public_key, rp_id) "
                    "VALUES (:u, :c, 'k', :rp) RETURNING id"
                ),
                {"u": str(user.id), "c": cid, "rp": rp},
            ).scalar_one()
        )


def _sign_in(anon_client, cid: bytes, legacy: bool) -> dict:
    import base64

    begin = anon_client.post("/api/auth/login/begin", json={"legacy": legacy}).json()
    raw = base64.urlsafe_b64encode(cid).rstrip(b"=").decode()
    done = anon_client.post(
        "/api/auth/login/complete",
        json={"flow_id": begin["flow_id"], "credential": {"id": raw, "rawId": raw}},
    )
    assert done.status_code == 200, done.text
    return {"begin": begin, "done": done.json()}


class TestSigningIn:
    def test_an_ordinary_sign_in_is_for_the_shared_name(self, anon_client, db, user, names,
                                                         checked):
        _passkey(db, user, b"shared-one", SHARED)
        result = _sign_in(anon_client, b"shared-one", legacy=False)

        assert result["begin"]["options"]["rpId"] == SHARED
        assert checked[-1]["expected_rp_id"] == SHARED
        assert result["done"]["upgrade"] is False

    def test_an_old_passkey_still_signs_in_to_the_pc(self, anon_client, db, user, names,
                                                      checked):
        old_id = _passkey(db, user, b"old-one", None)
        result = _sign_in(anon_client, b"old-one", legacy=True)

        assert result["begin"]["options"]["rpId"] == OLD
        assert checked[-1]["expected_rp_id"] == OLD
        # And the app is told to offer the swap, and which passkey it replaces.
        assert result["done"] == {"ok": True, "upgrade": True, "credential": old_id}

    def test_the_sign_in_screen_is_told_old_passkeys_are_accepted(self, anon_client, names):
        begin = anon_client.post("/api/auth/login/begin", json={}).json()
        assert begin["legacy_available"] is True

    def test_where_there_is_no_old_name_there_is_no_old_way_in(self, anon_client, monkeypatch):
        """The standby, and the PC once every device has moved."""
        from app.config import get_settings

        monkeypatch.setenv("RP_ID_LEGACY", "")
        get_settings.cache_clear()
        try:
            assert anon_client.post("/api/auth/login/begin",
                                    json={"legacy": True}).status_code == 404
            begin = anon_client.post("/api/auth/login/begin", json={}).json()
            assert begin["legacy_available"] is False
        finally:
            get_settings.cache_clear()

    def test_an_empty_request_still_signs_in(self, anon_client, names):
        """Clients from before the change send no body at all."""
        assert anon_client.post("/api/auth/login/begin").status_code == 200


class TestTheOneTapSwap:
    def _add(self, client, replaces: str | None) -> dict:
        begin = client.post("/api/auth/passkeys/begin").json()
        body = {"flow_id": begin["flow_id"], "credential": {}}
        if replaces:
            body["replaces"] = replaces
        r = client.post("/api/auth/passkeys/complete", json=body)
        assert r.status_code == 200, r.text
        return {"begin": begin, "done": r.json()}

    def _held(self, db) -> dict[bytes, str | None]:
        with db.connect() as conn:
            rows = conn.execute(text("SELECT credential_id, rp_id FROM credentials")).all()
        return {bytes(r[0]): r[1] for r in rows}

    def test_new_passkeys_are_for_the_shared_name(self, client, db, user, names, checked):
        result = self._add(client, None)
        assert result["begin"]["options"]["rp"]["id"] == SHARED
        assert checked[-1]["expected_rp_id"] == SHARED
        assert self._held(db)[b"new-passkey"] == SHARED

    def test_the_old_one_goes_once_the_new_one_is_saved(self, client, db, user, names,
                                                         checked):
        old_id = _passkey(db, user, b"old-one", None)
        assert self._add(client, old_id)["done"]["replaced"] is True
        assert self._held(db) == {b"new-passkey": SHARED}

    def test_only_an_old_passkey_can_be_replaced(self, client, db, user, names, checked):
        """A passkey already on the shared name is never taken away by this."""
        current = _passkey(db, user, b"current", SHARED)
        assert self._add(client, current)["done"]["replaced"] is False
        assert b"current" in self._held(db)

    def test_only_your_own(self, client, db, user, names, checked):
        with db.begin() as conn:
            other = conn.execute(
                text("INSERT INTO users (handle, display_name) VALUES ('kid', 'Kid') "
                     "RETURNING id")
            ).scalar_one()
            theirs = str(
                conn.execute(
                    text("INSERT INTO credentials (user_id, credential_id, public_key) "
                         "VALUES (:u, 'theirs', 'k') RETURNING id"),
                    {"u": str(other)},
                ).scalar_one()
            )
        assert self._add(client, theirs)["done"]["replaced"] is False
        assert b"theirs" in self._held(db)


class TestTheList:
    def test_an_old_passkey_says_it_works_on_the_pc_only(self, client, db, user, names):
        _passkey(db, user, b"old-one", None)
        _passkey(db, user, b"shared-one", SHARED)
        listed = client.get("/api/auth/passkeys").json()
        assert sorted(p["pc_only"] for p in listed) == [False, True]

    def test_nothing_is_limited_where_the_name_never_moved(self, client, db, user,
                                                          monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("RP_ID_LEGACY", "")
        get_settings.cache_clear()
        try:
            _passkey(db, user, b"old-one", None)
            assert [p["pc_only"] for p in client.get("/api/auth/passkeys").json()] == [False]
        finally:
            get_settings.cache_clear()
