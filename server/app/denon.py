"""Denon / Marantz receiver control.

The AVR-X1600H answers on two entirely separate protocols, and conflating them is
the usual mistake:

  * **HEOS CLI, TCP 1255** — ASCII commands, JSON responses. Streams media.
  * **AVR control, TCP 23** — ASCII commands, CR-terminated. Power, volume, zones.

Both are documented by Denon. Neither is HTTP, and the AVR-side protocol is *not*
HEOS CLI even on a HEOS-capable model.

Measured facts about this receiver that shape the code (ARCHITECTURE.md §5.6):

  * It exposes exactly **one** HEOS player, so it cannot run two different network
    streams at once.
  * ZONE2 cannot take HDMI, coaxial or optical — network or analog only.
  * ZONE2's source is already `NET`, so it never needs switching.

Connections are opened per operation rather than held. These receivers accept only
a small number of concurrent control connections, and a long-lived socket that dies
quietly is worse than a short one that reconnects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("homesh.denon")

HEOS_PORT = 1255
AVR_PORT = 23

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
# Denon's own device type. Matching this rather than upnp:rootdevice avoids waking
# every other UPnP device on the network.
SSDP_TARGET = "urn:schemas-denon-com:device:ACT-Denon:1"

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 4.0


class DenonError(Exception):
    pass


@dataclass(frozen=True)
class Discovered:
    """A receiver found on the LAN.

    `usn` is the stable identity — a UUID that survives reboots and DHCP changes.
    The address is a cache, never configuration (ARCHITECTURE.md §5.7).
    """

    usn: str
    address: str
    server: str = ""
    location: str = ""


@dataclass
class Player:
    """A HEOS player. On this model there is exactly one, and it is the whole box."""

    pid: int
    name: str
    model: str = ""
    version: str = ""
    ip: str = ""


@dataclass
class AvrState:
    power: bool | None = None
    main_zone: bool | None = None
    zone2: bool | None = None
    zone2_source: str | None = None
    zone2_volume: int | None = None
    main_volume: int | None = None
    muted: bool | None = None
    source: str | None = None
    raw: list[str] = field(default_factory=list)


# ── Discovery ───────────────────────────────────────────────────────────────


class _SsdpProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.responses: list[tuple[bytes, tuple[str, int]]] = []

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.responses.append((data, addr))


async def discover(timeout: float = 4.0) -> list[Discovered]:
    """Find receivers by SSDP. Returns them keyed by stable identity."""
    request = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 3\r\n"
        f"ST: {SSDP_TARGET}\r\n\r\n"
    ).encode()

    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        _SsdpProtocol, local_addr=("0.0.0.0", 0), allow_broadcast=True  # noqa: S104
    )

    try:
        # UDP is lossy; two sends materially improve the hit rate.
        transport.sendto(request, (SSDP_ADDR, SSDP_PORT))
        await asyncio.sleep(0.2)
        transport.sendto(request, (SSDP_ADDR, SSDP_PORT))
        await asyncio.sleep(timeout)
    finally:
        transport.close()

    found: dict[str, Discovered] = {}
    for data, addr in protocol.responses:
        text = data.decode("ascii", errors="replace")
        if not re.search(r"denon|marantz|heos|ACT-Denon", text, re.I):
            continue

        def header(name: str, _text: str = text) -> str:
            # _text is bound at definition time: closing over the loop variable
            # would make every header read the last response.
            m = re.search(rf"^{name}:\s*(.+)$", _text, re.I | re.M)
            return m.group(1).strip() if m else ""

        usn = header("USN") or addr[0]
        found.setdefault(
            usn,
            Discovered(usn=usn, address=addr[0], server=header("SERVER"),
                       location=header("LOCATION")),
        )

    return list(found.values())


# ── HEOS CLI (port 1255) ────────────────────────────────────────────────────


async def _heos_command(host: str, command: str, expect: str) -> dict:
    """Send one HEOS command and return the first response matching `expect`.

    The receiver emits unsolicited event messages on the same socket, so a naive
    "read one line" would frequently return somebody else's traffic.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, HEOS_PORT), CONNECT_TIMEOUT
        )
    except (TimeoutError, OSError) as exc:
        raise DenonError(f"cannot reach HEOS on {host}:{HEOS_PORT}: {exc}") from exc

    try:
        writer.write(f"{command}\r\n".encode())
        await writer.drain()

        deadline = asyncio.get_running_loop().time() + READ_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise DenonError(f"timed out waiting for {expect}")
            line = await asyncio.wait_for(reader.readline(), remaining)
            if not line:
                raise DenonError("connection closed by receiver")

            try:
                payload = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue  # keep-alives and partial frames

            if payload.get("heos", {}).get("command") == expect:
                if payload["heos"].get("result") == "fail":
                    raise DenonError(f"{expect} failed: {payload['heos'].get('message')}")
                return payload
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def get_players(host: str) -> list[Player]:
    payload = await _heos_command(host, "heos://player/get_players", "player/get_players")
    return [
        Player(
            pid=int(p["pid"]),
            name=p.get("name", ""),
            model=p.get("model", ""),
            version=p.get("version", ""),
            ip=p.get("ip", ""),
        )
        for p in payload.get("payload", [])
    ]


async def play_stream(host: str, pid: int, url: str) -> None:
    """Point the receiver at a URL and let it pull the audio itself.

    The receiver fetches this directly, without our session, which is why media
    URLs carry their authorisation in the URL (ARCHITECTURE.md §6).
    """
    await _heos_command(
        host,
        f"heos://browse/play_stream?pid={pid}&url={url}",
        "browse/play_stream",
    )


async def set_player_volume(host: str, pid: int, level: int) -> None:
    level = max(0, min(100, level))
    await _heos_command(
        host, f"heos://player/set_volume?pid={pid}&level={level}", "player/set_volume"
    )


async def player_stop(host: str, pid: int) -> None:
    await _heos_command(
        host, f"heos://player/set_play_state?pid={pid}&state=stop", "player/set_play_state"
    )


# ── AVR control (port 23) ───────────────────────────────────────────────────


async def avr_command(host: str, commands: list[str], read_for: float = 1.5) -> list[str]:
    """Send AVR commands and collect whatever the receiver reports back.

    Responses are unsolicited status lines rather than replies, so we drain for a
    short window instead of pairing request to response. The receiver also drops
    commands sent back-to-back, hence the pause between them.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, AVR_PORT), CONNECT_TIMEOUT
        )
    except (TimeoutError, OSError) as exc:
        raise DenonError(
            f"cannot reach AVR control on {host}:{AVR_PORT} — is Network Control set "
            f"to 'Always On'? ({exc})"
        ) from exc

    try:
        for cmd in commands:
            writer.write(f"{cmd}\r".encode("ascii"))
            await writer.drain()
            await asyncio.sleep(0.3)

        chunks: list[bytes] = []
        deadline = asyncio.get_running_loop().time() + read_for
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(reader.read(1024), remaining)
            except TimeoutError:
                break
            if not chunk:
                break
            chunks.append(chunk)

        text = b"".join(chunks).decode("ascii", errors="replace")
        return [line for line in re.split(r"[\r\n]+", text) if line.strip()]
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


def parse_avr_state(lines: list[str]) -> AvrState:
    """Interpret AVR status lines.

    Order matters: ZM/Z2 must be tested before the bare MV/PW prefixes, and Z2NET
    (a source) must not be mistaken for a Z2 volume.
    """
    state = AvrState(raw=list(lines))
    for line in lines:
        line = line.strip()
        if line == "PWON":
            state.power = True
        elif line in ("PWSTANDBY", "PWOFF"):
            state.power = False
        elif line == "ZMON":
            state.main_zone = True
        elif line == "ZMOFF":
            state.main_zone = False
        elif line == "Z2ON":
            state.zone2 = True
        elif line == "Z2OFF":
            state.zone2 = False
        elif line.startswith("Z2"):
            rest = line[2:]
            if rest.isdigit():
                state.zone2_volume = _decode_volume(rest)
            elif rest:
                state.zone2_source = rest
        elif line.startswith("MVMAX"):
            continue  # a limit, not the current level
        elif line.startswith("MV") and line[2:].isdigit():
            state.main_volume = _decode_volume(line[2:])
        elif line == "MUON":
            state.muted = True
        elif line == "MUOFF":
            state.muted = False
        elif line.startswith("SI"):
            state.source = line[2:]
    return state


def _decode_volume(digits: str) -> int:
    """Denon volumes are 2 digits, or 3 where the last is a half-step (435 = 43.5)."""
    return int(digits[:2]) if len(digits) == 3 else int(digits)


def _encode_volume(level: int) -> str:
    return f"{max(0, min(98, level)):02d}"


async def query_state(host: str) -> AvrState:
    lines = await avr_command(host, ["PW?", "ZM?", "Z2?", "SI?", "MV?", "MU?"])
    return parse_avr_state(lines)


async def wake(host: str) -> None:
    """Bring the receiver out of standby.

    Only works when Network Control is set to "Always On"; otherwise port 23 is
    closed while the receiver sleeps.
    """
    await avr_command(host, ["PWON"], read_for=1.0)


async def prepare_zone2(host: str, volume: int | None = None) -> list[str]:
    """Make the balcony ready to receive audio.

    Power on, enable ZONE2, select the network source and optionally set volume.
    ZONE2's source is already NET on this receiver, but selecting it explicitly
    costs nothing and makes the sequence correct on a receiver that has drifted.
    """
    commands = ["PWON", "Z2ON", "Z2NET"]
    if volume is not None:
        commands.append(f"Z2{_encode_volume(volume)}")
    return await avr_command(host, commands, read_for=2.0)


async def zone2_off(host: str) -> None:
    await avr_command(host, ["Z2OFF"], read_for=1.0)


async def set_zone2_volume(host: str, level: int) -> None:
    await avr_command(host, [f"Z2{_encode_volume(level)}"], read_for=1.0)
