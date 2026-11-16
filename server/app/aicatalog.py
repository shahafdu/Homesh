"""What each provider offers, kept as a table the settings page can choose from.

A model counts as free only when everything says so. OpenRouter lists a price
and a name; a ":free" name with a price above zero is stored as paid, because
the cap is built on this flag and a wrong "free" is the expensive mistake.

Gemini is "free" exactly when `GEMINI_NO_BILLING` says its Google project has
no billing account -- the platform then cannot charge, whatever the code does.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import text

from .config import get_settings
from .db import get_engine

log = logging.getLogger("homesh.ai")

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _zero(value) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def parse_openrouter(data: dict) -> list[dict]:
    out = []
    for entry in data.get("data") or []:
        model = entry.get("id")
        if not isinstance(model, str) or not model:
            continue
        pricing = entry.get("pricing") or {}
        free = (
            model.endswith(":free")
            and _zero(pricing.get("prompt"))
            and _zero(pricing.get("completion"))
        )
        ctx = entry.get("context_length")
        out.append({
            "provider": "openrouter",
            "model": model,
            "label": str(entry.get("name") or model)[:120],
            "free": free,
            "tools": "tools" in (entry.get("supported_parameters") or []),
            "context": int(ctx) if isinstance(ctx, int | float) else None,
        })
    return out


def parse_gemini(data: dict, *, no_billing: bool) -> list[dict]:
    out = []
    for entry in data.get("models") or []:
        if "generateContent" not in (entry.get("supportedGenerationMethods") or []):
            continue
        name = str(entry.get("name") or "")
        model = name.removeprefix("models/")
        if not model:
            continue
        ctx = entry.get("inputTokenLimit")
        out.append({
            "provider": "gemini",
            "model": model,
            "label": str(entry.get("displayName") or model)[:120],
            "free": no_billing,
            "tools": True,
            "context": int(ctx) if isinstance(ctx, int | float) else None,
        })
    return out


async def _fetch_openrouter() -> list[dict]:
    settings = get_settings()
    headers = {"X-Title": "Homesh"}
    if settings.ai_api_key.strip():
        headers["Authorization"] = f"Bearer {settings.ai_api_key.strip()}"
    url = settings.ai_base_url.strip().rstrip("/") + "/models"
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        response = await http.get(url, headers=headers)
    response.raise_for_status()
    return parse_openrouter(response.json())


async def _fetch_gemini() -> list[dict]:
    settings = get_settings()
    out: list[dict] = []
    token = ""
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        for _ in range(10):
            params = {"pageSize": "200"}
            if token:
                params["pageToken"] = token
            # The key goes in a header: a URL ends up in logs.
            response = await http.get(
                GEMINI_MODELS_URL, params=params,
                headers={"x-goog-api-key": settings.gemini_api_key.strip()},
            )
            response.raise_for_status()
            data = response.json()
            out += parse_gemini(data, no_billing=settings.gemini_no_billing)
            token = data.get("nextPageToken") or ""
            if not token:
                break
    return out


def _store(provider: str, rows: list[dict]) -> tuple[int, int]:
    """Upsert one provider's list. Returns (gone, now_paid)."""
    with get_engine().begin() as conn:
        was_free = {
            r[0]: r[1] for r in conn.execute(
                text("SELECT id, free FROM ai_models WHERE provider = :p"), {"p": provider}
            )
        }
        now_paid = 0
        for row in rows:
            mid = f"{provider}:{row['model']}"
            if was_free.get(mid) and not row["free"]:
                now_paid += 1
            conn.execute(
                text(
                    """
                    INSERT INTO ai_models (id, provider, model, label, free, tools, context)
                    VALUES (:id, :p, :m, :l, :f, :t, :c)
                    ON CONFLICT (id) DO UPDATE SET
                        label = EXCLUDED.label, free = EXCLUDED.free,
                        tools = EXCLUDED.tools, context = EXCLUDED.context,
                        seen_at = now(), gone_at = NULL
                    """
                ),
                {"id": mid, "p": provider, "m": row["model"], "l": row["label"],
                 "f": row["free"], "t": row["tools"], "c": row["context"]},
            )
        gone = conn.execute(
            text(
                "UPDATE ai_models SET gone_at = now() "
                "WHERE provider = :p AND NOT (id = ANY(:ids)) AND gone_at IS NULL"
            ),
            {"p": provider, "ids": [f"{provider}:{r['model']}" for r in rows]},
        ).rowcount
    return gone, now_paid


async def refresh() -> dict:
    """Re-read what the providers offer. A failed provider changes nothing."""
    settings = get_settings()
    counts = {"openrouter": 0, "gemini": 0, "gone": 0, "now_paid": 0}
    jobs = [("openrouter", _fetch_openrouter)]
    if settings.gemini_api_key.strip():
        jobs.append(("gemini", _fetch_gemini))
    for provider, fetch in jobs:
        try:
            rows = await fetch()
        except Exception as exc:  # noqa: BLE001 - one provider down must not stop the other
            log.warning("could not refresh the %s model list: %s", provider, type(exc).__name__)
            continue
        gone, now_paid = _store(provider, rows)
        counts[provider] = len(rows)
        counts["gone"] += gone
        counts["now_paid"] += now_paid
    return counts
