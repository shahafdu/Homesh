"""Passkey (WebAuthn) authentication.

Primary factor by design: nothing phishable, nothing to leak (ARCHITECTURE.md §6).
There is no public registration path — the first user is created with a one-time
bootstrap code printed to the server log, and every user after that is invited by
an existing admin.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .config import get_settings
from .db import get_engine
from .security import (
    SESSION_COOKIE,
    CurrentUser,
    audit,
    clear_session_cookie,
    create_session,
    optional_user,
    require_user,
    revoke_session,
    set_session_cookie,
)

log = logging.getLogger("homesh.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

# One-time code for creating the very first account. Regenerated on every restart,
# held only in memory, and cleared as soon as a user exists.
_bootstrap_code: str | None = None


# ── Challenge store ─────────────────────────────────────────────────────────
# WebAuthn is a two-step handshake, so the challenge issued in /begin must be
# recalled in /complete. In-memory with a short TTL: these are single-use and
# worthless after ~5 minutes, so they do not warrant a database round trip.

CHALLENGE_TTL = timedelta(minutes=5)


@dataclass
class _Flow:
    challenge: bytes
    purpose: str
    expires_at: datetime
    handle: str | None = None
    display_name: str | None = None


_flows: dict[str, _Flow] = {}


def _new_flow(challenge: bytes, purpose: str, **extra) -> str:
    _prune_flows()
    flow_id = secrets.token_urlsafe(16)
    _flows[flow_id] = _Flow(
        challenge=challenge,
        purpose=purpose,
        expires_at=datetime.now(UTC) + CHALLENGE_TTL,
        **extra,
    )
    return flow_id


def _take_flow(flow_id: str, purpose: str) -> _Flow:
    _prune_flows()
    flow = _flows.pop(flow_id, None)   # single use
    if flow is None or flow.purpose != purpose:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "challenge expired or invalid")
    return flow


def _prune_flows() -> None:
    now = datetime.now(UTC)
    for k in [k for k, v in _flows.items() if v.expires_at < now]:
        _flows.pop(k, None)


# ── Bootstrap ───────────────────────────────────────────────────────────────


def user_count() -> int:
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT count(*) FROM users")).scalar_one()


def ensure_bootstrap_code() -> str | None:
    """Issue a first-run code if the instance has no users yet."""
    global _bootstrap_code
    if user_count() > 0:
        _bootstrap_code = None
        return None
    _bootstrap_code = secrets.token_hex(4).upper()
    return _bootstrap_code


# ── Schemas ─────────────────────────────────────────────────────────────────


class RegisterBegin(BaseModel):
    handle: str = Field(min_length=2, max_length=40, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    bootstrap_code: str | None = None


class CompleteBody(BaseModel):
    flow_id: str
    credential: dict
    device_label: str | None = None


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.get("/state")
async def auth_state(user: CurrentUser | None = Depends(optional_user)) -> dict:
    """What the login screen needs to decide which form to show."""
    return {
        "has_users": user_count() > 0,
        "authenticated": user is not None,
        "user": None
        if user is None
        else {"handle": user.handle, "display_name": user.display_name, "is_admin": user.is_admin},
    }


@router.get("/me")
async def me(user: CurrentUser = Depends(require_user)) -> dict:
    return {
        "id": str(user.id),
        "handle": user.handle,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }


@router.post("/register/begin")
async def register_begin(
    body: RegisterBegin,
    inviter: CurrentUser | None = Depends(optional_user),
) -> dict:
    settings = get_settings()
    first_user = user_count() == 0

    if first_user:
        # secrets.compare_digest to keep the check constant-time.
        if not _bootstrap_code or not body.bootstrap_code:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "bootstrap code required")
        if not secrets.compare_digest(body.bootstrap_code.strip().upper(), _bootstrap_code):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid bootstrap code")
    elif inviter is None or not inviter.is_admin:
        # No public registration, ever.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "an admin must invite new users")

    with get_engine().connect() as conn:
        taken = conn.execute(
            text("SELECT 1 FROM users WHERE handle = :h"), {"h": body.handle}
        ).first()
    if taken:
        raise HTTPException(status.HTTP_409_CONFLICT, "handle already taken")

    # The user handle is not yet a row, so mint the id here and carry it through.
    import uuid

    user_id = uuid.uuid4()

    options = generate_registration_options(
        rp_id=settings.rp_id,
        rp_name=settings.rp_name,
        user_id=user_id.bytes,
        user_name=body.handle,
        user_display_name=body.display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Discoverable credentials give a genuinely usernameless login.
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )

    flow_id = _new_flow(
        options.challenge,
        "register",
        handle=body.handle,
        display_name=body.display_name,
    )
    return {
        "flow_id": flow_id,
        "user_id": str(user_id),
        "options": json.loads(options_to_json(options)),
    }


@router.post("/register/complete")
async def register_complete(body: CompleteBody, request: Request, response: Response) -> dict:
    settings = get_settings()
    flow = _take_flow(body.flow_id, "register")

    try:
        verified = verify_registration_response(
            credential=body.credential,
            expected_challenge=flow.challenge,
            expected_origin=settings.public_origin,
            expected_rp_id=settings.rp_id,
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001 - any failure is a rejected registration
        log.warning("registration verification failed: %s", exc)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "registration could not be verified"
        ) from exc

    ip = request.client.host if request.client else None

    with get_engine().begin() as conn:
        # Re-check inside the transaction: the first-user race must not create two admins.
        is_first = conn.execute(text("SELECT count(*) FROM users")).scalar_one() == 0

        user_id = conn.execute(
            text(
                """
                INSERT INTO users (handle, display_name, is_admin)
                VALUES (:h, :d, :admin)
                RETURNING id
                """
            ),
            {"h": flow.handle, "d": flow.display_name, "admin": is_first},
        ).scalar_one()

        conn.execute(
            text(
                """
                INSERT INTO credentials (user_id, credential_id, public_key, sign_count, nickname)
                VALUES (:uid, :cid, :pk, :sc, :nick)
                """
            ),
            {
                "uid": str(user_id),
                "cid": verified.credential_id,
                "pk": verified.credential_public_key,
                "sc": verified.sign_count,
                "nick": body.device_label,
            },
        )

        token = create_session(conn, user_id, body.device_label)
        audit(conn, "auth.register", user_id, {"handle": flow.handle, "first": is_first}, ip)

    if is_first:
        global _bootstrap_code
        _bootstrap_code = None
        log.info("first user created; bootstrap code retired")

    set_session_cookie(response, token)
    return {"ok": True, "handle": flow.handle, "is_admin": is_first}


@router.post("/login/begin")
async def login_begin() -> dict:
    settings = get_settings()
    # No allow_credentials: the authenticator offers whichever passkey it holds,
    # so the user never types a username.
    options = generate_authentication_options(
        rp_id=settings.rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    flow_id = _new_flow(options.challenge, "login")
    return {"flow_id": flow_id, "options": json.loads(options_to_json(options))}


@router.post("/login/complete")
async def login_complete(body: CompleteBody, request: Request, response: Response) -> dict:
    settings = get_settings()
    flow = _take_flow(body.flow_id, "login")
    ip = request.client.host if request.client else None

    raw_id = body.credential.get("rawId") or body.credential.get("id")
    if not raw_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed credential")

    from webauthn.helpers import base64url_to_bytes

    try:
        credential_id = base64url_to_bytes(raw_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed credential id") from exc

    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT c.id, c.user_id, c.public_key, c.sign_count
                FROM credentials c
                WHERE c.credential_id = :cid
                """
            ),
            {"cid": credential_id},
        ).first()

    if row is None:
        # Deliberately vague: do not confirm whether a credential exists.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication failed")

    cred_row_id, user_id, public_key, sign_count = row

    try:
        verified = verify_authentication_response(
            credential=body.credential,
            expected_challenge=flow.challenge,
            expected_origin=settings.public_origin,
            expected_rp_id=settings.rp_id,
            credential_public_key=bytes(public_key),
            credential_current_sign_count=sign_count,
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("authentication verification failed: %s", exc)
        with get_engine().begin() as conn:
            audit(conn, "auth.login.failed", user_id, {}, ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication failed") from exc

    with get_engine().begin() as conn:
        # A sign count that fails to advance can indicate a cloned authenticator.
        # Recorded rather than enforced: several platform authenticators always report 0.
        if verified.new_sign_count and verified.new_sign_count <= sign_count:
            audit(conn, "auth.signcount.anomaly", user_id,
                  {"stored": sign_count, "presented": verified.new_sign_count}, ip)

        conn.execute(
            text(
                "UPDATE credentials SET sign_count = :sc, last_used_at = now() WHERE id = :id"
            ),
            {"sc": verified.new_sign_count, "id": str(cred_row_id)},
        )
        token = create_session(conn, user_id, body.device_label)
        audit(conn, "auth.login", user_id, {}, ip)

    set_session_cookie(response, token)
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with get_engine().begin() as conn:
            revoke_session(conn, token)
    clear_session_cookie(response)
    return {"ok": True}
