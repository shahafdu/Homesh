"""Everything an administrator sets about the AI, kept in one database row.

No secret lives here: keys stay in `.env`. The shape is validated on the way in
so that what the router reads back is always something it can use.
"""

from __future__ import annotations

import json
import re
from datetime import time
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from .db import get_engine

TASKS = ("chat", "bulk", "translate")
MAX_PER_TASK = 8
_HHMM = re.compile(r"^\d\d:\d\d$")

DEFAULTS: dict = {
    "timezone": "UTC",
    "night": {"start": "01:00", "end": "07:00"},
    "paused": False,
    "tasks": {"chat": [], "bulk": [], "translate": []},
    "questions_per_day": 100,
    "bulk_batches_per_night": 100,
}


def _hhmm(value: str) -> str:
    if not isinstance(value, str) or not _HHMM.match(value):
        raise ValueError("times look like 01:30")
    try:
        time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("that is not a time of day") from exc
    return value


class Night(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str
    end: str

    _s = field_validator("start")(_hhmm)
    _e = field_validator("end")(_hhmm)


class Tasks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: list[str] | None = Field(default=None, max_length=MAX_PER_TASK)
    bulk: list[str] | None = Field(default=None, max_length=MAX_PER_TASK)
    translate: list[str] | None = Field(default=None, max_length=MAX_PER_TASK)


class SettingsUpdate(BaseModel):
    """Any subset of the settings. Unknown keys are refused."""

    model_config = ConfigDict(extra="forbid")
    timezone: str | None = None
    night: Night | None = None
    paused: bool | None = None
    tasks: Tasks | None = None
    provider_folders: list[UUID] | None = None
    questions_per_day: int | None = Field(default=None, ge=1, le=1000)
    bulk_batches_per_night: int | None = Field(default=None, ge=0, le=1000)

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except Exception as exc:  # noqa: BLE001 - any failure to load means unknown
            raise ValueError("unknown time zone") from exc
        return value


def load() -> dict:
    """The stored settings with defaults filled in."""
    with get_engine().connect() as conn:
        stored = conn.execute(text("SELECT data FROM ai_settings")).scalar_one_or_none() or {}
    return merged(stored)


def merged(stored: dict) -> dict:
    out = json.loads(json.dumps(DEFAULTS))
    for key, value in stored.items():
        if key == "tasks" and isinstance(value, dict):
            out["tasks"].update({k: v for k, v in value.items() if k in TASKS})
        elif key == "night" and isinstance(value, dict):
            out["night"].update(value)
        else:
            out[key] = value
    return out


def task_ids(task: str) -> list[str]:
    ids = load()["tasks"].get(task) or []
    return [i for i in ids if isinstance(i, str)]


def validate_models(update: SettingsUpdate, conn, cap_micros: int) -> None:
    """Check what the pydantic shape cannot: that ids exist, and what they cost.

    Only a model being added to a list is judged. One already there was accepted
    when it was chosen; judging it again once it has started to cost money would
    refuse every change to that list -- removing it included, when there are
    two. The spend gate still judges it at every call.
    """
    if update.tasks is not None:
        stored = merged(
            conn.execute(text("SELECT data FROM ai_settings")).scalar_one_or_none() or {}
        )
        for task in TASKS:
            ids = getattr(update.tasks, task)
            if ids is None:
                continue
            if len(set(ids)) != len(ids):
                raise ValueError(f"{task}: a model is listed twice")
            kept = set(stored["tasks"].get(task) or [])
            for mid in ids:
                if mid in kept:
                    continue
                row = conn.execute(
                    text("SELECT free, tools FROM ai_models WHERE id = :i"), {"i": mid}
                ).one_or_none()
                if row is None:
                    raise ValueError("that model is not in the list this server knows")
                if task == "chat" and not row.tools:
                    raise ValueError("a model that cannot use tools cannot answer questions")
                if not row.free and cap_micros <= 0:
                    raise ValueError(
                        "paid models cannot be chosen while no monthly limit is set"
                    )
    if update.provider_folders is not None:
        for sid in update.provider_folders:
            if conn.execute(
                text("SELECT 1 FROM sources WHERE id = :i"), {"i": str(sid)}
            ).scalar_one_or_none() is None:
                raise ValueError("that folder does not exist")


def save(conn, update: SettingsUpdate, user_id: UUID) -> list[str]:
    """Merge the fields that were sent. Returns their names."""
    sent = update.model_dump(exclude_unset=True, exclude_none=True, mode="json")
    if not sent:
        return []
    row = conn.execute(text("SELECT data FROM ai_settings FOR UPDATE")).scalar_one_or_none()
    data = dict(row or {})
    for key, value in sent.items():
        if key == "tasks":
            data["tasks"] = {**(data.get("tasks") or {}), **value}
        else:
            data[key] = value
    conn.execute(
        text(
            "INSERT INTO ai_settings (only_row, data, updated_by) VALUES (TRUE, "
            "CAST(:d AS jsonb), :u) ON CONFLICT (only_row) DO UPDATE SET "
            "data = EXCLUDED.data, updated_at = now(), updated_by = EXCLUDED.updated_by"
        ),
        {"d": json.dumps(data), "u": str(user_id)},
    )
    return sorted(sent)


