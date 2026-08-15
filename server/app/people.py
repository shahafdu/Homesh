"""Managing who has an account and what they may reach.

Kept apart from auth, which is about proving who you are. This is about what that
person is allowed to do once proven.

Two rules shape everything here:

**Access is explicit.** An account reaches what it has been granted and nothing
else. "Everything" is stored as its own fact, so a person with no grants has no
access rather than unlimited access — the failure mode of the opposite default is
silent and total.

**There is always an owner.** One account is marked as such and cannot be demoted,
removed, or restricted by anybody, including other administrators and including
itself. Administration can be shared without becoming a way to lose the house.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal, NamedTuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from .db import get_engine
from .security import CurrentUser, audit, require_user

log = logging.getLogger("homesh.people")
router = APIRouter(prefix="/api/people", tags=["people"])


INVITE_TTL = timedelta(days=7)


class InviteCreate(BaseModel):
    handle: str = Field(min_length=2, max_length=40, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    # Empty means empty. Granting the run of the house is done by asking for it.
    library: list[str] = Field(default_factory=list, max_length=100)
    zones: list[UUID] = Field(default_factory=list, max_length=50)
    all_library: bool = False
    all_zones: bool = False


class RulesUpdate(BaseModel):
    library: list[str] = Field(default_factory=list, max_length=100)
    zones: list[UUID] = Field(default_factory=list, max_length=50)
    all_library: bool = False
    all_zones: bool = False


class AdminUpdate(BaseModel):
    is_admin: bool


class _Place(NamedTuple):
    """The two things an audience can be set on.

    Folders and rooms are the same reconciliation over different tables. The
    statements are carried as literals rather than assembled from table names:
    the logic stays in one place, and no SQL is ever built by interpolation.
    """

    set_audience: str
    clear_grants: str
    everyone: str
    grant: str


FOLDER = _Place(
    set_audience="UPDATE sources SET audience = CAST(:a AS audience) WHERE id = :id",
    clear_grants="DELETE FROM user_library_rules WHERE path_prefix = :v",
    everyone="SELECT id FROM users WHERE NOT is_admin AND NOT all_library",
    grant=(
        "INSERT INTO user_library_rules (user_id, path_prefix) "
        "VALUES (:u, :v) ON CONFLICT DO NOTHING"
    ),
)

ROOM = _Place(
    set_audience="UPDATE zones SET audience = CAST(:a AS audience) WHERE id = :id",
    clear_grants="DELETE FROM user_zone_rules WHERE zone_id = CAST(:v AS uuid)",
    everyone="SELECT id FROM users WHERE NOT is_admin AND NOT all_zones",
    grant=(
        "INSERT INTO user_zone_rules (user_id, zone_id) "
        "VALUES (:u, CAST(:v AS uuid)) ON CONFLICT DO NOTHING"
    ),
)


class AudienceUpdate(BaseModel):
    audience: Literal["everyone", "admins", "selected"]
    # Only consulted for "selected"; ignored otherwise rather than rejected, so
    # a picker left populated after switching choice cannot grant by accident.
    users: list[UUID] = Field(default_factory=list, max_length=200)


def _require_admin(user: CurrentUser) -> None:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")


def _load_target(conn, user_id: UUID) -> tuple[bool, bool]:
    """(is_admin, is_owner) for the subject of an operation."""
    row = conn.execute(
        text("SELECT is_admin, is_owner FROM users WHERE id = :id"), {"id": str(user_id)}
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such person")
    return bool(row[0]), bool(row[1])


def _refuse_if_owner(is_owner: bool, what: str) -> None:
    if is_owner:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"the account owner cannot be {what}",
        )


@router.get("")
async def list_people(user: CurrentUser = Depends(require_user)) -> list[dict]:
    _require_admin(user)

    with get_engine().connect() as conn:
        people = conn.execute(
            text(
                """
                SELECT u.id, u.handle, u.display_name, u.is_admin, u.created_at,
                       count(c.id) AS passkeys, u.is_owner, u.all_library, u.all_zones
                FROM users u
                LEFT JOIN credentials c ON c.user_id = u.id
                GROUP BY u.id
                ORDER BY u.is_owner DESC, u.is_admin DESC, u.created_at
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
            "is_owner": p[6],
            "created_at": p[4].isoformat(),
            "passkeys": p[5],
            "library": lib_by_user.get(p[0], []),
            "zones": zones_by_user.get(p[0], []),
            "all_library": p[7],
            "all_zones": p[8],
        }
        for p in people
    ]


@router.post("/invites", status_code=status.HTTP_201_CREATED)
async def create_invite(body: InviteCreate, user: CurrentUser = Depends(require_user)) -> dict:
    """Invite someone, with their access decided up front.

    The invite carries the grants so the account is correctly scoped from its
    first sign-in, rather than existing briefly with the run of the house.
    """
    _require_admin(user)

    with get_engine().connect() as conn:
        taken = conn.execute(
            text("SELECT 1 FROM users WHERE handle = :h"), {"h": body.handle}
        ).first()
    if taken:
        raise HTTPException(status.HTTP_409_CONFLICT, "that username is taken")

    # Longer and more random than a pairing code: this one travels by message
    # and creates an account, so it should not be guessable or shoulder-read.
    code = secrets.token_urlsafe(12)

    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM invites WHERE expires_at < now() AND used_at IS NULL"))
        conn.execute(
            text(
                """
                INSERT INTO invites (code, handle, display_name, library_rules,
                                     zone_rules, all_library, all_zones,
                                     created_by, expires_at)
                VALUES (:c, :h, :d, CAST(:lib AS jsonb), CAST(:zones AS jsonb),
                        :alib, :azones, :by, :exp)
                """
            ),
            {
                "c": code,
                "h": body.handle,
                "d": body.display_name,
                "lib": json.dumps([f"/{p.strip().strip('/')}" for p in body.library if p.strip()]),
                "zones": json.dumps([str(z) for z in body.zones]),
                "alib": body.all_library,
                "azones": body.all_zones,
                "by": str(user.id),
                "exp": datetime.now(UTC) + INVITE_TTL,
            },
        )
        audit(conn, "invite.created", user.id, {"handle": body.handle}, None)

    return {"code": code, "expires_in_days": INVITE_TTL.days}


@router.get("/invites")
async def list_invites(user: CurrentUser = Depends(require_user)) -> list[dict]:
    _require_admin(user)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT code, handle, display_name, expires_at, used_at
                FROM invites WHERE used_at IS NULL AND expires_at > now()
                ORDER BY created_at DESC
                """
            )
        ).all()
    return [
        {
            "code": r[0],
            "handle": r[1],
            "display_name": r[2],
            "expires_at": r[3].isoformat(),
        }
        for r in rows
    ]


@router.delete("/invites/{code}")
async def revoke_invite(code: str, user: CurrentUser = Depends(require_user)) -> dict:
    _require_admin(user)
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM invites WHERE code = :c AND used_at IS NULL"),
                     {"c": code})
    return {"ok": True}


@router.get("/audiences")
async def list_audiences(user: CurrentUser = Depends(require_user)) -> dict:
    """Every folder and room, and who can currently reach it.

    Folders arrive by discovery rather than by anyone filling in a form — a Drive
    folder shared with the service account simply appears — so `audience: null`
    is a real and common state meaning nobody has ruled on it yet. Those are
    listed first because they are the ones holding a decision.
    """
    _require_admin(user)

    with get_engine().connect() as conn:
        folders = conn.execute(
            text("SELECT id, name, mount_prefix, audience FROM sources ORDER BY mount_prefix")
        ).all()
        rooms = conn.execute(text("SELECT id, name, audience FROM zones ORDER BY name")).all()
        lib_rules = conn.execute(
            text(
                """
                SELECT r.path_prefix, u.id, u.display_name
                FROM user_library_rules r JOIN users u ON u.id = r.user_id
                WHERE NOT u.is_admin
                """
            )
        ).all()
        zone_rules = conn.execute(
            text(
                """
                SELECT r.zone_id, u.id, u.display_name
                FROM user_zone_rules r JOIN users u ON u.id = r.user_id
                WHERE NOT u.is_admin
                """
            )
        ).all()

    def named(rows, key) -> list[dict]:
        return [{"id": str(uid), "display_name": name} for k, uid, name in rows if k == key]

    return {
        "folders": [
            {
                "id": str(f[0]),
                "name": f[1],
                "path": f[2],
                "audience": f[3],
                "selected": named(lib_rules, f[2]),
            }
            for f in sorted(folders, key=lambda f: (f[3] is not None, f[2]))
        ],
        "rooms": [
            {
                "id": str(r[0]),
                "name": r[1],
                "audience": r[2],
                "selected": named(zone_rules, r[0]),
            }
            for r in sorted(rooms, key=lambda r: (r[2] is not None, r[1]))
        ],
    }


def apply_audience(
    conn, *, place: _Place, key: UUID, grant_value: str, body: AudienceUpdate
) -> None:
    """Set an audience and reconcile the personal grants that go with it.

    Existing grants are deleted rather than left inert behind the ceiling: a
    folder switched to admins-only and later reopened to a chosen few must not
    silently revive whoever happened to hold it last year.
    """
    conn.execute(text(place.set_audience), {"a": body.audience, "id": str(key)})
    conn.execute(text(place.clear_grants), {"v": grant_value})

    if body.audience == "admins":
        return

    if body.audience == "everyone":
        # "Everyone" must be true of the people who already have accounts, not
        # only of those holding blanket access — otherwise a scoped child would
        # quietly be left out of something marked as shared with all. Accounts
        # that already hold everything need no row; the audience covers them.
        recipients = [r[0] for r in conn.execute(text(place.everyone)).all()]
    else:
        recipients = body.users

    for uid in recipients:
        conn.execute(text(place.grant), {"u": str(uid), "v": grant_value})


@router.put("/audiences/folders/{source_id}")
async def set_folder_audience(
    source_id: UUID, body: AudienceUpdate, user: CurrentUser = Depends(require_user)
) -> dict:
    _require_admin(user)

    with get_engine().begin() as conn:
        prefix = conn.execute(
            text("SELECT mount_prefix FROM sources WHERE id = :id"), {"id": str(source_id)}
        ).scalar()
        if prefix is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such folder")

        apply_audience(conn, place=FOLDER, key=source_id, grant_value=prefix, body=body)
        audit(
            conn,
            "audience.folder.set",
            user.id,
            {"folder": prefix, "audience": body.audience},
            None,
        )

    return {"ok": True}


@router.put("/audiences/rooms/{zone_id}")
async def set_room_audience(
    zone_id: UUID, body: AudienceUpdate, user: CurrentUser = Depends(require_user)
) -> dict:
    _require_admin(user)

    with get_engine().begin() as conn:
        name = conn.execute(
            text("SELECT name FROM zones WHERE id = :id"), {"id": str(zone_id)}
        ).scalar()
        if name is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such room")

        apply_audience(conn, place=ROOM, key=zone_id, grant_value=str(zone_id), body=body)
        audit(conn, "audience.room.set", user.id, {"room": name, "audience": body.audience}, None)

    return {"ok": True}


@router.put("/{user_id}/rules")
async def set_rules(
    user_id: UUID, body: RulesUpdate, user: CurrentUser = Depends(require_user)
) -> dict:
    """Replace someone's access, at any time after they joined.

    The whole grant is sent, not a patch: what arrives is what they will have.
    Access is meant to be revisable as children grow up, so this is the same
    operation an invitation performs, applied to an account that already exists.
    """
    _require_admin(user)

    with get_engine().connect() as conn:
        target_admin, target_owner = _load_target(conn, user_id)

    _refuse_if_owner(target_owner, "restricted")

    if target_admin:
        # Restricting an admin would be theatre: they can edit these rules.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "administrators are not restricted — remove admin first",
        )

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE users SET all_library = :a, all_zones = :z WHERE id = :u"),
            {"a": body.all_library, "z": body.all_zones, "u": str(user_id)},
        )

        conn.execute(
            text("DELETE FROM user_library_rules WHERE user_id = :u"), {"u": str(user_id)}
        )
        if not body.all_library:
            for prefix in body.library:
                cleaned = "/" + prefix.strip().strip("/")
                if cleaned == "/":
                    continue  # the whole library is the all_library flag, not a rule
                conn.execute(
                    text(
                        """
                        INSERT INTO user_library_rules (user_id, path_prefix)
                        VALUES (:u, :p) ON CONFLICT DO NOTHING
                        """
                    ),
                    {"u": str(user_id), "p": cleaned},
                )

        conn.execute(
            text("DELETE FROM user_zone_rules WHERE user_id = :u"), {"u": str(user_id)}
        )
        if not body.all_zones:
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
                "zones": [str(z) for z in body.zones],
                "all_library": body.all_library,
                "all_zones": body.all_zones,
            },
            None,
        )

    return {"ok": True}


@router.put("/{user_id}/admin")
async def set_admin(
    user_id: UUID, body: AdminUpdate, user: CurrentUser = Depends(require_user)
) -> dict:
    """Grant or withdraw administration.

    An administrator can create other administrators — the point is that a second
    adult can manage the household without waiting on the first. What they cannot
    do is turn that power on the owner, so sharing administration is never a way
    to be locked out of your own server.
    """
    _require_admin(user)

    with get_engine().begin() as conn:
        _, target_owner = _load_target(conn, user_id)
        _refuse_if_owner(target_owner, "removed from administrators")

        conn.execute(
            text("UPDATE users SET is_admin = :a WHERE id = :u"),
            {"a": body.is_admin, "u": str(user_id)},
        )

        if body.is_admin:
            # An administrator is unrestricted by definition; leaving stale rules
            # behind would make them reappear if admin were later withdrawn,
            # which is not what "you had full access" should decay into.
            conn.execute(
                text("DELETE FROM user_library_rules WHERE user_id = :u"), {"u": str(user_id)}
            )
            conn.execute(
                text("DELETE FROM user_zone_rules WHERE user_id = :u"), {"u": str(user_id)}
            )
            conn.execute(
                text("UPDATE users SET all_library = TRUE, all_zones = TRUE WHERE id = :u"),
                {"u": str(user_id)},
            )
        else:
            # Withdrawing administration lands on no access rather than on
            # whatever happened to be there. Someone must then say what they may
            # have, which is the explicit-by-default rule applied to a demotion.
            conn.execute(
                text("UPDATE users SET all_library = FALSE, all_zones = FALSE WHERE id = :u"),
                {"u": str(user_id)},
            )

        audit(
            conn,
            "person.admin.changed",
            user.id,
            {"subject": str(user_id), "is_admin": body.is_admin},
            None,
        )

    return {"ok": True}


@router.delete("/{user_id}")
async def remove_person(user_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    _require_admin(user)

    if user_id == user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "you cannot remove your own account")

    with get_engine().begin() as conn:
        _, target_owner = _load_target(conn, user_id)
        _refuse_if_owner(target_owner, "removed")

        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": str(user_id)})
        audit(conn, "person.removed", user.id, {"subject": str(user_id)}, None)

    return {"ok": True}
