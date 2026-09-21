"""Asking a model something, on terms the platform enforces.

The provider is configuration. OpenRouter first, because it is one key for many
models including free ones, and because it reports what each call cost so the
cap can be arithmetic rather than a guess. Nothing here is specific to a vendor
beyond `_openrouter`.

**The key is configuration and never leaves the machine.** `AI_API_KEY` in
`.env`; it is never returned by an endpoint, never logged, and never written into
a table. `status()` says whether a key is present, not what it is.

**Spending is refused by the platform, not avoided by the code.** Raised as the
load-bearing requirement it is: software that calls a paid API on a schedule is
software that can bill on its own when it has a bug. So:

- The default model is a free one, and a **paid model is refused outright while
  the cap is zero** -- which it is until somebody sets it. There is no code path
  that spends money on a fresh install.
- Above zero, the month's spend is summed from `ai_calls` and checked *before*
  the call. A budget alert reports after the fact; this refuses beforehand.
- Spending is granted per account (`users.may_spend_on_ai`), so an ordinary
  member of the household can use the free tier and cannot run up a bill.

**Every attempt is recorded**, refusals included, in `ai_calls` -- the history
that is readable in the app. What is recorded is what happened: who, what for,
which model, how many tokens, how much, how long. Never the question or the
answer; this is an account of the work, not a copy of the content.

**It holds no privileges.** Nothing here reads the catalog or writes to it. A
caller passes the text it wants sent and gets text back; whatever acts on that
answer does so through the same authenticated API a person uses, as that person.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from uuid import UUID

import httpx
from sqlalchemy import text

from .config import get_settings
from .db import get_engine

log = logging.getLogger("homesh.ai")

# A model whose name ends in this is free on OpenRouter. The distinction matters
# because it is the one the cap is built on.
FREE_SUFFIX = ":free"

# Long enough for a slow free-tier queue, short enough that nothing waits on it
# for ever. A tagging pass is many small calls, not one long one.
TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class AiError(Exception):
    """Asking failed. The message is meant to be read by a person."""


class Refused(AiError):
    """Not attempted: no provider, no permission, or no allowance left."""


@dataclass(frozen=True)
class Answer:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_micros: int


def free_model(model: str) -> bool:
    return model.strip().endswith(FREE_SUFFIX)


def configured() -> bool:
    settings = get_settings()
    return bool(settings.ai_provider.strip() and settings.ai_api_key.strip())


def spent_this_month(conn) -> int:
    return int(
        conn.execute(
            text(
                "SELECT COALESCE(sum(cost_micros), 0) FROM ai_calls "
                "WHERE at >= date_trunc('month', now())"
            )
        ).scalar_one()
    )


def status() -> dict:
    """What the interface may say about the AI. Never the key."""
    settings = get_settings()
    with get_engine().connect() as conn:
        spent = spent_this_month(conn) if _table_exists(conn) else 0
    cap = max(0, settings.ai_monthly_cap_micros)
    return {
        "configured": configured(),
        "provider": settings.ai_provider.strip() or None,
        "model": settings.ai_model.strip(),
        "free_model": free_model(settings.ai_model),
        # In millionths, as stored. The interface decides how to say it.
        "monthly_cap_micros": cap,
        "spent_this_month_micros": spent,
        # The plain consequence, so nothing has to infer it: with no cap, a paid
        # model cannot be used at all.
        "paid_models_allowed": cap > 0,
    }


def _table_exists(conn) -> bool:
    return bool(
        conn.execute(text("SELECT to_regclass('public.ai_calls') IS NOT NULL")).scalar_one()
    )


def _record(
    *, user_id: UUID | None, purpose: str, model: str, outcome: str,
    detail: str | None = None, prompt_tokens: int = 0, completion_tokens: int = 0,
    cost_micros: int = 0, duration_ms: int = 0,
) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO ai_calls (user_id, purpose, provider, model, outcome, detail,
                                      prompt_tokens, completion_tokens, cost_micros, duration_ms)
                VALUES (:u, :purpose, :provider, :model, :outcome, :detail,
                        :pt, :ct, :cost, :ms)
                """
            ),
            {
                "u": str(user_id) if user_id else None,
                "purpose": purpose[:80],
                "provider": get_settings().ai_provider.strip()[:40] or "none",
                "model": model[:120],
                "outcome": outcome,
                "detail": detail[:400] if detail else None,
                "pt": prompt_tokens,
                "ct": completion_tokens,
                "cost": cost_micros,
                "ms": duration_ms,
            },
        )


def _may_spend(user_id: UUID | None) -> bool:
    """Whether this account may use a model that costs money.

    The server itself -- a scheduled pass, with no user -- may, because what
    bounds it is the cap rather than a permission.
    """
    if user_id is None:
        return True
    with get_engine().connect() as conn:
        return bool(
            conn.execute(
                text("SELECT may_spend_on_ai OR is_owner FROM users WHERE id = :u"),
                {"u": str(user_id)},
            ).scalar_one_or_none()
        )


def check(purpose: str, user_id: UUID | None) -> str:
    """The model this request may use, or a refusal saying why it may not.

    Separate from asking, so that a caller can find out whether a question is
    possible before composing one -- and so the reasons live in one place.
    """
    settings = get_settings()
    model = settings.ai_model.strip()

    if not configured():
        raise Refused("no AI provider is set up on this server")

    if not free_model(model):
        cap = max(0, settings.ai_monthly_cap_micros)
        if cap <= 0:
            raise Refused(
                f"{model} is a paid model and no monthly limit is set, so it cannot be used. "
                "Set AI_MONTHLY_CAP_MICROS, or choose a free model."
            )
        if not _may_spend(user_id):
            raise Refused("this account may use the free model only")
        with get_engine().connect() as conn:
            spent = spent_this_month(conn)
        if spent >= cap:
            raise Refused(
                "this month's AI allowance is spent. It resets at the start of next month, "
                "or the limit can be raised."
            )
    return model


async def ask(
    *,
    purpose: str,
    prompt: str,
    user_id: UUID | None = None,
    system: str | None = None,
    max_tokens: int = 512,
) -> Answer:
    """Put one question to the model. Raises Refused or AiError.

    Every outcome is recorded, refusals included: the record is the point.
    """
    settings = get_settings()
    try:
        model = check(purpose, user_id)
    except Refused as refusal:
        _record(user_id=user_id, purpose=purpose, model=settings.ai_model.strip() or "none",
                outcome="refused", detail=str(refusal))
        raise

    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]

    started = time.monotonic()
    try:
        answer = await _openrouter(model, messages, max_tokens)
    except Exception as exc:  # noqa: BLE001 - reported to the caller and recorded
        _record(user_id=user_id, purpose=purpose, model=model, outcome="error",
                detail=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000))
        raise AiError(f"the model could not be reached: {exc}") from exc

    _record(
        user_id=user_id, purpose=purpose, model=answer.model, outcome="ok",
        prompt_tokens=answer.prompt_tokens, completion_tokens=answer.completion_tokens,
        cost_micros=answer.cost_micros,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return answer


async def _openrouter(model: str, messages: list[dict], max_tokens: int) -> Answer:
    """One call, and what it cost.

    The cost comes from the provider rather than a price table kept here: asking
    for it (`usage.include`) means the cap is arithmetic on what was actually
    charged, and a price change upstream cannot quietly make the cap wrong.
    """
    settings = get_settings()
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    headers = {
        "Authorization": f"Bearer {settings.ai_api_key.strip()}",
        "Content-Type": "application/json",
        # OpenRouter asks callers to identify themselves. A name, not an address:
        # nothing about this house goes with the request.
        "X-Title": "Homesh",
    }
    url = settings.ai_base_url.strip().rstrip("/") + "/chat/completions"

    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        response = await http.post(url, json=body, headers=headers)

    if response.status_code == 401:
        raise AiError("the provider refused the key (401). Check AI_API_KEY.")
    if response.status_code == 402:
        raise AiError("the provider says there is no credit for this model (402).")
    if response.status_code == 429:
        raise AiError("the provider is rate-limiting this key (429). Try again shortly.")
    if response.status_code != 200:
        raise AiError(f"the provider answered {response.status_code}: {response.text[:200]}")

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise AiError("the provider returned no answer")
    said = (choices[0].get("message") or {}).get("content") or ""

    usage = data.get("usage") or {}
    # `cost` is in credits, one credit being one unit of currency.
    cost = usage.get("cost") or 0
    return Answer(
        text=said.strip(),
        model=data.get("model") or model,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        cost_micros=int(round(float(cost) * 1_000_000)),
    )


def history(limit: int = 50) -> list[dict]:
    """What the AI has been asked to do, newest first. For the app to show."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.at, u.display_name, c.purpose, c.model, c.outcome, c.detail,
                       c.prompt_tokens + c.completion_tokens AS tokens,
                       c.cost_micros, c.duration_ms
                FROM ai_calls c
                LEFT JOIN users u ON u.id = c.user_id
                ORDER BY c.at DESC
                LIMIT :n
                """
            ),
            {"n": max(1, min(limit, 500))},
        ).all()
    return [
        {
            "at": r[0].isoformat(),
            # Nobody named means the server itself, on a schedule.
            "who": r[1],
            "purpose": r[2],
            "model": r[3],
            "outcome": r[4],
            "detail": r[5],
            "tokens": r[6],
            "cost_micros": r[7],
            "duration_ms": r[8],
        }
        for r in rows
    ]
