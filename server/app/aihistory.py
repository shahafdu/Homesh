"""What the assistant was asked, and what it did.

Two records with two readers, kept apart on purpose:

- `ai_questions` belongs to the person who asked. Only they read it, and they
  can clear it.
- `ai_actions` belongs to the household. It holds changes only -- made, refused
  or waiting for a yes -- and administrators read it. Clearing a question does
  not erase what was done because of it, and nothing here puts a question's text
  into it: an administrator sees what changed without seeing how somebody else
  worded it. Whoever calls `record_action` writes `summary` and `body` from what
  was done, never from what was asked.

This module stores and reads. It decides nothing about what the assistant may
do: that is decided by the route each of its requests goes through.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text as sql
from sqlalchemy.engine import Connection

from .db import get_engine

# Far beyond the time a question is allowed to take, so only one that nothing is
# working on any more -- the server stopped under it -- is ever this old.
STALE_AFTER = timedelta(minutes=3)
STALE_ANSWER = "The server restarted before this was answered. Ask again."

# One request's worth of the audit. A longer record is read in several.
MAX_EXPORT = timedelta(days=92)


def _clean(value: str) -> str:
    """Postgres refuses a NUL and half of a surrogate pair, and a model's words
    can hold either. Losing the character is better than losing the row, which
    for an action would mean a change with no record of it."""
    return value.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


def _clean_json(value):
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, dict):
        return {_clean(str(k)): _clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(v) for v in value]
    return value


def _json(value) -> str:
    # `default=str` for the ids, which arrive as UUIDs.
    return json.dumps(_clean_json(value), default=str)


def aware(when: datetime) -> datetime:
    """A time given without a zone is taken as UTC, which is what is stored."""
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _needle(q: str | None) -> str | None:
    return _clean(q or "").strip() or None


def start_question(
    conn: Connection, user_id: UUID, conversation: UUID, text: str, spoken: bool
) -> UUID:
    """Write a question down as 'thinking'.

    On the caller's connection, so that whatever it checks first -- one question
    at a time -- is checked in the same transaction.
    """
    return conn.execute(
        sql(
            """
            INSERT INTO ai_questions (user_id, conversation, text, spoken)
            VALUES (:u, :c, :t, :s)
            RETURNING id
            """
        ),
        {"u": str(user_id), "c": str(conversation), "t": _clean(text), "s": spoken},
    ).scalar_one()


def add_step(question_id: UUID, text: str) -> None:
    """Append one progress line. Nothing happens if the question was cleared."""
    step = {"at": datetime.now(UTC).isoformat(), "text": text}
    with get_engine().begin() as conn:
        conn.execute(
            sql("UPDATE ai_questions SET steps = steps || CAST(:s AS jsonb) WHERE id = :i"),
            # An array, so that appending adds one element.
            {"s": _json([step]), "i": str(question_id)},
        )


def finish(question_id: UUID, state: str, answer: str | None, results: list) -> None:
    """Close a question. One already closed stays as it is."""
    with get_engine().begin() as conn:
        conn.execute(
            sql(
                """
                UPDATE ai_questions
                SET state = :s, answer = :a, results = CAST(:r AS jsonb), finished_at = now()
                WHERE id = :i AND state = 'thinking'
                """
            ),
            {
                "s": state,
                "a": None if answer is None else _clean(answer),
                "r": _json(results),
                "i": str(question_id),
            },
        )


def record_action(
    *,
    question_id: UUID | None,
    user_id: UUID | None,
    tool: str,
    method: str,
    path: str,
    body: dict | None,
    outcome: str,
    status: int | None = None,
    summary: str,
    target: tuple[str, str] | None = None,
    expires_at: datetime | None = None,
) -> UUID:
    """Put one change, made or not, on the household's record."""
    kind, target_id = target if target else (None, None)
    with get_engine().begin() as conn:
        return conn.execute(
            sql(
                # The question and the account are looked up rather than written
                # as given: either can be removed while the change is being made,
                # and the change still has to be recorded.
                """
                INSERT INTO ai_actions (question_id, user_id, tool, method, path, body,
                                        outcome, status, summary, target_kind, target_id,
                                        expires_at)
                VALUES ((SELECT id FROM ai_questions WHERE id = CAST(:q AS uuid)),
                        (SELECT id FROM users WHERE id = CAST(:u AS uuid)),
                        :tool, :method, :path, CAST(:body AS jsonb),
                        :outcome, :status, :summary, :kind, :target, :expires)
                RETURNING id
                """
            ),
            {
                "q": str(question_id) if question_id else None,
                "u": str(user_id) if user_id else None,
                "tool": tool,
                "method": method,
                "path": _clean(path),
                "body": _json(body or {}),
                "outcome": outcome,
                "status": status,
                "summary": _clean(summary),
                "kind": kind,
                "target": None if target_id is None else str(target_id),
                "expires": expires_at,
            },
        ).scalar_one()


def my_history(
    user_id: UUID, q: str | None = None, before: datetime | None = None, limit: int = 30
) -> list[dict]:
    """One person's own questions, newest first, with what each one changed."""
    close_stale()
    expire_pending()
    with get_engine().connect() as conn:
        # Trigram likeness beside the plain match, as in the file-name search:
        # Postgres has no stemmer for several languages a household may use.
        # The likeness works on ASCII letters only (the database's locale is C);
        # the plain match works on any script.
        rows = conn.execute(
            sql(
                """
                SELECT id, asked_at, text, spoken, state, answer
                FROM ai_questions
                WHERE user_id = :u
                  AND (CAST(:q AS text) IS NULL
                       OR text ILIKE '%' || CAST(:q AS text) || '%'
                       OR word_similarity(CAST(:q AS text), text) > 0.3)
                  AND (CAST(:before AS timestamptz) IS NULL
                       OR asked_at < CAST(:before AS timestamptz))
                ORDER BY asked_at DESC, id DESC
                LIMIT :n
                """
            ),
            {
                "u": str(user_id),
                "q": _needle(q),
                "before": aware(before) if before else None,
                "n": limit,
            },
        ).mappings().all()
        done: dict[UUID, list[dict]] = {r["id"]: [] for r in rows}
        if done:
            for question_id, summary, outcome in conn.execute(
                sql(
                    """
                    SELECT question_id, summary, outcome FROM ai_actions
                    WHERE question_id = ANY(CAST(:ids AS uuid[]))
                    ORDER BY at, id
                    """
                ),
                {"ids": [str(i) for i in done]},
            ):
                done[question_id].append({"summary": summary, "outcome": outcome})
    return [
        {
            "id": str(r["id"]),
            "asked_at": r["asked_at"].isoformat(),
            "text": r["text"],
            "spoken": r["spoken"],
            "state": r["state"],
            "answer": r["answer"],
            "actions": done[r["id"]],
        }
        for r in rows
    ]


def clear(user_id: UUID, question_id: UUID | None) -> int:
    """Remove one of a person's questions, or all of them. Returns how many.

    The owner is part of the statement, so somebody else's id removes nothing.
    """
    with get_engine().begin() as conn:
        return conn.execute(
            sql(
                """
                DELETE FROM ai_questions
                WHERE user_id = :u
                  AND (CAST(:i AS uuid) IS NULL OR id = CAST(:i AS uuid))
                """
            ),
            {"u": str(user_id), "i": str(question_id) if question_id else None},
        ).rowcount


def audit(
    q: str | None = None, user_id: UUID | None = None, before: datetime | None = None,
    limit: int = 50,
) -> list[dict]:
    """The household's record of changes, newest first.

    Never reads `ai_questions`: what somebody asked is not part of this.
    """
    expire_pending()
    with get_engine().connect() as conn:
        rows = conn.execute(
            sql(
                """
                SELECT a.id, a.at, u.display_name, a.tool, a.summary, a.outcome,
                       a.target_kind, a.target_id
                FROM ai_actions a
                LEFT JOIN users u ON u.id = a.user_id
                WHERE (CAST(:q AS text) IS NULL
                       OR a.summary ILIKE '%' || CAST(:q AS text) || '%'
                       OR word_similarity(CAST(:q AS text), a.summary) > 0.3)
                  AND (CAST(:u AS uuid) IS NULL OR a.user_id = CAST(:u AS uuid))
                  AND (CAST(:before AS timestamptz) IS NULL
                       OR a.at < CAST(:before AS timestamptz))
                ORDER BY a.at DESC, a.id DESC
                LIMIT :n
                """
            ),
            {
                "q": _needle(q),
                "u": str(user_id) if user_id else None,
                "before": aware(before) if before else None,
                "n": limit,
            },
        ).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "at": r["at"].isoformat(),
            # Nobody named means the account has since been removed.
            "who": r["display_name"],
            "tool": r["tool"],
            "summary": r["summary"],
            "outcome": r["outcome"],
            "target": (
                {"kind": r["target_kind"], "id": r["target_id"]} if r["target_kind"] else None
            ),
        }
        for r in rows
    ]


def export(since: datetime, until: datetime) -> Iterator[str]:
    """Every action from `since` up to but not including `until`, oldest first,
    as one JSON object per line, with the request each one made.

    The rows are read here, before anything is sent: a response streamed from
    a query that has not run yet has already answered 200 when the query
    fails, and the administrator keeps a file that silently stops part-way.
    """
    expire_pending()
    with get_engine().connect() as conn:
        rows = conn.execute(
            sql(
                """
                SELECT a.id, a.at, u.display_name AS who, a.tool, a.method, a.path, a.body,
                       a.outcome, a.status, a.summary, a.target_kind, a.target_id,
                       a.expires_at
                FROM ai_actions a
                LEFT JOIN users u ON u.id = a.user_id
                WHERE a.at >= :since AND a.at < :until
                ORDER BY a.at, a.id
                """
            ),
            {"since": aware(since), "until": aware(until)},
        ).mappings().all()
    return _lines(rows)


def _lines(rows) -> Iterator[str]:
    for r in rows:
        line = {
            "id": str(r["id"]),
            "at": r["at"].isoformat(),
            "who": r["who"],
            "tool": r["tool"],
            "method": r["method"],
            "path": r["path"],
            "body": r["body"],
            "outcome": r["outcome"],
            "status": r["status"],
            "summary": r["summary"],
            "target": (
                {"kind": r["target_kind"], "id": r["target_id"]} if r["target_kind"] else None
            ),
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
        }
        # ASCII escapes, so no character in a name can break a line in two.
        yield json.dumps(line) + "\n"


def expire_pending() -> int:
    """An offer nobody answered in time is no longer an offer."""
    with get_engine().begin() as conn:
        return conn.execute(
            sql(
                "UPDATE ai_actions SET outcome = 'expired' "
                "WHERE outcome = 'pending' AND expires_at <= now()"
            )
        ).rowcount


def close_stale() -> int:
    """Fail the questions nothing is working on, so none is 'thinking' for ever."""
    with get_engine().begin() as conn:
        return conn.execute(
            sql(
                """
                UPDATE ai_questions
                SET state = 'failed', answer = :a, finished_at = now()
                WHERE state = 'thinking' AND asked_at < now() - CAST(:age AS interval)
                """
            ),
            {"a": STALE_ANSWER, "age": STALE_AFTER},
        ).rowcount
