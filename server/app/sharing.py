"""Sharing a Drive-hosted file by link.

For the files this cannot send any other way. A two-hour video will not go
through a phone's share sheet — the whole file has to sit in memory first — and
it will not go through email at all. If it already lives in Drive, the copy is
there and the link costs nothing to make.

Two rules shape this:

**The link points at Drive, never at this server.** Sending someone a URL into
the house would be handing them a way in; a Drive link is a copy of one file,
served by Google, revocable, and telling them nothing about what else exists.

**Reader, never writer.** The person receiving it is being sent something to
watch, not an invitation to change the original.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text

from .access import may_access_item
from .config import get_settings
from .db import get_engine
from .security import CurrentUser, audit, require_user
from .sources.gdrive import (
    DriveError,
    DrivePermissionError,
    create_link,
    link_for,
    revoke_link,
)

log = logging.getLogger("homesh.sharing")
router = APIRouter(prefix="/api/items", tags=["sharing"])


# Said in one place so the API and the screen cannot describe it differently.
NOT_IN_DRIVE = "This file is not in Google Drive, so there is no Drive link to make."

CANNOT_SHARE = (
    "The Homesh account can read this folder but not share from it. In Google "
    "Drive, share the folder with it as an Editor and leave 'Editors can change "
    "permissions and share' enabled."
)


def _drive_file(item_id: UUID) -> tuple[str, str] | None:
    """(drive file id, filename) if this item has a replica in Drive.

    The id is resolved through the connector rather than read from a column:
    replicas record where a file sits in the namespace, not the provider's
    identifier for it. Drive's own id is derived by walking to the file, which is
    the same route streaming takes — so a rename in Drive cannot leave this
    pointing at a file that has moved.
    """
    from .library import connector_for

    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT r.dir_path, r.filename, s.id
                FROM replicas r JOIN sources s ON s.id = r.source_id
                WHERE r.item_id = :id AND s.kind = 'gdrive'
                LIMIT 1
                """
            ),
            {"id": str(item_id)},
        ).first()

    if row is None:
        return None

    dir_path, filename, source_id = row
    connector = connector_for(source_id)
    if connector is None:
        return None

    try:
        entry = connector.stat(f"{dir_path}/{filename}".strip("/"))
    except (FileNotFoundError, DriveError) as exc:
        log.warning("could not resolve %s in Drive: %s", filename, exc)
        return None

    return (entry.remote_id, filename) if entry.remote_id else None


def _key_path() -> Path:
    path = Path(get_settings().gdrive_key_file)
    if not path.is_file():
        raise HTTPException(status.HTTP_409_CONFLICT, "Google Drive is not connected.")
    return path


def _require_item(item_id: UUID, user: CurrentUser) -> tuple[str, str]:
    if not may_access_item(item_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")
    found = _drive_file(item_id)
    if found is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_IN_DRIVE)
    return found


@router.get("/{item_id}/drive-link")
async def get_drive_link(item_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    """Whether this file can be shared by link, and whether it already is.

    Answers for any file rather than erroring, because the screen asks about
    everything it shows and "not applicable" is a normal answer.
    """
    if not may_access_item(item_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    found = _drive_file(item_id)
    if found is None:
        return {"supported": False, "reason": NOT_IN_DRIVE, "url": None}

    # Only administrators may publish; everyone else is told plainly rather than
    # being shown a button that will refuse them.
    if not user.is_admin:
        return {"supported": False, "reason": "Only an administrator can share by link.",
                "url": None}

    file_id, _name = found
    try:
        return {"supported": True, "url": link_for(_key_path(), file_id), "reason": None}
    except DrivePermissionError:
        return {"supported": False, "reason": CANNOT_SHARE, "url": None}
    except DriveError as exc:
        return {"supported": False, "reason": str(exc), "url": None}


@router.post("/{item_id}/drive-link")
async def make_drive_link(item_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    """Publish a read-only link to this file.

    Deliberately administrators only. The link makes one file readable by anyone
    who holds it, which is a decision about the household's data rather than a
    per-person convenience — and it is revocable from the same screen.
    """
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an administrator can share by link")

    file_id, filename = _require_item(item_id, user)

    try:
        url = create_link(_key_path(), file_id)
    except DrivePermissionError as exc:
        log.warning("Drive refused to share %s: %s", filename, exc)
        raise HTTPException(status.HTTP_409_CONFLICT, CANNOT_SHARE) from exc
    except DriveError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    with get_engine().begin() as conn:
        # Worth recording: this is the one action that puts a file outside the
        # house, so it should be answerable later who did it and to what.
        audit(conn, "share.drive_link.created", user.id,
              {"item": str(item_id), "filename": filename}, None)

    return {"url": url}


@router.delete("/{item_id}/drive-link")
async def drop_drive_link(item_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an administrator can share by link")

    file_id, filename = _require_item(item_id, user)

    try:
        revoke_link(_key_path(), file_id)
    except DrivePermissionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, CANNOT_SHARE) from exc
    except DriveError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    with get_engine().begin() as conn:
        audit(conn, "share.drive_link.revoked", user.id,
              {"item": str(item_id), "filename": filename}, None)

    return {"ok": True}
