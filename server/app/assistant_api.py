"""The assistant's record, read over HTTP.

A person reads and clears their own questions. An administrator reads what the
assistant changed across the household, which never includes what was asked. The
storing and the queries are in `aihistory.py`; what is decided here is only who
may ask for which.

None of the writes here is on the standby's list, so the standby refuses them.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from . import aihistory
from .security import CurrentUser, require_user

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


def _admin(user: CurrentUser) -> None:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "administrators only")


@router.get("/history")
async def my_history(
    q: str = Query("", max_length=200),
    before: datetime | None = None,
    limit: int = Query(30, ge=1, le=100),
    user: CurrentUser = Depends(require_user),
) -> list[dict]:
    """The caller's own questions, newest first."""
    return aihistory.my_history(user.id, q, before, limit)


@router.delete("/history/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_one(question_id: UUID, user: CurrentUser = Depends(require_user)) -> Response:
    # Somebody else's question answers as one that does not exist.
    if not aihistory.clear(user.id, question_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such question")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/history")
async def clear_all(user: CurrentUser = Depends(require_user)) -> dict:
    return {"cleared": aihistory.clear(user.id, None)}


@router.get("/audit")
async def audit(
    q: str = Query("", max_length=200),
    who: UUID | None = Query(None, alias="user"),
    before: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(require_user),
) -> list[dict]:
    """What the assistant changed, or was stopped from changing, newest first."""
    _admin(user)
    return aihistory.audit(q, who, before, limit)


@router.get("/audit/export")
async def export_audit(
    since: datetime,
    until: datetime,
    user: CurrentUser = Depends(require_user),
) -> StreamingResponse:
    """The same record with the requests themselves, as a file to keep."""
    _admin(user)
    since, until = aihistory.aware(since), aihistory.aware(until)
    if until < since:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "the end of the range is before its start"
        )
    if until - since > aihistory.MAX_EXPORT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"one export covers at most {aihistory.MAX_EXPORT.days} days",
        )
    name = f"homesh-assistant-{since:%Y%m%d}-{until:%Y%m%d}.ndjson"
    return StreamingResponse(
        aihistory.export(since, until),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
