"""Thumbnail generation and cache.

Thumbnails live on the always-on node, which is what lets you browse and recognise
your whole photo library with the RAID powered off (ARCHITECTURE.md §3.3). Only the
full-resolution original needs the source to be reachable.

The cache is served through an authorised endpoint, never as a static directory — a
public thumbnail directory is a common and quiet leak in self-hosted media servers.
"""

from __future__ import annotations

import io
import logging
import subprocess
from pathlib import Path
from uuid import UUID

from .config import get_settings
from .sources.local import LocalConnector

log = logging.getLogger("homesh.thumbs")


def cache_root() -> Path:
    """Where thumbnails live. Resolved per call so configuration can change."""
    return Path(get_settings().cache_dir) / "thumbs"

# Two sizes, matching the two tile views. Small is deliberately small: a folder of
# two thousand tracks should not pull two thousand large images.
SIZES = {"small": 160, "large": 480}

# Written when a file genuinely has no artwork, so we do not re-run ffmpeg on every
# page view for a track that will never have a cover.
EMPTY = b"\x00"

_MAX_SOURCE_BYTES = 80 * 1024 * 1024  # refuse to decode absurd images


class ThumbError(Exception):
    pass


def cache_path(item_id: UUID, size: str) -> Path:
    # Shard by the first two hex characters: a single directory holding a hundred
    # thousand files is slow to list on most filesystems.
    key = item_id.hex
    return cache_root() / size / key[:2] / f"{key}.webp"


def _encode(img, target: int) -> bytes:
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(img)  # honour camera rotation
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((target, target), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=82, method=4)
    return buf.getvalue()


def _from_image(data: bytes, target: int) -> bytes:
    from PIL import Image

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:  # pragma: no cover - HEIC support is optional
        pass

    with Image.open(io.BytesIO(data)) as img:
        return _encode(img, target)


def _from_audio(data: bytes, target: int) -> bytes:
    """Embedded cover art, if the file carries any."""
    import mutagen

    f = mutagen.File(io.BytesIO(data))
    if f is None:
        raise ThumbError("unreadable audio")

    art: bytes | None = None
    tags = getattr(f, "tags", None)

    # Each container stores artwork differently; there is no common accessor.
    if tags is not None:
        for key in tags.keys():
            if key.startswith("APIC"):  # ID3
                art = tags[key].data
                break
        if art is None and "covr" in tags:  # MP4
            art = bytes(tags["covr"][0])
    if art is None and getattr(f, "pictures", None):  # FLAC
        art = f.pictures[0].data

    if not art:
        raise ThumbError("no embedded artwork")
    return _from_image(art, target)


def _from_video(path: Path, target: int) -> bytes:
    """A single frame, taken a little way in.

    Frame zero is very often black or a title card, so seek in before grabbing.
    """
    cmd = [
        "ffmpeg", "-v", "error",
        "-ss", "10",
        "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale={target}:-1:force_original_aspect_ratio=decrease",
        "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=30, check=False)  # noqa: S603

    if proc.returncode != 0 or not proc.stdout:
        # Shorter than the seek, or not decodable — retry from the very start.
        cmd[cmd.index("-ss") + 1] = "0"
        proc = subprocess.run(cmd, capture_output=True, timeout=30, check=False)  # noqa: S603

    if proc.returncode != 0 or not proc.stdout:
        raise ThumbError(f"ffmpeg produced no frame: {proc.stderr[:200]!r}")

    return _from_image(proc.stdout, target)


def generate(item_id: UUID, kind: str, connector: LocalConnector, rel_path: str,
             size: str = "small") -> Path:
    """Produce and cache one thumbnail. Returns its path.

    Raises ThumbError when the item cannot have one; the caller records that so the
    work is not repeated.
    """
    if size not in SIZES:
        raise ThumbError(f"unknown size {size!r}")

    target = SIZES[size]
    out = cache_path(item_id, size)
    if out.exists():
        return out

    source = connector._resolve(rel_path)  # noqa: SLF001 - same package boundary
    if not source.is_file():
        raise ThumbError("source file not reachable")

    if kind == "video":
        data = _from_video(source, target)
    else:
        if source.stat().st_size > _MAX_SOURCE_BYTES:
            raise ThumbError("source too large to decode")
        raw = source.read_bytes()
        if kind == "photo":
            data = _from_image(raw, target)
        elif kind == "audio":
            data = _from_audio(raw, target)
        else:
            raise ThumbError(f"no thumbnail strategy for kind {kind!r}")

    out.parent.mkdir(parents=True, exist_ok=True)
    # Write then rename: a reader must never observe a half-written file, and two
    # concurrent generators must not corrupt each other's output.
    tmp = out.with_suffix(f".{id(data)}.tmp")
    tmp.write_bytes(data)
    tmp.replace(out)
    return out


def mark_absent(item_id: UUID, size: str) -> None:
    """Record that this item has no artwork, so we stop trying."""
    out = cache_path(item_id, size)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(EMPTY)


def is_absent_marker(path: Path) -> bool:
    return path.is_file() and path.stat().st_size == len(EMPTY)
