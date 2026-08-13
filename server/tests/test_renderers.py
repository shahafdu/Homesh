"""Renderer pairing and the command channel.

Pairing is the only place an unauthenticated caller touches the system, so most of
these are about what that caller cannot do.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.renderers import CODE_ALPHABET, CODE_LENGTH, _hash


def _begin(client, key="device-abc", name="Bedroom TV"):
    r = client.post("/api/renderers/pair/begin", json={"device_key": key, "device_name": name})
    assert r.status_code == 200, r.text
    return r.json()


class TestPairingCode:
    def test_code_avoids_ambiguous_characters(self):
        """These are read off a screen across a room and typed on a phone."""
        for bad in "O0I1S5":
            assert bad not in CODE_ALPHABET, f"{bad} is easy to misread"

    def test_begin_is_open_to_an_unauthenticated_device(self, anon_client, db):
        """A screen has no credential yet, so this cannot require one."""
        body = _begin(anon_client)
        assert len(body["code"]) == CODE_LENGTH
        assert body["poll_token"]
        assert body["expires_in"] > 0

    def test_begin_yields_nothing_but_a_code(self, anon_client, db):
        """Worth stating: an open endpoint must not hand out anything usable."""
        body = _begin(anon_client)
        assert set(body) == {"code", "poll_token", "expires_in"}

    def test_unclaimed_code_reports_waiting(self, anon_client, db):
        body = _begin(anon_client)
        status = anon_client.get(
            f"/api/renderers/pair/status?poll_token={body['poll_token']}"
        ).json()
        assert status["status"] == "waiting"
        assert "device_token" not in status

    def test_unknown_poll_token_404s(self, anon_client, db):
        assert anon_client.get("/api/renderers/pair/status?poll_token=nope").status_code == 404


class TestClaiming:
    def test_claim_requires_authentication(self, anon_client, db):
        body = _begin(anon_client)
        r = anon_client.post(
            "/api/renderers/pair/claim", json={"code": body["code"], "name": "Bedroom"}
        )
        assert r.status_code == 401

    def test_claim_then_collect(self, client, anon_client, db):
        body = _begin(anon_client)

        claimed = client.post(
            "/api/renderers/pair/claim", json={"code": body["code"], "name": "Bedroom"}
        )
        assert claimed.status_code == 200, claimed.text

        # The screen collects its credential on the next poll.
        status = anon_client.get(
            f"/api/renderers/pair/status?poll_token={body['poll_token']}"
        ).json()
        assert status["status"] == "paired"
        assert status["device_token"]
        assert status["renderer_id"] == claimed.json()["renderer_id"]

    def test_device_token_is_handed_over_exactly_once(self, client, anon_client, db):
        """A replayed poll must not fetch the credential a second time."""
        body = _begin(anon_client)
        client.post("/api/renderers/pair/claim", json={"code": body["code"], "name": "Bedroom"})

        first = anon_client.get(f"/api/renderers/pair/status?poll_token={body['poll_token']}")
        assert first.json()["status"] == "paired"

        second = anon_client.get(f"/api/renderers/pair/status?poll_token={body['poll_token']}")
        assert second.status_code in (404, 410), "the credential was collectable twice"

    def test_token_is_stored_only_as_a_hash(self, client, anon_client, db):
        body = _begin(anon_client)
        client.post("/api/renderers/pair/claim", json={"code": body["code"], "name": "Bedroom"})
        token = anon_client.get(
            f"/api/renderers/pair/status?poll_token={body['poll_token']}"
        ).json()["device_token"]

        with db.connect() as conn:
            stored = conn.execute(text("SELECT token_hash FROM renderers")).scalar_one()
        assert bytes(stored) == _hash(token)
        assert token.encode() not in bytes(stored)

    def test_unknown_code_404s(self, client, db):
        r = client.post("/api/renderers/pair/claim", json={"code": "ZZZZZZ", "name": "X"})
        assert r.status_code == 404

    def test_a_code_cannot_be_claimed_twice(self, client, anon_client, db):
        body = _begin(anon_client)
        client.post("/api/renderers/pair/claim", json={"code": body["code"], "name": "Bedroom"})
        again = client.post(
            "/api/renderers/pair/claim", json={"code": body["code"], "name": "Someone else"}
        )
        assert again.status_code == 409

    def test_expired_code_is_refused(self, client, anon_client, db):
        body = _begin(anon_client)
        with db.begin() as conn:
            conn.execute(text("UPDATE pairing_codes SET expires_at = now() - interval '1 hour'"))

        r = client.post("/api/renderers/pair/claim", json={"code": body["code"], "name": "X"})
        assert r.status_code == 410

    def test_code_is_case_insensitive(self, client, anon_client, db):
        """It is typed by hand off a screen; case is not a meaningful distinction."""
        body = _begin(anon_client)
        r = client.post(
            "/api/renderers/pair/claim", json={"code": body["code"].lower(), "name": "Bedroom"}
        )
        assert r.status_code == 200


class TestPairingCreatesAZone:
    def test_a_paired_screen_becomes_a_zone(self, client, anon_client, db):
        """A screen is only useful as a zone, so pairing makes one."""
        body = _begin(anon_client)
        client.post("/api/renderers/pair/claim", json={"code": body["code"], "name": "Bedroom"})

        zones = client.get("/api/zones").json()
        assert [z["name"] for z in zones] == ["Bedroom"]
        assert zones[0]["renderer"]["kind"] == "tvapp"

    def test_repairing_updates_rather_than_duplicating(self, client, anon_client, db):
        """The device key is the identity, so a re-pair is the same screen."""
        first = _begin(anon_client, key="same-device")
        client.post("/api/renderers/pair/claim", json={"code": first["code"], "name": "Bedroom"})

        second = _begin(anon_client, key="same-device")
        client.post("/api/renderers/pair/claim", json={"code": second["code"], "name": "Bedroom"})

        with db.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM renderers WHERE device_key = 'same-device'")
            ).scalar_one()
        assert count == 1

    def test_repairing_invalidates_the_previous_credential(self, client, anon_client, db):
        """Re-pairing should retire the old token, not leave two working."""
        first = _begin(anon_client, key="same-device")
        client.post("/api/renderers/pair/claim", json={"code": first["code"], "name": "Bedroom"})
        old = anon_client.get(
            f"/api/renderers/pair/status?poll_token={first['poll_token']}"
        ).json()["device_token"]

        second = _begin(anon_client, key="same-device")
        client.post("/api/renderers/pair/claim", json={"code": second["code"], "name": "Bedroom"})
        new = anon_client.get(
            f"/api/renderers/pair/status?poll_token={second['poll_token']}"
        ).json()["device_token"]

        assert old != new
        with db.connect() as conn:
            stored = conn.execute(text("SELECT token_hash FROM renderers")).scalar_one()
        assert bytes(stored) == _hash(new)


class TestCommandChannel:
    def test_socket_refuses_an_unknown_token(self, client):
        """An invalid device credential must never reach application code."""
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect("/api/renderers/ws?token=not-a-real-token"):
                pass
        assert caught.value.code == 1008

    def test_paired_screen_can_connect_and_is_seen_as_ready(self, client, anon_client, db):
        body = _begin(anon_client)
        client.post("/api/renderers/pair/claim", json={"code": body["code"], "name": "Bedroom"})
        token = anon_client.get(
            f"/api/renderers/pair/status?poll_token={body['poll_token']}"
        ).json()["device_token"]

        with client.websocket_connect(f"/api/renderers/ws?token={token}"):
            listed = client.get("/api/renderers").json()
            assert listed[0]["connected"] is True
            assert listed[0]["state"] == "ready"

        # Once the socket drops, the screen is no longer claimed to be reachable.
        assert client.get("/api/renderers").json()[0]["connected"] is False

    def test_state_reports_are_persisted_to_the_session(self, client, anon_client, db):
        body = _begin(anon_client)
        client.post("/api/renderers/pair/claim", json={"code": body["code"], "name": "Bedroom"})
        token = anon_client.get(
            f"/api/renderers/pair/status?poll_token={body['poll_token']}"
        ).json()["device_token"]

        zone_id = client.get("/api/zones").json()[0]["id"]
        with db.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO play_sessions (zone_id, queue, cursor, state)
                    VALUES (:z, '[]'::jsonb, 0, 'playing')
                    """
                ),
                {"z": zone_id},
            )

        with client.websocket_connect(f"/api/renderers/ws?token={token}") as ws:
            ws.send_json({"type": "state", "state": "playing", "position_ms": 42000})
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"   # round trip proves it was read

        with db.connect() as conn:
            position = conn.execute(
                text("SELECT position_ms FROM play_sessions WHERE zone_id = :z"), {"z": zone_id}
            ).scalar_one()
        assert position == 42000

    def test_listing_requires_authentication(self, anon_client):
        assert anon_client.get("/api/renderers").status_code == 401
