"""The AI's own endpoints: what it is set up as, and what it has done.

Deliberately thin. Nothing here decides anything about spending or permission --
that is `ai.py`, so that the rules live in one place and every caller meets them,
including the ones that will exist later.
"""

from __future__ import annotations

import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from . import ai, aicatalog, aisettings
from .config import get_settings
from .db import get_engine
from .security import CurrentUser, audit, require_user
from .standby import is_standby
from .throttle import Limit, hit

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
async def ai_status(user: CurrentUser = Depends(require_user)) -> dict:
    """Whether the AI is available, and what it may cost.

    Any signed-in account may ask: the interface has to know whether to offer the
    feature at all, and the answer contains no secret -- whether a key is present
    but never the key.
    """
    answer = ai.status()
    features = {
        # Not offered on the standby: what it did there would be recorded
        # there, and the next restore from the PC would replace the record.
        "assistant": not is_standby() and ai.chat_available(),
        # The night worker that these three depend on does not exist yet; they
        # turn on when it does. Absent is the honest answer until then.
        "meaning_search": False,
        "voice": False,
        "faces": False,
    }
    if not user.is_admin:
        # What an ordinary member of the household needs: is it on, and is it
        # free. What has been spent is the owner's business.
        return {
            "configured": answer["configured"],
            "model": answer["model"],
            "free_model": answer["free_model"],
            "features": features,
        }
    return {**answer, "features": features}


@router.get("/history")
async def ai_history(
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = Depends(require_user),
) -> list[dict]:
    """What the AI has been asked to do, newest first.

    Administrators only, because it names who asked for what across the whole
    household. Every attempt is here, refusals included -- a refusal is the
    interesting entry, since it says the cap held.
    """
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "administrators only")
    return ai.history(limit)


def _admin(user: CurrentUser) -> None:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "administrators only")


def _settings_view() -> dict:
    """The settings, defaults filled in, and what the page needs beside them."""
    settings = get_settings()
    data = aisettings.load()
    with get_engine().connect() as conn:
        if data.get("provider_folders") is None:
            # Absent means "every folder open to everyone"; an explicit empty
            # list means none.
            data["provider_folders"] = [
                str(r[0]) for r in conn.execute(
                    text("SELECT id FROM sources WHERE audience = 'everyone' ORDER BY name")
                )
            ]
        spent = ai.spent_this_month(conn)
    gemini_key = bool(settings.gemini_api_key.strip())
    data["providers"] = {
        "openrouter": {"key": bool(settings.ai_api_key.strip())},
        "gemini": {
            "key": gemini_key,
            "usable": ai.provider_usable("gemini"),
            "why": None if (not gemini_key or settings.gemini_no_billing)
            else "GEMINI_NO_BILLING is not set to true",
        },
    }
    data["cap_micros"] = max(0, settings.ai_monthly_cap_micros)
    data["spent_micros"] = spent
    return data


@router.get("/settings")
async def get_settings_route(user: CurrentUser = Depends(require_user)) -> dict:
    _admin(user)
    return _settings_view()


@router.put("/settings")
async def put_settings(
    update: aisettings.SettingsUpdate, request: Request,
    user: CurrentUser = Depends(require_user),
) -> dict:
    _admin(user)
    cap = max(0, get_settings().ai_monthly_cap_micros)
    with get_engine().begin() as conn:
        try:
            aisettings.validate_models(update, conn, cap)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        changed = aisettings.save(conn, update, user.id)
        if changed:
            ip = request.client.host if request.client else None
            audit(conn, "ai.settings", user.id, {"changed": changed}, ip)
    return _settings_view()


@router.get("/models")
async def list_models(
    free: bool | None = None,
    tools: bool | None = None,
    q: str = Query("", max_length=80),
    user: CurrentUser = Depends(require_user),
) -> list[dict]:
    _admin(user)
    cap = max(0, get_settings().ai_monthly_cap_micros)
    usable = [p for p in ("openrouter", "gemini") if ai.provider_usable(p)]
    needle = q.strip().replace("%", "").replace("_", "")
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, provider, model, label, free, tools, context,
                       cooling_until, last_ok_at, last_error
                FROM ai_models
                WHERE gone_at IS NULL
                  AND provider = ANY(:usable)
                  AND (CAST(:free AS boolean) IS NULL OR free = :free)
                  AND (CAST(:tools AS boolean) IS NULL OR tools = :tools)
                  AND (:cap > 0 OR free)
                  AND (:q = '' OR label ILIKE :like OR model ILIKE :like)
                ORDER BY free DESC, label
                LIMIT 500
                """
            ),
            {"free": free, "tools": tools, "cap": cap, "q": needle, "usable": usable,
             "like": "%" + needle + "%"},
        ).mappings().all()
    return [
        {**dict(r),
         "cooling_until": r["cooling_until"].isoformat() if r["cooling_until"] else None,
         "last_ok_at": r["last_ok_at"].isoformat() if r["last_ok_at"] else None}
        for r in rows
    ]


@router.post("/models/refresh")
async def refresh_models(user: CurrentUser = Depends(require_user)) -> dict:
    _admin(user)
    return await aicatalog.refresh()


class TestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str


@router.post("/models/test")
async def test_model(body: TestRequest, user: CurrentUser = Depends(require_user)) -> dict:
    _admin(user)
    hit("ai-test", str(user.id), Limit(10, timedelta(minutes=5)))
    # Only a model from the catalogue: its stored price is what the spend gate
    # reads. An id typed by hand would fall back to judging "free" by its name.
    with get_engine().connect() as conn:
        known = conn.execute(
            text("SELECT 1 FROM ai_models WHERE id = :i"), {"i": body.id}
        ).scalar_one_or_none()
    if known is None:
        return {"ok": False, "why": "that model is not in the list this server knows"}
    started = time.monotonic()
    try:
        await ai.chat(
            task="test", purpose="test", user_id=user.id, only=body.id, max_tokens=5,
            messages=[{"role": "system", "content": "Reply with the single word: ready"},
                      {"role": "user", "content": "ready?"}],
        )
    except ai.AiError as exc:
        return {"ok": False, "why": str(exc)}
    return {"ok": True, "ms": int((time.monotonic() - started) * 1000)}


@router.get("/usage")
async def usage(user: CurrentUser = Depends(require_user)) -> dict:
    _admin(user)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT provider || ':' || model AS id, count(*) AS calls,
                       count(*) FILTER (WHERE outcome <> 'ok') AS errors,
                       count(*) FILTER (WHERE fallback) AS fallbacks
                FROM ai_calls WHERE at >= date_trunc('day', now())
                GROUP BY 1 ORDER BY 2 DESC
                """
            )
        ).mappings().all()
        spent = ai.spent_this_month(conn)
    return {"today": [dict(r) for r in rows], "month_spent_micros": spent}
