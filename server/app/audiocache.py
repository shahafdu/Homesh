"""Tracks from Drive, kept on disk after the first play.

Drive costs about 1.4 seconds before the first byte of any read, every read. For
a film that is paid once and forgotten; for music it is paid on every track, and
it is the gap between pressing play and hearing something. Nothing in the
protocol removes it -- so the answer is to stop asking twice.

**Audio only, and only from a remote source.** A local file is already fast, and
a library of films would fill any disk. A track is a few megabytes, an album a
few dozen, and a household plays the same music again and again.

**The first play is not made slower to fill the cache.** The request is served
from Drive as before while a copy is fetched alongside it, one at a time, so
filling never competes with the track that is playing. The second play reads
from disk.

**A cached file is named with its size.** If the file behind it changes the name
no longer matches, so the stale copy is never served -- it is simply not a hit.

The budget is a ceiling, not a target: what has gone longest unplayed is deleted
first, which for music is the right answer nearly always.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from .config import get_settings

log = logging.getLogger("homesh.audiocache")

# Which extensions this is for. The container matters, not the codec: these are
# served untouched, exactly as they are on Drive.
AUDIO = frozenset({"mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "wma"})

# One fill at a time. Two would halve the bandwidth of the track somebody is
# listening to in order to fetch one they are not.
_filling = asyncio.Semaphore(1)
# Items being fetched right now, so a second range request for the same track
# does not start a second copy of the same work.
_underway: set[str] = set()

# Read in large pieces: this is a whole file over a network, not a live stream.
CHUNK = 1024 * 1024


def root() -> Path:
    return Path(get_settings().cache_dir) / "audio"


def budget() -> int:
    return max(0, get_settings().audio_cache_mb) * 1024 * 1024


def biggest_file() -> int:
    """Above this a track is left to Drive. A two-hour uncompressed recording is
    not what this is for, and one of them would evict a hundred songs."""
    return get_settings().audio_cache_file_mb * 1024 * 1024


def wanted(ext: str, size: int, connector) -> bool:
    """Whether this is a file worth keeping a copy of.

    Local sources are excluded by having a root on disk already: the connector
    for a folder on this machine carries one, a Drive connector does not.
    """
    if budget() <= 0 or ext.lower() not in AUDIO:
        return False
    if size <= 0 or size > biggest_file():
        return False
    return getattr(connector, "root", None) is None


def path_for(item_id: UUID, size: int) -> Path:
    # Sharded like the thumbnails, and carrying the size so that a file which
    # changed behind us cannot be served from an old copy.
    key = item_id.hex
    return root() / key[:2] / f"{key}-{size}.audio"


def hit(item_id: UUID, size: int) -> Path | None:
    """The cached copy, if there is a whole one. Marks it as just used."""
    path = path_for(item_id, size)
    try:
        if path.stat().st_size != size:
            return None
        # Its own timestamp is how "least recently played" is decided, and access
        # times cannot be relied on: many filesystems are mounted without them.
        os.utime(path, None)
    except OSError:
        return None
    return path


def read_range(path: Path, start: int, end: int) -> Iterator[bytes]:
    """The bytes a range asked for, from the cached file."""
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            block = handle.read(min(CHUNK, remaining))
            if not block:
                return
            remaining -= len(block)
            yield block


def fill(item_id: UUID, size: int, connector, rel: str) -> bool:
    """Fetch the whole file and keep it. Returns whether a copy now exists.

    Written beside the real name and moved into place at the end, so a fetch cut
    off half way is never mistaken for a complete file -- the same reason
    backups and thumbnails do it.
    """
    target = path_for(item_id, size)
    if target.exists():
        return True
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".filling-", delete=False
    ) as handle:
        partial = Path(handle.name)
    written = 0
    try:
        chunks = connector.open_range(rel, 0, size - 1)
        try:
            with partial.open("wb") as out:
                for chunk in chunks:
                    out.write(chunk)
                    written += len(chunk)
        finally:
            close = getattr(chunks, "close", None)
            if close:
                close()
        if written != size:
            # Short read: the file changed, or the connection ended early.
            # Either way this is not the file, and half a track is worse than
            # none -- it would be served as if whole.
            raise OSError(f"read {written} of {size} bytes")
        partial.replace(target)
    except Exception as exc:  # noqa: BLE001 - a fill that fails costs only speed
        partial.unlink(missing_ok=True)
        log.info("not keeping a copy of %s: %s", item_id, exc)
        return False

    log.info("kept %s for later (%.1f MB)", item_id, size / 1e6)
    sweep()
    return True


def want(item_id: UUID, size: int, connector, rel: str) -> None:
    """Fetch a copy in the background, if one is worth having and none is on its
    way. Returns at once: the play in progress must not wait for this."""
    key = f"{item_id.hex}-{size}"
    if key in _underway or path_for(item_id, size).exists():
        return
    _underway.add(key)

    async def run() -> None:
        try:
            async with _filling:
                await asyncio.to_thread(fill, item_id, size, connector, rel)
        finally:
            _underway.discard(key)

    try:
        asyncio.get_running_loop().create_task(run())
    except RuntimeError:  # pragma: no cover - no loop, so nothing to schedule
        _underway.discard(key)


def kept() -> list[tuple[Path, float, int]]:
    """Every cached file as (path, last played, size)."""
    found = []
    for path in root().glob("*/*.audio"):
        try:
            st = path.stat()
        except OSError:
            continue
        found.append((path, st.st_mtime, st.st_size))
    return found


def sweep() -> int:
    """Delete the longest-unplayed files until the cache is inside its budget.
    Returns how many went."""
    files = kept()
    total = sum(size for _p, _m, size in files)
    limit = budget()
    if total <= limit:
        return 0

    gone = 0
    for path, _when, size in sorted(files, key=lambda f: f[1]):
        if total <= limit:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        gone += 1
    if gone:
        log.info("audio cache over %d MB: removed %d file(s)", limit // 1024 // 1024, gone)
    return gone
