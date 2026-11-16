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
    provider: str = "none", task: str | None = None, fallback: bool = False,
    detail: str | None = None, prompt_tokens: int = 0, completion_tokens: int = 0,
    cost_micros: int = 0, duration_ms: int = 0,
) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO ai_calls (user_id, purpose, provider, model, outcome, detail,
                                      prompt_tokens, completion_tokens, cost_micros, duration_ms,
                                      task, fallback)
                VALUES (:u, :purpose, :provider, :model, :outcome, :detail,
                        :pt, :ct, :cost, :ms, :task, :fb)
                """
            ),
            {
                "u": str(user_id) if user_id else None,
                "purpose": purpose[:80],
                "provider": provider[:40] or "none",
                "model": model[:120],
                "outcome": outcome,
                "detail": detail[:400] if detail else None,
                "pt": prompt_tokens,
                "ct": completion_tokens,
                "cost": cost_micros,
                "ms": duration_ms,
                "task": task,
                "fb": fallback,
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


def _gate(model: str, free: bool, user_id: UUID | None) -> None:
    """The spend rules for one model. Raises Refused saying why not."""
    if free:
        return
    settings = get_settings()
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


def check(purpose: str, user_id: UUID | None) -> str:
    """The model this request may use, or a refusal saying why it may not.

    Separate from asking, so that a caller can find out whether a question is
    possible before composing one -- and so the reasons live in one place.
    """
    model = get_settings().ai_model.strip()
    if not configured():
        raise Refused("no AI provider is set up on this server")
    _gate(model, free_model(model), user_id)
    return model


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text exactly as the model sent it


@dataclass(frozen=True)
class Reply:
    text: str
    tool_calls: tuple[ToolCall, ...]
    model_id: str  # "<provider>:<model>"
    prompt_tokens: int
    completion_tokens: int
    cost_micros: int
    fallback: bool  # True when the first choice was not what answered


class ProviderBusy(AiError):
    """The provider will not answer for a while: try the next model."""

    def __init__(self, message: str, cool_for: int) -> None:
        super().__init__(message)
        self.cool_for = cool_for


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
COOL_MIN, COOL_MAX, COOL_DEFAULT = 60, 3600, 600


def _retry_after(value: str | None) -> int:
    try:
        seconds = int(float(value)) if value else COOL_DEFAULT
    except ValueError:
        seconds = COOL_DEFAULT
    return max(COOL_MIN, min(COOL_MAX, seconds))


def _candidate_rows(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, free, tools, gone_at IS NOT NULL AS gone, "
                "COALESCE(cooling_until > now(), FALSE) AS cooling "
                "FROM ai_models WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        ).mappings().all()
    return {r["id"]: dict(r) for r in rows}


def provider_usable(provider: str) -> bool:
    """Whether a provider has a key and may be used at all."""
    settings = get_settings()
    if provider == "openrouter":
        return configured()
    if provider == "gemini":
        # Never offered unless the project is stated to have no billing account.
        return bool(settings.gemini_api_key.strip()) and settings.gemini_no_billing
    return False


def chat_available() -> bool:
    """Whether at least one chat candidate would be tried (not the spend gate)."""
    from . import aisettings

    settings = get_settings()
    listed = aisettings.task_ids("chat") or [f"openrouter:{settings.ai_model.strip()}"]
    rows = _candidate_rows(listed)
    for model_id in listed:
        provider = model_id.partition(":")[0]
        row = rows.get(model_id)
        if not provider_usable(provider) or (row is None and provider != "openrouter"):
            continue
        if row is not None and (row["gone"] or row["cooling"] or not row["tools"]):
            continue
        return True
    return False


def _note(model_id: str, *, ok: bool, error: str | None = None, cool_for: int = 0) -> None:
    with get_engine().begin() as conn:
        if ok:
            conn.execute(
                text("UPDATE ai_models SET last_ok_at = now(), cooling_until = NULL "
                     "WHERE id = :i"), {"i": model_id})
        else:
            conn.execute(
                text(
                    "UPDATE ai_models SET last_error = :e, last_error_at = now(), "
                    "cooling_until = CASE WHEN CAST(:c AS int) > 0 "
                    "THEN now() + make_interval(secs => CAST(:c AS int)) "
                    "ELSE cooling_until END WHERE id = :i"
                ),
                {"i": model_id, "e": (error or "")[:400], "c": cool_for},
            )


async def chat(
    *, task: str, purpose: str, messages: list[dict], user_id: UUID | None = None,
    tools: list[dict] | None = None, max_tokens: int = 1024, only: str | None = None,
) -> Reply:
    """Ask the best model that is available and permitted. Raises Refused or AiError.

    `only` names one model and skips the configured list (and its cooling), for
    the page's Test button -- but never the spend gate.

    Never logged: the messages, the tools, the reply, a key.
    """
    from . import aisettings

    settings = get_settings()
    if only:
        listed = [only]
    else:
        listed = aisettings.task_ids(task) or [f"openrouter:{settings.ai_model.strip()}"]
    rows = _candidate_rows(listed)

    refusal: str | None = None
    skipped_busy = False
    last_failure: Exception | None = None
    first = listed[0]

    for model_id in listed:
        provider, _, model = model_id.partition(":")
        row = rows.get(model_id)
        if not provider_usable(provider):
            continue
        if row is None and provider != "openrouter":
            continue
        if row is not None and row["gone"] and not only:
            continue
        if row is not None and row["cooling"] and not only:
            skipped_busy = True
            continue
        if tools and row is not None and not row["tools"]:
            continue
        free = row["free"] if row is not None else free_model(model)
        try:
            _gate(model, free, user_id)
        except Refused as why:
            refusal = refusal or str(why)
            continue

        fallback = model_id != first
        started = time.monotonic()
        try:
            reply = await _call(provider, model, messages, tools, max_tokens)
        except ProviderBusy as exc:
            last_failure = exc
            _record(user_id=user_id, purpose=purpose, model=model, outcome="error",
                    provider=provider, task=task, fallback=fallback, detail=str(exc),
                    duration_ms=int((time.monotonic() - started) * 1000))
            _note(model_id, ok=False, error=str(exc), cool_for=exc.cool_for)
            log.info("model %s busy (%s, task %s)", model_id, exc.cool_for, task)
            continue
        except Exception as exc:  # noqa: BLE001 - reported to the caller and recorded
            _record(user_id=user_id, purpose=purpose, model=model, outcome="error",
                    provider=provider, task=task, fallback=fallback,
                    detail=f"{type(exc).__name__}: {exc}",
                    duration_ms=int((time.monotonic() - started) * 1000))
            _note(model_id, ok=False, error=type(exc).__name__)
            raise AiError(f"the model could not be reached: {exc}") from exc

        answered = reply.model_id.partition(":")[2] or model
        _record(user_id=user_id, purpose=purpose, model=answered, outcome="ok",
                provider=provider, task=task, fallback=fallback,
                prompt_tokens=reply.prompt_tokens, completion_tokens=reply.completion_tokens,
                cost_micros=reply.cost_micros,
                duration_ms=int((time.monotonic() - started) * 1000))
        _note(model_id, ok=True)
        return Reply(text=reply.text, tool_calls=reply.tool_calls,
                     model_id=f"{provider}:{answered}",
                     prompt_tokens=reply.prompt_tokens,
                     completion_tokens=reply.completion_tokens,
                     cost_micros=reply.cost_micros, fallback=fallback)

    if last_failure is not None:
        # Every failed attempt was recorded as it happened.
        raise AiError(f"the model could not be reached: {last_failure}")
    if refusal:
        message = refusal
    elif skipped_busy:
        message = "every model in the list is busy right now. Try again in a few minutes."
    else:
        message = "no AI provider is set up on this server"
    _record(user_id=user_id, purpose=purpose, model=first.partition(":")[2] or "none",
            outcome="refused", provider=first.partition(":")[0], task=task, detail=message)
    raise Refused(message)


async def _call(provider: str, model: str, messages: list[dict],
                tools: list[dict] | None, max_tokens: int) -> Reply:
    settings = get_settings()
    if provider == "gemini":
        return await _chat_completions(
            GEMINI_BASE, settings.gemini_api_key.strip(), "gemini", model, messages, tools,
            max_tokens, ask_for_cost=False)
    if tools is None:
        # The plain path keeps its original shape: text in, text out.
        answer = await _openrouter(model, messages, max_tokens)
        return Reply(text=answer.text, tool_calls=(), model_id=f"openrouter:{answer.model}",
                     prompt_tokens=answer.prompt_tokens,
                     completion_tokens=answer.completion_tokens,
                     cost_micros=answer.cost_micros, fallback=False)
    return await _chat_completions(
        settings.ai_base_url.strip(), settings.ai_api_key.strip(), "openrouter", model,
        messages, tools, max_tokens, ask_for_cost=True)


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
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    reply = await chat(task="bulk", purpose=purpose, messages=messages, user_id=user_id,
                       max_tokens=max_tokens)
    return Answer(text=reply.text, model=reply.model_id.partition(":")[2],
                  prompt_tokens=reply.prompt_tokens,
                  completion_tokens=reply.completion_tokens, cost_micros=reply.cost_micros)


async def _openrouter(model: str, messages: list[dict], max_tokens: int) -> Answer:
    """One plain call to OpenRouter, and what it cost."""
    settings = get_settings()
    reply = await _chat_completions(
        settings.ai_base_url.strip(), settings.ai_api_key.strip(), "openrouter", model,
        messages, None, max_tokens, ask_for_cost=True)
    return Answer(text=reply.text, model=reply.model_id.partition(":")[2] or model,
                  prompt_tokens=reply.prompt_tokens,
                  completion_tokens=reply.completion_tokens, cost_micros=reply.cost_micros)


async def _chat_completions(
    base_url: str, key: str, provider: str, model: str, messages: list[dict],
    tools: list[dict] | None, max_tokens: int, ask_for_cost: bool,
) -> Reply:
    """One chat-completions call; both providers speak this format.

    The cost comes from the provider rather than a price table kept here: asking
    for it (`usage.include`) means the cap is arithmetic on what was actually
    charged, and a price change upstream cannot quietly make the cap wrong. The
    key goes in a header, never a URL.
    """
    body: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    if ask_for_cost:
        body["usage"] = {"include": True}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # A name, not an address: nothing about this house goes with the request.
        "X-Title": "Homesh",
    }
    url = base_url.rstrip("/") + "/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport()) as http:
            response = await http.post(url, json=body, headers=headers)
    except httpx.TimeoutException as exc:
        raise ProviderBusy("the provider did not answer in time", COOL_DEFAULT) from exc

    code = response.status_code
    if code in (401, 402):
        raise ProviderBusy(
            "the provider refused the key (401). Check the key in .env." if code == 401
            else "the provider says there is no credit for this model (402).", 3600)
    if code == 429 or code >= 500:
        raise ProviderBusy(
            "the provider is rate-limiting this key (429). Try again shortly." if code == 429
            else f"the provider answered {code}",
            _retry_after(response.headers.get("retry-after")))
    if code != 200:
        raise AiError(f"the provider answered {code}: {response.text[:200]}")

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise AiError("the provider returned no answer")
    message = choices[0].get("message") or {}
    calls = tuple(
        ToolCall(
            id=str(c.get("id") or ""),
            name=str((c.get("function") or {}).get("name") or ""),
            arguments=str((c.get("function") or {}).get("arguments") or ""),
        )
        for c in (message.get("tool_calls") or [])
    )
    usage = data.get("usage") or {}
    # `cost` is in credits, one credit being one unit of currency.
    cost = (usage.get("cost") or 0) if ask_for_cost else 0
    return Reply(
        text=(message.get("content") or "").strip(),
        tool_calls=calls,
        model_id=f"{provider}:{data.get('model') or model}",
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        cost_micros=int(round(float(cost) * 1_000_000)),
        fallback=False,
    )


# Tests replace this to answer without a network.
def _transport() -> httpx.AsyncBaseTransport | None:
    return None


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
