"""Zones and server-owned sessions.

The receiver is mocked (see test_denon.py for why), so these cover the part that
must be right regardless of hardware: that the session is the server's own record
of intent, and survives the hardware failing.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager

import pytest
from sqlalchemy import text

from app import denon
from app.scanner import scan_source
from app.sources.local import LocalConnector
from tests.test_denon import FakeAvr, FakeHeos

BALCONY_PREROLL = [{"avr": "PWON"}, {"avr": "Z2ON"}, {"avr": "Z2NET"}]


@pytest.fixture
def scanned(source, monkeypatch):
    sid, prefix, root = source
    name = prefix.rsplit("/", 1)[-1]
    monkeypatch.setenv("MEDIA_ROOTS", f"{name}={root}")
    monkeypatch.setenv("DENON_HOST", "127.0.0.1")
    monkeypatch.setenv("LAN_BASE_URL", "http://192.0.2.10:8080")

    from app.config import get_settings

    get_settings.cache_clear()
    scan_source(sid, LocalConnector(root))
    yield sid, prefix, root
    get_settings.cache_clear()


class _ServerThread:
    """Runs an asyncio mock server in its own loop.

    These tests are synchronous (TestClient) while the fakes are asyncio servers,
    and the application calls them from a third loop again. Giving each fake its
    own thread and loop is what keeps it listening for the whole test.
    """

    def __init__(self, server):
        self.server = server
        self.loop = asyncio.new_event_loop()
        self.port: int | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.port = self.loop.run_until_complete(self.server.start())
        self.loop.run_forever()

    def start(self) -> int:
        self.thread.start()
        deadline = time.time() + 5
        while self.port is None and time.time() < deadline:
            time.sleep(0.01)
        if self.port is None:
            raise RuntimeError("mock server failed to start")
        return self.port

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)


@pytest.fixture
def receiver():
    """A mock Denon answering on both of its protocols."""
    heos, avr = FakeHeos(), FakeAvr()
    heos_thread, avr_thread = _ServerThread(heos), _ServerThread(avr)

    original = denon.HEOS_PORT, denon.AVR_PORT
    denon.HEOS_PORT = heos_thread.start()
    denon.AVR_PORT = avr_thread.start()

    yield heos, avr

    denon.HEOS_PORT, denon.AVR_PORT = original
    heos_thread.stop()
    avr_thread.stop()


@contextmanager
def orphan_zone(db):
    """A zone with no renderer bound, to exercise the refusal path."""
    with db.begin() as conn:
        zone_id = conn.execute(
            text("INSERT INTO zones (name) VALUES ('Orphan') RETURNING id")
        ).scalar_one()
    yield zone_id
    with db.begin() as conn:
        conn.execute(text("DELETE FROM zones WHERE id = :z"), {"z": str(zone_id)})


def _make_zone(client, name="Balcony", preroll=None):
    r = client.post(
        "/api/zones",
        json={
            "name": name,
            "renderer_kind": "heos",
            "device_key": f"uuid:test::{name}",
            "preroll": BALCONY_PREROLL if preroll is None else preroll,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _items(db, limit=3):
    with db.connect() as conn:
        return [
            str(r[0])
            for r in conn.execute(
                text(
                    """
                    SELECT i.id FROM items i JOIN replicas r ON r.item_id = i.id
                    WHERE i.kind = 'audio' ORDER BY r.filename LIMIT :n
                    """
                ),
                {"n": limit},
            ).all()
        ]


class TestZoneManagement:
    def test_create_and_list(self, client, db, scanned):
        _make_zone(client)
        zones = client.get("/api/zones").json()
        assert [z["name"] for z in zones] == ["Balcony"]
        assert zones[0]["renderer"]["kind"] == "heos"
        assert zones[0]["session"] is None

    def test_duplicate_name_rejected(self, client, db, scanned):
        _make_zone(client)
        r = client.post(
            "/api/zones",
            json={"name": "Balcony", "renderer_kind": "heos", "device_key": "uuid:other"},
        )
        assert r.status_code == 409

    def test_creation_is_admin_only(self, client, db, scanned, user, monkeypatch):
        from app.main import app
        from app.security import CurrentUser, require_user

        plain = CurrentUser(id=user.id, handle="x", display_name="X", is_admin=False)
        app.dependency_overrides[require_user] = lambda: plain
        try:
            r = client.post(
                "/api/zones",
                json={"name": "Kitchen", "renderer_kind": "heos", "device_key": "uuid:k"},
            )
            assert r.status_code == 403
        finally:
            app.dependency_overrides[require_user] = lambda: user

    def test_listing_requires_authentication(self, anon_client):
        assert anon_client.get("/api/zones").status_code == 401


class TestPlayback:
    def test_play_orchestrates_then_pushes(self, client, db, scanned, receiver):
        heos, avr = receiver
        zone_id = _make_zone(client)
        items = _items(db)

        r = client.post(f"/api/zones/{zone_id}/play", json={"item_ids": items})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "playing"

        # Power and zone selection must happen before audio is pushed, or the
        # receiver drops the stream on a sleeping zone.
        assert avr.received == ["PWON", "Z2ON", "Z2NET"]
        assert any("play_stream" in c for c in heos.received)

    def test_pushed_url_is_lan_reachable(self, client, db, scanned, receiver):
        """localhost would mean nothing to a receiver across the room."""
        heos, _avr = receiver
        zone_id = _make_zone(client)

        client.post(f"/api/zones/{zone_id}/play", json={"item_ids": _items(db)})
        pushed = next(c for c in heos.received if "play_stream" in c)
        assert "http://192.0.2.10:8080" in pushed
        assert "localhost" not in pushed

    def test_pushed_url_carries_a_token(self, client, db, scanned, receiver):
        heos, _avr = receiver
        zone_id = _make_zone(client)
        client.post(f"/api/zones/{zone_id}/play", json={"item_ids": _items(db)})

        pushed = next(c for c in heos.received if "play_stream" in c)
        assert "?t=" in pushed, "the receiver has no session, so the URL must carry auth"

    def test_session_records_the_whole_queue(self, client, db, scanned, receiver):
        zone_id = _make_zone(client)
        items = _items(db)
        client.post(f"/api/zones/{zone_id}/play", json={"item_ids": items, "start_index": 1})

        zone = client.get("/api/zones").json()[0]
        assert zone["session"]["queue_length"] == len(items)
        assert zone["session"]["cursor"] == 1
        assert zone["session"]["current_item"] == items[1]
        assert zone["session"]["state"] == "playing"

    def test_start_index_past_the_end_is_clamped(self, client, db, scanned, receiver):
        zone_id = _make_zone(client)
        items = _items(db)
        r = client.post(
            f"/api/zones/{zone_id}/play", json={"item_ids": items, "start_index": 99}
        )
        assert r.status_code == 200
        assert client.get("/api/zones").json()[0]["session"]["cursor"] == len(items) - 1

    def test_stop_clears_the_session(self, client, db, scanned, receiver):
        zone_id = _make_zone(client)
        client.post(f"/api/zones/{zone_id}/play", json={"item_ids": _items(db)})

        assert client.post(f"/api/zones/{zone_id}/stop").json()["state"] == "idle"
        assert client.get("/api/zones").json()[0]["session"]["state"] == "idle"

    def test_zone_without_renderer_refuses(self, client, db, scanned):
        with orphan_zone(db) as zone_id:
            r = client.post(f"/api/zones/{zone_id}/play", json={"item_ids": _items(db)})
            assert r.status_code == 409


class TestHardwareFailure:
    """The session is the server's record of intent, so it must outlive the hardware."""

    def test_unreachable_receiver_reports_502(self, client, db, scanned):
        zone_id = _make_zone(client)
        original, denon.AVR_PORT = denon.AVR_PORT, 9
        denon.CONNECT_TIMEOUT = 0.5
        try:
            r = client.post(f"/api/zones/{zone_id}/play", json={"item_ids": _items(db)})
            assert r.status_code == 502
        finally:
            denon.AVR_PORT = original
            denon.CONNECT_TIMEOUT = 5.0

    def test_failed_play_does_not_leave_a_false_playing_state(self, client, db, scanned):
        zone_id = _make_zone(client)
        original, denon.AVR_PORT = denon.AVR_PORT, 9
        denon.CONNECT_TIMEOUT = 0.5
        try:
            client.post(f"/api/zones/{zone_id}/play", json={"item_ids": _items(db)})
        finally:
            denon.AVR_PORT = original
            denon.CONNECT_TIMEOUT = 5.0

        session = client.get("/api/zones").json()[0]["session"]
        assert session["state"] == "idle", "the tower would have claimed it was playing"

    def test_stop_succeeds_even_when_the_receiver_is_gone(self, client, db, scanned, receiver):
        """The user asked for silence; refusing would leave the tower lying."""
        zone_id = _make_zone(client)
        client.post(f"/api/zones/{zone_id}/play", json={"item_ids": _items(db)})

        original, denon.AVR_PORT = denon.AVR_PORT, 9
        heos_original, denon.HEOS_PORT = denon.HEOS_PORT, 9
        denon.CONNECT_TIMEOUT = 0.5
        try:
            assert client.post(f"/api/zones/{zone_id}/stop").status_code == 200
        finally:
            denon.AVR_PORT, denon.HEOS_PORT = original, heos_original
            denon.CONNECT_TIMEOUT = 5.0

        assert client.get("/api/zones").json()[0]["session"]["state"] == "idle"

    def test_missing_lan_base_url_is_explained(self, client, db, scanned, receiver, monkeypatch):
        monkeypatch.setenv("LAN_BASE_URL", "")
        from app.config import get_settings

        get_settings.cache_clear()
        zone_id = _make_zone(client)
        r = client.post(f"/api/zones/{zone_id}/play", json={"item_ids": _items(db)})
        assert r.status_code == 502
        assert "LAN_BASE_URL" in r.json()["detail"]


class TestVolume:
    def test_zone2_volume_uses_the_avr_not_heos(self, client, db, scanned, receiver):
        """HEOS sets the player level, which is the main zone — wrong room."""
        _heos, avr = receiver
        zone_id = _make_zone(client)
        avr.received.clear()

        assert client.post(f"/api/zones/{zone_id}/volume", json={"level": 40}).status_code == 200
        assert avr.received == ["Z240"]

    def test_volume_is_recorded_on_the_session(self, client, db, scanned, receiver):
        zone_id = _make_zone(client)
        client.post(f"/api/zones/{zone_id}/play", json={"item_ids": _items(db)})
        client.post(f"/api/zones/{zone_id}/volume", json={"level": 35})
        assert client.get("/api/zones").json()[0]["session"]["volume"] == 35

    def test_out_of_range_rejected(self, client, db, scanned, receiver):
        zone_id = _make_zone(client)
        assert client.post(f"/api/zones/{zone_id}/volume", json={"level": 150}).status_code == 422


class TestDeviceState:
    def test_reports_what_the_hardware_says(self, client, db, scanned, receiver):
        zone_id = _make_zone(client)
        state = client.get(f"/api/zones/{zone_id}/device").json()
        assert state["reachable"] is True
        assert state["zone2_source"] == "NET"
        assert state["main_volume"] == 43

    def test_unreachable_is_reported_not_raised(self, client, db, scanned):
        zone_id = _make_zone(client)
        original, denon.AVR_PORT = denon.AVR_PORT, 9
        denon.CONNECT_TIMEOUT = 0.5
        try:
            state = client.get(f"/api/zones/{zone_id}/device").json()
            assert state["reachable"] is False
            assert "Network Control" in state["reason"]
        finally:
            denon.AVR_PORT = original
            denon.CONNECT_TIMEOUT = 5.0


class TestExternalUse:
    """A receiver plays Spotify and AirPlay without us.

    Reporting only our own playback would show "ready" for a room that is audibly
    in use — and sending a track there would cut somebody off, because this
    receiver has exactly one network player.
    """

    def test_listing_reports_a_room_used_by_something_else(
        self, client, db, scanned, receiver, monkeypatch
    ):
        _make_zone(client)

        async def busy_elsewhere(_state):
            from app.occupancy import Occupancy

            return Occupancy(busy=True, ours=False, detail="Bohemian Rhapsody — via Spotify")

        monkeypatch.setattr("app.occupancy.receiver_occupancy", busy_elsewhere)

        zone = client.get("/api/zones").json()[0]
        assert zone["external"]["busy"] is True
        assert "Spotify" in zone["external"]["detail"]

    def test_our_own_playback_is_not_reported_as_external(
        self, client, db, scanned, receiver, monkeypatch
    ):
        _make_zone(client)

        async def ours(_state):
            from app.occupancy import Occupancy

            return Occupancy(busy=True, ours=True)

        monkeypatch.setattr("app.occupancy.receiver_occupancy", ours)
        assert client.get("/api/zones").json()[0]["external"] is None

    def test_play_refuses_to_interrupt_without_being_asked(
        self, client, db, scanned, receiver, monkeypatch
    ):
        zone_id = _make_zone(client)

        async def busy_elsewhere(_state):
            from app.occupancy import Occupancy

            return Occupancy(busy=True, ours=False, detail="Bohemian Rhapsody — via Spotify")

        monkeypatch.setattr("app.occupancy.receiver_occupancy", busy_elsewhere)

        r = client.post(f"/api/zones/{zone_id}/play", json={"item_ids": _items(db)})
        assert r.status_code == 409
        assert "Spotify" in r.json()["detail"]
        assert "stop it" in r.json()["detail"]

    def test_take_over_proceeds_when_asked(self, client, db, scanned, receiver, monkeypatch):
        """Interrupting is allowed — it just has to be a decision."""
        zone_id = _make_zone(client)

        async def busy_elsewhere(_state):
            from app.occupancy import Occupancy

            return Occupancy(busy=True, ours=False, detail="something else")

        monkeypatch.setattr("app.occupancy.receiver_occupancy", busy_elsewhere)

        r = client.post(
            f"/api/zones/{zone_id}/play",
            json={"item_ids": _items(db), "take_over": True},
        )
        assert r.status_code == 200
        assert r.json()["state"] == "playing"

    def test_an_unreachable_receiver_is_not_reported_as_free(
        self, client, db, scanned, receiver, monkeypatch
    ):
        """Unreachable and idle are different things, and only one is actionable."""
        _make_zone(client)

        async def unreachable(_state):
            from app.occupancy import Occupancy

            return Occupancy(busy=False, ours=False, reachable=False, detail="cannot reach")

        monkeypatch.setattr("app.occupancy.receiver_occupancy", unreachable)

        zone = client.get("/api/zones").json()[0]
        assert zone["external"]["unreachable"] is True

    def test_a_screen_is_not_probed_for_external_use(self, client, db, scanned, monkeypatch):
        """Only the receiver can be driven by something else behind our back."""
        called = False

        async def tracker(_state):
            nonlocal called
            called = True
            from app.occupancy import Occupancy

            return Occupancy(busy=False, ours=False)

        monkeypatch.setattr("app.occupancy.receiver_occupancy", tracker)

        client.post(
            "/api/zones",
            json={"name": "Screen", "renderer_kind": "tvapp", "device_key": "uuid:screen"},
        )
        client.get("/api/zones")
        assert called is False, "a screen was probed as though it were a receiver"
