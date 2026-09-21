"""Files whose container confuses a television, rewritten rather than re-encoded.

One mp4 in this library plays from the beginning in a browser and four seconds in
on a set-top box, every time, instantly. Everything inside it is ordinary: the
first frame is a keyframe at 0.000 s, the sync-sample table's first entry is
sample 1, no edit list trims anything, the index is at the front and the track
durations agree. The only thing that distinguishes it from a file that plays
correctly is a non-standard box in front of the index:

    ftyp beam moov mdat     <- this one starts four seconds in
    ftyp moov mdat          <- this one is fine

`beam` is nobody's standard. A parser is supposed to skip a box it does not know,
and Chromium evidently does; the box's own player evidently does something else.
We cannot fix the player, and we do not have to: rewriting the container without
touching a single compressed frame produces `ftyp moov free mdat`, which every
player reads the same way. Measured at 0.34 s for 12 MB, against tens of seconds
to re-encode it -- and the picture is bit-for-bit what it was.

**Only for a screen, and only when the container is odd.** The browser plays these
files correctly, so nothing changes there; and a file with an ordinary container
is handed over untouched, as before.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import subprocess
from pathlib import Path
from uuid import UUID

from .config import get_settings
from .signing import mint

log = logging.getLogger("homesh.rewrap")

# Containers this is worth looking at. Anything else either has no such boxes or
# is already being converted for other reasons.
LOOKS_LIKE_MP4 = {"mp4", "m4v", "mov", "m4a"}

# Top-level boxes an mp4 may legitimately carry. A name outside this set before
# the index is what the television trips over.
KNOWN_BOXES = frozenset({
    "ftyp", "styp", "moov", "mdat", "free", "skip", "wide", "uuid", "meta",
    "pnot", "moof", "mfra", "sidx", "ssix", "prft", "pdin", "mfro", "junk",
})

# Enough to reach the index in any faststart file, and nothing like enough to be
# worth reading twice -- so the verdict is remembered per item.
HEADER_BYTES = 96 * 1024

_verdicts: dict[UUID, bool] = {}
# One rewrite at a time: it is cheap, and two at once would still be two
# simultaneous reads of the same slow source.
_slot = asyncio.Semaphore(1)


def cache_path(item_id: UUID) -> Path:
    return Path(get_settings().cache_dir) / "video" / f"{item_id}-plain.mp4"


def _top_level_boxes(head: bytes) -> list[str]:
    """Box names in order, stopping after the index or when the header runs out."""
    names: list[str] = []
    at = 0
    while at + 8 <= len(head):
        size = struct.unpack(">I", head[at : at + 4])[0]
        name = head[at + 4 : at + 8].decode("latin1", "replace")
        names.append(name)
        if size == 1:
            if at + 16 > len(head):
                break
            size = struct.unpack(">Q", head[at + 8 : at + 16])[0]
        if size < 8:
            break
        if name == "moov":
            break  # everything that matters comes before the index
        at += size
    return names


def odd_container(item_id: UUID, ext: str, connector, rel: str) -> bool:
    """Whether this file carries a box a television may not skip properly.

    Remembered per item: it is a property of the file, and reading a header from
    Drive costs a round trip.
    """
    if ext.lower().lstrip(".") not in LOOKS_LIKE_MP4:
        return False
    if item_id in _verdicts:
        return _verdicts[item_id]

    head = b""
    try:
        chunks = connector.open_range(rel, 0, HEADER_BYTES - 1)
        try:
            for chunk in chunks:
                head += chunk
                if len(head) >= HEADER_BYTES:
                    break
        finally:
            close = getattr(chunks, "close", None)
            if close:
                close()
    except Exception as exc:  # noqa: BLE001 - an unreadable header is not odd, just unread
        log.info("could not read the container of %s: %s", item_id, exc)
        return False

    names = _top_level_boxes(head)
    strange = [n for n in names if n not in KNOWN_BOXES]
    verdict = bool(strange)
    if verdict:
        log.info("%s carries %s before its index; a screen gets a rewritten copy",
                 item_id, ", ".join(sorted(set(strange))))
    _verdicts[item_id] = verdict
    return verdict


def forget(item_id: UUID | None = None) -> None:
    """Drop what was decided about a file -- after a rescan, or in tests."""
    if item_id is None:
        _verdicts.clear()
    else:
        _verdicts.pop(item_id, None)


async def ensure(item_id: UUID, viewer: UUID) -> Path | None:
    """The rewritten copy, made if it does not exist yet. None if it cannot be.

    Falling back to the original is deliberate: a file that plays four seconds in
    is better than a file that does not play.
    """
    target = cache_path(item_id)
    if target.is_file() and target.stat().st_size > 0:
        return target

    async with _slot:
        if target.is_file() and target.stat().st_size > 0:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".partial.mp4")

        # Read through our own stream endpoint, as the live transcode does: it
        # is the one place that knows how to reach every source, and ffmpeg gets
        # ranges over HTTP rather than a path that may not exist here.
        token = mint(item_id, viewer, "stream", ttl=3600)
        source = f"http://127.0.0.1:8080/api/stream/{item_id}?t={token}"
        args = [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
            "-i", source,
            # Every stream, and not one frame re-encoded.
            "-map", "0", "-c", "copy",
            # The index at the front, so a player can start without reading the
            # whole file first.
            "-movflags", "+faststart",
            "-y", str(partial),
        ]
        try:
            done = await asyncio.to_thread(
                subprocess.run, args, capture_output=True, text=True, timeout=300, check=False,  # noqa: S603
            )
        except Exception as exc:  # noqa: BLE001
            partial.unlink(missing_ok=True)
            log.warning("could not rewrite %s: %s", item_id, exc)
            return None

        if done.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
            log.warning("could not rewrite %s: %s", item_id, (done.stderr or "")[:200])
            return None

        partial.replace(target)
        log.info("rewrote the container of %s (%.1f MB)", item_id,
                 target.stat().st_size / 1e6)
        return target
