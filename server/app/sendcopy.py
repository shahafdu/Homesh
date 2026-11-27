"""A copy that opens, for sending an old format to somebody else.

A WMV or a WMA sent as it is arrives as a file the other person cannot play. So
the server converts it at the moment it is sent, hands the converted file over,
and deletes it. Nothing joins the watching cache: that one exists so that a film
being watched is converted once, while a send happens once by nature, and a
second copy of every clip ever sent would only fill the disk.

Only short files. This is for clips and songs; a long one is refused before
anything is fetched, and a Drive link is the way to send it
(docs/design/08-sharing.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import text
from starlette.background import BackgroundTask

from . import ffinput, transcode
from .access import may_access_item
from .childenv import child_env
from .config import get_settings
from .db import get_engine
from .security import CurrentUser, require_user
from .stream import _disposition, resolve_playable
from .throttle import Limit, hit

log = logging.getLogger("homesh.sendcopy")
router = APIRouter(prefix="/api/items", tags=["sharing"])

# Chromium's own list of extensions a page may attach to a share, and so the
# list of what needs no copy. web/src/formats.ts holds the same one; both are
# checked against server/tests/send_copy_cases.json.
SHAREABLE = frozenset({
    "avif", "bmp", "css", "csv", "ehtml", "flac", "gif", "htm", "html", "ico",
    "jfif", "jpeg", "jpg", "m4a", "m4v", "mp3", "mp4", "mpeg", "mpg", "oga",
    "ogg", "ogm", "ogv", "opus", "pdf", "pjp", "pjpeg", "png", "shtml", "svg",
    "svgz", "text", "tif", "tiff", "txt", "wav", "weba", "webm", "webp", "xbm",
})

# Sound the catalog may have filed as "other", because the scanner does not
# list these extensions as audio. MIDI is deliberately absent: it holds notes
# rather than sound, so there is nothing in it for an encoder to convert.
AUDIO_EXTS = frozenset({
    "aac", "ac3", "aif", "aifc", "aiff", "alac", "amr", "ape", "au", "awb",
    "caf", "dts", "m4b", "mka", "mp2", "mpc", "ra", "tta", "wma", "wv",
})

MEDIA_TYPES = {"mp4": "video/mp4", "m4a": "audio/mp4"}

# What a phone can hold in memory to hand to its share sheet: MAX_SHARE_BYTES
# in web/src/share.ts.
MAX_BYTES = 256 * 1024 * 1024

# How long a send waits for the one conversion slot before being turned away.
SLOT_WAIT_SECONDS = 30.0

# The slot is shared with watching, so an encoder stuck on a damaged file must
# not hold it for ever. Several times what the longest permitted file takes on
# a low-power CPU.
CONVERT_TIMEOUT_SECONDS = 30 * 60.0
PROBE_TIMEOUT_SECONDS = 60.0

SEND_LIMIT = Limit(10, timedelta(minutes=10))

# A copy is deleted as soon as it has been sent. The sweep is for the ones that
# never were: a dropped connection, or the server stopping half way.
LEFTOVER_AGE = timedelta(hours=1)
SWEEP_EVERY = timedelta(minutes=15)

TOO_LONG = "Too long to convert for sending. Share a Drive link instead."
TOO_BIG = "Too big to attach even after converting. Share a Drive link instead."
BUSY = "The server is busy converting another file. Try again in a few minutes."
FAILED = "This file could not be converted for sending. Share a Drive link instead."
NO_COPY = "There is no converted copy to make of this file."


def target_for(kind: str, ext: str | None) -> str | None:
    """"mp4", "m4a", or None where no copy is made."""
    suffix = (ext or "").lower().lstrip(".")
    # First, because two of these (mpg, mpeg) can be attached as they are and
    # still arrive as something the receiving phone cannot play.
    if transcode.needs_conversion(suffix):
        return "mp4"
    if suffix in SHAREABLE:
        return None
    if kind == "video":
        return "mp4"
    if kind == "audio" or suffix in AUDIO_EXTS:
        return "m4a"
    return None


def _dir() -> Path:
    path = Path(get_settings().cache_dir) / "sendcopy"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sweep(now: float | None = None) -> int:
    """Delete copies old enough that nobody can still be receiving them."""
    cutoff = (now or time.time()) - LEFTOVER_AGE.total_seconds()
    gone = 0
    for path in _dir().iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                gone += 1
        except OSError as exc:
            # Deleted by its own response between the listing and here, or
            # still locked. Either way the next pass settles it.
            log.debug("could not sweep %s: %s", path.name, exc)
    return gone


async def sweep_forever() -> None:
    """At start-up and every quarter hour. Cancelled on shutdown."""
    while True:
        try:
            gone = await asyncio.to_thread(sweep)
            if gone:
                log.info("removed %d leftover send copy(ies)", gone)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the next pass tries again
            log.exception("could not sweep send copies")
        await asyncio.sleep(SWEEP_EVERY.total_seconds())


async def _run(args: list[str], timeout: float) -> tuple[int, bytes, str]:
    """(exit code, stdout, stderr) of a child, killed if it overruns."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        return -1, b"", "timed out"
    finally:
        # Timed out, or the request was cancelled: nobody is left to want it.
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    return proc.returncode or 0, out, err.decode(errors="replace")


async def _probe_seconds(source: ffinput.Input) -> float | None:
    """The length ffprobe reports, or None when it cannot tell."""
    code, out, _err = await _run(
        ffinput.ffprobe_argv(
            source, ["-v", "error", "-show_entries", "format=duration", "-of", "json"]
        ),
        PROBE_TIMEOUT_SECONDS,
    )
    if code != 0:
        return None
    try:
        seconds = float(json.loads(out)["format"]["duration"])
    except (ValueError, KeyError, TypeError):
        return None
    return seconds if seconds > 0 else None


def ffmpeg_args(source: ffinput.Input, target: str, output: Path,
                max_seconds: int) -> list[str]:
    if target == "mp4":
        streams = [
            # First video and first audio track, neither required, as in the
            # watching conversion.
            "-map", "0:v:0?", "-map", "0:a:0?",
            # Deinterlaced, at most 1080 lines, and both sides even because
            # H.264 in 4:2:0 refuses an odd one.
            "-vf", "yadif,scale=-2:'min(1080,trunc(ih/2)*2)'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            # Old formats carry pixel layouts no phone decodes.
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
        ]
    else:
        streams = [
            # Required here: a file with no sound to convert must fail. -vn
            # leaves out cover art, which would otherwise become a video track.
            "-map", "0:a:0", "-vn",
            "-c:a", "aac", "-b:a", "192k",
            "-map_metadata", "0",
        ]
    return ffinput.ffmpeg_argv(
        source,
        [
            *streams,
            "-movflags", "+faststart",
            # A recorded length can be wrong or missing. Stopping just past the
            # limit bounds the work, and the result is measured afterwards, so a
            # long file is refused rather than sent cut short.
            "-t", str(max_seconds + 2),
            # ffmpeg stops here and still reports success, so the size is
            # checked afterwards too.
            "-fs", str(MAX_BYTES),
            str(output),
        ],
        before=["-hide_banner", "-nostdin", "-y", "-loglevel", "error"],
    )


async def _convert(source: ffinput.Input, target: str, output: Path, max_seconds: int,
                   item_id: UUID) -> float:
    """Write the copy to `output`, or raise. Returns its length in seconds."""
    code, _out, err = await _run(
        ffmpeg_args(source, target, output, max_seconds), CONVERT_TIMEOUT_SECONDS
    )
    if code != 0 or not output.is_file() or output.stat().st_size == 0:
        said = ffinput.redact(err).strip()[-400:]
        log.warning("send copy of %s failed (exit %d): %s", item_id, code, said)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, FAILED)
    if output.stat().st_size >= MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_BIG)
    seconds = await _probe_seconds(ffinput.file_input(output))
    if seconds is None:
        log.warning("send copy of %s has no readable length", item_id)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, FAILED)
    # A second of grace: encoders pad the end by a frame or so.
    if seconds > max_seconds + 1:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LONG)
    return seconds


@router.post("/{item_id}/send-copy")
async def send_copy(item_id: UUID, user: CurrentUser = Depends(require_user)) -> FileResponse:
    """Convert this file and hand the result over, keeping nothing.

    A POST because it starts real work. It writes only its own temporary file.
    """
    if not may_access_item(item_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT kind::text, duration_ms FROM items WHERE id = :id"),
            {"id": str(item_id)},
        ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")
    kind, duration_ms = row

    _connector, _rel, filename, _size, ext = resolve_playable(item_id)
    target = target_for(kind, ext)
    if target is None:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, NO_COPY)

    hit("share-convert", str(user.id), SEND_LIMIT)

    max_seconds = get_settings().share_convert_max_seconds
    # Long enough to wait for the slot and then read the whole file.
    source = ffinput.stream_input(item_id, user.id, ttl=int(CONVERT_TIMEOUT_SECONDS) + 600)

    known = duration_ms / 1000 if duration_ms else None
    if known is None:
        known = await _probe_seconds(source)
    if known is not None and known > max_seconds:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LONG)

    # One encoder at a time, whether it is converting for watching or sending.
    try:
        await asyncio.wait_for(transcode._slot.acquire(), SLOT_WAIT_SECONDS)
    except TimeoutError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, BUSY, headers={"Retry-After": "60"}
        ) from exc

    # Named at random, never after the file.
    output = _dir() / f"{uuid4().hex}.{target}"
    started = time.monotonic()
    keep = False
    try:
        seconds = await _convert(source, target, output, max_seconds, item_id)
        keep = True
    finally:
        transcode._slot.release()
        if not keep:
            output.unlink(missing_ok=True)

    log.info(
        "send copy of %s: %s to %s, %.0f s long, %d bytes, took %.1f s",
        item_id, ext or "?", target, seconds, output.stat().st_size,
        time.monotonic() - started,
    )

    stem = filename.rsplit(".", 1)[0] or filename
    return FileResponse(
        output,
        media_type=MEDIA_TYPES[target],
        headers={
            "Content-Disposition": _disposition(f"{stem}.{target}", attachment=True),
            "Cache-Control": "no-store",
        },
        background=BackgroundTask(output.unlink, missing_ok=True),
    )
