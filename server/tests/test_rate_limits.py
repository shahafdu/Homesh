"""Attempt limits on the doors anybody can knock on.

Sign-in, the first-run code, invitations and device codes are reachable without
an account, and each attempt costs the server something: a signature check, a
held challenge, a row in the audit log. These tests are about the ceiling on
that -- and about the ceiling not standing in the way of ordinary use.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app import throttle


class TestTheCounter:
    def test_it_allows_the_allowance_and_then_refuses(self):
        limit = throttle.Limit(3, timedelta(minutes=5))
        for _ in range(3):
            throttle.hit("door", "someone", limit)
        with pytest.raises(HTTPException) as refused:
            throttle.hit("door", "someone", limit)
        assert refused.value.status_code == 429

    def test_it_says_how_long_to_wait(self):
        limit = throttle.Limit(1, timedelta(minutes=5))
        start = datetime(2026, 9, 20, 12, tzinfo=UTC)
        throttle.hit("door", "someone", limit, now=start)
        with pytest.raises(HTTPException) as refused:
            throttle.hit("door", "someone", limit, now=start + timedelta(minutes=1))
        # Four minutes of the window left, and a header a client can act on.
        assert 230 <= int(refused.value.headers["Retry-After"]) <= 245

    def test_the_window_moves(self):
        limit = throttle.Limit(1, timedelta(minutes=5))
        start = datetime(2026, 9, 20, 12, tzinfo=UTC)
        throttle.hit("door", "someone", limit, now=start)
        throttle.hit("door", "someone", limit, now=start + timedelta(minutes=6))

    def test_one_caller_does_not_spend_anothers_allowance(self):
        """Where callers can be told apart at all -- see the note in throttle.py."""
        limit = throttle.Limit(1, timedelta(minutes=5))
        throttle.hit("door", "first", limit)
        throttle.hit("door", "second", limit)

    def test_doors_are_counted_separately(self):
        limit = throttle.Limit(1, timedelta(minutes=5))
        throttle.hit("front", "someone", limit)
        throttle.hit("back", "someone", limit)

    def test_success_forgives_the_fumbles(self):
        limit = throttle.Limit(2, timedelta(minutes=5))
        throttle.hit("sign-in", "someone", limit)
        throttle.forgive("sign-in", "someone")
        throttle.hit("sign-in", "someone", limit)
        throttle.hit("sign-in", "someone", limit)

    def test_it_remembers_a_bounded_number_of_callers(self):
        """A flood from many addresses must not grow the memory without end."""
        limit = throttle.Limit(1, timedelta(minutes=5))
        for n in range(throttle.MAX_KEYS + 50):
            throttle.hit("door", f"caller-{n}", limit)
        assert len(throttle._seen) <= throttle.MAX_KEYS


class TestSigningIn:
    def _attempt(self, anon_client) -> int:
        begin = anon_client.post("/api/auth/login/begin", json={})
        if begin.status_code != 200:
            return begin.status_code
        passkey = {"id": "AAAA", "rawId": "AAAA"}
        done = anon_client.post(
            "/api/auth/login/complete",
            json={"flow_id": begin.json()["flow_id"], "credential": passkey},
        )
        return done.status_code

    def test_guessing_is_refused_before_long(self, anon_client, db, user):
        codes = [self._attempt(anon_client) for _ in range(throttle.SIGN_IN.allowed + 2)]
        assert codes[0] == 401, "an unknown credential is a failed sign-in"
        assert 429 in codes, "an endless stream of sign-in attempts was accepted"
        assert codes.count(401) <= throttle.SIGN_IN.allowed

    def test_the_count_is_before_the_check_not_after_the_failure(self, anon_client, db,
                                                                 user, monkeypatch):
        """A signature check costs the same whether it passes, and that cost is
        what a flood is spending."""
        from app import auth

        monkeypatch.setattr(
            auth, "verify_authentication_response",
            lambda **kw: SimpleNamespace(new_sign_count=0),
        )
        with db.begin() as conn:
            conn.execute(
                text("INSERT INTO credentials (user_id, credential_id, public_key, rp_id) "
                     "VALUES (:u, :c, 'k', :rp)"),
                {"u": str(user.id), "c": b"\x00\x00\x00", "rp": auth.get_settings().rp_id},
            )
        # Every one of these succeeds, so nothing is "failing" -- and the door is
        # still counted. The successes forgive themselves, so it never refuses.
        for _ in range(throttle.SIGN_IN.allowed + 2):
            assert self._attempt(anon_client) == 200

    def test_a_flood_of_challenges_is_bounded(self, anon_client):
        codes = [
            anon_client.post("/api/auth/login/begin", json={}).status_code
            for _ in range(throttle.CHALLENGE.allowed + 2)
        ]
        assert 429 in codes

    def test_the_server_holds_only_so_many_challenges(self, anon_client):
        """Anybody can ask for one without an account."""
        from app import auth

        auth._flows.clear()
        for n in range(auth.MAX_FLOWS + 20):
            auth._new_flow(b"c", "login", rp_id="example.ts.net")
            assert len(auth._flows) <= auth.MAX_FLOWS, n


class TestTheFirstRunCode:
    """The one moment a stranger reaching the server could make themselves its
    owner. Eight hex characters is plenty against a few tries, nothing against a
    million."""

    def _register(self, anon_client, code: str):
        return anon_client.post(
            "/api/auth/register/begin",
            json={"handle": "intruder", "display_name": "Intruder", "bootstrap_code": code},
        )

    @pytest.fixture
    def fresh_server(self, db, monkeypatch):
        """No users yet, so a first-run code exists."""
        from app import auth

        with db.begin() as conn:
            conn.execute(text("DELETE FROM users"))
        code = auth.ensure_bootstrap_code()
        assert code is not None
        yield code
        auth._bootstrap_code = None

    def test_wrong_codes_retire_it(self, anon_client, fresh_server):
        from app import auth

        for _ in range(auth.BOOTSTRAP_TRIES):
            assert self._register(anon_client, "DEADBEEF").status_code == 403
        # Even the real code no longer works: it was thrown away.
        assert self._register(anon_client, fresh_server).status_code == 403
        assert auth._bootstrap_code is None

    def test_the_right_code_still_works_after_a_fumble(self, anon_client, fresh_server):
        assert self._register(anon_client, "DEADBEEF").status_code == 403
        # 400 rather than 403: past the code, into the passkey ceremony.
        assert self._register(anon_client, fresh_server).status_code == 200

    def test_and_the_attempts_are_rationed_too(self, anon_client, fresh_server):
        codes = [
            self._register(anon_client, "DEADBEEF").status_code
            for _ in range(throttle.CODES.allowed + 2)
        ]
        assert 429 in codes


class TestOrdinaryUseIsNotRationed:
    def test_an_admin_can_invite_as_often_as_they_like(self, client, db, user):
        """Inviting is not guessing, and an admin inviting a household is not a
        flood."""
        for n in range(throttle.CODES.allowed + 5):
            r = client.post(
                "/api/people/invites",
                json={"handle": f"guest{n}", "display_name": f"Guest {n}"},
            )
            assert r.status_code in (200, 201), r.text

    def test_looking_up_invitations_is_bounded(self, anon_client):
        codes = [
            anon_client.get("/api/auth/invite/nonsense").status_code
            for _ in range(throttle.INVITES.allowed + 2)
        ]
        assert codes[0] == 404
        assert 429 in codes
