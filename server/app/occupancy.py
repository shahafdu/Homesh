"""Who is using a zone, including when it is not us.

A receiver plays Spotify Connect, AirPlay and its own internet radio without this
application being involved at all. If the control tower only reported playback it
had started, it would show "ready" for a room that is audibly in use — and worse,
sending a track there would cut somebody off, because the AVR-X1600H has exactly
one network player (ARCHITECTURE.md §5.6).

So the tower asks the hardware rather than trusting its own records.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from . import denon
from .config import get_settings

log = logging.getLogger("homesh.occupancy")

# The tower polls every few seconds and the receiver accepts few concurrent
# connections, so answers are cached briefly. Long enough to spare the hardware,
# short enough that "someone just pressed play" surfaces quickly.
CACHE_TTL = 6.0

# Sources the receiver reports. Numbers come from the HEOS CLI specification.
SOURCE_NAMES = {
    1: "Pandora", 2: "Rhapsody", 3: "TuneIn", 4: "Spotify", 5: "Deezer",
    6: "Napster", 7: "iHeartRadio", 8: "SiriusXM", 9: "Soundcloud",
    10: "Tidal", 13: "Amazon Music", 1024: "a local source", 1025: "AirPlay",
    1026: "a network stream", 1027: "USB",
}


@dataclass
class Occupancy:
    """What a zone is doing, and whether we are the cause."""

    busy: bool
    ours: bool
    detail: str | None = None
    reachable: bool = True


_cache: dict[str, tuple[float, Occupancy]] = {}
_lock = asyncio.Lock()


def _describe(now_playing: dict) -> str:
    """A phrase a person can act on, rather than a source id."""
    source = SOURCE_NAMES.get(now_playing.get("source_id"))
    song = now_playing.get("song") or now_playing.get("station")

    if source and song:
        return f"{song} — via {source}"
    if source:
        return f"in use by {source}"
    if song:
        return str(song)
    return "playing something else"


# Inputs that mean the network player — anything else on the main zone is some
# other box: a television, a console, a turntable.
NETWORK_INPUTS = {"NET", "HEOS", "BT", "AUX"}


def _avr_busy(state: denon.AvrState, zone2: bool) -> str | None:
    """Whether the AVR shows this zone in use by something that is not us.

    The case that prompted this: a child watching television through the
    receiver. The HEOS player is idle, so asking only HEOS reports the living
    room as free — while it is audibly, visibly in use. The main zone being
    powered on with a non-network input is the evidence, and it is evidence the
    receiver was willing to give all along.
    """
    if zone2:
        if state.zone2 and state.zone2_source and state.zone2_source not in NETWORK_INPUTS:
            return f"in use ({state.zone2_source})"
        return None

    # Positive evidence only. A receiver that has not said it is on must not be
    # reported as occupied — a silent amplifier is the normal case, and guessing
    # busy would block playback in a free room.
    powered = state.power is True or state.main_zone is True
    if powered and state.source and state.source not in NETWORK_INPUTS:
        return f"in use ({state.source})"
    return None


async def receiver_occupancy(
    our_session_state: str | None, zone2: bool = False
) -> Occupancy:
    """Ask the receiver what it is doing.

    `our_session_state` is what we believe we started; when the receiver is busy
    and we did not cause it, something else is using the room.
    """
    settings = get_settings()
    host = settings.denon_host.strip()
    if not host:
        return Occupancy(busy=False, ours=False, reachable=False, detail="no receiver configured")

    key = f"{host}:{'z2' if zone2 else 'main'}"
    async with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            return cached[1]

    try:
        players = await denon.get_players(host)
        if not players:
            result = Occupancy(busy=False, ours=False, reachable=False,
                               detail="receiver reports no player")
        else:
            pid = players[0].pid
            state = await denon.get_play_state(host, pid)
            playing = state == "play"

            if not playing:
                # The network player being idle does not mean the room is. Ask
                # the amplifier itself: a television watched through it leaves
                # HEOS untouched while the main zone is plainly in use.
                try:
                    avr = await denon.query_state(host)
                    reason = _avr_busy(avr, zone2)
                except denon.DenonError:
                    reason = None
                result = (
                    Occupancy(busy=True, ours=False, detail=reason)
                    if reason
                    else Occupancy(busy=False, ours=False)
                )
            else:
                ours = our_session_state in ("playing", "buffering")
                detail = None
                if not ours:
                    # Only ask what is on when it is not ours; it is an extra
                    # round trip to hardware that answers slowly.
                    try:
                        detail = _describe(await denon.get_now_playing(host, pid))
                    except denon.DenonError:
                        detail = "playing something else"
                result = Occupancy(busy=True, ours=ours, detail=detail)
    except denon.DenonError as exc:
        # Unreachable is not the same as idle, and saying so is the point.
        log.debug("occupancy check failed: %s", exc)
        result = Occupancy(busy=False, ours=False, reachable=False, detail=str(exc))

    async with _lock:
        _cache[key] = (time.monotonic(), result)
    return result


def invalidate() -> None:
    """Drop the cache after we change something, so the next read is truthful."""
    _cache.clear()
