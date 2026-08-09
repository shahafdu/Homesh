"""User interface preferences.

Values are validated against a whitelist rather than stored as free-form JSON: the
prefs blob is written straight from the client, so an unchecked write would let any
authenticated session stuff arbitrary data into the users table.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from .db import get_engine
from .security import CurrentUser, require_user

log = logging.getLogger("hearth.prefs")
router = APIRouter(prefix="/api/prefs", tags=["prefs"])

# key -> (allowed values, default). Anything not listed here is rejected.
ALLOWED: dict[str, tuple[frozenset[str], str]] = {
    # Colour direction. All three ship; the user picks (see docs mockups).
    "palette": (frozenset({"warm", "studio", "daylight"}), "warm"),
    # "auto" follows the operating system.
    "appearance": (frozenset({"auto", "light", "dark"}), "auto"),
    # How a folder is listed. Tiles come in two sizes because one size cannot serve
    # both a 12-photo album and a folder of two thousand tracks.
    "view": (frozenset({"details", "columns", "tiles-small", "tiles-large"}), "details"),
}

DEFAULTS = {k: default for k, (_, default) in ALLOWED.items()}


class PrefsUpdate(BaseModel):
    palette: str | None = None
    appearance: str | None = None
    view: str | None = None


def _merged(raw: dict) -> dict:
    """Defaults overlaid with stored values, dropping anything no longer valid."""
    out = dict(DEFAULTS)
    for key, value in (raw or {}).items():
        allowed = ALLOWED.get(key)
        if allowed and value in allowed[0]:
            out[key] = value
    return out


@router.get("")
async def get_prefs(user: CurrentUser = Depends(require_user)) -> dict:
    with get_engine().connect() as conn:
        raw = conn.execute(
            text("SELECT prefs FROM users WHERE id = :id"), {"id": str(user.id)}
        ).scalar_one()
    return _merged(raw)


@router.put("")
async def put_prefs(body: PrefsUpdate, user: CurrentUser = Depends(require_user)) -> dict:
    updates = body.model_dump(exclude_none=True)

    for key, value in updates.items():
        allowed, _ = ALLOWED[key]  # key set is fixed by the model
        if value not in allowed:
            # Literal 422: the Starlette constant was renamed between versions.
            raise HTTPException(422, f"{key} must be one of {sorted(allowed)}")

    if updates:
        with get_engine().begin() as conn:
            # Merge rather than replace, so a client that knows about one key does
            # not wipe preferences set by a newer client.
            conn.execute(
                text("UPDATE users SET prefs = prefs || CAST(:patch AS jsonb) WHERE id = :id"),
                {"patch": json.dumps(updates), "id": str(user.id)},
            )

    return await get_prefs(user)
