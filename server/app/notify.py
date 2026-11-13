"""The mailbox: short messages for a person, fetched by their phones.

Homesh itself and the apps beside it post here; Homesh Connect asks for what is
new and shows it as an Android notification. There is no push service in
between (docs/design/07-notifications.md says why), so a phone either asks every
so often or holds a request open -- `wait_for` is what lets it hold one open
without holding a database connection with it.

A message carries a *path* inside the app that sent it, never an address.
`valid_path` is what keeps it so, and it is the line of defence that matters
most here: whatever a sender writes, joined to the app's own address it can
only ever land inside that app.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import unicodedata
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text

from .db import get_engine

log = logging.getLogger("homesh.notify")

MAX_TTL = timedelta(days=7)
DEFAULT_TTL = timedelta(days=1)

MAX_PATH = 512

# The sender name the server itself uses. Never configurable, so no app can
# post as Homesh.
HOMESH = "Homesh"

# More long polls than this at once and the next one returns at once: the
# phone simply asks again later. Bounds what a stolen token can make the
# server hold open.
MAX_WAITERS = 200

# What `to` means besides a handle. No account may have one of these as its
# handle: a message meant for that one person would otherwise go to everybody.
RESERVED_HANDLES = ("everyone", "admins")


def reserved_handle(handle: str) -> bool:
    return handle.strip().casefold() in RESERVED_HANDLES


def valid_path(path: str) -> bool:
    """True when `path`, appended to an app's address, stays inside that app.

    The rules, which the phone and the web app repeat exactly:
    - between 1 and 512 characters, starting with "/";
    - no "//" anywhere, so neither "//host" nor "/a//b";
    - no backslash, which some browsers read as a slash;
    - no control or invisible formatting character (newlines included);
    - no "@" or ":" before the first "/" after the start, so the first
      segment can never read as credentials, a host or a scheme.
    """
    if not isinstance(path, str) or not path or len(path) > MAX_PATH:
        return False
    if not path.startswith("/") or "//" in path or "\\" in path:
        return False
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in path):
        return False
    first = path[1:].split("/", 1)[0]
    return "@" not in first and ":" not in first


# ── Sending ─────────────────────────────────────────────────────────────────

# Recipients, by what `to` says. Fixed statements, so nothing from a request
# reaches the SQL text.
_INSERT = """
    INSERT INTO notify_messages (user_id, sender, title, body, path, tag, expires_at)
    SELECT id, :sender, :title, :body, :path, :tag, :expires
    FROM users WHERE {who}
    RETURNING user_id
"""
_EVERYONE = text(_INSERT.format(who="TRUE"))
_ADMINS = text(_INSERT.format(who="is_admin"))
_ONE = text(_INSERT.format(who="handle = :handle"))
_BY_ID = text(_INSERT.format(who="id = :uid"))


def send(
    *,
    sender: str,
    to: str | UUID,
    title: str,
    body: str = "",
    path: str = "/",
    tag: str | None = None,
    ttl: timedelta = DEFAULT_TTL,
) -> int:
    """Post one message to `to`: a handle, "everyone" or "admins" -- or, from
    inside the server, an account's id, which can never mean anyone else.

    Returns how many people it was written for. An unknown handle writes
    nothing and is not an error, so a sender cannot use this to find out which
    handles exist.
    """
    if not valid_path(path):
        raise ValueError("a message's path must stay inside the app that sent it")
    ttl = max(timedelta(minutes=1), min(ttl, MAX_TTL))
    params = {
        "sender": sender,
        "title": title,
        "body": body,
        "path": path,
        "tag": tag,
        "expires": datetime.now(UTC) + ttl,
    }
    if isinstance(to, UUID):
        statement, params["uid"] = _BY_ID, str(to)
    elif to == "everyone":
        statement = _EVERYONE
    elif to == "admins":
        statement = _ADMINS
    else:
        statement, params["handle"] = _ONE, to

    with get_engine().begin() as conn:
        recipients = conn.execute(statement, params).scalars().all()

    # After the commit, so a phone woken by this finds the row there.
    _wake(recipients)
    return len(recipients)


# ── Reading ─────────────────────────────────────────────────────────────────


def _message(row, *, with_tag: bool = False, with_read: bool = False) -> dict:
    out = {
        "id": row.id,
        "at": row.at.isoformat(),
        "sender": row.sender,
        "title": row.title,
        "body": row.body,
        "path": row.path,
    }
    if with_tag:
        out["tag"] = row.tag
    if with_read:
        out["read"] = row.read_at is not None
    return out


def inbox(user_id: UUID, after: int, limit: int = 50) -> list[dict]:
    """What a phone has not had yet, oldest first, nothing expired."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, at, sender, title, body, path, tag
                FROM notify_messages
                WHERE user_id = :u AND id > :after AND expires_at > now()
                ORDER BY id
                LIMIT :n
                """
            ),
            {"u": str(user_id), "after": after, "n": limit},
        ).all()
    return [_message(r, with_tag=True) for r in rows]


def newest_id(user_id: UUID) -> int:
    """Where a newly registered phone starts, so it does not replay a week."""
    with get_engine().connect() as conn:
        found = conn.execute(
            text("SELECT max(id) FROM notify_messages WHERE user_id = :u"),
            {"u": str(user_id)},
        ).scalar()
    return int(found or 0)


def mine(user_id: UUID, limit: int = 50) -> list[dict]:
    """A person's latest messages, newest first, for the web app's list."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, at, sender, title, body, path, read_at
                FROM notify_messages
                WHERE user_id = :u AND expires_at > now()
                ORDER BY id DESC
                LIMIT :n
                """
            ),
            {"u": str(user_id), "n": limit},
        ).all()
    return [_message(r, with_read=True) for r in rows]


def mark_read(user_id: UUID, upto: int) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE notify_messages SET read_at = now()
                WHERE user_id = :u AND id <= :upto AND read_at IS NULL
                """
            ),
            {"u": str(user_id), "upto": upto},
        )
    return result.rowcount


def prune() -> int:
    """Throw away expired messages and claims that are spent or stale."""
    with get_engine().begin() as conn:
        messages = conn.execute(
            text("DELETE FROM notify_messages WHERE expires_at <= now()")
        ).rowcount
        claims = conn.execute(
            text("DELETE FROM notify_claims WHERE used_at IS NOT NULL OR expires_at <= now()")
        ).rowcount
    return messages + claims


# ── Waiting ─────────────────────────────────────────────────────────────────
#
# A long poll waits on an event, not on the database. `send` usually runs on a
# worker thread (a plain `def` route, or the upkeep loop's to_thread), while a
# waiter's event belongs to the event loop, so the event is set through
# `call_soon_threadsafe` and the bookkeeping is under a plain lock.
#
# Each send also bumps a per-person counter. A route reads it before looking
# in the mailbox and hands it to `wait_for`, which returns at once if anything
# was sent in between -- otherwise a message landing in that gap would wait out
# the whole poll.

_lock = threading.Lock()
_waiters: dict[UUID, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}
_sent: dict[UUID, int] = {}


def generation(user_id: UUID) -> int:
    with _lock:
        return _sent.get(user_id, 0)


def _wake(user_ids) -> None:
    with _lock:
        woken = []
        for uid in user_ids:
            _sent[uid] = _sent.get(uid, 0) + 1
            woken.extend(_waiters.get(uid, ()))
    for loop, event in woken:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            # The loop has closed; nobody is left waiting on it.
            pass


async def wait_for(user_id: UUID, seconds: float, since: int | None = None) -> None:
    """Return when something is sent to this person, or after `seconds`.

    `since` is a `generation()` read earlier; if a message went out after it,
    this returns at once.
    """
    entry = (asyncio.get_running_loop(), asyncio.Event())
    with _lock:
        if since is not None and _sent.get(user_id, 0) != since:
            return
        if sum(len(s) for s in _waiters.values()) >= MAX_WAITERS:
            return
        _waiters.setdefault(user_id, set()).add(entry)
    try:
        await asyncio.wait_for(entry[1].wait(), seconds)
    except TimeoutError:
        pass
    finally:
        with _lock:
            held = _waiters.get(user_id)
            if held is not None:
                held.discard(entry)
                if not held:
                    del _waiters[user_id]
