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
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from .db import get_engine
from .sources.local import LocalConnector

log = logging.getLogger("hearth.metadata")

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
                SELECT i.id, i.kind::text, r.dir_path, r.filename
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

    for item_id, kind, dir_path, filename in rows:
        result.processed += 1
        rel = f"{dir_path}/{filename}" if dir_path else filename

        try:
            path = connector._resolve(rel)  # noqa: SLF001 - same package boundary
            if not path.is_file():
                continue
            tags, duration_ms = READERS[kind](path)
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
