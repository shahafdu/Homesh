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


class TestWhichZoneHearsIt:
    """One HEOS player, two zones — so "HEOS is playing" names no room.

    The receiver has a single network player. Asking it alone reported every
    zone busy whenever anything streamed: Spotify in the living room marked the
    balcony occupied while the balcony was switched off. The zone's own input is
    what tells them apart.
    """

    @staticmethod
    def _state(**kwargs):
        from app.denon import AvrState

        return AvrState(**kwargs)

    def test_a_zone_switched_off_is_not_hearing_it(self):
        """Measured against the real receiver: main on NET, zone2 off."""
        from app.occupancy import _hears_network

        state = self._state(power=True, main_zone=True, source="NET",
                            zone2=False, zone2_source="NET")
        assert _hears_network(state, zone2=False) is True
        assert _hears_network(state, zone2=False) != _hears_network(state, zone2=True)
        assert _hears_network(state, zone2=True) is False

    def test_a_zone_on_the_television_is_not_hearing_it(self):
        from app.occupancy import _hears_network

        state = self._state(power=True, main_zone=True, source="TV",
                            zone2=True, zone2_source="NET")
        assert _hears_network(state, zone2=False) is False
        assert _hears_network(state, zone2=True) is True

    def test_both_can_hear_it_when_both_are_on_the_network(self):
        """Not a contradiction: ZONE2 can follow the main zone's source."""
        from app.occupancy import _hears_network

        state = self._state(power=True, main_zone=True, source="NET",
                            zone2=True, zone2_source="NET")
        assert _hears_network(state, zone2=False) is True
        assert _hears_network(state, zone2=True) is True

    def test_an_unknown_source_counts_as_hearing_it(self):
        """A false "busy" interrupts nobody; a false "free" talks over someone."""
        from app.occupancy import _hears_network

        state = self._state(power=True, main_zone=True, source=None, zone2=True)
        assert _hears_network(state, zone2=False) is True
        assert _hears_network(state, zone2=True) is True
