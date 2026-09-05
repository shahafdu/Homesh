"""Denon control, exercised against a mock receiver.

The real AVR-X1600H is not available to CI, so these speak to a socket server that
replays the exact frames the real one produced during discovery (see
ARCHITECTURE.md §5.6). That keeps the protocol handling honest without hardware.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app import denon

# ── Mock receivers ──────────────────────────────────────────────────────────


class FakeHeos:
    """Speaks the HEOS CLI dialect on an ephemeral port."""

    def __init__(self, players: list[dict] | None = None, fail: str | None = None):
        self.players = players if players is not None else [
            {
                "name": "Denon AVR-X1600H",
                "pid": -1765854565,
                "model": "Denon AVR-X1600H",
                "version": "3.139.173",
                "ip": "192.0.2.42",
                "network": "wired",
                "lineout": 0,
            }
        ]
        self.fail = fail
        self.received: list[str] = []
        self.server: asyncio.Server | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                cmd = line.decode().strip()
                self.received.append(cmd)

                # Real receivers emit unsolicited events; if the client naively read
                # the first line it would pick this up instead of its answer.
                writer.write(
                    json.dumps(
                        {"heos": {"command": "event/player_now_playing_progress",
                                  "message": "pid=1&cur_pos=1000"}}
                    ).encode() + b"\r\n"
                )

                name = cmd.replace("heos://", "").split("?")[0]
                if self.fail == name:
                    body = {"heos": {"command": name, "result": "fail",
                                     "message": "eid=2&text=Denied"}}
                elif name == "player/get_players":
                    body = {"heos": {"command": name, "result": "success", "message": ""},
                            "payload": self.players}
                else:
                    body = {"heos": {"command": name, "result": "success", "message": ""}}

                writer.write(json.dumps(body).encode() + b"\r\n")
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            return
        finally:
            writer.close()


class FakeAvr:
    """Speaks the port-23 dialect, replying with the status lines the real one sends."""

    REPLIES = {
        "PW?": ["PWON"],
        "ZM?": ["ZMON"],
        "Z2?": ["Z2OFF", "Z2NET", "Z259"],
        "SI?": ["SITV"],
        "MV?": ["MV43", "MVMAX 94"],
        "MU?": ["MUOFF"],
        "PWON": ["PWON"],
        "Z2ON": ["Z2ON"],
        "Z2OFF": ["Z2OFF"],
        "Z2NET": ["Z2NET"],
    }

    def __init__(self) -> None:
        self.received: list[str] = []
        self.server: asyncio.Server | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        buf = b""
        try:
            while True:
                chunk = await reader.read(256)
                if not chunk:
                    return
                buf += chunk
                while b"\r" in buf:
                    raw, buf = buf.split(b"\r", 1)
                    cmd = raw.decode().strip()
                    if not cmd:
                        continue
                    self.received.append(cmd)
                    replies = self.REPLIES.get(cmd)
                    if replies is None and cmd.startswith("Z2") and cmd[2:].isdigit():
                        replies = [cmd]        # volume echo
                    for r in replies or []:
                        writer.write(f"{r}\r".encode())
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            return
        finally:
            writer.close()


@pytest.fixture
async def heos():
    server = FakeHeos()
    port = await server.start()
    original = denon.HEOS_PORT
    denon.HEOS_PORT = port
    yield server
    denon.HEOS_PORT = original
    await server.stop()


@pytest.fixture
async def avr():
    server = FakeAvr()
    port = await server.start()
    original = denon.AVR_PORT
    denon.AVR_PORT = port
    yield server
    denon.AVR_PORT = original
    await server.stop()


# ── HEOS ────────────────────────────────────────────────────────────────────


class TestHeos:
    async def test_get_players(self, heos):
        players = await denon.get_players("127.0.0.1")
        assert len(players) == 1
        assert players[0].pid == -1765854565
        assert players[0].model == "Denon AVR-X1600H"

    async def test_single_player_is_the_documented_constraint(self, heos):
        """One player means one network stream; the zone design depends on this."""
        assert len(await denon.get_players("127.0.0.1")) == 1

    async def test_unsolicited_events_are_skipped(self, heos):
        """The mock emits an event before every reply, as the real receiver does."""
        players = await denon.get_players("127.0.0.1")
        assert players, "an event message was mistaken for the response"

    async def test_play_stream_sends_the_url(self, heos):
        await denon.play_stream("127.0.0.1", 123, "http://example/stream?t=abc")
        assert any("play_stream" in c and "url=http://example/stream" in c
                   for c in heos.received)

    async def test_volume_is_clamped(self, heos):
        await denon.set_player_volume("127.0.0.1", 1, 250)
        assert "level=100" in heos.received[-1]
        await denon.set_player_volume("127.0.0.1", 1, -10)
        assert "level=0" in heos.received[-1]

    async def test_failure_result_raises(self):
        server = FakeHeos(fail="player/get_players")
        port = await server.start()
        original, denon.HEOS_PORT = denon.HEOS_PORT, port
        try:
            with pytest.raises(denon.DenonError, match="failed"):
                await denon.get_players("127.0.0.1")
        finally:
            denon.HEOS_PORT = original
            await server.stop()

    async def test_unreachable_host_raises_clearly(self):
        original, denon.HEOS_PORT = denon.HEOS_PORT, 9
        denon.CONNECT_TIMEOUT = 0.5
        try:
            with pytest.raises(denon.DenonError, match="cannot reach HEOS"):
                await denon.get_players("127.0.0.1")
        finally:
            denon.HEOS_PORT = original
            denon.CONNECT_TIMEOUT = 5.0


# ── AVR control ─────────────────────────────────────────────────────────────


class TestAvr:
    async def test_query_state_matches_the_real_receiver(self, avr):
        state = await denon.query_state("127.0.0.1")
        assert state.power is True
        assert state.main_zone is True
        assert state.zone2 is False
        assert state.zone2_source == "NET"
        assert state.zone2_volume == 59
        assert state.main_volume == 43
        assert state.source == "TV"
        assert state.muted is False

    async def test_prepare_zone2_sends_the_orchestration_sequence(self, avr):
        await denon.prepare_zone2("127.0.0.1", volume=40)
        assert avr.received == ["PWON", "Z2ON", "Z2NET", "Z240"]

    async def test_prepare_zone2_without_volume_leaves_it_alone(self, avr):
        await denon.prepare_zone2("127.0.0.1")
        assert avr.received == ["PWON", "Z2ON", "Z2NET"]

    async def test_wake_only_powers_on(self, avr):
        await denon.wake("127.0.0.1")
        assert avr.received == ["PWON"]

    async def test_unreachable_avr_names_the_likely_cause(self):
        original, denon.AVR_PORT = denon.AVR_PORT, 9
        denon.CONNECT_TIMEOUT = 0.5
        try:
            with pytest.raises(denon.DenonError, match="Network Control"):
                await denon.query_state("127.0.0.1")
        finally:
            denon.AVR_PORT = original
            denon.CONNECT_TIMEOUT = 5.0


class TestStateParsing:
    """The wire format is ambiguous in places; these are the traps."""

    def test_mvmax_is_not_the_current_volume(self):
        state = denon.parse_avr_state(["MV43", "MVMAX 94"])
        assert state.main_volume == 43

    def test_zone2_source_is_not_read_as_a_volume(self):
        state = denon.parse_avr_state(["Z2NET"])
        assert state.zone2_source == "NET"
        assert state.zone2_volume is None

    def test_zone2_on_is_not_read_as_a_source(self):
        state = denon.parse_avr_state(["Z2ON"])
        assert state.zone2 is True
        assert state.zone2_source is None

    def test_half_step_volume(self):
        """Three digits means a half step: 435 is 43.5, not 435."""
        assert denon.parse_avr_state(["MV435"]).main_volume == 43

    def test_standby(self):
        assert denon.parse_avr_state(["PWSTANDBY"]).power is False

    def test_real_concatenated_response(self):
        """The receiver replies as one run of lines, exactly as captured."""
        lines = ["PWON", "ZMON", "SLPOFF", "Z2OFF", "Z2NET", "Z259",
                 "SVOFF", "SITV", "MV43", "MVMAX 94", "MUOFF"]
        state = denon.parse_avr_state(lines)
        assert (state.power, state.zone2, state.zone2_source, state.zone2_volume) == (
            True, False, "NET", 59,
        )

    def test_volume_encoding_is_two_digits(self):
        assert denon._encode_volume(5) == "05"
        assert denon._encode_volume(40) == "40"
        assert denon._encode_volume(500) == "98"  # clamped to the maximum
