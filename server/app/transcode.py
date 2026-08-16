"""Making video playable that a browser cannot decode.

The server does not encode as a matter of course — endpoints decode, and remux is
nearly free where transcode is not (ARCHITECTURE.md §3.2). This is the exception
that principle always allowed for: MPEG-2.

No current browser can play MPEG-2 video. There is no codec to enable and no
container trick; the decoder was never shipped. A DVD rip or an HDV camcorder
tape is therefore unwatchable anywhere except a device with its own decoder —
which most television boxes have, and no phone browser does.

So conversion is offered rather than assumed: it is expensive, it is only needed
for some files and only on some screens, and asking for it is a decision the
person watching should make.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import text

from .access import may_access_item
from .config import get_settings
from .db import get_engine
from .security import CurrentUser, require_user
from .signing import mint
from .stream import resolve_playable

log = logging.getLogger("homesh.transcode")
router = APIRouter(prefix="/api/videos", tags=["video"])


# Containers whose video is, in practice, MPEG-2 or another codec no browser
# carries. Everything else is offered as direct play first and only lands here
# if the browser actually refuses it.
NEEDS_CONVERSION = {"m2t", "mts", "vob", "mpg", "mpeg", "m1v", "m2v", "mod", "tod",
                    "dv", "mxf", "rm", "rmvb", "asf", "divx"}

# One at a time. Encoding will use every core it is given, and a household that
# cannot browse its library while a tape converts has been given a worse problem
# than the one being solved.
_slot = asyncio.Semaphore(1)


def needs_conversion(ext: str | None) -> bool:
    return (ext or "").lower().lstrip(".") in NEEDS_CONVERSION


def _cache_dir() -> Path:
    path = Path(get_settings().cache_dir) / "video"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _output_for(item_id: UUID) -> Path:
    return _cache_dir() / f"{item_id}.mp4"


def _record(item_id: UUID, **fields) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    with get_engine().begin() as conn:
        conn.execute(
            text(f"UPDATE transcodes SET {sets} WHERE item_id = :id"),  # noqa: S608
            {**fields, "id": str(item_id)},
        )


def _finish(item_id: UUID, **fields) -> None:
    """Record a terminal state, stamping when it ended."""
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    with get_engine().begin() as conn:
        conn.execute(
            text(f"UPDATE transcodes SET {sets}, ended_at = now() WHERE item_id = :id"),  # noqa: S608
            {**fields, "id": str(item_id)},
        )


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+)")
_TIME = re.compile(r"out_time_ms=(\d+)")


async def _run(item_id: UUID, user_id: UUID, ext: str) -> None:
    """Convert one file, reporting progress as it goes."""
    output = _output_for(item_id)
    partial = output.with_suffix(".part.mp4")

    async with _slot:
        _record(item_id, state="running")

        # ffmpeg reads through our own streaming endpoint rather than a local
        # path, so a Drive file works exactly as a local one does and nothing has
        # to be downloaded twice. The token is short-lived but generous: this is
        # a long read of a very large file.
        token = mint(item_id, user_id, "stream", ttl=12 * 3600)
        source = f"http://127.0.0.1:8080/api/stream/{item_id}?t={token}"

        args = [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-i", source,
            # HDV and DVD are interlaced; leaving it produces combing on every
            # pan, which is the first thing anyone notices.
            "-vf", "yadif",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            # Lets a browser start playing before the whole file has arrived.
            "-movflags", "+faststart",
            "-progress", "pipe:1", "-loglevel", "error",
            str(partial),
        ]

        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        total_ms: float | None = None
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace")
                if total_ms is None:
                    found = _DURATION.search(line)
                    if found:
                        h, m, sec = (int(x) for x in found.groups())
                        total_ms = (h * 3600 + m * 60 + sec) * 1000

                at = _TIME.search(line)
                if at and total_ms:
                    pct = min(99, int(int(at.group(1)) / 1000 / total_ms * 100))
                    _record(item_id, progress=pct)

            await proc.wait()
        except asyncio.CancelledError:
            proc.kill()
            raise

        if proc.returncode != 0:
            err = (await proc.stderr.read()).decode(errors="replace")[-400:] if proc.stderr else ""
            log.warning("transcode of %s failed: %s", item_id, err)
            partial.unlink(missing_ok=True)
            _finish(item_id, state="failed", error=err or "conversion failed")
            return

        # Moved into place only when complete, so a crash cannot leave a
        # half-converted film cached as though it were finished.
        shutil.move(str(partial), str(output))
        _finish(item_id, state="done", progress=100, output_path=str(output))
        log.info("converted %s for browser playback", item_id)


@router.get("/{item_id}/conversion")
async def conversion_status(item_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    """Whether this video needs converting, and how far along it is."""
    if not may_access_item(item_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    _connector, _rel, _name, _size, ext = resolve_playable(item_id)
    if not needs_conversion(ext):
        return {"needed": False, "state": None, "progress": 0}

    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT state, progress, error FROM transcodes WHERE item_id = :id"),
            {"id": str(item_id)},
        ).first()

    if row is None:
        return {"needed": True, "state": None, "progress": 0, "error": None}

    state, progress, error = row
    # A finished job whose file has been cleared from the cache is not finished.
    if state == "done" and not _output_for(item_id).is_file():
        return {"needed": True, "state": None, "progress": 0, "error": None}

    return {"needed": True, "state": state, "progress": progress, "error": error}


@router.post("/{item_id}/conversion", status_code=status.HTTP_202_ACCEPTED)
async def start_conversion(item_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    if not may_access_item(item_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    _connector, _rel, _name, size, ext = resolve_playable(item_id)
    if not needs_conversion(ext):
        raise HTTPException(status.HTTP_409_CONFLICT, "this video plays without converting")

    if _output_for(item_id).is_file():
        return {"state": "done", "progress": 100}

    with get_engine().begin() as conn:
        existing = conn.execute(
            text("SELECT state FROM transcodes WHERE item_id = :id"), {"id": str(item_id)}
        ).scalar()
        if existing in ("queued", "running"):
            return {"state": existing}

        conn.execute(
            text(
                """
                INSERT INTO transcodes (item_id, state, progress, source_size, started_at)
                VALUES (:id, 'queued', 0, :size, now())
                ON CONFLICT (item_id) DO UPDATE
                SET state = 'queued', progress = 0, error = NULL, started_at = now(),
                    ended_at = NULL
                """
            ),
            {"id": str(item_id), "size": size},
        )

    # Deliberately not awaited: an hour of tape takes tens of minutes, and the
    # caller wants a job number rather than a timeout.
    task = asyncio.create_task(_run(item_id, user.id, ext or ""))
    _running.add(task)
    task.add_done_callback(_running.discard)

    return {"state": "queued", "progress": 0}


# Strong references, so a long conversion is not garbage-collected mid-encode.
_running: set[asyncio.Task] = set()


@router.get("/{item_id}/converted")
async def converted_video(item_id: UUID, user: CurrentUser = Depends(require_user)) -> FileResponse:
    """The converted file, once there is one."""
    if not may_access_item(item_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    output = _output_for(item_id)
    if not output.is_file():
        raise HTTPException(status.HTTP_409_CONFLICT, "this video has not been converted yet")

    return FileResponse(output, media_type="video/mp4")
