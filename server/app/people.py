"""Managing who has an account and what they may reach.

Kept apart from auth, which is about proving who you are. This is about what that
person is allowed to do once proven.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from .db import get_engine
from .security import CurrentUser, audit, require_user

log = logging.getLogger("homesh.people")
router = APIRouter(prefix="/api/people", tags=["people"])


class RulesUpdate(BaseModel):
    # Virtual path prefixes, e.g. ["/local/library/Music"]. An empty list means
    # no restriction — which is the difference between "everything" and
    # "nothing", so it is worth being explicit about in the UI.
    library: list[str] | None = Field(default=None, max_length=100)
    zones: list[UUID] | None = Field(default=None, max_length=50)


def _require_admin(user: CurrentUser) -> None:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")


@router.get("")
async def list_people(user: CurrentUser = Depends(require_user)) -> list[dict]:
    _require_admin(user)

    with get_engine().connect() as conn:
        people = conn.execute(
            text(
                """
                SELECT u.id, u.handle, u.display_name, u.is_admin, u.created_at,
                       count(c.id) AS passkeys
                FROM users u
                LEFT JOIN credentials c ON c.user_id = u.id
                GROUP BY u.id
                ORDER BY u.created_at
                """
            )
        ).all()

        library = conn.execute(
            text("SELECT user_id, path_prefix FROM user_library_rules")
        ).all()
        zones = conn.execute(
            text(
                """
                SELECT r.user_id, r.zone_id, z.name
                FROM user_zone_rules r JOIN zones z ON z.id = r.zone_id
                """
            )
        ).all()

    lib_by_user: dict[UUID, list[str]] = {}
    for uid, prefix in library:
        lib_by_user.setdefault(uid, []).append(prefix)

    zones_by_user: dict[UUID, list[dict]] = {}
    for uid, zid, name in zones:
        zones_by_user.setdefault(uid, []).append({"id": str(zid), "name": name})

    return [
        {
            "id": str(p[0]),
            "handle": p[1],
            "display_name": p[2],
            "is_admin": p[3],
            "created_at": p[4].isoformat(),
            "passkeys": p[5],
            # None rather than [] when unrestricted, because "no rules" and "an
            # empty allow-list" would otherwise look identical and mean opposite
            # things.
            "library": lib_by_user.get(p[0]),
            "zones": zones_by_user.get(p[0]),
        }
        for p in people
    ]


@router.put("/{user_id}/rules")
async def set_rules(
    user_id: UUID, body: RulesUpdate, user: CurrentUser = Depends(require_user)
) -> dict:
    """Replace someone's access rules.

    Sending null for either list leaves that dimension alone; sending an empty
    list removes the restriction entirely.
    """
    _require_admin(user)

    with get_engine().connect() as conn:
        target = conn.execute(
            text("SELECT is_admin FROM users WHERE id = :id"), {"id": str(user_id)}
        ).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such person")

    if target[0]:
        # Restricting an admin would be theatre: they can edit the rules.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "administrators are not restricted — they can change these rules",
        )

    with get_engine().begin() as conn:
        if body.library is not None:
            conn.execute(
                text("DELETE FROM user_library_rules WHERE user_id = :u"),
                {"u": str(user_id)},
            )
            for prefix in body.library:
                cleaned = "/" + prefix.strip().strip("/")
                if cleaned == "/":
                    continue  # "everything" is expressed by having no rules
                conn.execute(
                    text(
                        """
                        INSERT INTO user_library_rules (user_id, path_prefix)
                        VALUES (:u, :p) ON CONFLICT DO NOTHING
                        """
                    ),
                    {"u": str(user_id), "p": cleaned},
                )

        if body.zones is not None:
            conn.execute(
                text("DELETE FROM user_zone_rules WHERE user_id = :u"), {"u": str(user_id)}
            )
            for zone_id in body.zones:
                conn.execute(
                    text(
                        """
                        INSERT INTO user_zone_rules (user_id, zone_id)
                        VALUES (:u, :z) ON CONFLICT DO NOTHING
                        """
                    ),
                    {"u": str(user_id), "z": str(zone_id)},
                )

        audit(
            conn,
            "access.rules.changed",
            user.id,
            {
                "subject": str(user_id),
                "library": body.library,
                "zones": [str(z) for z in (body.zones or [])],
            },
            None,
        )

    return {"ok": True}


@router.delete("/{user_id}")
async def remove_person(user_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    _require_admin(user)

    if user_id == user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "you cannot remove your own account")

    with get_engine().begin() as conn:
        admins = conn.execute(text("SELECT count(*) FROM users WHERE is_admin")).scalar_one()
        target = conn.execute(
            text("SELECT is_admin FROM users WHERE id = :id"), {"id": str(user_id)}
        ).first()
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such person")
        if target[0] and admins <= 1:
            # Locking everyone out of administration is not a recoverable state.
            raise HTTPException(status.HTTP_409_CONFLICT, "that is the only administrator")

        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": str(user_id)})
        audit(conn, "person.removed", user.id, {"subject": str(user_id)}, None)

    return {"ok": True}
