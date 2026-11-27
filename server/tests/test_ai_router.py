"""The model router: an ordered list per task, fallback, the catalogue, the settings.

The HTTP layer is replaced with httpx.MockTransport throughout. Nothing here
reaches a provider.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import text

from app import ai, aicatalog

KEY = "sk-or-v1-marker-key-for-tests-000000"
GKEY = "gemini-marker-key-for-tests-000000"
FREE_A = "openrouter:vendor/alpha:free"
FREE_B = "openrouter:vendor/beta:free"
PAID = "openrouter:vendor/gamma"
GEM = "gemini:gemini-flash"

# Recorded shapes, trimmed to the fields the parsers read.
OPENROUTER_SAMPLE = {
    "data": [
        {"id": "vendor/alpha:free", "name": "Alpha", "context_length": 128000,
         "pricing": {"prompt": "0", "completion": "0"},
         "supported_parameters": ["tools", "max_tokens"]},
        {"id": "vendor/sneaky:free", "name": "Sneaky", "context_length": 8000,
         "pricing": {"prompt": "0.000001", "completion": "0.000002"},
         "supported_parameters": ["max_tokens"]},
        {"id": "vendor/gamma", "name": "Gamma", "context_length": 32000,
         "pricing": {"prompt": "0", "completion": "0"},
         "supported_parameters": ["tools"]},
    ]
}
GEMINI_SAMPLE = {
    "models": [
        {"name": "models/gemini-flash", "displayName": "Gemini Flash",
         "inputTokenLimit": 1000000, "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/embedding-x", "displayName": "Embed",
         "inputTokenLimit": 2048, "supportedGenerationMethods": ["embedContent"]},
    ]
}


def _env(monkeypatch, **env):
    from app.config import get_settings

    base = {"AI_PROVIDER": "openrouter", "AI_API_KEY": KEY, "AI_MODEL": "vendor/alpha:free",
            "AI_MONTHLY_CAP_MICROS": "0", "GEMINI_API_KEY": "", "GEMINI_NO_BILLING": "false"}
    base.update(env)
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean_settings():
    from app.config import get_settings

    yield
    get_settings.cache_clear()


def _model(db, mid, *, free=True, tools=True, gone=False, cooling=False):
    provider, _, model = mid.partition(":")
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ai_models (id, provider, model, label, free, tools, gone_at, "
                "cooling_until) VALUES (:i, :p, :m, :m, :f, :t, "
                "CASE WHEN :g THEN now() END, "
                "CASE WHEN :c THEN now() + interval '10 minutes' END)"
            ),
            {"i": mid, "p": provider, "m": model, "f": free, "t": tools, "g": gone,
             "c": cooling},
        )


def _tasks(db, **tasks):
    with db.begin() as conn:
        conn.execute(
            text("UPDATE ai_settings SET data = CAST(:d AS jsonb)"),
            {"d": json.dumps({"tasks": tasks})},
        )


def _serve(monkeypatch, handler):
    """Answer every provider request with `handler`; returns the recorded requests."""
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(ai, "_transport", lambda: httpx.MockTransport(wrapped))
    return seen


def _ok(model="x", content="hello", **extra):
    return httpx.Response(200, json={
        "model": model, "choices": [{"message": {"content": content, **extra}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "cost": 0}})


def _calls(db):
    with db.connect() as conn:
        return conn.execute(
            text("SELECT model, outcome, fallback, task FROM ai_calls ORDER BY at, id")
        ).all()


MSGS = [{"role": "user", "content": "hi"}]


class TestTheRouter:
    async def test_with_no_settings_the_old_model_answers(self, db, user, monkeypatch):
        _env(monkeypatch)
        seen = _serve(monkeypatch, lambda r: _ok("vendor/alpha:free"))
        reply = await ai.chat(task="chat", purpose="t", messages=MSGS, user_id=user.id)
        assert reply.model_id == FREE_A and reply.fallback is False
        assert json.loads(seen[0].content)["model"] == "vendor/alpha:free"

    async def test_a_busy_model_hands_over_to_the_next(self, db, user, monkeypatch):
        _env(monkeypatch)
        _model(db, FREE_A)
        _model(db, FREE_B)
        _tasks(db, chat=[FREE_A, FREE_B])

        def handler(request):
            if json.loads(request.content)["model"] == "vendor/alpha:free":
                return httpx.Response(429, headers={"Retry-After": "5"})
            return _ok("vendor/beta:free")

        _serve(monkeypatch, handler)
        reply = await ai.chat(task="chat", purpose="t", messages=MSGS, user_id=user.id)
        assert reply.model_id == FREE_B and reply.fallback is True
        assert [(m, o, f) for m, o, f, _ in _calls(db)] == [
            ("vendor/alpha:free", "error", False), ("vendor/beta:free", "ok", True)]
        with db.connect() as conn:
            until = conn.execute(
                text("SELECT extract(epoch FROM cooling_until - now()) FROM ai_models "
                     "WHERE id = :i"), {"i": FREE_A}).scalar_one()
        # Retry-After of 5 s is raised to the 60 s floor.
        assert 50 < until <= 60

    @pytest.mark.parametrize(("header", "low", "high"), [
        ("99999", 3500, 3600), ("120", 110, 120), (None, 590, 600)])
    async def test_retry_after_is_clamped(self, db, user, monkeypatch, header, low, high):
        _env(monkeypatch)
        _model(db, FREE_A)
        _tasks(db, chat=[FREE_A])
        headers = {"Retry-After": header} if header else {}
        _serve(monkeypatch, lambda r: httpx.Response(429, headers=headers))
        with pytest.raises(ai.AiError):
            await ai.chat(task="chat", purpose="t", messages=MSGS, user_id=user.id)
        with db.connect() as conn:
            until = conn.execute(
                text("SELECT extract(epoch FROM cooling_until - now()) FROM ai_models "
                     "WHERE id = :i"), {"i": FREE_A}).scalar_one()
        assert low < until <= high

    async def test_a_paid_model_is_skipped_while_the_cap_is_zero(self, db, user, monkeypatch):
        _env(monkeypatch)
        _model(db, PAID, free=False)
        _model(db, FREE_B)
        _tasks(db, chat=[PAID, FREE_B])
        seen = _serve(monkeypatch, lambda r: _ok("vendor/beta:free"))
        reply = await ai.chat(task="chat", purpose="t", messages=MSGS, user_id=user.id)
        assert reply.model_id == FREE_B
        assert [json.loads(r.content)["model"] for r in seen] == ["vendor/beta:free"]

    async def test_a_paid_model_is_refused_to_an_account_that_may_not_spend(
            self, db, user, monkeypatch):
        _env(monkeypatch, AI_MONTHLY_CAP_MICROS="1000000")
        _model(db, PAID, free=False)
        _tasks(db, chat=[PAID])
        with db.begin() as conn:
            kid = conn.execute(
                text("INSERT INTO users (handle, display_name) VALUES ('kid', 'Kid') "
                     "RETURNING id")).scalar_one()
        seen = _serve(monkeypatch, lambda r: _ok())
        with pytest.raises(ai.Refused, match="free model only"):
            await ai.chat(task="chat", purpose="t", messages=MSGS, user_id=kid)
        assert not seen

    async def test_every_model_cooling_is_one_refusal(self, db, user, monkeypatch):
        _env(monkeypatch)
        _model(db, FREE_A, cooling=True)
        _model(db, FREE_B, cooling=True)
        _tasks(db, chat=[FREE_A, FREE_B])
        seen = _serve(monkeypatch, lambda r: _ok())
        with pytest.raises(ai.Refused, match="busy right now"):
            await ai.chat(task="chat", purpose="t", messages=MSGS, user_id=user.id)
        assert not seen
        assert [o for _, o, _, _ in _calls(db)] == ["refused"]

    async def test_a_model_without_tools_is_skipped_when_tools_are_asked_for(
            self, db, user, monkeypatch):
        _env(monkeypatch)
        _model(db, FREE_A, tools=False)
        _model(db, FREE_B)
        _tasks(db, bulk=[FREE_A, FREE_B])
        _serve(monkeypatch, lambda r: _ok("vendor/beta:free"))
        tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
        reply = await ai.chat(task="bulk", purpose="t", messages=MSGS, tools=tools)
        assert reply.model_id == FREE_B

    async def test_tool_calls_come_back_as_sent(self, db, user, monkeypatch):
        _env(monkeypatch)
        call = {"id": "c1", "type": "function",
                "function": {"name": "find", "arguments": "{\"q\": \"x\"}"}}
        _serve(monkeypatch, lambda r: _ok(content=None, tool_calls=[call]))
        tools = [{"type": "function", "function": {"name": "find", "parameters": {}}}]
        reply = await ai.chat(task="chat", purpose="t", messages=MSGS, tools=tools)
        assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
            ("c1", "find", "{\"q\": \"x\"}")]

    async def test_gemini_is_never_a_candidate_without_the_no_billing_statement(
            self, db, user, monkeypatch):
        _env(monkeypatch, GEMINI_API_KEY=GKEY, GEMINI_NO_BILLING="false")
        _model(db, GEM)
        _tasks(db, chat=[GEM])
        seen = _serve(monkeypatch, lambda r: _ok())
        with pytest.raises(ai.Refused):
            await ai.chat(task="chat", purpose="t", messages=MSGS, user_id=user.id)
        assert not seen

    async def test_the_gemini_key_is_in_a_header_and_never_a_url(self, db, user, monkeypatch):
        _env(monkeypatch, GEMINI_API_KEY=GKEY, GEMINI_NO_BILLING="true")
        _model(db, GEM)
        _tasks(db, chat=[GEM])
        seen = _serve(monkeypatch, lambda r: _ok("gemini-flash"))
        reply = await ai.chat(task="chat", purpose="t", messages=MSGS, user_id=user.id)
        assert reply.model_id == GEM and reply.cost_micros == 0
        assert GKEY not in str(seen[0].url)
        assert GKEY in seen[0].headers["authorization"]
        assert "generativelanguage" in seen[0].url.host


class TestTheCatalogue:
    def test_a_free_name_with_a_price_is_stored_as_paid(self):
        rows = {r["model"]: r for r in aicatalog.parse_openrouter(OPENROUTER_SAMPLE)}
        assert rows["vendor/alpha:free"]["free"] is True
        assert rows["vendor/alpha:free"]["tools"] is True
        assert rows["vendor/sneaky:free"]["free"] is False
        # Free by price but not by name: treated as paid.
        assert rows["vendor/gamma"]["free"] is False

    def test_gemini_keeps_only_chat_models_and_strips_the_prefix(self):
        rows = aicatalog.parse_gemini(GEMINI_SAMPLE, no_billing=True)
        assert [(r["model"], r["free"], r["context"]) for r in rows] == [
            ("gemini-flash", True, 1000000)]
        assert aicatalog.parse_gemini(GEMINI_SAMPLE, no_billing=False)[0]["free"] is False

    async def test_a_model_that_leaves_the_list_is_marked_gone(self, db, monkeypatch):
        _env(monkeypatch)
        payloads = [OPENROUTER_SAMPLE,
                    {"data": [OPENROUTER_SAMPLE["data"][0]]}, OPENROUTER_SAMPLE]
        state = {"n": 0}

        def handler(request):
            body = payloads[state["n"]]
            return httpx.Response(200, json=body)

        real = httpx.AsyncClient

        class Client(real):
            def __init__(self, *a, **kw):
                super().__init__(*a, transport=httpx.MockTransport(handler), **kw)

        monkeypatch.setattr(aicatalog.httpx, "AsyncClient", Client)

        def gone():
            with db.connect() as conn:
                return conn.execute(
                    text("SELECT gone_at IS NOT NULL FROM ai_models "
                         "WHERE id = 'openrouter:vendor/gamma'")).scalar_one()

        await aicatalog.refresh()
        assert gone() is False
        state["n"] = 1
        counts = await aicatalog.refresh()
        assert gone() is True and counts["gone"] == 2
        state["n"] = 2
        await aicatalog.refresh()
        assert gone() is False

    async def test_a_failed_refresh_changes_nothing(self, db, monkeypatch):
        _env(monkeypatch)
        _model(db, FREE_A)

        class Client(httpx.AsyncClient):
            def __init__(self, *a, **kw):
                super().__init__(*a, transport=httpx.MockTransport(
                    lambda r: httpx.Response(500)), **kw)

        monkeypatch.setattr(aicatalog.httpx, "AsyncClient", Client)
        await aicatalog.refresh()
        with db.connect() as conn:
            assert conn.execute(
                text("SELECT gone_at IS NULL FROM ai_models WHERE id = :i"),
                {"i": FREE_A}).scalar_one()


class TestTheSettingsRoutes:
    def _put(self, client, body):
        return client.put("/api/ai/settings", json=body)

    def test_a_good_change_is_saved_and_audited(self, client, db, monkeypatch):
        _env(monkeypatch)
        _model(db, FREE_A)

        def audited():
            # audit_log is not emptied between tests: count from a baseline.
            with db.connect() as conn:
                return conn.execute(text(
                    "SELECT count(*) FROM audit_log WHERE event = 'ai.settings'")).scalar_one()

        before = audited()
        r = self._put(client, {"night": {"start": "02:00", "end": "06:30"},
                               "timezone": "Europe/Paris", "tasks": {"chat": [FREE_A]}})
        assert r.status_code == 200
        body = r.json()
        assert body["night"] == {"start": "02:00", "end": "06:30"}
        assert body["tasks"]["chat"] == [FREE_A] and body["tasks"]["bulk"] == []
        assert client.get("/api/ai/settings").json()["timezone"] == "Europe/Paris"
        assert audited() == before + 1

    @pytest.mark.parametrize("body", [
        {"surprise": 1},
        {"night": {"start": "25:00", "end": "06:00"}},
        {"night": {"start": "1:00", "end": "06:00"}},
        {"timezone": "Not/AZone"},
        {"questions_per_day": 0},
        {"bulk_batches_per_night": 1001},
        {"provider_folders": ["00000000-0000-0000-0000-000000000000"]},
        {"tasks": {"chat": ["openrouter:unknown"]}},
        {"tasks": {"chat": [f"openrouter:m{n}" for n in range(9)]}},
    ])
    def test_nonsense_is_refused(self, client, db, monkeypatch, body):
        _env(monkeypatch)
        assert self._put(client, body).status_code == 422

    def test_a_model_without_tools_cannot_answer_questions(self, client, db, monkeypatch):
        _env(monkeypatch)
        _model(db, FREE_A, tools=False)
        assert self._put(client, {"tasks": {"chat": [FREE_A]}}).status_code == 422
        assert self._put(client, {"tasks": {"bulk": [FREE_A]}}).status_code == 200

    def test_a_paid_model_is_refused_while_the_cap_is_zero(self, client, db, monkeypatch):
        _env(monkeypatch)
        _model(db, PAID, free=False)
        assert self._put(client, {"tasks": {"chat": [PAID]}}).status_code == 422
        _env(monkeypatch, AI_MONTHLY_CAP_MICROS="5000000")
        assert self._put(client, {"tasks": {"chat": [PAID]}}).status_code == 200

    def test_the_model_list_hides_paid_ones_while_the_cap_is_zero(self, client, db, monkeypatch):
        _env(monkeypatch)
        _model(db, FREE_A)
        _model(db, PAID, free=False)
        ids = [m["id"] for m in client.get("/api/ai/models").json()]
        assert ids == [FREE_A]
        _env(monkeypatch, AI_MONTHLY_CAP_MICROS="5000000")
        assert {m["id"] for m in client.get("/api/ai/models").json()} == {FREE_A, PAID}

    def test_status_carries_features_for_everyone(self, client, db, monkeypatch):
        _env(monkeypatch)
        body = client.get("/api/ai/status").json()
        assert body["features"]["assistant"] is True
        _env(monkeypatch, AI_API_KEY="")
        assert client.get("/api/ai/status").json()["features"]["assistant"] is False

    def test_the_test_button_reports_success_and_failure(self, client, db, monkeypatch):
        _env(monkeypatch)
        _model(db, FREE_A)
        _serve(monkeypatch, lambda r: _ok("vendor/alpha:free"))
        r = client.post("/api/ai/models/test", json={"id": FREE_A}).json()
        assert r["ok"] is True and r["ms"] >= 0
        _serve(monkeypatch, lambda r: httpx.Response(429))
        r = client.post("/api/ai/models/test", json={"id": FREE_A}).json()
        assert r["ok"] is False and r["why"]

    def test_the_test_button_cannot_spend(self, client, db, monkeypatch):
        _env(monkeypatch)
        _model(db, PAID, free=False)
        seen = _serve(monkeypatch, lambda r: _ok())
        r = client.post("/api/ai/models/test", json={"id": PAID}).json()
        assert r["ok"] is False and "no monthly limit" in r["why"]
        assert not seen

    def test_no_response_contains_a_key(self, client, db, monkeypatch):
        _env(monkeypatch, GEMINI_API_KEY=GKEY, GEMINI_NO_BILLING="true")
        _model(db, FREE_A)
        _model(db, GEM)
        _serve(monkeypatch, lambda r: _ok("vendor/alpha:free"))
        bodies = [
            client.get("/api/ai/status").text,
            client.get("/api/ai/settings").text,
            client.get("/api/ai/models").text,
            client.get("/api/ai/usage").text,
            client.get("/api/ai/history").text,
            client.post("/api/ai/models/test", json={"id": FREE_A}).text,
            client.put("/api/ai/settings", json={"paused": True}).text,
        ]
        for body in bodies:
            assert KEY not in body and GKEY not in body

    def test_gemini_says_why_it_is_not_used(self, client, db, monkeypatch):
        _env(monkeypatch, GEMINI_API_KEY=GKEY, GEMINI_NO_BILLING="false")
        gem = client.get("/api/ai/settings").json()["providers"]["gemini"]
        assert gem["key"] is True and gem["usable"] is False and "GEMINI_NO_BILLING" in gem["why"]

    def test_every_admin_route_refuses_a_member(self, client, db, monkeypatch):
        from app.security import CurrentUser, optional_user, require_user

        _env(monkeypatch)
        with db.begin() as conn:
            uid = conn.execute(
                text("INSERT INTO users (handle, display_name) VALUES ('kid', 'Kid') "
                     "RETURNING id")).scalar_one()
        guest = CurrentUser(id=uid, handle="kid", display_name="Kid", is_admin=False)
        client.app.dependency_overrides[require_user] = lambda: guest
        client.app.dependency_overrides[optional_user] = lambda: guest
        assert client.get("/api/ai/settings").status_code == 403
        assert client.put("/api/ai/settings", json={"paused": True}).status_code == 403
        assert client.get("/api/ai/models").status_code == 403
        assert client.post("/api/ai/models/refresh").status_code == 403
        assert client.post("/api/ai/models/test", json={"id": FREE_A}).status_code == 403
        assert client.get("/api/ai/usage").status_code == 403


class TestWhatThePageNeeds:
    def test_every_listed_model_is_described_even_when_it_cannot_be_chosen(
        self, client, db, monkeypatch
    ):
        _env(monkeypatch)
        _model(db, FREE_A, cooling=True)
        _model(db, FREE_B, gone=True)
        _model(db, PAID, free=False)
        _tasks(db, chat=[FREE_A, FREE_B], bulk=[PAID])
        listed = client.get("/api/ai/settings").json()["listed"]
        assert set(listed) == {FREE_A, FREE_B, PAID}
        assert listed[FREE_A]["cooling_until"] and not listed[FREE_A]["gone"]
        assert listed[FREE_B]["gone"] is True
        assert listed[PAID]["free"] is False
        assert listed[PAID]["label"] == "vendor/gamma"

    def test_nothing_listed_describes_nothing(self, client, db, monkeypatch):
        _env(monkeypatch)
        _model(db, FREE_A)
        assert client.get("/api/ai/settings").json()["listed"] == {}

    def test_openrouter_says_why_it_is_not_used(self, client, db, monkeypatch):
        _env(monkeypatch, AI_PROVIDER="")
        found = client.get("/api/ai/settings").json()["providers"]["openrouter"]
        assert found["key"] is True and found["usable"] is False and found["why"]
        _env(monkeypatch)
        found = client.get("/api/ai/settings").json()["providers"]["openrouter"]
        assert found["usable"] is True and found["why"] is None

    def test_models_that_started_to_cost_money_can_be_removed_one_at_a_time(
        self, client, db, monkeypatch
    ):
        _env(monkeypatch)
        other = "openrouter:vendor/delta"
        _model(db, PAID, free=False)
        _model(db, other, free=False)
        _model(db, FREE_A)
        _tasks(db, chat=[PAID, other])

        def put(chat):
            return client.put("/api/ai/settings", json={"tasks": {"chat": chat}}).status_code

        # Kept and reordered, then one removed while the other is still listed.
        assert put([PAID, other, FREE_A]) == 200
        assert put([FREE_A, other]) == 200
        # Removed is removed: choosing it again is judged like any new choice.
        assert put([FREE_A, other, PAID]) == 422

    def test_a_paid_model_kept_in_one_list_cannot_be_added_to_another(
        self, client, db, monkeypatch
    ):
        _env(monkeypatch)
        _model(db, PAID, free=False)
        _tasks(db, chat=[PAID])
        r = client.put("/api/ai/settings", json={"tasks": {"bulk": [PAID]}})
        assert r.status_code == 422


class TestWhatTheReviewFound:
    async def test_an_unreachable_provider_hands_over_to_the_next(self, db, user, monkeypatch):
        _env(monkeypatch, GEMINI_API_KEY=GKEY, GEMINI_NO_BILLING="true")
        _model(db, FREE_A)
        _model(db, GEM)
        _tasks(db, chat=[FREE_A, GEM])

        def handler(request):
            if "generativelanguage" not in request.url.host:
                raise httpx.ConnectError("name resolution failed", request=request)
            return _ok("models/gemini-flash")

        _serve(monkeypatch, handler)
        reply = await ai.chat(task="chat", purpose="t", messages=MSGS, user_id=user.id)
        # The prefix Gemini may send back is not part of the stored id.
        assert reply.model_id == GEM and reply.fallback is True
        assert [(m, o) for m, o, _, _ in _calls(db)] == [
            ("vendor/alpha:free", "error"), ("gemini-flash", "ok")]
        with db.connect() as conn:
            assert conn.execute(
                text("SELECT cooling_until > now() FROM ai_models WHERE id = :i"),
                {"i": FREE_A}).scalar_one()

    @pytest.mark.parametrize("value", ["inf", "nan", "Wed, 21 Oct 2026 07:28:00 GMT"])
    def test_a_strange_retry_after_falls_back_to_the_default(self, value):
        assert ai._retry_after(value) == ai.COOL_DEFAULT

    async def test_a_key_echoed_by_the_provider_is_cut_out(self, db, user, monkeypatch):
        _env(monkeypatch)
        _serve(monkeypatch, lambda r: httpx.Response(400, text=f"bad key {KEY} given"))
        with pytest.raises(ai.AiError) as caught:
            await ai.chat(task="chat", purpose="t", messages=MSGS, user_id=user.id)
        assert KEY not in str(caught.value) and "[key]" in str(caught.value)
        with db.connect() as conn:
            details = conn.execute(text("SELECT detail FROM ai_calls")).scalars().all()
        assert details and all(KEY not in d for d in details)

    def test_the_test_button_takes_only_a_catalogued_model(self, client, db, monkeypatch):
        _env(monkeypatch)
        seen = _serve(monkeypatch, lambda r: _ok())
        # Free by its name alone, but nothing has said what it costs.
        r = client.post("/api/ai/models/test", json={"id": "openrouter:vendor/new:free"})
        assert r.json() == {"ok": False, "why": "that model is not in the list this server knows"}
        assert not seen

    def test_unusable_providers_are_left_out_of_the_model_list(self, client, db, monkeypatch):
        _env(monkeypatch, GEMINI_API_KEY=GKEY, GEMINI_NO_BILLING="false")
        _model(db, FREE_A)
        _model(db, GEM)
        assert [m["id"] for m in client.get("/api/ai/models").json()] == [FREE_A]
        _env(monkeypatch, GEMINI_API_KEY=GKEY, GEMINI_NO_BILLING="true")
        assert {m["id"] for m in client.get("/api/ai/models").json()} == {FREE_A, GEM}

    def test_the_refresh_route_answers_counts_and_no_key(self, client, db, monkeypatch):
        _env(monkeypatch, GEMINI_API_KEY=GKEY, GEMINI_NO_BILLING="true")

        def handler(request):
            if "generativelanguage" in request.url.host:
                return httpx.Response(200, json=GEMINI_SAMPLE)
            return httpx.Response(200, json=OPENROUTER_SAMPLE)

        real = httpx.AsyncClient

        class Client(real):
            def __init__(self, *a, **kw):
                super().__init__(*a, transport=httpx.MockTransport(handler), **kw)

        monkeypatch.setattr(aicatalog.httpx, "AsyncClient", Client)
        r = client.post("/api/ai/models/refresh")
        assert r.status_code == 200
        assert r.json() == {"openrouter": 3, "gemini": 1, "gone": 0, "now_paid": 0}
        assert KEY not in r.text and GKEY not in r.text
