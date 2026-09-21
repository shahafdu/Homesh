"""Asking a model something, on terms the platform enforces.

The rules under test are the ones that were agreed before any of this was built:
the provider is configuration, the key never leaves the machine, spending is
refused by the platform rather than avoided by the code, and every attempt is
recorded where it can be read.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app import ai

KEY = "sk-or-v1-not-a-real-key-0000000000000000"
FREE = "openai/gpt-oss-20b:free"
PAID = "openai/gpt-oss-120b"


@pytest.fixture
def openrouter(monkeypatch):
    """A provider set up with a free model, as a fresh install would be."""
    from app.config import get_settings

    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("AI_API_KEY", KEY)
    monkeypatch.setenv("AI_MODEL", FREE)
    monkeypatch.setenv("AI_MONTHLY_CAP_MICROS", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set(monkeypatch, **env):
    from app.config import get_settings

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def _calls(db) -> list[tuple]:
    with db.connect() as conn:
        return conn.execute(
            text("SELECT purpose, model, outcome, detail, cost_micros FROM ai_calls ORDER BY at")
        ).all()


class TestWhetherItIsAvailableAtAll:
    def test_with_nothing_configured_the_feature_is_simply_absent(self, db, monkeypatch):
        _set(monkeypatch, AI_PROVIDER="", AI_API_KEY="")
        assert ai.configured() is False
        assert ai.status()["configured"] is False

    def test_a_provider_without_a_key_is_not_configured(self, db, monkeypatch):
        _set(monkeypatch, AI_PROVIDER="openrouter", AI_API_KEY="")
        assert ai.configured() is False

    def test_the_free_model_is_the_default(self):
        from app.config import Settings

        assert Settings().ai_model.endswith(":free")
        assert Settings().ai_monthly_cap_micros == 0


class TestTheKeyStaysHere:
    def test_status_says_there_is_one_but_never_what_it_is(self, db, openrouter):
        assert KEY not in repr(ai.status())
        assert ai.status()["configured"] is True

    def test_nor_does_the_endpoint(self, client, db, user, openrouter):
        body = client.get("/api/ai/status").text
        assert KEY not in body
        assert "sk-or" not in body

    async def test_nor_does_the_record_of_a_call(self, db, user, openrouter, monkeypatch):
        """Including the row written when a call is refused."""
        _set(monkeypatch, AI_MODEL=PAID, AI_MONTHLY_CAP_MICROS="0")
        with pytest.raises(ai.Refused):
            await ai.ask(purpose="ask", prompt="anything", user_id=user.id)
        assert _calls(db), "the refusal was not recorded at all"
        assert KEY not in repr(_calls(db))


class TestSpendingIsRefusedNotAvoided:
    def test_a_paid_model_is_refused_while_no_limit_is_set(self, db, user, openrouter,
                                                            monkeypatch):
        """The default state of a fresh install: nothing can spend anything."""
        _set(monkeypatch, AI_MODEL=PAID, AI_MONTHLY_CAP_MICROS="0")
        with pytest.raises(ai.Refused, match="no monthly limit"):
            ai.check("ask", user.id)

    def test_a_free_model_needs_no_limit(self, db, user, openrouter):
        assert ai.check("ask", user.id) == FREE

    def test_an_ordinary_account_may_not_spend(self, db, user, openrouter, monkeypatch):
        _set(monkeypatch, AI_MODEL=PAID, AI_MONTHLY_CAP_MICROS="1000000")
        with db.begin() as conn:
            ordinary = conn.execute(
                text("INSERT INTO users (handle, display_name) VALUES ('kid', 'Kid') "
                     "RETURNING id")
            ).scalar_one()
        with pytest.raises(ai.Refused, match="free model only"):
            ai.check("ask", ordinary)

    def test_an_account_that_was_granted_it_may(self, db, user, openrouter, monkeypatch):
        _set(monkeypatch, AI_MODEL=PAID, AI_MONTHLY_CAP_MICROS="1000000")
        with db.begin() as conn:
            granted = conn.execute(
                text("INSERT INTO users (handle, display_name, may_spend_on_ai) "
                     "VALUES ('grown', 'Grown-up', TRUE) RETURNING id")
            ).scalar_one()
        assert ai.check("ask", granted) == PAID

    def test_the_owner_may(self, db, user, openrouter, monkeypatch):
        _set(monkeypatch, AI_MODEL=PAID, AI_MONTHLY_CAP_MICROS="1000000")
        assert ai.check("ask", user.id) == PAID

    def test_the_month_is_summed_before_the_call_not_after(self, db, user, openrouter,
                                                           monkeypatch):
        """A budget alert reports what has been spent. This refuses beforehand."""
        _set(monkeypatch, AI_MODEL=PAID, AI_MONTHLY_CAP_MICROS="1000")
        with db.begin() as conn:
            conn.execute(
                text("INSERT INTO ai_calls (purpose, provider, model, outcome, cost_micros) "
                     "VALUES ('ask', 'openrouter', :m, 'ok', 900)"),
                {"m": PAID},
            )
        assert ai.check("ask", user.id) == PAID, "still inside the allowance"

        with db.begin() as conn:
            conn.execute(
                text("INSERT INTO ai_calls (purpose, provider, model, outcome, cost_micros) "
                     "VALUES ('ask', 'openrouter', :m, 'ok', 200)"),
                {"m": PAID},
            )
        with pytest.raises(ai.Refused, match="allowance is spent"):
            ai.check("ask", user.id)

    def test_last_months_spending_does_not_count(self, db, user, openrouter, monkeypatch):
        _set(monkeypatch, AI_MODEL=PAID, AI_MONTHLY_CAP_MICROS="1000")
        with db.begin() as conn:
            conn.execute(
                text("INSERT INTO ai_calls (at, purpose, provider, model, outcome, cost_micros) "
                     "VALUES (now() - interval '40 days', 'ask', 'openrouter', :m, 'ok', 99999)"),
                {"m": PAID},
            )
        assert ai.check("ask", user.id) == PAID


class TestEveryAttemptIsRecorded:
    async def test_an_answer_is_recorded_with_what_it_cost(self, db, user, openrouter,
                                                           monkeypatch):
        async def answered(model, messages, max_tokens):
            return ai.Answer(text="mellow", model=model, prompt_tokens=11,
                             completion_tokens=3, cost_micros=0)

        monkeypatch.setattr(ai, "_openrouter", answered)
        answer = await ai.ask(purpose="tag-track", prompt="What is this song like?",
                             user_id=user.id)
        assert answer.text == "mellow"

        [(purpose, model, outcome, _detail, cost)] = _calls(db)
        assert (purpose, model, outcome, cost) == ("tag-track", FREE, "ok", 0)

    async def test_a_refusal_is_recorded_too(self, db, user, openrouter, monkeypatch):
        """The interesting entry: it says the cap held."""
        _set(monkeypatch, AI_MODEL=PAID, AI_MONTHLY_CAP_MICROS="0")
        with pytest.raises(ai.Refused):
            await ai.ask(purpose="ask", prompt="anything", user_id=user.id)
        [(purpose, _model, outcome, detail, _cost)] = _calls(db)
        assert (purpose, outcome) == ("ask", "refused")
        assert "no monthly limit" in detail

    async def test_a_failure_is_recorded_and_reported(self, db, user, openrouter, monkeypatch):
        async def broken(model, messages, max_tokens):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(ai, "_openrouter", broken)
        with pytest.raises(ai.AiError, match="could not be reached"):
            await ai.ask(purpose="ask", prompt="anything", user_id=user.id)
        [(_purpose, _model, outcome, detail, _cost)] = _calls(db)
        assert outcome == "error" and "connection reset" in detail

    async def test_the_question_and_the_answer_are_not_kept(self, db, user, openrouter,
                                                            monkeypatch):
        """An account of the work, not a copy of the content."""
        async def answered(model, messages, max_tokens):
            return ai.Answer(text="a secret-sounding answer", model=model,
                             prompt_tokens=1, completion_tokens=1, cost_micros=0)

        monkeypatch.setattr(ai, "_openrouter", answered)
        await ai.ask(purpose="ask", prompt="a private question about my family",
                     user_id=user.id)
        recorded = repr(_calls(db))
        assert "private question" not in recorded
        assert "secret-sounding" not in recorded

    async def test_a_scheduled_pass_is_visible_as_nobody(self, db, user, openrouter,
                                                          monkeypatch):
        async def answered(model, messages, max_tokens):
            return ai.Answer(text="ok", model=model, prompt_tokens=1, completion_tokens=1,
                             cost_micros=0)

        monkeypatch.setattr(ai, "_openrouter", answered)
        await ai.ask(purpose="tag-library", prompt="x", user_id=None)
        assert ai.history()[0]["who"] is None


class TestWhoMaySeeWhat:
    def test_anybody_signed_in_may_ask_whether_it_is_on(self, client, db, user, openrouter):
        body = client.get("/api/ai/status").json()
        assert body["configured"] is True and body["free_model"] is True

    def test_what_has_been_spent_is_the_administrators_business(self, client, db, user,
                                                               openrouter):
        from app.security import CurrentUser, optional_user, require_user

        assert "spent_this_month_micros" in client.get("/api/ai/status").json()

        with db.begin() as conn:
            uid = conn.execute(
                text("INSERT INTO users (handle, display_name) VALUES ('kid', 'Kid') "
                     "RETURNING id")
            ).scalar_one()
        guest = CurrentUser(id=uid, handle="kid", display_name="Kid", is_admin=False)
        client.app.dependency_overrides[require_user] = lambda: guest
        client.app.dependency_overrides[optional_user] = lambda: guest

        assert "spent_this_month_micros" not in client.get("/api/ai/status").json()
        assert client.get("/api/ai/history").status_code == 403

    def test_the_history_reads_newest_first(self, client, db, user, openrouter):
        with db.begin() as conn:
            for n, when in ((1, "2 hours"), (2, "1 hour"), (3, "1 minute")):
                conn.execute(
                    text("INSERT INTO ai_calls (at, purpose, provider, model, outcome) "
                         "VALUES (now() - CAST(:ago AS interval), :p, 'openrouter', :m, 'ok')"),
                    {"ago": when, "p": f"job{n}", "m": FREE},
                )
        listed = client.get("/api/ai/history").json()
        assert [entry["purpose"] for entry in listed] == ["job3", "job2", "job1"]
