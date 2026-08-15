"""Who may see what, and play where.

Two independent allow-lists per person: which parts of the library they can see,
and which rooms they can play in. **No rules means no restriction** — so the
adults need no configuration at all, and restriction is something you apply to
the accounts that need it.

Enforcement has to happen on every read path, including the ones that do not
carry a session. A signed media URL authorises by token rather than cookie, so
the check is repeated when the bytes are served: otherwise a link shared between
two accounts would walk straight past the scope.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text

from .db import get_engine

log = logging.getLogger("homesh.access")


def library_rules(user_id: UUID) -> list[str] | None:
    """Allowed path prefixes, or None when the account is unrestricted."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT path_prefix FROM user_library_rules WHERE user_id = :u"),
            {"u": str(user_id)},
        ).all()
    return [r[0].rstrip("/") for r in rows] if rows else None


def zone_rules(user_id: UUID) -> set[UUID] | None:
    """Allowed zone ids, or None when the account may use any room."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT zone_id FROM user_zone_rules WHERE user_id = :u"),
            {"u": str(user_id)},
        ).all()
    return {r[0] for r in rows} if rows else None


def can_read(path: str, rules: list[str] | None) -> bool:
    """May this person open things at this path?"""
    if rules is None:
        return True
    path = "/" + path.strip("/")
    return any(path == r or path.startswith(r + "/") for r in rules)


def can_traverse(path: str, rules: list[str] | None) -> bool:
    """May this person navigate *through* here to reach something allowed?

    Someone restricted to /local/library/Music still has to be able to open
    /local/library to get there — while seeing only Music inside it.
    """
    if rules is None:
        return True
    path = "/" + path.strip("/")
    if can_read(path, rules):
        return True
    return any(r == path or r.startswith(path + "/") for r in rules)


def visible(path: str, rules: list[str] | None) -> bool:
    """Should this entry appear in a listing at all?"""
    return can_read(path, rules) or can_traverse(path, rules)


def can_use_zone(zone_id: UUID, rules: set[UUID] | None) -> bool:
    return rules is None or zone_id in rules


def item_paths(item_id: UUID) -> list[str]:
    """Every virtual path an item is reachable at.

    An item can have replicas in more than one source, so permission is decided
    across all of them rather than whichever happened to be listed first.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.mount_prefix, r.dir_path
                FROM replicas r JOIN sources s ON s.id = r.source_id
                WHERE r.item_id = :id
                """
            ),
            {"id": str(item_id)},
        ).all()
    return [f"{prefix}/{dir_path}".rstrip("/") for prefix, dir_path in rows]


def may_access_item(item_id: UUID, user_id: UUID) -> bool:
    """Whether this person may open this item, wherever it lives."""
    rules = library_rules(user_id)
    if rules is None:
        return True
    paths = item_paths(item_id)
    # No replica at all is a missing item, not a permission decision; the caller
    # turns that into a 404.
    return any(can_read(p, rules) for p in paths)
