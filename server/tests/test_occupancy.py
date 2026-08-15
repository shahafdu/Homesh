"""Reading what the receiver is actually doing."""

from __future__ import annotations

import pytest

from app import denon, occupancy
from app.occupancy import Occupancy, _describe


class TestDescribe:
    """The phrase has to be something a person can act on."""

    def test_names_the_source_and_the_song(self):
        assert _describe({"source_id": 4, "song": "Bohemian Rhapsody"}) == (
            "Bohemian Rhapsody — via Spotify"
        )

    def test_source_alone(self):
        assert _describe({"source_id": 1025}) == "in use by AirPlay"

    def test_station_stands_in_for_a_song(self):
        assert _describe({"source_id": 3, "station": "BBC Radio 6"}) == "BBC Radio 6 — via TuneIn"

    def test_unknown_source_still_says_something_useful(self):
        assert _describe({}) == "playing something else"


class TestOccupancy:
    @pytest.fixture(autouse=True)
    def _clear(self):
        occupancy.invalidate()
        yield
        occupancy.invalidate()

    async def test_no_receiver_configured_is_reported_as_unreachable(self, monkeypatch):
        monkeypatch.setenv("DENON_HOST", "")
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            seen = await occupancy.receiver_occupancy(None)
            assert seen.reachable is False
            assert seen.busy is False
        finally:
            get_settings.cache_clear()

    async def test_unreachable_receiver_is_not_reported_as_free(self, monkeypatch):
        """Silence from the hardware must not be read as an empty room."""
        monkeypatch.setenv("DENON_HOST", "127.0.0.1")
        from app.config import get_settings

        get_settings.cache_clear()
        original, denon.HEOS_PORT = denon.HEOS_PORT, 9
        denon.CONNECT_TIMEOUT = 0.4
        try:
            seen = await occupancy.receiver_occupancy(None)
            assert seen.reachable is False
        finally:
            denon.HEOS_PORT = original
            denon.CONNECT_TIMEOUT = 5.0
            get_settings.cache_clear()

    def test_cache_is_cleared_after_we_change_something(self):
        occupancy._cache["host"] = (0.0, Occupancy(busy=True, ours=False))
        occupancy.invalidate()
        assert occupancy._cache == {}
