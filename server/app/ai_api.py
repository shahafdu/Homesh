"""The AI's own endpoints: what it is set up as, and what it has done.

Deliberately thin. Nothing here decides anything about spending or permission --
that is `ai.py`, so that the rules live in one place and every caller meets them,
including the ones that will exist later.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from . import ai
from .security import CurrentUser, require_user

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
async def ai_status(user: CurrentUser = Depends(require_user)) -> dict:
    """Whether the AI is available, and what it may cost.

    Any signed-in account may ask: the interface has to know whether to offer the
    feature at all, and the answer contains no secret -- whether a key is present
    but never the key.
    """
    answer = ai.status()
    if not user.is_admin:
        # What an ordinary member of the household needs: is it on, and is it
        # free. What has been spent is the owner's business.
        return {
            "configured": answer["configured"],
            "model": answer["model"],
            "free_model": answer["free_model"],
        }
    return answer


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
