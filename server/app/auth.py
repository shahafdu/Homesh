"""Passkey (WebAuthn) authentication.

Primary factor by design: nothing phishable, nothing to leak (ARCHITECTURE.md §6).
There is no public registration path — the first user is created with a one-time
bootstrap code printed to the server log, and every user after that is invited by
an existing admin.
"""

from __future__ import annotations

import hashlib
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
    invite_code: str | None = None


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
    # Both are ignored when an invite is used: the inviter chose them, and
    # letting the invitee override would defeat the point of scoping an account
    # before it exists.
    handle: str = Field(default="", max_length=40)
    display_name: str = Field(default="", max_length=80)
    bootstrap_code: str | None = None
    invite_code: str | None = None


class CompleteBody(BaseModel):
    flow_id: str
    credential: dict
    device_label: str | None = None


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.get("/invite/{code}")
async def invite_details(code: str) -> dict:
    """What an invite is for, so the sign-up screen can greet by name.

    Unauthenticated by necessity, since the person has no account yet. It reveals
    only what they are about to be told anyway.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT handle, display_name FROM invites
                WHERE code = :c AND used_at IS NULL AND expires_at > now()
                """
            ),
            {"c": code},
        ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this invitation is not valid")
    return {"handle": row[0], "display_name": row[1]}


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
    handle, display_name = body.handle.strip(), body.display_name.strip()

    if body.invite_code:
        # The invite decides who this is. Registration happens on the invitee own
        # device, which is the whole point: a passkey belongs to the authenticator
        # that created it, so an admin enrolling someone else would enrol the
        # wrong fingerprint.
        with get_engine().connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT handle, display_name FROM invites
                    WHERE code = :c AND used_at IS NULL AND expires_at > now()
                    """
                ),
                {"c": body.invite_code},
            ).first()
        if row is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "this invitation is not valid")
        handle, display_name = row[0], row[1]
    elif first_user:
        # secrets.compare_digest to keep the check constant-time.
        if not _bootstrap_code or not body.bootstrap_code:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "bootstrap code required")
        if not secrets.compare_digest(body.bootstrap_code.strip().upper(), _bootstrap_code):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid bootstrap code")
    elif inviter is None or not inviter.is_admin:
        # No public registration, ever.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "an admin must invite new users")

    if not handle or not display_name:
        raise HTTPException(422, "a username and display name are required")

    with get_engine().connect() as conn:
        taken = conn.execute(
            text("SELECT 1 FROM users WHERE handle = :h"), {"h": handle}
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
        user_name=handle,
        user_display_name=display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Discoverable credentials give a genuinely usernameless login.
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )

    flow_id = _new_flow(
        options.challenge,
        "register",
        handle=handle,
        display_name=display_name,
        invite_code=body.invite_code,
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
                INSERT INTO users (handle, display_name, is_admin, is_owner,
                                   all_library, all_zones)
                VALUES (:h, :d, :admin, :owner, :admin, :admin)
                RETURNING id
                """
            ),
            # The account that sets the server up owns it, permanently. Everyone
            # else starts with nothing until granted something.
            {"h": flow.handle, "d": flow.display_name, "admin": is_first, "owner": is_first},
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

        # An invited account arrives already scoped. Applying the rules in the
        # same transaction as the account means it never exists, even briefly,
        # with the run of the house.
        if flow.invite_code:
            invite = conn.execute(
                text(
                    """
                    SELECT library_rules, zone_rules, all_library, all_zones
                    FROM invites
                    WHERE code = :c AND used_at IS NULL AND expires_at > now()
                    """
                ),
                {"c": flow.invite_code},
            ).first()
            if invite is None:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "this invitation is not valid")

            conn.execute(
                text("UPDATE users SET all_library = :a, all_zones = :z WHERE id = :u"),
                {"a": invite[2], "z": invite[3], "u": str(user_id)},
            )

            for prefix in invite[0] or []:
                conn.execute(
                    text(
                        """
                        INSERT INTO user_library_rules (user_id, path_prefix)
                        VALUES (:u, :p) ON CONFLICT DO NOTHING
                        """
                    ),
                    {"u": str(user_id), "p": prefix},
                )
            for zone_id in invite[1] or []:
                conn.execute(
                    text(
                        """
                        INSERT INTO user_zone_rules (user_id, zone_id)
                        VALUES (:u, :z) ON CONFLICT DO NOTHING
                        """
                    ),
                    {"u": str(user_id), "z": zone_id},
                )
            conn.execute(
                text("UPDATE invites SET used_at = now(), user_id = :u WHERE code = :c"),
                {"u": str(user_id), "c": flow.invite_code},
            )

        token = create_session(conn, user_id, body.device_label)
        audit(
            conn,
            "auth.register",
            user_id,
            {"handle": flow.handle, "first": is_first, "invited": bool(flow.invite_code)},
            ip,
        )

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


# ── Linking a device that cannot use a passkey ──────────────────────────────
#
# WebAuthn is only available in a secure context. A phone reaching this server
# over plain http at a LAN address has no navigator.credentials at all, so it can
# neither present a passkey nor enrol one — the account is unreachable from the
# device it is most wanted on.
#
# A link code closes that gap without weakening the model. It can only be issued
# from a session that is already signed in, so it proves the same thing a passkey
# proves: somebody already holding this account authorised this device. It is
# single use, expires in minutes, and is stored only as a hash.
#
# It is not a password. There is nothing to reuse, nothing to phish at leisure,
# and nothing in the database worth stealing.

LINK_TTL = timedelta(minutes=10)
LINK_CODE_LENGTH = 8

# Same alphabet as device pairing: no O/0, I/1 or S/5, because this gets read off
# one screen and typed into another.
LINK_ALPHABET = "ABCDEFGHJKLMNPQRTUVWXYZ2346789"

# Coarse per-address throttle. The codes carry ~39 bits over a ten-minute window,
# so guessing is already hopeless; this makes it loud as well as futile.
_link_attempts: dict[str, list[datetime]] = {}
MAX_LINK_ATTEMPTS = 10
LINK_ATTEMPT_WINDOW = timedelta(minutes=5)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _too_many_attempts(ip: str | None) -> bool:
    if ip is None:
        return False
    now = datetime.now(UTC)
    recent = [t for t in _link_attempts.get(ip, []) if now - t < LINK_ATTEMPT_WINDOW]
    recent.append(now)
    _link_attempts[ip] = recent
    return len(recent) > MAX_LINK_ATTEMPTS


class LinkClaim(BaseModel):
    code: str = Field(min_length=LINK_CODE_LENGTH, max_length=LINK_CODE_LENGTH + 4)
    device_label: str | None = Field(default=None, max_length=80)


@router.post("/devices/link")
async def create_device_link(user: CurrentUser = Depends(require_user)) -> dict:
    """Issue a code that signs this same account in on another device."""
    code = "".join(secrets.choice(LINK_ALPHABET) for _ in range(LINK_CODE_LENGTH))
    expires = datetime.now(UTC) + LINK_TTL

    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM device_links WHERE expires_at < now()"))
        conn.execute(
            text(
                """
                INSERT INTO device_links (code_hash, user_id, expires_at)
                VALUES (:h, :u, :e)
                """
            ),
            {"h": _hash_code(code), "u": str(user.id), "e": expires},
        )
        audit(conn, "auth.device_link.issued", user.id, {}, None)

    settings = get_settings()
    return {
        "code": code,
        "expires_in": int(LINK_TTL.total_seconds()),
        # The address to type on the other device. Configuration, read from the
        # environment — it is never written down in this repository.
        "address": settings.lan_base_url or settings.public_origin,
    }


@router.post("/devices/claim")
async def claim_device_link(
    body: LinkClaim, request: Request, response: Response
) -> dict:
    """Exchange a code for a session on this device."""
    ip = request.client.host if request.client else None
    if _too_many_attempts(ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts — wait a few minutes"
        )

    code = body.code.strip().upper().replace(" ", "")

    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT user_id FROM device_links
                WHERE code_hash = :h AND used_at IS NULL AND expires_at > now()
                """
            ),
            {"h": _hash_code(code)},
        ).first()
        if row is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "that code is not valid")

        user_id = row[0]
        # Marked used inside the same transaction as the session it creates, so
        # two devices racing on one code cannot both end up signed in.
        conn.execute(
            text("UPDATE device_links SET used_at = now() WHERE code_hash = :h"),
            {"h": _hash_code(code)},
        )
        token = create_session(conn, user_id, body.device_label)
        audit(conn, "auth.device_link.claimed", user_id, {"label": body.device_label}, ip)

        who = conn.execute(
            text("SELECT handle, display_name FROM users WHERE id = :u"), {"u": str(user_id)}
        ).first()

    set_session_cookie(response, token)
    return {"ok": True, "handle": who[0], "display_name": who[1]}
