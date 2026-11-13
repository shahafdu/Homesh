"""The mailbox's routes: for apps that post, for people, and for phones.

Three callers, three credentials, none interchangeable:

- an app beside Homesh posts with its own sending key, from NOTIFY_SENDERS;
- a person in the web app uses their session, as everywhere else;
- a phone reads with its device token, made from a single-use claim code.

`send`, `devices/claim` and `inbox` are the only routes in Homesh that take no
session. Each is authenticated by a key or a code, rate-limited, and able to do
exactly one thing; that is the whole of the exception, and nothing else may
join it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from . import notify, throttle
from .config import get_settings
from .db import get_engine
from .security import CurrentUser, audit, require_user

router = APIRouter(prefix="/api/notify", tags=["notify"])

CLAIM_TTL = timedelta(minutes=2)
MAX_PHONES = 10
INBOX_LIMIT = 50

# Wrong sending keys and unknown device tokens, per caller. A key carries at
# least 32 random characters, so this is about the cost of knocking rather
# than the odds of guessing.
WRONG_KEYS = throttle.Limit(20, timedelta(minutes=5))
WRONG_TOKENS = throttle.Limit(30, timedelta(minutes=5))

# What one app may post. A household app with something to say says it a few
# times an hour; sixty in five minutes is a loop gone wrong.
PER_SENDER = throttle.Limit(60, timedelta(minutes=5))

NEW_PHONES = throttle.Limit(10, timedelta(minutes=5))
TESTS = throttle.Limit(5, timedelta(minutes=5))


def _hash(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


def _bearer(request: Request) -> str | None:
    scheme, _, value = (request.headers.get("authorization") or "").partition(" ")
    value = value.strip()
    return value if scheme.lower() == "bearer" and value else None


def senders() -> dict[str, str]:
    from .main import notify_senders, phone_apps

    settings = get_settings()
    return notify_senders(settings.notify_senders, phone_apps(settings.phone_apps))


def _sender_for(key: str | None) -> str | None:
    """The configured name whose key this is.

    Every key is compared, in constant time, with no early exit: how long the
    answer takes says nothing about which key came close.
    """
    found = None
    given = (key or "").encode()
    for name, configured in senders().items():
        if hmac.compare_digest(given, configured.encode()):
            found = name
    return found if key else None


# ── For apps: the sending key ───────────────────────────────────────────────


class Post(BaseModel):
    # Forbidding extra fields is what stops a sender naming itself: there is no
    # "sender" field to fill, and an unknown one is refused rather than ignored.
    model_config = ConfigDict(extra="forbid")

    to: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    title: str = Field(min_length=1, max_length=80)
    body: str = Field(default="", max_length=300)
    path: str = "/"
    tag: str | None = Field(default=None, max_length=64)
    ttl_minutes: int = Field(default=1440, ge=1, le=10080)

    @field_validator("path")
    @classmethod
    def _inside_the_app(cls, value: str) -> str:
        if not notify.valid_path(value):
            raise ValueError("path must be a path inside the app, starting with a single /")
        return value


@router.post("/send")
def post_message(body: Post, request: Request) -> dict:
    sender = _sender_for(_bearer(request))
    if sender is None:
        throttle.hit("notify-key", throttle.caller(request), WRONG_KEYS)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown sending key")
    throttle.hit("notify-send", sender, PER_SENDER)

    queued = notify.send(
        sender=sender,
        to=body.to,
        title=body.title,
        body=body.body,
        path=body.path,
        tag=body.tag,
        ttl=timedelta(minutes=body.ttl_minutes),
    )
    return {"queued": queued}


# ── For a person: the session ───────────────────────────────────────────────


@router.get("/messages")
def my_messages(
    limit: int = Query(INBOX_LIMIT, ge=1, le=INBOX_LIMIT),
    user: CurrentUser = Depends(require_user),
) -> list[dict]:
    return notify.mine(user.id, limit)


class ReadUpTo(BaseModel):
    upto: int = Field(ge=0)


@router.post("/messages/read")
def read_messages(body: ReadUpTo, user: CurrentUser = Depends(require_user)) -> dict:
    return {"marked": notify.mark_read(user.id, body.upto)}


@router.get("/devices")
def my_phones(user: CurrentUser = Depends(require_user)) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, label, created_at, last_seen_at FROM notify_devices
                WHERE user_id = :u AND revoked_at IS NULL
                ORDER BY created_at
                """
            ),
            {"u": str(user.id)},
        ).all()
    return [
        {
            "id": str(r.id),
            "label": r.label,
            "created_at": r.created_at.isoformat(),
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        }
        for r in rows
    ]


class NewPhone(BaseModel):
    label: str = Field(min_length=1, max_length=60)

    @field_validator("label")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("a phone needs a name")
        return value


def _live_phones(conn, user_id) -> int:
    return conn.execute(
        text("SELECT count(*) FROM notify_devices WHERE user_id = :u AND revoked_at IS NULL"),
        {"u": str(user_id)},
    ).scalar_one()


@router.post("/devices")
def new_phone(body: NewPhone, user: CurrentUser = Depends(require_user)) -> dict:
    """A claim code the phone exchanges for its own token.

    The code, not the token, travels through the page: the page hands it to
    the phone app in a link, and a link is the sort of thing that ends up in a
    browser's history. Single use and two minutes make that harmless.
    """
    throttle.hit("notify-phones", str(user.id), NEW_PHONES)
    claim = secrets.token_urlsafe(32)
    with get_engine().begin() as conn:
        if _live_phones(conn, user.id) >= MAX_PHONES:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{MAX_PHONES} phones already have notifications on. Remove one first.",
            )
        conn.execute(
            text(
                """
                INSERT INTO notify_claims (code_hash, user_id, label, expires_at)
                VALUES (:h, :u, :label, :e)
                """
            ),
            {
                "h": _hash(claim),
                "u": str(user.id),
                "label": body.label,
                "e": datetime.now(UTC) + CLAIM_TTL,
            },
        )
    return {"claim": claim, "expires_in": int(CLAIM_TTL.total_seconds())}


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_phone(device_id: UUID, user: CurrentUser = Depends(require_user)) -> Response:
    with get_engine().begin() as conn:
        gone = conn.execute(
            text(
                """
                UPDATE notify_devices SET revoked_at = now()
                WHERE id = :id AND user_id = :u AND revoked_at IS NULL
                """
            ),
            {"id": str(device_id), "u": str(user.id)},
        ).rowcount
    if not gone:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such phone")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/test")
def send_test(user: CurrentUser = Depends(require_user)) -> dict:
    throttle.hit("notify-test", str(user.id), TESTS)
    queued = notify.send(
        sender=notify.HOMESH, to=user.id, title="Notifications are working", path="/"
    )
    return {"queued": queued}


# ── For a phone: the claim, then the device token ───────────────────────────


class Claim(BaseModel):
    claim: str = Field(min_length=16, max_length=128)


@router.post("/devices/claim")
def claim_phone(body: Claim, request: Request) -> dict:
    throttle.hit("notify-claim", throttle.caller(request), throttle.CODES)
    ip = request.client.host if request.client else None

    with get_engine().begin() as conn:
        # Found and spent in one statement, so a code works once even when two
        # phones race for it.
        row = conn.execute(
            text(
                """
                UPDATE notify_claims SET used_at = now()
                WHERE code_hash = :h AND used_at IS NULL AND expires_at > now()
                RETURNING user_id, label
                """
            ),
            {"h": _hash(body.claim)},
        ).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "that code is not valid")
        user_id, label = row
        if _live_phones(conn, user_id) >= MAX_PHONES:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{MAX_PHONES} phones already have notifications on. Remove one first.",
            )

        token = secrets.token_urlsafe(32)
        device_id = conn.execute(
            text(
                """
                INSERT INTO notify_devices (user_id, label, token_hash)
                VALUES (:u, :label, :h) RETURNING id
                """
            ),
            {"u": str(user_id), "label": label, "h": _hash(token)},
        ).scalar_one()
        audit(conn, "notify.device", user_id, {"device": str(device_id)}, ip)
        who = conn.execute(
            text("SELECT display_name FROM users WHERE id = :u"), {"u": str(user_id)}
        ).scalar_one()

    # The only time the token is ever shown. The cursor starts the phone at
    # now, so turning notifications on does not replay a week of messages.
    return {"token": token, "cursor": notify.newest_id(user_id), "who": who}


def _phone_for(token: str) -> tuple[UUID, UUID] | None:
    """(device id, user id) for a live token, noting when it was last seen."""
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, user_id FROM notify_devices "
                "WHERE token_hash = :h AND revoked_at IS NULL"
            ),
            {"h": _hash(token)},
        ).first()
        if row is None:
            return None
        # At most once a minute: a phone holding a long poll open asks every
        # fifty seconds, and a row rewritten that often is churn for nothing.
        conn.execute(
            text(
                """
                UPDATE notify_devices SET last_seen_at = now()
                WHERE id = :id
                  AND (last_seen_at IS NULL OR last_seen_at < now() - interval '1 minute')
                """
            ),
            {"id": str(row.id)},
        )
    return row.id, row.user_id


@router.get("/inbox")
async def phone_inbox(
    request: Request,
    after: int = Query(0, ge=0),
    wait: int = Query(0, ge=0, le=50),
) -> dict:
    """What is new for this phone's person. Optionally held open until there is.

    Async so that a held request costs a coroutine rather than one of the
    threadpool's few threads; the database work goes to a thread, and nothing
    holds a connection while waiting.
    """
    from .standby import role

    token = _bearer(request)
    found = await asyncio.to_thread(_phone_for, token) if token else None
    if found is None:
        throttle.hit("notify-token", throttle.caller(request), WRONG_TOKENS)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "this phone is not registered")
    _, user_id = found

    mark = notify.generation(user_id)
    messages = await asyncio.to_thread(notify.inbox, user_id, after, INBOX_LIMIT)
    if not messages and wait > 0:
        await notify.wait_for(user_id, wait, since=mark)
        messages = await asyncio.to_thread(notify.inbox, user_id, after, INBOX_LIMIT)

    return {
        # Ids are per machine, so the phone keeps a cursor for each.
        "role": role(),
        "cursor": messages[-1]["id"] if messages else after,
        "messages": messages,
    }
