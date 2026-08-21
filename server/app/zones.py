"""Zones and server-owned playback sessions — the control tower.

The server owns playback state, not the phone (ARCHITECTURE.md §5.8). A session
binds to a *zone*, so the phone can die mid-song and the music keeps playing, and
moving audio to another room is a rebinding rather than a re-cast.

A zone is more than a destination: it carries the orchestration needed to make
sound actually come out. For the balcony that means powering the receiver on,
enabling ZONE2 and selecting the network source before any audio is pushed.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import denon, lanaddr, occupancy
from .access import can_use_zone, may_access_item, zone_scope
from .config import get_settings
from .db import get_engine
from .people import ROOM, AudienceUpdate, apply_audience
from .security import CurrentUser, audit, require_user
from .signing import mint
from .transcode import needs_conversion

log = logging.getLogger("homesh.zones")
router = APIRouter(prefix="/api/zones", tags=["zones"])


class ZoneError(Exception):
    pass


@dataclass
class Zone:
    id: UUID
    name: str
    renderer_id: UUID | None
    renderer_kind: str | None
    device_key: str | None
    preroll: list[dict]
    postroll: list[dict]
    idle_timeout_s: int | None


# ── Requests ────────────────────────────────────────────────────────────────


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    renderer_kind: str = Field(pattern="^(tvapp|heos|cast|browser)$")
    device_key: str = Field(min_length=1, max_length=200)
    # Ordered hardware commands, e.g. [{"avr": "PWON"}, {"avr": "Z2ON"}]
    preroll: list[dict] = Field(default_factory=list)
    postroll: list[dict] = Field(default_factory=list)
    volume: int | None = Field(default=None, ge=0, le=100)
    # Who the room is for, decided as it is created. Omitted means undecided,
    # which reads as admins-only until somebody says otherwise — a room paired
    # this afternoon should not be playable by the household before then.
    audience: Literal["everyone", "admins", "selected"] | None = None
    grant_to: list[UUID] = Field(default_factory=list, max_length=200)


class PlayRequest(BaseModel):
    # A folder of songs is a queue, and folders in this library run to fifteen
    # hundred tracks. Five hundred was a guess that a real folder walked straight
    # past, and the refusal arrived as a sentence about list validation.
    item_ids: list[UUID] = Field(min_length=1, max_length=5000)
    start_index: int = Field(default=0, ge=0)
    # The receiver has one network player, so starting here stops whatever else
    # is using it. That should be a decision, not a surprise.
    take_over: bool = False


class VolumeRequest(BaseModel):
    level: int = Field(ge=0, le=100)


class ZoneUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


# ── Helpers ─────────────────────────────────────────────────────────────────


# Commands that act on the main zone. A room that sends any of these is using it.
_MAIN_ZONE_PREFIXES = ("PW", "ZM", "SI", "MV")


def _is_zone2(preroll: list[dict] | None) -> bool:
    """Whether a room uses the receiver's second zone *and nothing else*.

    It decides which zone's power and input the occupancy check reads, and both
    mistakes are visible: judging the balcony by the main zone makes a television
    downstairs look like the balcony being busy, and judging the living room by
    zone 2 makes that television invisible.

    A room that drives both — "Living Room + Balcony" — is not zone 2. It occupies
    the main zone, so it has to be judged by it.
    """
    commands = [c.get("avr", "") for c in (preroll or [])]
    touches_zone2 = any(c.startswith("Z2") for c in commands)
    touches_main = any(
        c.startswith(p) for c in commands for p in _MAIN_ZONE_PREFIXES if not c.startswith("Z2")
    )
    return touches_zone2 and not touches_main


def _media_base() -> str:
    """The origin a *device on the LAN* can fetch media from.

    Not PUBLIC_ORIGIN: that is usually localhost, which means nothing to a
    receiver on the other side of the room. The receiver fetches the stream
    itself, so this has to be an address it can actually route to.
    """
    base = (lanaddr.lan_base() or "").rstrip("/")
    if not base:
        raise ZoneError(
            "LAN_BASE_URL is not set. A receiver fetches media over the network, "
            "so it needs an address it can reach — e.g. http://192.0.2.10:8080 — "
            "rather than localhost."
        )
    return base


def _load_zone(zone_id: UUID) -> Zone:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT z.id, z.name, z.renderer_id, r.kind::text, r.device_key,
                       z.preroll, z.postroll, z.idle_timeout_s
                FROM zones z
                LEFT JOIN renderers r ON r.id = z.renderer_id
                WHERE z.id = :id
                """
            ),
            {"id": str(zone_id)},
        ).first()

    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such zone")
    return Zone(
        id=row[0], name=row[1], renderer_id=row[2], renderer_kind=row[3],
        device_key=row[4], preroll=row[5] or [], postroll=row[6] or [],
        idle_timeout_s=row[7],
    )


def _require_zone_access(zone_id: UUID, user: CurrentUser) -> None:
    """404 rather than 403: a room outside your scope should not be confirmed."""
    any_zone, allowed = zone_scope(user.id)
    if not can_use_zone(zone_id, any_zone, allowed):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such zone")


async def _run_commands(zone: Zone, commands: list[dict]) -> list[str]:
    """Execute a zone's orchestration steps.

    Each step names its transport, so a zone can mix them — the balcony needs AVR
    telnet for power and zone selection, then HEOS for the audio itself.
    """
    if not commands:
        return []

    host = get_settings().denon_host.strip()
    if not host:
        raise ZoneError("DENON_HOST is not set, so the receiver cannot be reached")

    avr_commands = [c["avr"] for c in commands if "avr" in c]
    if not avr_commands:
        return []
    return await denon.avr_command(host, avr_commands, read_for=2.0)


# ── Endpoints ───────────────────────────────────────────────────────────────


def _describe(item_ids: list[str]) -> dict[str, dict]:
    """Filename and tags for the tracks a tower is about to display.

    One query for every room rather than one per room: the tower polls every few
    seconds, and a listing that opened a connection per zone would spend more
    time describing music than playing it.
    """
    if not item_ids:
        return {}

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT i.id,
                       min(r.filename) AS filename,
                       max(CASE WHEN m.key = 'title'  THEN m.value END) AS title,
                       max(CASE WHEN m.key = 'artist' THEN m.value END) AS artist,
                       -- A bar needs something to be a fraction of. Without it
                       -- the tower could show a position but never how far
                       -- through, which is not a seek bar.
                       min(i.duration_ms) AS duration_ms
                FROM items i
                JOIN replicas r ON r.item_id = i.id
                LEFT JOIN item_metadata m ON m.item_id = i.id
                WHERE i.id = ANY(CAST(:ids AS uuid[]))
                GROUP BY i.id
                """
            ),
            {"ids": item_ids},
        ).all()

    return {
        str(r[0]): {
            # The filename is always there and always sent. A tag may be missing
            # or wrong; the name of the file is neither.
            "filename": r[1],
            "title": r[2],
            "artist": r[3],
            "duration_ms": r[4],
        }
        for r in rows
    }


@router.get("")
async def list_zones(user: CurrentUser = Depends(require_user)) -> list[dict]:
    """Every zone with its current session — this is the control tower's view."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT z.id, z.name, r.kind::text, r.state::text, r.name,
                       s.state::text, s.queue, s.cursor, s.position_ms, s.volume,
                       s.updated_at, z.preroll, s.duration_ms
                FROM zones z
                LEFT JOIN renderers r ON r.id = z.renderer_id
                LEFT JOIN play_sessions s ON s.zone_id = z.id
                ORDER BY z.name
                """
            )
        ).all()

    any_zone, allowed = zone_scope(user.id)

    # Everything currently playing anywhere, described in one go.
    playing_now = [
        (row[6] or [])[row[7] or 0]
        for row in rows
        if row[5] and (row[7] or 0) < len(row[6] or [])
    ]
    described = _describe(playing_now)

    out = []
    for row in rows:
        # Rooms this person may not use are not listed. A child seeing the living
        # room greyed out learns only that it exists and is out of reach.
        if not can_use_zone(row[0], any_zone, allowed):
            continue
        queue = row[6] or []
        cursor = row[7] or 0
        kind = row[2]
        session_state = row[5]

        # A receiver plays Spotify and AirPlay without us. Asking it is the only
        # way the tower can be honest about whether a room is free.
        external = None
        if kind == "heos":
            # Which half of the receiver this room is, so the check looks at the
            # right zone's power and input rather than at the amplifier in
            # general.
            # Read, never ask. The listing is polled every few seconds by every
            # open client; talking to the receiver here made it the slowest screen
            # in the app.
            seen = occupancy.cached_occupancy(zone2=_is_zone2(row[11]))
            if seen is None:
                external = {"busy": False, "unreachable": True,
                            "detail": "waiting for the receiver"}
                seen = occupancy.Occupancy(busy=False, ours=False, reachable=False)
            if seen.busy and not seen.ours:
                external = {"busy": True, "detail": seen.detail}
            elif not seen.reachable and external is None:
                external = {"busy": False, "unreachable": True, "detail": seen.detail}
            elif seen.note:
                external = {"busy": False, "detail": seen.note}

            # Our own record can outlive the music: the receiver is handed one
            # track and never told there was a queue, so when it finishes nothing
            # tells the server. Left alone the tower goes on naming a song that
            # stopped an hour ago.
            if session_state in ("playing", "buffering") and not seen.busy and seen.reachable:
                # Only once the session has had time to become true. A receiver
                # takes a moment to pick up a stream, and reconciling on the
                # first poll would cancel the track we had just started.
                with get_engine().begin() as conn:
                    cleared = conn.execute(
                        text(
                            """
                            UPDATE play_sessions SET state = 'idle', updated_at = now()
                            WHERE zone_id = :z
                              AND updated_at < now() - interval '60 seconds'
                            """
                        ),
                        {"z": str(row[0])},
                    ).rowcount
                if cleared:
                    session_state = "idle"

        out.append(
            {
                "id": str(row[0]),
                "name": row[1],
                "external": external,
                "renderer": {"kind": row[2], "state": row[3], "name": row[4]}
                if row[2]
                else None,
                "session": {
                    "state": session_state,
                    "queue_length": len(queue),
                    "cursor": cursor,
                    "current_item": queue[cursor] if cursor < len(queue) else None,
                    # What it actually is. A tower that says "track 2 of 5"
                    # answers a question nobody asked: the thing you want to know
                    # from the next room is what is playing, not where it sits in
                    # a list.
                    "now": described.get(queue[cursor]) if cursor < len(queue) else None,
                    "position_ms": row[8],
                    # What the screen reports, which is the only source for a
                    # live transcode — the catalog has no length for one.
                    "duration_ms": row[12],
                    "volume": row[9],
                    "updated_at": row[10].isoformat() if row[10] else None,
                }
                if row[5]
                else None,
            }
        )
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_zone(body: ZoneCreate, user: CurrentUser = Depends(require_user)) -> dict:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")

    with get_engine().begin() as conn:
        renderer_id = conn.execute(
            text(
                """
                INSERT INTO renderers (kind, name, device_key, state)
                VALUES (CAST(:kind AS renderer_kind), :name, :key, 'unavailable')
                ON CONFLICT (device_key) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """
            ),
            {"kind": body.renderer_kind, "name": body.name, "key": body.device_key},
        ).scalar_one()

        try:
            zone_id = conn.execute(
                text(
                    """
                    INSERT INTO zones (name, renderer_id, preroll, postroll, audience)
                    VALUES (:name, :rid, CAST(:pre AS jsonb), CAST(:post AS jsonb),
                            CAST(:aud AS audience))
                    RETURNING id
                    """
                ),
                {
                    "name": body.name,
                    "rid": str(renderer_id),
                    "pre": json.dumps(body.preroll),
                    "post": json.dumps(body.postroll),
                    "aud": body.audience,
                },
            ).scalar_one()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_409_CONFLICT, "a zone with that name exists") from exc

        # Reuse the same reconciliation the audience endpoint performs, so a room
        # created with an audience and one changed afterwards end up identical.
        if body.audience is not None:
            apply_audience(
                conn,
                place=ROOM,
                key=zone_id,
                grant_value=str(zone_id),
                body=AudienceUpdate(audience=body.audience, users=body.grant_to),
            )

    return {"id": str(zone_id), "name": body.name}


@router.post("/{zone_id}/play")
async def play(
    zone_id: UUID, body: PlayRequest, user: CurrentUser = Depends(require_user)
) -> dict:
    """Start playback in a zone: record the session, orchestrate, then push audio."""
    zone = _load_zone(zone_id)
    _require_zone_access(zone_id, user)

    # Sending something you cannot open to a room would be a way around the
    # library scope, so the content is checked as well as the room.
    for item in body.item_ids:
        if not may_access_item(item, user.id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    if zone.renderer_kind is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "zone has no renderer bound")

    index = min(body.start_index, len(body.item_ids) - 1)
    item_id = body.item_ids[index]

    if zone.renderer_kind == "heos" and not body.take_over:
        # The cached answer, which the background poller keeps a few seconds
        # fresh. Only if nothing is known yet — a cold start — is the receiver
        # asked directly, because that costs seconds and this is a button press.
        zone2 = _is_zone2(zone.preroll)
        seen = occupancy.cached_occupancy(zone2=zone2)
        if seen is None:
            seen = await occupancy.receiver_occupancy(None, zone2=zone2)
        if seen.busy and not seen.ours:
            # 409 rather than 403: nothing is forbidden, the room is simply busy,
            # and the caller can repeat the request having decided to interrupt.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{zone.name} is already playing {seen.detail or 'something else'}. "
                f"Starting here will stop it.",
            )

    # The session is written first and independently of the hardware. If the
    # receiver is unreachable the intent is still recorded, so the control tower
    # can show what was asked for and offer to retry.
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO play_sessions (zone_id, queue, cursor, position_ms, state)
                VALUES (:zid, CAST(:queue AS jsonb), :cursor, 0, 'buffering')
                ON CONFLICT (zone_id) DO UPDATE
                SET queue = EXCLUDED.queue, cursor = EXCLUDED.cursor,
                    position_ms = 0, duration_ms = NULL,
                    state = 'buffering', updated_at = now()
                """
            ),
            {
                "zid": str(zone_id),
                "queue": json.dumps([str(i) for i in body.item_ids]),
                "cursor": index,
            },
        )

    if zone.renderer_kind == "tvapp":
        return await _push_to_screen(zone, item_id, user)

    if zone.renderer_kind != "heos":
        # Cast and browser renderers arrive in later phases.
        return {"zone": zone.name, "state": "buffering", "pushed": False}

    return await _stream_to_receiver(zone, item_id, user, with_preroll=True)


async def _stream_to_receiver(
    zone: Zone, item_id: UUID, user: CurrentUser, *, with_preroll: bool
) -> dict:
    """Point the receiver at one item.

    Extracted so that starting a queue and skipping within one take exactly the
    same path. The preroll — powering a zone, selecting its input — is skipped
    when skipping: the zone is already on, and re-sending those commands makes
    the receiver click between tracks.
    """
    settings = get_settings()
    host = settings.denon_host.strip()
    if not host:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "DENON_HOST is not set")

    try:
        if with_preroll:
            await _run_commands(zone, zone.preroll)

        # A receiver pulls for the length of a track, so the short browser TTL
        # would expire mid-song. This URL is LAN-scoped and still bound to one
        # item and user.
        token = mint(item_id, user.id, "stream", ttl=settings.cast_url_ttl_minutes * 60)
        url = f"{_media_base()}/api/stream/{item_id}?t={token}"

        players = await denon.get_players(host)
        if not players:
            raise ZoneError("the receiver reports no HEOS player")
        await denon.play_stream(host, players[0].pid, url)
    except (denon.DenonError, ZoneError) as exc:
        with get_engine().begin() as conn:
            conn.execute(
                text("UPDATE play_sessions SET state = 'idle' WHERE zone_id = :z"),
                {"z": str(zone.id)},
            )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE play_sessions SET state = 'playing', position_ms = 0, "
                 "duration_ms = NULL, updated_at = now() WHERE zone_id = :z"),
            {"z": str(zone.id)},
        )
    occupancy.invalidate()

    return {"zone": zone.name, "state": "playing", "pushed": True}


async def _push_to_screen(zone: Zone, item_id: UUID, user: CurrentUser) -> dict:
    """Send a play command to a paired screen over its open socket.

    Unlike the receiver, a screen holds a connection, so there is no orchestration
    to run and nothing to wake — it is either there or it is not, and saying which
    is more useful than a timeout.
    """
    from .renderers import hub

    if zone.renderer_id is None or not hub.is_connected(zone.renderer_id):
        with get_engine().begin() as conn:
            conn.execute(
                text("UPDATE play_sessions SET state = 'idle' WHERE zone_id = :z"),
                {"z": str(zone.id)},
            )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"{zone.name} is not connected — open Homesh on that screen",
        )

    settings = get_settings()
    token = mint(item_id, user.id, "stream", ttl=settings.cast_url_ttl_minutes * 60)

    # A screen fetches media itself, exactly as the receiver does, so the URL has
    # to be reachable from the device rather than from the browser.
    base = _media_base() if lanaddr.lan_base() else settings.public_origin.rstrip("/")

    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT r.filename, i.kind::text,
                       (SELECT string_agg(m.value, ' · ' ORDER BY m.key)
                        FROM item_metadata m
                        WHERE m.item_id = i.id AND m.key IN ('artist', 'album')) AS tags
                FROM items i JOIN replicas r ON r.item_id = i.id
                WHERE i.id = :id LIMIT 1
                """
            ),
            {"id": str(item_id)},
        ).first()

    filename, kind, tags = row if row else ("", "audio", None)

    # A screen decodes with the chip it has. WMV and AVI carry codecs no
    # television decoder has ever supported, and handing one over produced
    # exactly what a browser produces: "cannot play that format".
    #
    # The answer is the transcode the viewer already uses, not a decoder
    # bundled into the app. Shipping one would mean ExoPlayer with its FFmpeg
    # extension — a build system, several megabytes, and a second decoder to
    # keep working — to solve on the television a problem the server has
    # already solved for the browser. This is one URL.
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if kind == "video" and needs_conversion(ext):
        media_url = f"{base}/api/videos/{item_id}/live.mp4?t={token}"
    else:
        media_url = f"{base}/api/stream/{item_id}?t={token}"

    sent = await hub.send(
        zone.renderer_id,
        {
            "type": "play",
            "item_id": str(item_id),
            "url": media_url,
            "filename": filename,
            "tags": tags,
            "kind": kind,
        },
    )
    if not sent:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{zone.name} did not accept the command")

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE play_sessions SET state = 'playing', updated_at = now() "
                 "WHERE zone_id = :z"),
            {"z": str(zone.id)},
        )
    return {"zone": zone.name, "state": "playing", "pushed": True}


@router.post("/{zone_id}/stop")
async def stop(zone_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    zone = _load_zone(zone_id)
    _require_zone_access(zone_id, user)
    settings = get_settings()

    if zone.renderer_kind == "tvapp" and zone.renderer_id:
        from .renderers import hub

        await hub.send(zone.renderer_id, {"type": "stop"})
    elif zone.renderer_kind == "heos" and settings.denon_host.strip():
        host = settings.denon_host.strip()
        try:
            players = await denon.get_players(host)
            if players:
                await denon.player_stop(host, players[0].pid)
            await _run_commands(zone, zone.postroll)
        except (denon.DenonError, ZoneError) as exc:
            # The session still ends: the user asked for silence, and refusing
            # because the receiver did not answer would leave the tower lying.
            log.warning("stop could not reach the receiver: %s", exc)

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE play_sessions SET state = 'idle', position_ms = 0, "
                 "duration_ms = NULL, updated_at = now() WHERE zone_id = :z"),
            {"z": str(zone_id)},
        )
    occupancy.invalidate()
    return {"zone": zone.name, "state": "idle"}


@router.post("/{zone_id}/volume")
async def set_volume(
    zone_id: UUID, body: VolumeRequest, user: CurrentUser = Depends(require_user)
) -> dict:
    zone = _load_zone(zone_id)
    _require_zone_access(zone_id, user)
    settings = get_settings()

    if zone.renderer_kind == "tvapp" and zone.renderer_id:
        from .renderers import hub

        await hub.send(zone.renderer_id, {"type": "volume", "volume": body.level})
    elif zone.renderer_kind == "heos" and settings.denon_host.strip():
        host = settings.denon_host.strip()
        # ZONE2 volume is an AVR command, not a HEOS one: HEOS sets the player's
        # level, which is the main zone.
        zone2 = any(c.get("avr", "").startswith("Z2") for c in zone.preroll)
        try:
            if zone2:
                await denon.set_zone2_volume(host, body.level)
            else:
                players = await denon.get_players(host)
                if players:
                    await denon.set_player_volume(host, players[0].pid, body.level)
        except denon.DenonError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE play_sessions SET volume = :v, updated_at = now() "
                 "WHERE zone_id = :z"),
            {"v": body.level, "z": str(zone_id)},
        )
    return {"zone": zone.name, "volume": body.level}


@router.get("/{zone_id}/device")
async def device_state(zone_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    """What the hardware actually reports, as opposed to what we last asked for."""
    zone = _load_zone(zone_id)
    _require_zone_access(zone_id, user)
    host = get_settings().denon_host.strip()

    if zone.renderer_kind != "heos" or not host:
        return {"reachable": False, "reason": "no receiver configured for this zone"}

    try:
        state = await denon.query_state(host)
    except denon.DenonError as exc:
        return {"reachable": False, "reason": str(exc)}

    return {
        "reachable": True,
        "power": state.power,
        "main_zone": state.main_zone,
        "zone2": state.zone2,
        "zone2_source": state.zone2_source,
        "zone2_volume": state.zone2_volume,
        "main_volume": state.main_volume,
        "source": state.source,
    }


# ── Transport ───────────────────────────────────────────────────────────────
#
# The receiver is sent one URL at a time and has no idea a queue exists, so
# skipping is the server's job rather than the hardware's. Pausing is the
# opposite: the receiver holds the stream, so only it can pause it.
#
# Both kinds of renderer answer the same four endpoints, because a phone should
# not have to know what is in the room to control it.


def _queue_of(zone_id: UUID) -> tuple[list[str], int]:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT queue, cursor FROM play_sessions WHERE zone_id = :z"),
            {"z": str(zone_id)},
        ).first()
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing is playing in that room")
    return list(row[0] or []), int(row[1] or 0)


async def _set_paused(zone: Zone, paused: bool) -> None:
    settings = get_settings()
    if zone.renderer_kind == "tvapp" and zone.renderer_id:
        from .renderers import hub

        await hub.send(zone.renderer_id, {"type": "pause" if paused else "resume"})
    elif zone.renderer_kind == "heos" and settings.denon_host.strip():
        host = settings.denon_host.strip()
        players = await denon.get_players(host)
        if not players:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "the receiver reports no player")
        await denon.set_play_state(host, players[0].pid, "pause" if paused else "play")


@router.post("/{zone_id}/pause")
async def pause(zone_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    zone = _load_zone(zone_id)
    _require_zone_access(zone_id, user)
    try:
        await _set_paused(zone, True)
    except denon.DenonError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE play_sessions SET state = 'paused', updated_at = now() "
                 "WHERE zone_id = :z"),
            {"z": str(zone_id)},
        )
    occupancy.invalidate()
    return {"zone": zone.name, "state": "paused"}


@router.post("/{zone_id}/resume")
async def resume(zone_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    zone = _load_zone(zone_id)
    _require_zone_access(zone_id, user)
    try:
        await _set_paused(zone, False)
    except denon.DenonError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE play_sessions SET state = 'playing', updated_at = now() "
                 "WHERE zone_id = :z"),
            {"z": str(zone_id)},
        )
    occupancy.invalidate()
    return {"zone": zone.name, "state": "playing"}


class SeekRequest(BaseModel):
    position_ms: int = Field(ge=0, description="Where to jump to, from the start")


@router.post("/{zone_id}/seek")
async def seek(
    zone_id: UUID, body: SeekRequest, user: CurrentUser = Depends(require_user)
) -> dict:
    """Move to a point in what is playing in another room.

    The one control the tower was missing, and the one an hour-long video most
    needs: without it the only way past a slow passage in the bedroom was to
    walk to the bedroom.
    """
    zone = _load_zone(zone_id)
    _require_zone_access(zone_id, user)

    if zone.renderer_kind != "tvapp" or not zone.renderer_id:
        # HEOS seeks within a stream it is being fed, and a stream that is being
        # transcoded live has no index to seek in. Saying so beats a control
        # that silently does nothing.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{zone.name} plays through the receiver, which cannot be moved through",
        )

    from .renderers import hub

    if not await hub.send(
        zone.renderer_id, {"type": "seek", "position_ms": body.position_ms}
    ):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"{zone.name} is not connected — open Homesh on that screen",
        )

    # Recorded straight away rather than waiting for the screen to report back,
    # so the bar in the tower moves the moment it is dragged.
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE play_sessions SET position_ms = :p, updated_at = now() "
                 "WHERE zone_id = :z"),
            {"p": body.position_ms, "z": str(zone_id)},
        )
    return {"zone": zone.name, "position_ms": body.position_ms}


@router.get("/{zone_id}/queue")
async def zone_queue(zone_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    """What a room is going to play, in order.

    The tower could say "track 4 of 31" but never what the other thirty were,
    so a playlist sent to a room became a black box: no way to see what was
    coming or to choose something else from it without sending the whole list
    again from the beginning.
    """
    _load_zone(zone_id)
    _require_zone_access(zone_id, user)

    queue, cursor = _queue_of(zone_id)
    described = _describe([UUID(i) for i in queue])

    return {
        "cursor": cursor,
        "tracks": [
            {
                "index": i,
                "item_id": item,
                # Same shape a listing uses, so the filename is always present
                # even where a tag is missing.
                **(described.get(item) or {"filename": None, "title": None,
                                           "artist": None, "duration_ms": None}),
            }
            for i, item in enumerate(queue)
        ],
    }


class JumpRequest(BaseModel):
    index: int = Field(ge=0, description="Which track in the queue to play")


@router.post("/{zone_id}/jump")
async def jump(
    zone_id: UUID, body: JumpRequest, user: CurrentUser = Depends(require_user)
) -> dict:
    """Play a particular track of what the room already has.

    Skipping forward one at a time works but is not a way to reach the ninth
    song of a playlist from another room.
    """
    queue, cursor = _queue_of(zone_id)
    if body.index >= len(queue):
        raise HTTPException(status.HTTP_409_CONFLICT, "that track is not in the queue")
    return await _skip(zone_id, user, body.index - cursor)


@router.post("/{zone_id}/shuffle")
async def shuffle_queue(zone_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    """Reorder what a room has not played yet.

    Deliberately an action rather than a switch. A switch has to mean something
    for tracks already behind the cursor, and a room's queue is a fixed list
    that was sent to it — so "shuffle the rest" is both the whole of what anyone
    wants here and the only thing that can be said honestly. What is playing
    keeps playing; everything after it is reordered, and the tower shows the new
    order immediately.
    """
    _load_zone(zone_id)
    _require_zone_access(zone_id, user)

    queue, cursor = _queue_of(zone_id)
    upcoming = queue[cursor + 1 :]
    if len(upcoming) < 2:
        raise HTTPException(status.HTTP_409_CONFLICT, "there is nothing left to shuffle")

    # secrets, not random: ruff's S311 is right that a predictable shuffle is a
    # smell, and this costs nothing.
    shuffled = list(upcoming)
    for i in range(len(shuffled) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        shuffled[i], shuffled[j] = shuffled[j], shuffled[i]

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE play_sessions SET queue = CAST(:q AS jsonb), updated_at = now() "
                 "WHERE zone_id = :z"),
            {"q": json.dumps(queue[: cursor + 1] + shuffled), "z": str(zone_id)},
        )

    return {"shuffled": len(shuffled)}


async def _skip(zone_id: UUID, user: CurrentUser, delta: int) -> dict:
    zone = _load_zone(zone_id)
    _require_zone_access(zone_id, user)

    queue, cursor = _queue_of(zone_id)
    if not queue:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing is playing in that room")

    target = cursor + delta
    if target < 0:
        # Back from the first track restarts it, which is what every music player
        # does and what the button is reached for.
        target = 0
    if target >= len(queue):
        return await stop(zone_id, user)

    item_id = UUID(queue[target])
    if not may_access_item(item_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE play_sessions SET cursor = :c, position_ms = 0, duration_ms = NULL, "
                 "updated_at = now() WHERE zone_id = :z"),
            {"c": target, "z": str(zone_id)},
        )

    if zone.renderer_kind == "tvapp":
        return await _push_to_screen(zone, item_id, user)
    if zone.renderer_kind == "heos":
        # No preroll: the zone is already on, and re-running it makes the
        # receiver click between tracks.
        return await _stream_to_receiver(zone, item_id, user, with_preroll=False)
    return {"zone": zone.name, "state": "buffering", "pushed": False}


@router.post("/{zone_id}/next")
async def next_track(zone_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    return await _skip(zone_id, user, 1)


@router.post("/{zone_id}/previous")
async def previous_track(zone_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    return await _skip(zone_id, user, -1)


# ── Looking after rooms ─────────────────────────────────────────────────────
#
# A room could be created and never renamed or removed, which meant a typo at
# pairing time was permanent and a screen taken out of the house stayed on the
# list for ever.


@router.put("/{zone_id}")
async def rename_zone(
    zone_id: UUID, body: ZoneUpdate, user: CurrentUser = Depends(require_user)
) -> dict:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    _load_zone(zone_id)

    with get_engine().begin() as conn:
        try:
            conn.execute(
                text("UPDATE zones SET name = :n WHERE id = :z"),
                {"n": body.name.strip(), "z": str(zone_id)},
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status.HTTP_409_CONFLICT, "a room with that name already exists"
            ) from exc

    return {"id": str(zone_id), "name": body.name.strip()}


@router.delete("/{zone_id}")
async def remove_zone(zone_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    """Forget a room, and the screen behind it.

    The renderer goes too. Leaving it would mean a device that still holds a
    valid credential and can still be sent to, which is not what "remove" means
    to anybody — and re-pairing the same screen creates a fresh one anyway.
    """
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")

    zone = _load_zone(zone_id)

    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM zones WHERE id = :z"), {"z": str(zone_id)})
        if zone.renderer_id:
            conn.execute(
                text("DELETE FROM renderers WHERE id = :r"), {"r": str(zone.renderer_id)}
            )
        audit(conn, "zone.removed", user.id, {"name": zone.name}, None)

    # Any socket it still holds is now unauthenticated; drop it so an uninstalled
    # or re-homed screen cannot go on receiving commands.
    if zone.renderer_id:
        from .renderers import hub

        await hub.disconnect(zone.renderer_id)

    occupancy.invalidate()
    return {"ok": True}
