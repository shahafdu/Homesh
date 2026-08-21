"""Renderer registration and the command channel.

A screen running our app is a *renderer*. It dials out, registers itself, and then
holds a WebSocket over which the server sends commands and it reports state. That
direction matters: no inbound ports, no addresses to configure, and DHCP moving the
device costs nothing (ARCHITECTURE.md §5.7).

Pairing is a short code shown on the screen and typed on a phone. Nobody should
ever type a password with a TV remote.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from .db import get_engine
from .people import ROOM, AudienceUpdate, apply_audience
from .security import CurrentUser, require_user

log = logging.getLogger("homesh.renderers")
router = APIRouter(prefix="/api/renderers", tags=["renderers"])

PAIRING_TTL = timedelta(minutes=10)

# Unambiguous alphabet: no O/0, I/1, S/5. These get read off a screen across a
# room and typed on a phone, so shapes that look alike cost real time.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRTUVWXYZ2346789"
CODE_LENGTH = 6


def _hash(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def _make_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


# ── Live connections ────────────────────────────────────────────────────────


@dataclass
class Connection:
    renderer_id: UUID
    socket: WebSocket
    name: str
    last_state: dict = field(default_factory=dict)


class Hub:
    """Every connected renderer, and the controllers watching them.

    In-memory on purpose: a connection cannot outlive the process holding it, so
    persisting it would only ever be a lie about what is reachable. Durable state
    lives in play_sessions.
    """

    def __init__(self) -> None:
        self._renderers: dict[UUID, Connection] = {}
        self._watchers: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add_renderer(self, conn: Connection) -> None:
        async with self._lock:
            existing = self._renderers.get(conn.renderer_id)
            self._renderers[conn.renderer_id] = conn
        if existing is not None:
            # A screen that reconnects before the old socket timed out. Drop the
            # stale one rather than leaving two claiming the same zone.
            try:
                await existing.socket.close()
            except Exception as exc:  # noqa: BLE001
                # Already gone is the normal case here, not a problem.
                log.debug("closing a superseded socket for %s: %s", conn.renderer_id, exc)
        await self._set_state(conn.renderer_id, "ready")
        await self.broadcast_presence()

    async def remove_renderer(self, renderer_id: UUID) -> None:
        async with self._lock:
            self._renderers.pop(renderer_id, None)
        await self._set_state(renderer_id, "unavailable")
        _end_session(renderer_id)
        await self.broadcast_presence()

    def is_connected(self, renderer_id: UUID) -> bool:
        return renderer_id in self._renderers

    async def disconnect(self, renderer_id: UUID) -> None:
        """Close a renderer's socket and forget it.

        Used when a room is removed: the credential it holds has just been
        deleted, so the socket is authenticating against nothing. Leaving it open
        would mean a screen that had been removed could still be sent to until it
        happened to reconnect.
        """
        async with self._lock:
            conn = self._renderers.pop(renderer_id, None)
        if conn is not None:
            try:
                await conn.socket.close()
            except Exception as exc:  # noqa: BLE001
                log.debug("closing a removed renderer's socket: %s", exc)
        await self.broadcast_presence()

    async def send(self, renderer_id: UUID, message: dict) -> bool:
        conn = self._renderers.get(renderer_id)
        if conn is None:
            return False
        try:
            await conn.socket.send_text(json.dumps(message))
            return True
        except Exception as exc:  # noqa: BLE001
            log.info("renderer %s send failed: %s", renderer_id, exc)
            await self.remove_renderer(renderer_id)
            return False

    async def add_watcher(self, socket: WebSocket) -> None:
        async with self._lock:
            self._watchers.add(socket)

    async def remove_watcher(self, socket: WebSocket) -> None:
        async with self._lock:
            self._watchers.discard(socket)

    async def broadcast_presence(self) -> None:
        payload = {
            "type": "presence",
            "renderers": [
                {"id": str(rid), "name": c.name, "state": c.last_state}
                for rid, c in self._renderers.items()
            ],
        }
        await self._fan_out(payload)

    async def broadcast_state(self, renderer_id: UUID, state: dict) -> None:
        conn = self._renderers.get(renderer_id)
        if conn:
            conn.last_state = state
        await self._fan_out({"type": "state", "renderer": str(renderer_id), "state": state})

    async def _fan_out(self, payload: dict) -> None:
        message = json.dumps(payload)
        dead = []
        for socket in list(self._watchers):
            try:
                await socket.send_text(message)
            except Exception:  # noqa: BLE001
                dead.append(socket)
        for socket in dead:
            await self.remove_watcher(socket)

    @staticmethod
    async def _set_state(renderer_id: UUID, state: str) -> None:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE renderers
                    SET state = CAST(:s AS renderer_state), last_seen_at = now()
                    WHERE id = :id
                    """
                ),
                {"s": state, "id": str(renderer_id)},
            )


hub = Hub()


# ── Pairing ─────────────────────────────────────────────────────────────────


class PairBegin(BaseModel):
    device_key: str = Field(min_length=4, max_length=200)
    device_name: str = Field(default="TV", max_length=80)


class PairClaim(BaseModel):
    code: str = Field(min_length=CODE_LENGTH, max_length=CODE_LENGTH + 2)
    name: str = Field(min_length=1, max_length=60)
    # Who the room is for. Omitted leaves it undecided, which reads as
    # admins-only: a screen paired this afternoon should not become playable by
    # the whole household before anyone has said it should be.
    audience: Literal["everyone", "admins", "selected"] | None = None
    grant_to: list[UUID] = Field(default_factory=list, max_length=200)


@router.post("/pair/begin")
async def pair_begin(body: PairBegin) -> dict:
    """Called by the screen. Deliberately unauthenticated — it has no credential yet.

    All this yields is a code that must be typed by someone already signed in, so
    an unauthenticated caller gains nothing but a string that expires.
    """
    code = _make_code()
    poll_token = secrets.token_urlsafe(24)

    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM pairing_codes WHERE expires_at < now()"))
        conn.execute(
            text(
                """
                INSERT INTO pairing_codes
                    (code, device_key, device_name, poll_hash, expires_at)
                VALUES (:code, :key, :name, :poll, :exp)
                """
            ),
            {
                "code": code,
                "key": body.device_key,
                "name": body.device_name,
                "poll": _hash(poll_token),
                "exp": datetime.now(UTC) + PAIRING_TTL,
            },
        )

    return {
        "code": code,
        "poll_token": poll_token,
        "expires_in": int(PAIRING_TTL.total_seconds()),
    }


@router.get("/pair/status")
async def pair_status(poll_token: str = Query(...)) -> dict:
    """Polled by the screen until somebody claims its code."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT p.claimed_at, p.renderer_id, p.expires_at, r.name, r.token_hash
                FROM pairing_codes p
                LEFT JOIN renderers r ON r.id = p.renderer_id
                WHERE p.poll_hash = :h
                """
            ),
            {"h": _hash(poll_token)},
        ).first()

    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown pairing attempt")

    claimed_at, renderer_id, expires_at, name, _token_hash = row
    if claimed_at is None:
        if expires_at < datetime.now(UTC):
            return {"status": "expired"}
        return {"status": "waiting"}

    # The device token is handed over exactly once, at the moment of claiming, and
    # the pairing row is consumed so a replayed poll cannot fetch it again.
    with get_engine().begin() as conn:
        token = conn.execute(
            text("SELECT device_token FROM pairing_handoff WHERE poll_hash = :h"),
            {"h": _hash(poll_token)},
        ).scalar_one_or_none()
        digest = _hash(poll_token)
        conn.execute(text("DELETE FROM pairing_handoff WHERE poll_hash = :h"), {"h": digest})
        conn.execute(text("DELETE FROM pairing_codes WHERE poll_hash = :h"), {"h": digest})

    if token is None:
        raise HTTPException(status.HTTP_410_GONE, "this pairing result was already collected")

    return {
        "status": "paired",
        "renderer_id": str(renderer_id),
        "name": name,
        "device_token": token,
    }


@router.post("/pair/claim")
async def pair_claim(body: PairClaim, user: CurrentUser = Depends(require_user)) -> dict:
    """Called from the phone by someone already signed in."""
    code = body.code.strip().upper().replace(" ", "")

    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT device_key, device_name, poll_hash, expires_at, claimed_at
                FROM pairing_codes WHERE code = :c
                """
            ),
            {"c": code},
        ).first()

        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no device is showing that code")
        device_key, _device_name, poll_hash, expires_at, claimed_at = row
        if claimed_at is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "that code was already used")
        if expires_at < datetime.now(UTC):
            raise HTTPException(status.HTTP_410_GONE, "that code has expired")

        device_token = secrets.token_urlsafe(32)

        # Re-pairing the same physical device updates it rather than creating a
        # second entry — device_key is the stable identity, not the row id.
        renderer_id = conn.execute(
            text(
                """
                INSERT INTO renderers (kind, name, device_key, state, token_hash,
                                       paired_at, paired_by, capabilities)
                VALUES ('tvapp', :name, :key, 'unavailable', :tok, now(), :uid,
                        '{"video": true, "audio": true, "seek": true, "volume": true}'::jsonb)
                ON CONFLICT (device_key) DO UPDATE
                SET name = EXCLUDED.name, token_hash = EXCLUDED.token_hash,
                    paired_at = now(), paired_by = EXCLUDED.paired_by
                RETURNING id
                """
            ),
            {"name": body.name, "key": device_key, "tok": _hash(device_token), "uid": str(user.id)},
        ).scalar_one()

        conn.execute(
            text(
                """
                INSERT INTO pairing_handoff (poll_hash, device_token)
                VALUES (:h, :t)
                ON CONFLICT (poll_hash) DO UPDATE SET device_token = EXCLUDED.device_token
                """
            ),
            {"h": poll_hash, "t": device_token},
        )
        conn.execute(
            text(
                """
                UPDATE pairing_codes SET claimed_at = now(), renderer_id = :r
                WHERE code = :c
                """
            ),
            {"r": str(renderer_id), "c": code},
        )

        # A paired screen is only useful as a zone, so create one with its name.
        zone_id = conn.execute(
            text(
                """
                INSERT INTO zones (name, renderer_id, audience)
                VALUES (:name, :rid, CAST(:aud AS audience))
                ON CONFLICT (name) DO UPDATE SET renderer_id = EXCLUDED.renderer_id
                RETURNING id
                """
            ),
            {"name": body.name, "rid": str(renderer_id), "aud": body.audience},
        ).scalar_one()

        # Same reconciliation as the audience screen performs, so a room answered
        # at pairing time and one answered later end up in identical states.
        if body.audience is not None:
            apply_audience(
                conn,
                place=ROOM,
                key=zone_id,
                grant_value=str(zone_id),
                body=AudienceUpdate(audience=body.audience, users=body.grant_to),
            )

    return {"renderer_id": str(renderer_id), "name": body.name}


# ── The command channel ─────────────────────────────────────────────────────


def _renderer_for_token(token: str) -> tuple[UUID, str] | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT id, name FROM renderers WHERE token_hash = :h"),
            {"h": _hash(token)},
        ).first()
    return (row[0], row[1]) if row else None


@router.websocket("/ws")
async def renderer_socket(websocket: WebSocket, token: str = Query(...)) -> None:
    """Held open by a screen. The server pushes commands; the screen reports state."""
    found = _renderer_for_token(token)
    if found is None:
        # 1008 policy violation: the socket is refused before it is accepted, so an
        # invalid device credential never reaches application code.
        await websocket.close(code=1008)
        return

    renderer_id, name = found
    await websocket.accept()
    conn = Connection(renderer_id=renderer_id, socket=websocket, name=name)
    await hub.add_renderer(conn)
    log.info("renderer connected: %s (%s)", name, renderer_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = message.get("type")
            if kind == "state":
                state = {
                    "state": message.get("state"),
                    "position_ms": message.get("position_ms"),
                    "duration_ms": message.get("duration_ms"),
                    "item_id": message.get("item_id"),
                }
                await hub.broadcast_state(renderer_id, state)
                _persist_position(renderer_id, state)
            elif kind == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.info("renderer %s disconnected: %s", renderer_id, exc)
    finally:
        await hub.remove_renderer(renderer_id)
        log.info("renderer gone: %s", name)


def _persist_position(renderer_id: UUID, state: dict) -> None:
    """Keep the durable session roughly in step with the screen.

    Roughly is the right word: this arrives every few seconds, and the session is
    the record of intent rather than a frame-accurate mirror.
    """
    position = state.get("position_ms")
    if position is None:
        return
    # coalesce, because the length arrives a moment after the position does —
    # the screen knows where it is before it knows how long the file runs, and
    # a later null must not erase a length already learned.
    duration = state.get("duration_ms")
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                UPDATE play_sessions s
                SET position_ms = :p,
                    duration_ms = coalesce(:d, s.duration_ms),
                    updated_at = now()
                FROM zones z
                WHERE s.zone_id = z.id AND z.renderer_id = :r
                """
            ),
            {
                "p": int(position),
                "d": int(duration) if duration else None,
                "r": str(renderer_id),
            },
        )


def _end_session(renderer_id: UUID) -> None:
    """A screen that has gone is not still playing.

    Closing the app on the television left the session marked `playing`, so the
    control tower showed a room happily playing to a screen that no longer
    existed — and pressing play there did nothing, because resuming asks the
    screen to carry on and there was no screen to ask.

    Idle rather than a new state: the schema has four and this is precisely what
    the fourth means — a room with nothing playing in it. The queue and the
    position are kept. What was being watched and how far in is exactly what
    somebody wants when the television comes back on; it is the claim that it is
    *playing* that was false.
    """
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                UPDATE play_sessions s
                SET state = 'idle', updated_at = now()
                FROM zones z
                WHERE s.zone_id = z.id AND z.renderer_id = :r
                  AND s.state IN ('playing', 'paused', 'buffering')
                """
            ),
            {"r": str(renderer_id)},
        )


@router.websocket("/watch")
async def watch_socket(websocket: WebSocket) -> None:
    """Held open by a controller (the phone) to see every renderer live."""
    await websocket.accept()
    await hub.add_watcher(websocket)
    await hub.broadcast_presence()
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove_watcher(websocket)


@router.get("")
async def list_renderers(_: CurrentUser = Depends(require_user)) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, kind::text, name, state::text, last_seen_at, paired_at
                FROM renderers ORDER BY name
                """
            )
        ).all()
    return [
        {
            "id": str(r[0]),
            "kind": r[1],
            "name": r[2],
            # The database records what we last knew; the hub knows what is
            # actually holding a socket right now.
            "state": "ready" if hub.is_connected(r[0]) else r[3],
            "connected": hub.is_connected(r[0]),
            "last_seen_at": r[4].isoformat() if r[4] else None,
            "paired_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]
