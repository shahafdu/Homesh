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
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import denon, occupancy
from .config import get_settings
from .db import get_engine
from .security import CurrentUser, require_user
from .signing import mint

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


class PlayRequest(BaseModel):
    item_ids: list[UUID] = Field(min_length=1, max_length=500)
    start_index: int = Field(default=0, ge=0)
    # The receiver has one network player, so starting here stops whatever else
    # is using it. That should be a decision, not a surprise.
    take_over: bool = False


class VolumeRequest(BaseModel):
    level: int = Field(ge=0, le=100)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _media_base() -> str:
    """The origin a *device on the LAN* can fetch media from.

    Not PUBLIC_ORIGIN: that is usually localhost, which means nothing to a
    receiver on the other side of the room. The receiver fetches the stream
    itself, so this has to be an address it can actually route to.
    """
    settings = get_settings()
    base = settings.lan_base_url.strip().rstrip("/")
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


@router.get("")
async def list_zones(_: CurrentUser = Depends(require_user)) -> list[dict]:
    """Every zone with its current session — this is the control tower's view."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT z.id, z.name, r.kind::text, r.state::text, r.name,
                       s.state::text, s.queue, s.cursor, s.position_ms, s.volume,
                       s.updated_at
                FROM zones z
                LEFT JOIN renderers r ON r.id = z.renderer_id
                LEFT JOIN play_sessions s ON s.zone_id = z.id
                ORDER BY z.name
                """
            )
        ).all()

    out = []
    for row in rows:
        queue = row[6] or []
        cursor = row[7] or 0
        kind = row[2]
        session_state = row[5]

        # A receiver plays Spotify and AirPlay without us. Asking it is the only
        # way the tower can be honest about whether a room is free.
        external = None
        if kind == "heos":
            seen = await occupancy.receiver_occupancy(session_state)
            if seen.busy and not seen.ours:
                external = {"busy": True, "detail": seen.detail}
            elif not seen.reachable:
                external = {"busy": False, "unreachable": True, "detail": seen.detail}

        out.append(
            {
                "id": str(row[0]),
                "name": row[1],
                "external": external,
                "renderer": {"kind": row[2], "state": row[3], "name": row[4]}
                if row[2]
                else None,
                "session": {
                    "state": row[5],
                    "queue_length": len(queue),
                    "cursor": cursor,
                    "current_item": queue[cursor] if cursor < len(queue) else None,
                    "position_ms": row[8],
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
                    INSERT INTO zones (name, renderer_id, preroll, postroll)
                    VALUES (:name, :rid, CAST(:pre AS jsonb), CAST(:post AS jsonb))
                    RETURNING id
                    """
                ),
                {
                    "name": body.name,
                    "rid": str(renderer_id),
                    "pre": json.dumps(body.preroll),
                    "post": json.dumps(body.postroll),
                },
            ).scalar_one()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_409_CONFLICT, "a zone with that name exists") from exc

    return {"id": str(zone_id), "name": body.name}


@router.post("/{zone_id}/play")
async def play(
    zone_id: UUID, body: PlayRequest, user: CurrentUser = Depends(require_user)
) -> dict:
    """Start playback in a zone: record the session, orchestrate, then push audio."""
    zone = _load_zone(zone_id)
    if zone.renderer_kind is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "zone has no renderer bound")

    index = min(body.start_index, len(body.item_ids) - 1)
    item_id = body.item_ids[index]

    if zone.renderer_kind == "heos" and not body.take_over:
        seen = await occupancy.receiver_occupancy(None)
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
                    position_ms = 0, state = 'buffering', updated_at = now()
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

    settings = get_settings()
    host = settings.denon_host.strip()
    if not host:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "DENON_HOST is not set")

    try:
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
                {"z": str(zone_id)},
            )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE play_sessions SET state = 'playing', updated_at = now() "
                 "WHERE zone_id = :z"),
            {"z": str(zone_id)},
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
    base = _media_base() if settings.lan_base_url.strip() else settings.public_origin.rstrip("/")

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

    sent = await hub.send(
        zone.renderer_id,
        {
            "type": "play",
            "item_id": str(item_id),
            "url": f"{base}/api/stream/{item_id}?t={token}",
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
                 "updated_at = now() WHERE zone_id = :z"),
            {"z": str(zone_id)},
        )
    occupancy.invalidate()
    return {"zone": zone.name, "state": "idle"}


@router.post("/{zone_id}/volume")
async def set_volume(
    zone_id: UUID, body: VolumeRequest, user: CurrentUser = Depends(require_user)
) -> dict:
    zone = _load_zone(zone_id)
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
async def device_state(zone_id: UUID, _: CurrentUser = Depends(require_user)) -> dict:
    """What the hardware actually reports, as opposed to what we last asked for."""
    zone = _load_zone(zone_id)
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
