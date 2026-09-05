"""Short-lived signed media URLs.

No media URL is ever guessable or permanent (ARCHITECTURE.md §6). A token binds an
item to a user and an expiry, so a URL that leaks is useless within minutes and
useless immediately to anyone else.

Cast receivers and TV apps fetch media themselves, without our cookies, which is why
authorisation has to travel in the URL rather than in a session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from uuid import UUID

from .config import get_settings


class TokenError(Exception):
    """Malformed, tampered with, or expired."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key() -> bytes:
    secret = get_settings().secret_key
    if not secret:
        raise TokenError("SECRET_KEY is not configured")
    return base64.urlsafe_b64decode(secret)


@dataclass(frozen=True)
class MediaClaim:
    item_id: UUID
    user_id: UUID
    purpose: str  # "stream" or "thumb"
    expires_at: int


def mint(item_id: UUID, user_id: UUID, purpose: str = "stream", ttl: int | None = None) -> str:
    settings = get_settings()
    seconds = ttl if ttl is not None else settings.media_url_ttl_minutes * 60

    payload = json.dumps(
        {
            "i": str(item_id),
            "u": str(user_id),
            "p": purpose,
            "e": int(time.time()) + seconds,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()

    sig = hmac.new(_key(), payload, hashlib.sha256).digest()
    return f"{_b64e(payload)}.{_b64e(sig)}"


def verify(token: str, expected_purpose: str = "stream") -> MediaClaim:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64d(payload_b64)
        sig = _b64d(sig_b64)
    except Exception as exc:  # noqa: BLE001
        raise TokenError("malformed token") from exc

    expected = hmac.new(_key(), payload, hashlib.sha256).digest()
    # Constant-time: a timing side channel here would leak the signature.
    if not hmac.compare_digest(sig, expected):
        raise TokenError("bad signature")

    try:
        data = json.loads(payload)
        claim = MediaClaim(
            item_id=UUID(data["i"]),
            user_id=UUID(data["u"]),
            purpose=data["p"],
            expires_at=int(data["e"]),
        )
    except Exception as exc:  # noqa: BLE001
        raise TokenError("malformed payload") from exc

    if claim.purpose != expected_purpose:
        # A thumbnail token must not unlock the full-resolution original.
        raise TokenError("wrong purpose")
    if claim.expires_at < time.time():
        raise TokenError("expired")

    return claim
