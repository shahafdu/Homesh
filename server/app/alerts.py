"""Alerts to administrators: who signed in, what knocked, who changed what.

Everything here goes through `notify.send`, to administrators only, from
"Homesh", and says *who* and *what kind of thing* -- never the name of a
folder, a file, a playlist or a room, and never an address. A phone's lock
screen is not a safe place for what the library holds.

Nothing in this module may break the thing that triggered it. Sign-in calls
`on_audit` after the audit row is written, so every entry point catches
everything and logs. The "at most once an hour" limits live in memory: a
restart costs, at worst, one notification too many.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter, deque
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text

from . import notify
from .db import get_engine

log = logging.getLogger("homesh.alerts")

HOUR = 3600.0

FAILED_LOGINS = 5
FAILED_WINDOW = 600.0

# How long a burst of changes is gathered before one message is sent.
BURST_SECONDS = 600.0

SIGNINS = "alert_signins"
CHANGES = "alert_changes"

_lock = threading.Lock()
_last_sent: dict[str, float] = {}
_failures: deque[float] = deque()

# The doors a rate limit guards, in words. A bucket not listed is still
# reported, as "a door", so a new limit is never silent.
DOORS = {
    "sign-in": "sign-in",
    "challenge": "sign-in",
    "register": "invitations",
    "invite": "invitations",
    "codes": "sign-in codes",
    "pairing": "screen pairing",
    "crashes": "crash reports",
    "notify-key": "sending keys",
    "notify-send": "sending messages",
    "notify-claim": "phone codes",
    "notify-token": "phone tokens",
}

# Routes whose successful writes count as "someone changed something". Routes
# added by later tasks are NOT watched until a row is added here: the table is
# the list, and anything not on it is silent. A convenience for the household,
# not an audit -- the audit log remains the record.
WATCHED: list[tuple[re.Pattern[str], frozenset[str] | None, str]] = [
    (re.compile(r"^/api/playlists(/|$)"), None, "playlists"),
    (re.compile(r"^/api/library/items/[^/]+/tags$"), None, "song and file details"),
    (re.compile(r"^/api/faces/"), None, "faces"),
    (re.compile(r"^/api/sharing/"), None, "shared links"),
    (re.compile(r"^/api/people(/|$)"), None, "accounts and access"),
    (re.compile(r"^/api/library/sources(/|$)"), None, "folders"),
    (re.compile(r"^/api/zones/?$"), None, "rooms"),
    (re.compile(r"^/api/zones/[^/]+$"), frozenset({"PUT", "DELETE"}), "rooms"),
    (re.compile(r"^/api/backups(/|$)"), None, "backups"),
    (re.compile(r"^/api/ai/"), None, "assistant settings"),
    (re.compile(r"^/api/notify/devices"), None, "notification phones"),
]
_WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# actor id -> (counts by kind, whether every change was the assistant's)
_bursts: dict[UUID, tuple[Counter, bool]] = {}


def clear() -> None:
    """For tests, and for nothing else."""
    with _lock:
        _last_sent.clear()
        _failures.clear()
        _bursts.clear()


def _due(key: str, every: float, now: float | None = None) -> bool:
    """True, and remembered, when `key` has not been sent within `every`."""
    now = time.monotonic() if now is None else now
    with _lock:
        last = _last_sent.get(key)
        if last is not None and now - last < every:
            return False
        _last_sent[key] = now
        return True


def _admins(pref: str | None, *, skip: UUID | None = None) -> list[UUID]:
    """Administrators who want this kind of alert. `pref` None means all."""
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT id, prefs FROM users WHERE is_admin")).all()
    out = []
    for uid, prefs in rows:
        if skip is not None and uid == skip:
            continue
        if pref is not None and (prefs or {}).get(pref, "on") == "off":
            continue
        out.append(uid)
    return out


def _tell(
    title: str, body: str, tag: str | None, pref: str | None, skip: UUID | None = None
) -> None:
    for uid in _admins(pref, skip=skip):
        notify.send(
            sender=notify.HOMESH, to=uid, title=title[:80], body=body[:300],
            path="/", tag=tag, ttl=timedelta(days=1),
        )


def _name(user_id: UUID | None, fallback: str = "Someone") -> str:
    if user_id is None:
        return fallback
    with get_engine().connect() as conn:
        name = conn.execute(
            text("SELECT display_name FROM users WHERE id = :u"), {"u": str(user_id)}
        ).scalar()
    return name or fallback


# ── Security events ─────────────────────────────────────────────────────────


def on_audit(event: str, user_id: UUID | None, detail: dict) -> None:
    try:
        _on_audit(event, user_id, detail or {})
    except Exception:  # noqa: BLE001 - signing in must never depend on this
        log.exception("could not raise an alert for %s", event)


def _on_audit(event: str, user_id: UUID | None, detail: dict) -> None:
    if event == "auth.login":
        label = str(detail.get("label") or "")
        _tell(f"{_name(user_id)} signed in", label, None, SIGNINS)
    elif event == "auth.login.failed":
        now = time.monotonic()
        with _lock:
            _failures.append(now)
            while _failures and now - _failures[0] > FAILED_WINDOW:
                _failures.popleft()
            count = len(_failures)
        if count >= FAILED_LOGINS and _due("login-failed", HOUR, now):
            _tell("Failed sign-in attempts", f"{count} in the last 10 minutes",
                  "login-failed", SIGNINS)
    elif event == "auth.passkey.added":
        _tell(f"{_name(user_id)} added a passkey", "", None, SIGNINS)
    elif event == "auth.passkey.removed":
        _tell(f"{_name(user_id)} removed a passkey", "", None, SIGNINS)
    elif event == "auth.signcount.anomaly":
        # Ignores the preference on purpose: this one is about a stolen key.
        _tell("A passkey may have been copied",
              f"{_name(user_id)}'s passkey was used out of order. "
              "Remove it and make a new one.", "passkey-copied", None)
    elif event == "auth.device_link.claimed":
        _tell(f"{_name(user_id)} signed in a new device with a code", "", None, SIGNINS)
    elif event == "auth.register" and detail.get("invited"):
        # The row is not committed yet when this runs, so the name may not be
        # readable: the handle in the entry is the fallback.
        _tell(f"{_name(user_id, str(detail.get('handle') or 'Someone'))} joined Homesh",
              "", None, SIGNINS)


def on_pairing_started() -> None:
    try:
        if _due("pairing", HOUR):
            _tell("A screen is asking to be paired", "", "pairing", SIGNINS)
    except Exception:  # noqa: BLE001
        log.exception("could not raise a pairing alert")


def on_paired(user_id: UUID) -> None:
    try:
        _tell(f"{_name(user_id)} paired a screen", "", None, SIGNINS)
    except Exception:  # noqa: BLE001
        log.exception("could not raise a pairing alert")


def on_throttle(bucket: str, window: timedelta) -> None:
    """A limit was tripped. Called once per caller per window by throttle.hit;
    this narrows it to once per door per window."""
    try:
        if _due(f"throttle:{bucket}", window.total_seconds()):
            door = DOORS.get(bucket, "a door")
            _tell("Too many attempts", f"Somebody is knocking too often at {door}.",
                  f"throttle-{bucket}"[:64], SIGNINS)
    except Exception:  # noqa: BLE001
        log.exception("could not raise a rate-limit alert")


# ── Changes made by other people ────────────────────────────────────────────


def watched_kind(method: str, path: str) -> str | None:
    if method not in _WRITES:
        return None
    for pattern, methods, kind in WATCHED:
        if pattern.search(path) and (methods is None or method in methods):
            return kind
    return None


def record_change(actor: UUID, kind: str, *, via_assistant: bool = False) -> None:
    """Count one change; the first of a burst starts the timer that reports it."""
    try:
        with _lock:
            known = _bursts.get(actor)
            if known is None:
                _bursts[actor] = (Counter({kind: 1}), via_assistant)
                timer = threading.Timer(BURST_SECONDS, flush, args=(actor,))
                timer.daemon = True
                timer.start()
            else:
                known[0][kind] += 1
                _bursts[actor] = (known[0], known[1] and via_assistant)
    except Exception:  # noqa: BLE001
        log.exception("could not count a change")


def _summary(counts: Counter) -> str:
    return ", ".join(f"{n} {kind}" for kind, n in counts.most_common())


def flush(actor: UUID) -> None:
    """Send one message for the actor's burst, to every administrator but them."""
    try:
        with _lock:
            counts, assistant = _bursts.pop(actor, (Counter(), False))
        if not counts:
            return
        text_ = _summary(counts) + (" through the assistant" if assistant else "")
        _tell(f"{_name(actor)} made changes", text_, f"changes-{str(actor)[:8]}",
              CHANGES, skip=actor)
    except Exception:  # noqa: BLE001
        log.exception("could not send a change summary")
