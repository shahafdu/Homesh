"""Who may see what, and play where.

Access is **explicit**: an account reaches only what it has been granted. Nothing
granted means nothing reachable, which is what an empty list of ticks looks like
and therefore what it should mean.

"Everything" is a stored fact of its own rather than the absence of rules, so the
two states cannot be confused — the earlier design, where no rules meant no
restriction, read exactly backwards in the one place that matters most.

Enforcement happens on every read path, including those that carry no session. A
signed media URL authorises by token, so the check is repeated when bytes are
served: otherwise a link forwarded between accounts would walk past the scope.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text

from .db import get_engine

log = logging.getLogger("homesh.access")


@dataclass(frozen=True)
class Scope:
    """What one account may reach.

    `everything` is separate from an empty list on purpose: "all folders" and "no
    folders" must not be representable by the same value.
    """

    everything: bool
    allowed: tuple[str, ...] = ()

    @property
    def nothing(self) -> bool:
        return not self.everything and not self.allowed


def library_scope(user_id: UUID) -> Scope:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT is_admin, all_library FROM users WHERE id = :u"), {"u": str(user_id)}
        ).first()
        if row is None:
            return Scope(everything=False)
        is_admin, all_library = row

        # Administrators are unrestricted by definition: they can edit these
        # rules, so enforcing them would be theatre.
        if is_admin or all_library:
            return Scope(everything=True)

        rules = conn.execute(
            text("SELECT path_prefix FROM user_library_rules WHERE user_id = :u"),
            {"u": str(user_id)},
        ).all()

    return Scope(everything=False, allowed=tuple(r[0].rstrip("/") for r in rules))


def zone_scope(user_id: UUID) -> tuple[bool, set[UUID]]:
    """(may use any room, otherwise the specific rooms allowed)."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT is_admin, all_zones FROM users WHERE id = :u"), {"u": str(user_id)}
        ).first()
        if row is None:
            return False, set()
        is_admin, all_zones = row
        if is_admin or all_zones:
            return True, set()

        rules = conn.execute(
            text("SELECT zone_id FROM user_zone_rules WHERE user_id = :u"),
            {"u": str(user_id)},
        ).all()
    return False, {r[0] for r in rules}


def can_read(path: str, scope: Scope) -> bool:
    """May this person open things at this path?"""
    if scope.everything:
        return True
    if scope.nothing:
        return False
    path = "/" + path.strip("/")
    return any(path == r or path.startswith(r + "/") for r in scope.allowed)


def can_traverse(path: str, scope: Scope) -> bool:
    """May this person navigate *through* here to reach something allowed?

    Someone granted only /local/library/Music still has to open /local/library to
    get there — while seeing nothing else inside it.
    """
    if scope.everything:
        return True
    if scope.nothing:
        return False
    path = "/" + path.strip("/")
    if can_read(path, scope):
        return True
    return any(r == path or r.startswith(path + "/") for r in scope.allowed)


def visible(path: str, scope: Scope) -> bool:
    """Should this entry appear in a listing at all?"""
    return can_read(path, scope) or can_traverse(path, scope)


def can_use_zone(zone_id: UUID, any_zone: bool, allowed: set[UUID]) -> bool:
    return any_zone or zone_id in allowed


def item_paths(item_id: UUID) -> list[str]:
    """Every virtual path an item is reachable at.

    An item can have replicas in more than one source, so permission is decided
    across all of them rather than whichever was listed first.
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
    scope = library_scope(user_id)
    if scope.everything:
        return True
    if scope.nothing:
        return False
    return any(can_read(p, scope) for p in item_paths(item_id))
