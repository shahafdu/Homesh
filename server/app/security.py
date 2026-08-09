"""Sessions, cookies and the current-user dependency.

Session tokens are opaque random strings. Only their SHA-256 hash is stored, so a
database dump does not yield usable sessions (ARCHITECTURE.md §6).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .config import get_settings
from .db import get_engine

SESSION_COOKIE = "hearth_session"
SESSION_TTL = timedelta(days=30)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    handle: str
    display_name: str
    is_admin: bool


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def create_session(conn: Connection, user_id: UUID, device_label: str | None) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        text(
            """
            INSERT INTO auth_sessions (user_id, refresh_hash, device_label, expires_at)
            VALUES (:uid, :h, :label, :exp)
            """
        ),
        {
            "uid": str(user_id),
            "h": _hash(token),
            "label": device_label,
            "exp": datetime.now(UTC) + SESSION_TTL,
        },
    )
    return token


def revoke_session(conn: Connection, token: str) -> None:
    conn.execute(
        text("UPDATE auth_sessions SET revoked_at = now() WHERE refresh_hash = :h"),
        {"h": _hash(token)},
    )


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        # Strict would break the redirect back from an external identity flow later;
        # Lax is the right balance for a cookie that is never used cross-site.
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _lookup(token: str) -> CurrentUser | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT u.id, u.handle, u.display_name, u.is_admin
                FROM auth_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.refresh_hash = :h
                  AND s.revoked_at IS NULL
                  AND s.expires_at > now()
                """
            ),
            {"h": _hash(token)},
        ).first()

    if row is None:
        return None
    return CurrentUser(id=row[0], handle=row[1], display_name=row[2], is_admin=row[3])


async def optional_user(request: Request) -> CurrentUser | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return _lookup(token)


async def require_user(
    user: CurrentUser | None = Depends(optional_user),
) -> CurrentUser:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    return user


def audit(conn: Connection, event: str, user_id: UUID | None, detail: dict, ip: str | None) -> None:
    """Append to the audit log. Every auth event goes through here (§6)."""
    import json

    conn.execute(
        text(
            """
            INSERT INTO audit_log (user_id, event, detail, ip)
            VALUES (:uid, :ev, CAST(:d AS jsonb), CAST(:ip AS inet))
            """
        ),
        {
            "uid": str(user_id) if user_id else None,
            "ev": event,
            "d": json.dumps(detail),
            "ip": ip,
        },
    )
