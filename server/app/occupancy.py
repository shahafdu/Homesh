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

# How stale an answer may be before it is refreshed. The refresh happens on a
# timer rather than inside a request: asking the receiver takes seconds — several
# round trips, each with a read window, because its status arrives as unsolicited
# lines rather than replies — and the control tower asks every four seconds.
# Done inline that made opening the rooms screen a five-to-ten second wait, and
# occasionally a failed fetch when the browser gave up first.
CACHE_TTL = 6.0

# How long an answer may be served after it stopped being refreshed, before the
# tower is told it does not know. Being briefly out of date is better than being
# slow; being permanently out of date without saying so is not.
STALE_AFTER = 60.0

# Sources the receiver reports. Numbers come from the HEOS CLI specification.
SOURCE_NAMES = {
    1: "Pandora", 2: "Rhapsody", 3: "TuneIn", 4: "Spotify", 5: "Deezer",
    6: "Napster", 7: "iHeartRadio", 8: "SiriusXM", 9: "Soundcloud",
    10: "Tidal", 13: "Amazon Music", 1024: "a local source", 1025: "AirPlay",
    1026: "a network stream", 1027: "USB",
}


@dataclass
class Occupancy:
    """What a zone is doing, and whether we are the cause.

    `busy` means something is actually playing. `note` is weaker: the amplifier
    is switched on with an input selected, which is worth showing and is not
    evidence that anybody is listening. A receiver left on after the television
    was switched off looks identical to one in use, and reporting that as
    occupied claims to know something we do not.
    """

    busy: bool
    ours: bool
    detail: str | None = None
    reachable: bool = True
    note: str | None = None


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


def _avr_note(state: denon.AvrState, zone2: bool) -> str | None:
    """What the amplifier is switched to, when it is not us.

    The case that prompted this: a child watching television through the
    receiver. HEOS is idle, so asking only HEOS reported the living room as free
    while it was plainly in use.

    It is deliberately a note rather than a verdict. The receiver cannot say
    whether sound is coming out — an amplifier left on after the television was
    switched off reports exactly what one in use reports. So this says what is
    known, "on, TV input", and leaves the conclusion to whoever is standing in
    the room.
    """
    if zone2:
        if state.zone2 and state.zone2_source and state.zone2_source not in NETWORK_INPUTS:
            return f"on · {state.zone2_source}"
        return None

    # Positive evidence only. A receiver that has not said it is on must not be
    # reported as occupied — a silent amplifier is the normal case, and guessing
    # busy would block playback in a free room.
    powered = state.power is True or state.main_zone is True
    if powered and state.source and state.source not in NETWORK_INPUTS:
        return f"on · {state.source}"
    return None


def cached_occupancy(zone2: bool = False) -> Occupancy | None:
    """The last thing the receiver said, without asking it again.

    None when nothing is known yet or the answer has gone stale, which the caller
    reports as "not responding" rather than as "free".
    """
    settings = get_settings()
    host = settings.denon_host.strip()
    if not host:
        return Occupancy(busy=False, ours=False, reachable=False, detail="no receiver configured")

    entry = _cache.get(f"{host}:{'z2' if zone2 else 'main'}")
    if entry is None:
        return None
    at, value = entry
    if time.monotonic() - at > STALE_AFTER:
        return None
    return value


async def refresh_loop() -> None:
    """Keep the cached answers current, off the request path.

    Both zones on every tick: one conversation with the receiver answers for the
    whole amplifier, and asking twice would double the traffic to a device that
    accepts very few connections at once.
    """
    settings = get_settings()
    if not settings.denon_host.strip():
        log.info("no receiver configured — not polling")
        return

    while True:
        for zone2 in (False, True):
            try:
                await receiver_occupancy(None, zone2=zone2, force=True)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.debug("occupancy refresh failed", exc_info=True)
        await asyncio.sleep(CACHE_TTL / 2)


async def receiver_occupancy(
    our_session_state: str | None, zone2: bool = False, force: bool = False
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
        if not force and cached and time.monotonic() - cached[0] < CACHE_TTL:
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
                    note = _avr_note(avr, zone2)
                except denon.DenonError:
                    note = None
                # Not busy: nothing is playing. The note travels alongside so the
                # tower can say what the receiver is switched to without claiming
                # the room is occupied.
                result = Occupancy(busy=False, ours=False, note=note)
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
