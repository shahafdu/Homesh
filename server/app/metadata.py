"""Metadata extraction.

Tags are **additive**. They never replace the filename, and every value records
where it came from, so a tag written by a fingerprinting service or a model can
never masquerade as something the file itself claimed (ARCHITECTURE.md §2, §9).

This runs as a pass after scanning rather than inside it. A first scan stays fast
and the folder view becomes usable immediately; tags arrive afterwards.
"""

from __future__ import annotations

import json
import logging
import pathlib
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from .db import get_engine
from .sources.local import LocalConnector

log = logging.getLogger("homesh.metadata")

# Keys we surface in listings. Deliberately small: a listing needs a handful of
# facts, not everything a container can carry.
LISTING_KEYS = ("title", "artist", "album", "albumartist", "track", "year")

# Tag names differ per container, so each canonical key needs its aliases.
_AUDIO_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("TIT2", "title", "\xa9nam"),
    "artist": ("TPE1", "artist", "\xa9ART"),
    "album": ("TALB", "album", "\xa9alb"),
    "albumartist": ("TPE2", "albumartist", "aART"),
    "track": ("TRCK", "tracknumber", "trkn"),
    "year": ("TDRC", "date", "\xa9day", "year"),
}


@dataclass
class ExtractResult:
    processed: int = 0
    tagged: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _clean(value: object) -> str | None:
    """Normalise whatever a tag library hands back into a plain string."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    # MP4 track numbers arrive as (track, total) tuples.
    if isinstance(value, tuple):
        value = value[0]
    text_value = str(value).strip()
    if not text_value:
        return None
    # "1/12" is a track position, not a number; keep only the position.
    if "/" in text_value and text_value.split("/")[0].strip().isdigit():
        text_value = text_value.split("/")[0].strip()
    return text_value[:500]


def read_audio(path: Path) -> tuple[dict[str, str], int | None]:
    """Tags and duration from an audio file. Returns ({}, None) when unreadable."""
    import mutagen

    try:
        f = mutagen.File(path)
    except Exception as exc:  # noqa: BLE001 - a corrupt file is data, not a crash
        log.debug("unreadable audio %s: %s", path.name, exc)
        return {}, None

    if f is None:
        return {}, None

    tags: dict[str, str] = {}
    container = getattr(f, "tags", None)
    if container is not None:
        for key, aliases in _AUDIO_ALIASES.items():
            for alias in aliases:
                try:
                    raw = container.get(alias)
                except Exception as exc:  # noqa: BLE001
                    # Some containers raise on unknown keys rather than returning
                    # None. Not worth a warning; try the next alias.
                    log.debug("tag %s unreadable in %s: %s", alias, path.name, exc)
                    continue
                # ID3 frames carry their value in .text
                value = _clean(getattr(raw, "text", raw))
                if value:
                    tags[key] = value
                    break

    duration_ms = None
    info = getattr(f, "info", None)
    if info is not None and getattr(info, "length", None):
        duration_ms = int(info.length * 1000)

    return tags, duration_ms


def read_photo(path: Path) -> tuple[dict[str, str], int | None]:
    """Capture date and dimensions. The date is what a timeline view sorts on."""
    from PIL import Image

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:  # pragma: no cover
        pass

    tags: dict[str, str] = {}
    try:
        with Image.open(path) as img:
            tags["width"] = str(img.width)
            tags["height"] = str(img.height)
            exif = img.getexif()
            # 36867 DateTimeOriginal, 306 DateTime
            for tag_id in (36867, 306):
                value = exif.get(tag_id)
                if value:
                    tags["taken_at"] = str(value).strip()
                    break
    except Exception as exc:  # noqa: BLE001
        log.debug("unreadable image %s: %s", path.name, exc)

    return tags, None


def read_video(path: Path) -> tuple[dict[str, str], int | None]:
    """Duration and dimensions via ffprobe.

    ffprobe reads the container header rather than decoding, so this stays cheap
    even for large files.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=width,height",
        "-of", "json", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=20, check=False)  # noqa: S603
        if proc.returncode != 0:
            return {}, None
        data = json.loads(proc.stdout or b"{}")
    except Exception as exc:  # noqa: BLE001
        log.debug("ffprobe failed on %s: %s", path.name, exc)
        return {}, None

    tags: dict[str, str] = {}
    for stream in data.get("streams", []):
        if stream.get("width") and stream.get("height"):
            tags["width"] = str(stream["width"])
            tags["height"] = str(stream["height"])
            break

    duration_ms = None
    raw = data.get("format", {}).get("duration")
    if raw:
        try:
            duration_ms = int(float(raw) * 1000)
        except ValueError:
            pass

    return tags, duration_ms


READERS = {"audio": read_audio, "photo": read_photo, "video": read_video}


# ── Reading tags from a source that is not a filesystem ─────────────────────
#
# The readers below take a path, because that is what mutagen and Pillow want.
# A Drive file has no path, so a bounded prefix is fetched and written to a
# temporary one. The point is the bound: a music library is 9,500 files, and
# fetching all of every one to read three strings would move hundreds of
# gigabytes to learn something that lives in the first few kilobytes.

# Enough for a photo's EXIF, or an ID3 tag carrying cover art.
_PREFIX_BYTES = 256 * 1024

# ID3v2 declares its own length in its first ten bytes, so an MP3 usually needs
# only that much fetched — a few kilobytes rather than a quarter of a megabyte.
_ID3_HEADER = 10


def _id3_length(head: bytes) -> int | None:
    """Total ID3v2 tag length from its header, if this is one."""
    if len(head) < _ID3_HEADER or head[:3] != b"ID3":
        return None
    # A syncsafe integer: seven bits per byte, so the high bit never collides
    # with an MPEG frame sync.
    size = 0
    for byte in head[6:10]:
        if byte & 0x80:
            return None
        size = (size << 7) | byte
    return _ID3_HEADER + size


def _fetch_prefix(connector, rel_path: str, want: int) -> bytes:
    collected = bytearray()
    for chunk in connector.open_range(rel_path, 0, want - 1):
        collected += chunk
        if len(collected) >= want:
            break
    return bytes(collected)


# Audio frames fetched past the end of the tag. mutagen refuses a file it cannot
# sync to an MPEG frame in, so the tag alone parses as nothing at all — which is
# exactly what happened: every Drive track came back untagged despite having
# perfectly good tags.
_FRAME_MARGIN = 64 * 1024


def _duration_from_bitrate(path, true_size: int | None) -> int | None:
    """Length in milliseconds, worked out rather than measured.

    A prefix cannot be timed: mutagen reports how long the fragment lasts, which
    for a 70 KB slice of a four-minute song is a few seconds. That is why the
    duration was being thrown away — and why every track in this library showed
    no length at all, since everything in it comes from Drive and is read as a
    prefix.

    But the bitrate is declared in the first frame, which the prefix does
    contain, and the true size is already in the catalog. Length is the one
    divided by the other. Exact for constant bitrate, and close enough for
    variable, where mutagen reports the average the file itself declares.
    """
    if not true_size:
        return None
    try:
        import mutagen

        f = mutagen.File(path)
        bitrate = getattr(getattr(f, "info", None), "bitrate", None)
    except Exception:  # noqa: BLE001 - an unreadable file simply has no length
        return None

    if not bitrate:
        return None
    return int(true_size * 8 / bitrate * 1000)


@contextmanager
def _local_copy(connector, rel_path: str, filename: str):
    """A real file holding enough of a remote one to read its tags.

    Yields (path, partial). Local sources are handed straight through and are
    never partial; a remote one is a prefix, and the flag says so because a
    truncated file still parses — it simply reports the duration of the fragment
    rather than of the track. A wrong duration is worse than no duration.
    """
    resolve = getattr(connector, "_resolve", None)
    if resolve is not None:
        path = resolve(rel_path)
        if path.is_file():
            yield path, False
            return

    head = _fetch_prefix(connector, rel_path, _ID3_HEADER)
    tag_len = _id3_length(head)
    want = min(tag_len + _FRAME_MARGIN, _PREFIX_BYTES) if tag_len else _PREFIX_BYTES

    data = _fetch_prefix(connector, rel_path, want)
    if not data:
        raise OSError("could not read any of the file")

    suffix = pathlib.Path(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = pathlib.Path(tmp.name)
    try:
        yield tmp_path, True
    finally:
        tmp_path.unlink(missing_ok=True)


def extract_for_source(
    source_id: UUID, connector: LocalConnector, limit: int | None = None
) -> ExtractResult:
    """Read tags for items in this source that have none yet.

    Only untouched items are considered, so re-running is cheap and a large
    library can be worked through in batches.
    """
    result = ExtractResult()
    if not connector.available:
        result.errors.append("source unavailable")
        return result

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT i.id, i.kind::text, r.dir_path, r.filename, i.size_bytes
                FROM items i
                JOIN replicas r ON r.item_id = i.id
                WHERE r.source_id = :sid
                  AND r.available
                  AND i.kind IN ('audio', 'photo', 'video')
                  AND NOT EXISTS (
                      SELECT 1 FROM item_metadata m
                      WHERE m.item_id = i.id AND m.origin = 'file'
                  )
                ORDER BY r.dir_path, r.filename
                LIMIT :lim
                """
            ),
            # Always parameterised rather than concatenated: building SQL by
            # string is how injection gets in, even when today's input is safe.
            {"sid": str(source_id), "lim": limit if limit else 10_000_000},
        ).all()

    for item_id, kind, dir_path, filename, true_size in rows:
        result.processed += 1
        rel = f"{dir_path}/{filename}" if dir_path else filename

        try:
            with _local_copy(connector, rel, filename) as (path, partial):
                tags, duration_ms = READERS[kind](path)
                if partial:
                    # What was measured is the length of the fragment, not of the
                    # track. Worked out from the declared bitrate and the real
                    # size instead of being discarded.
                    duration_ms = (
                        _duration_from_bitrate(path, true_size)
                        if kind == "audio"
                        else None
                    )
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the pass
            result.failed += 1
            log.debug("metadata failed for %s: %s", filename, exc)
            tags, duration_ms = {}, None

        with engine.begin() as conn:
            for key, value in tags.items():
                conn.execute(
                    text(
                        """
                        INSERT INTO item_metadata (item_id, key, value, origin, confidence)
                        VALUES (:id, :k, :v, 'file', 1.0)
                        ON CONFLICT (item_id, key, origin)
                        DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                        """
                    ),
                    {"id": str(item_id), "k": key, "v": value},
                )

            if duration_ms:
                conn.execute(
                    text("UPDATE items SET duration_ms = :d WHERE id = :id"),
                    {"d": duration_ms, "id": str(item_id)},
                )

            # A marker so a file with genuinely no tags is not retried forever.
            # It is recorded as origin 'file' because that is what was inspected.
            if not tags:
                conn.execute(
                    text(
                        """
                        INSERT INTO item_metadata (item_id, key, value, origin, confidence)
                        VALUES (:id, 'scanned', :at, 'file', 1.0)
                        ON CONFLICT (item_id, key, origin) DO NOTHING
                        """
                    ),
                    {"id": str(item_id), "at": datetime.now().isoformat(timespec="seconds")},
                )
            else:
                result.tagged += 1

    log.info(
        "metadata pass: %d processed, %d tagged, %d failed",
        result.processed, result.tagged, result.failed,
    )
    return result


def backfill_durations(source_id: UUID, connector, limit: int | None = None) -> int:
    """Work out lengths for tracks catalogued before lengths could be worked out.

    The tag pass skips anything it has already described, so a fix to how
    duration is derived reaches nothing without a pass of its own. Only items
    still missing a length are touched, which makes this cheap to re-run and
    means it converges rather than repeating work.
    """
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT i.id, r.dir_path, r.filename, i.size_bytes
                FROM items i
                JOIN replicas r ON r.item_id = i.id
                WHERE r.source_id = :sid AND r.available
                  AND i.kind = 'audio'
                  AND i.duration_ms IS NULL
                  AND i.size_bytes > 0
                ORDER BY r.dir_path, r.filename
                LIMIT :lim
                """
            ),
            {"sid": str(source_id), "lim": limit if limit else 10_000_000},
        ).all()

    filled = 0
    for item_id, dir_path, filename, size in rows:
        rel = f"{dir_path}/{filename}" if dir_path else filename
        try:
            with _local_copy(connector, rel, filename) as (path, _partial):
                duration_ms = _duration_from_bitrate(path, size)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the pass
            log.debug("no length for %s: %s", filename, exc)
            continue

        if not duration_ms:
            continue

        with engine.begin() as conn:
            conn.execute(
                text("UPDATE items SET duration_ms = :d WHERE id = :id"),
                {"d": duration_ms, "id": str(item_id)},
            )
        filled += 1

    if filled:
        log.info("filled in %d track lengths", filled)
    return filled
