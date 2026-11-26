"""The assistant's history and audit.

Two records with two readers. A person's questions are theirs alone; what the
assistant changed is the household's, readable by an administrator without
learning how anybody worded a question. Most of what is tested here is that
line between the two, and that neither record loses what it should keep.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import aihistory, standby
from app.main import app
from app.security import CurrentUser, optional_user, require_user

# A word found nowhere else, to look for in answers that must not carry it.
MARKER = "zyxquokka"


@pytest.fixture
def member(db) -> CurrentUser:
    """Somebody in the household who is not an administrator."""
    with db.begin() as conn:
        uid = conn.execute(
            text(
                "INSERT INTO users (handle, display_name, is_admin) "
                "VALUES ('sam', 'Sam', FALSE) RETURNING id"
            )
        ).scalar_one()
    return CurrentUser(id=uid, handle="sam", display_name="Sam", is_admin=False)


def _act_as(who: CurrentUser) -> None:
    app.dependency_overrides[require_user] = lambda: who
    app.dependency_overrides[optional_user] = lambda: who


def _ask(db, who: CurrentUser, words: str, *, done: bool = True) -> uuid.UUID:
    with db.begin() as conn:
        qid = aihistory.start_question(conn, who.id, uuid.uuid4(), words, False)
    if done:
        aihistory.finish(qid, "done", "Here you are.", [])
    return qid


def _did(who: CurrentUser, question: uuid.UUID | None, summary: str, **extra) -> uuid.UUID:
    fields = {
        "tool": "create_playlist",
        "method": "POST",
        "path": "/api/playlists",
        "body": {"name": "Evening"},
        "outcome": "done",
        "status": 200,
    }
    fields.update(extra)
    return aihistory.record_action(
        question_id=question, user_id=who.id, summary=summary, **fields
    )


def _actions(db) -> list:
    with db.connect() as conn:
        return conn.execute(
            text("SELECT question_id, user_id, summary, outcome FROM ai_actions ORDER BY at")
        ).all()


def _age(db, question: uuid.UUID, minutes: int) -> None:
    with db.begin() as conn:
        conn.execute(
            text(
                "UPDATE ai_questions SET asked_at = now() - make_interval(mins => :m) "
                "WHERE id = :i"
            ),
            {"m": minutes, "i": str(question)},
        )


def _range(days_back: float = 1, days_on: float = 1) -> dict:
    now = datetime.now(UTC)
    return {
        "since": (now - timedelta(days=days_back)).isoformat(),
        "until": (now + timedelta(days=days_on)).isoformat(),
    }


class TestAPersonsOwnHistory:
    def test_it_holds_what_was_asked_and_what_came_of_it(self, client, db, user):
        qid = _ask(db, user, "play something quiet in the study", done=False)
        aihistory.add_step(qid, "Searching by name...")
        aihistory.add_step(qid, "Starting it...")
        _did(user, qid, "Started 3 tracks in the Study", tool="play")
        aihistory.finish(qid, "done", "Playing now.", [uuid.uuid4()])

        [entry] = client.get("/api/assistant/history").json()

        assert entry.pop("asked_at")
        assert entry == {
            "id": str(qid),
            "text": "play something quiet in the study",
            "spoken": False,
            "state": "done",
            "answer": "Playing now.",
            "actions": [{"summary": "Started 3 tracks in the Study", "outcome": "done"}],
        }
        with db.connect() as conn:
            steps, results = conn.execute(
                text("SELECT steps, results FROM ai_questions")
            ).one()
        assert [s["text"] for s in steps] == ["Searching by name...", "Starting it..."]
        assert all(s["at"] for s in steps)
        assert len(results) == 1

    def test_a_person_reads_only_their_own(self, client, db, user, member):
        _ask(db, user, "mine")
        theirs = _ask(db, member, "theirs")

        assert [e["text"] for e in client.get("/api/assistant/history").json()] == ["mine"]
        _act_as(member)
        assert [e["text"] for e in client.get("/api/assistant/history").json()] == ["theirs"]
        assert client.get("/api/assistant/history", params={"q": "mine"}).json() == []

        # Not even an administrator: another account's id is one that is not there.
        _act_as(user)
        assert client.delete(f"/api/assistant/history/{theirs}").status_code == 404
        assert client.delete(f"/api/assistant/history/{uuid.uuid4()}").status_code == 404
        _act_as(member)
        assert len(client.get("/api/assistant/history").json()) == 1

    def test_newest_first_and_a_page_at_a_time(self, client, db, user):
        for minutes, words in ((30, "oldest"), (20, "middle"), (10, "newest")):
            _age(db, _ask(db, user, words), minutes)

        first = client.get("/api/assistant/history", params={"limit": 2}).json()
        rest = client.get(
            "/api/assistant/history", params={"limit": 2, "before": first[-1]["asked_at"]}
        ).json()

        assert [e["text"] for e in first] == ["newest", "middle"]
        assert [e["text"] for e in rest] == ["oldest"]

    def test_search_forgives_a_typo(self, client, db, user):
        _ask(db, user, "play something mellow in the study")
        _ask(db, user, "photos of the garden")

        found = client.get("/api/assistant/history", params={"q": "melow"}).json()

        assert [e["text"] for e in found] == ["play something mellow in the study"]

    def test_search_works_in_a_right_to_left_script(self, client, db, user):
        _ask(db, user, "شغل موسيقى هادئة")
        _ask(db, user, "photos of the garden")

        # As written only. The database's locale is C, where trigrams are made
        # of ASCII letters alone, so likeness cannot forgive a typo here and
        # the plain match is what finds it.
        found = client.get("/api/assistant/history", params={"q": "موسيقى"}).json()

        assert [e["text"] for e in found] == ["شغل موسيقى هادئة"]

    def test_clearing_one_keeps_what_it_changed(self, client, db, user):
        gone = _ask(db, user, "make a playlist")
        kept = _ask(db, user, "something else")
        _did(user, gone, "Created playlist Evening, 3 tracks")

        assert client.delete(f"/api/assistant/history/{gone}").status_code == 204

        assert [e["id"] for e in client.get("/api/assistant/history").json()] == [str(kept)]
        assert _actions(db) == [
            (None, user.id, "Created playlist Evening, 3 tracks", "done")
        ]

    def test_clearing_everything_clears_only_ones_own(self, client, db, user, member):
        mine = _ask(db, user, "one")
        _ask(db, user, "two")
        _ask(db, member, "theirs")
        _did(user, mine, "Created playlist Evening")

        assert client.delete("/api/assistant/history").json() == {"cleared": 2}

        assert client.get("/api/assistant/history").json() == []
        assert len(_actions(db)) == 1
        _act_as(member)
        assert len(client.get("/api/assistant/history").json()) == 1

    def test_removing_an_account_removes_its_questions_and_keeps_its_actions(
        self, client, db, user, member
    ):
        qid = _ask(db, member, "make a playlist")
        _did(member, qid, "Created playlist Evening")

        assert client.delete(f"/api/people/{member.id}").status_code == 200

        with db.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM ai_questions")).scalar_one() == 0
        assert _actions(db) == [(None, None, "Created playlist Evening", "done")]
        [entry] = client.get("/api/assistant/audit").json()
        assert entry["who"] is None

    def test_signing_in_is_required(self, anon_client, db):
        assert anon_client.get("/api/assistant/history").status_code == 401
        assert anon_client.delete("/api/assistant/history").status_code == 401
        assert anon_client.get("/api/assistant/audit").status_code == 401
        assert anon_client.get("/api/assistant/audit/export", params=_range()).status_code == 401


class TestAQuestionNothingIsWorkingOn:
    def test_a_stale_one_reads_as_failed(self, client, db, user):
        qid = _ask(db, user, "interrupted", done=False)
        _age(db, qid, 4)

        [entry] = client.get("/api/assistant/history").json()

        assert entry["state"] == "failed"
        assert entry["answer"] == aihistory.STALE_ANSWER

    def test_a_recent_one_is_left_thinking(self, client, db, user):
        _age(db, _ask(db, user, "still going", done=False), 2)

        [entry] = client.get("/api/assistant/history").json()

        assert entry["state"] == "thinking"

    def test_the_server_closes_them_as_it_starts(self, db, user):
        qid = _ask(db, user, "interrupted", done=False)
        _age(db, qid, 4)

        with TestClient(app):
            pass

        with db.connect() as conn:
            row = conn.execute(
                text("SELECT state, finished_at IS NOT NULL FROM ai_questions")
            ).one()
        assert tuple(row) == ("failed", True)

    def test_a_closed_question_is_not_answered_again(self, db, user):
        qid = _ask(db, user, "once")

        aihistory.finish(qid, "failed", "Too late.", [])

        with db.connect() as conn:
            assert conn.execute(text("SELECT state FROM ai_questions")).scalar_one() == "done"


class TestTheRecordSurvivesBadInput:
    def test_a_change_made_after_its_question_was_cleared_is_still_recorded(self, db, user):
        qid = _ask(db, user, "cleared while working", done=False)
        aihistory.clear(user.id, qid)

        aihistory.add_step(qid, "Starting it...")
        aihistory.finish(qid, "done", "Done.", [])
        _did(user, qid, "Started 3 tracks")

        assert _actions(db) == [(None, user.id, "Started 3 tracks", "done")]

    def test_characters_the_database_refuses_are_dropped_not_the_row(self, db, user):
        qid = _ask(db, user, "odd", done=False)

        aihistory.add_step(qid, "step\x00 one \ud800")
        aihistory.finish(qid, "done", "answer\x00 \udfff", [])
        _did(user, qid, "Renamed\x00 it \ud800", body={"name\x00": ["a\x00b", "\ud800"]})

        with db.connect() as conn:
            assert conn.execute(text("SELECT answer FROM ai_questions")).scalar_one() == "answer ?"
            assert conn.execute(text("SELECT body FROM ai_actions")).scalar_one() == {
                "name": ["ab", "?"]
            }
        assert _actions(db)[0].summary == "Renamed it ?"


class TestTheHouseholdsAudit:
    def test_it_says_who_changed_what_and_where_to_look(self, client, db, user, member):
        playlist = str(uuid.uuid4())
        qid = _ask(db, member, "make a playlist")
        _did(member, qid, "Created playlist Evening", target=("playlist", playlist))
        _did(user, None, "Stopped the Study", tool="stop", outcome="failed", status=404)

        newest, oldest = client.get("/api/assistant/audit").json()

        assert (newest["who"], newest["outcome"], newest["target"]) == ("Tester", "failed", None)
        assert oldest.pop("id") and oldest.pop("at")
        assert oldest == {
            "who": "Sam",
            "tool": "create_playlist",
            "summary": "Created playlist Evening",
            "outcome": "done",
            "target": {"kind": "playlist", "id": playlist},
        }

    def test_it_can_be_narrowed_searched_and_paged(self, client, db, user, member):
        _did(member, None, "Created playlist Evening")
        _did(user, None, "Stopped the Study")

        def read(**params):
            got = client.get("/api/assistant/audit", params=params)
            assert got.status_code == 200
            return [a["summary"] for a in got.json()]

        assert read(user=str(member.id)) == ["Created playlist Evening"]
        assert read(q="playlst") == ["Created playlist Evening"]
        newest = client.get("/api/assistant/audit", params={"limit": 1}).json()
        assert read(before=newest[0]["at"]) == ["Created playlist Evening"]

    def test_the_export_carries_the_request_itself(self, client, db, user):
        _did(user, None, "Created playlist Evening", body={"name": "Evening"})
        _did(user, None, "Stopped the Study", path="/api/zones/x/stop", body=None)

        got = client.get("/api/assistant/audit/export", params=_range())

        assert got.status_code == 200
        assert got.headers["content-type"].startswith("application/x-ndjson")
        assert got.headers["content-disposition"].startswith("attachment; filename=")
        first, second = [json.loads(line) for line in got.text.splitlines()]
        assert (first["method"], first["path"], first["body"], first["status"]) == (
            "POST", "/api/playlists", {"name": "Evening"}, 200,
        )
        assert (second["path"], second["body"], second["who"]) == (
            "/api/zones/x/stop", {}, "Tester",
        )

    def test_the_export_holds_only_its_range(self, client, db, user):
        _did(user, None, "Long ago")
        with db.begin() as conn:
            conn.execute(text("UPDATE ai_actions SET at = now() - interval '10 days'"))
        _did(user, None, "Today")

        got = client.get("/api/assistant/audit/export", params=_range())

        assert [json.loads(line)["summary"] for line in got.text.splitlines()] == ["Today"]

    def test_a_range_over_92_days_is_refused(self, client, db):
        def ask(since: str, until: str) -> int:
            return client.get(
                "/api/assistant/audit/export", params={"since": since, "until": until}
            ).status_code

        assert ask("2026-01-01T00:00:00Z", "2026-04-03T00:00:00Z") == 200  # 92 days
        assert ask("2026-01-01T00:00:00Z", "2026-04-03T00:00:01Z") == 422
        assert ask("2026-02-01T00:00:00Z", "2026-01-01T00:00:00Z") == 422
        # With a zone on one end only, the two must still be comparable.
        assert ask("2026-01-01T00:00:00", "2026-01-02T00:00:00Z") == 200

    def test_only_an_administrator_reads_it(self, client, db, member):
        _act_as(member)

        assert client.get("/api/assistant/audit").status_code == 403
        assert client.get("/api/assistant/audit/export", params=_range()).status_code == 403

    def test_it_never_carries_what_was_asked(self, client, db, user, member):
        qid = _ask(db, member, f"make a playlist for {MARKER}", done=False)
        aihistory.add_step(qid, f"Searching for {MARKER}...")
        _did(member, qid, "Created playlist Evening")
        aihistory.finish(qid, "done", f"Made it, {MARKER}.", [])

        for params in ({}, {"q": "playlist"}, {"user": str(member.id)}):
            got = client.get("/api/assistant/audit", params=params)
            assert got.json() and MARKER not in got.text, params
        assert client.get("/api/assistant/audit", params={"q": MARKER}).json() == []
        exported = client.get("/api/assistant/audit/export", params=_range()).text
        assert exported and MARKER not in exported

    def test_an_unanswered_offer_expires(self, client, db, user):
        now = datetime.now(UTC)
        _did(user, None, "Delete the playlist Old", outcome="pending", status=None,
             expires_at=now - timedelta(seconds=1))
        _did(user, None, "Turn the Study up to 80?", outcome="pending", status=None,
             expires_at=now + timedelta(minutes=10))

        outcomes = {a["summary"]: a["outcome"] for a in client.get("/api/assistant/audit").json()}

        assert outcomes == {
            "Delete the playlist Old": "expired",
            "Turn the Study up to 80?": "pending",
        }


class TestOnTheStandby:
    @pytest.fixture
    def on_standby(self, monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("AI_PROVIDER", "openrouter")
        monkeypatch.setenv("AI_API_KEY", "not-a-real-key")
        monkeypatch.setenv("AI_MODEL", "vendor/alpha:free")

        def set_role(name: str) -> None:
            monkeypatch.setenv("HOMESH_ROLE", name)
            get_settings.cache_clear()

        yield set_role
        get_settings.cache_clear()

    def test_the_assistant_is_not_offered(self, client, db, on_standby):
        on_standby("primary")
        assert client.get("/api/ai/status").json()["features"]["assistant"] is True
        on_standby("standby")
        assert client.get("/api/ai/status").json()["features"]["assistant"] is False

    @pytest.mark.parametrize(
        "path", ["/api/assistant/history", f"/api/assistant/history/{uuid.uuid4()}"]
    )
    def test_nothing_is_cleared_there(self, path):
        assert standby.classify("DELETE", path) == "refused"
