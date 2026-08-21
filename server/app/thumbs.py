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
from contextlib import closing
from pathlib import Path
from uuid import UUID

from .config import get_settings

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

# Enough of a film for ffmpeg to find an early frame. Fetching more from a remote
# source to make one thumbnail would be wasteful.
_VIDEO_PREFIX_BYTES = 24 * 1024 * 1024


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


def _read_prefix(connector, rel_path: str, limit: int) -> bytes:
    """Read at most `limit` bytes through the connector.

    Bytes rather than a filesystem path, because a source may not be a filesystem
    — the same call works for a local file and a Drive one. A bound matters here:
    a thumbnail never needs a whole 4 GB film.
    """
    collected = bytearray()
    # closing(): see the note in stream.py. A tiles view asks for forty of these
    # at once and each one breaks early, so this is where abandoned responses
    # piled up fastest.
    with closing(connector.open_range(rel_path, 0, limit - 1)) as chunks:
        for chunk in chunks:
            collected += chunk
            if len(collected) >= limit:
                break
    return bytes(collected)


def generate(item_id: UUID, kind: str, connector, rel_path: str,
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

    if kind == "video":
        # ffmpeg wants a seekable file. A local source already is one; anything
        # remote gets a bounded prefix written to a temp file, which is enough to
        # decode an early frame without dragging the whole film across.
        local = getattr(connector, "root", None)
        if local is not None:
            source = connector._resolve(rel_path)  # noqa: SLF001 - same package
            if not source.is_file():
                raise ThumbError("source file not reachable")
            data = _from_video(source, target)
        else:
            import tempfile

            prefix = _read_prefix(connector, rel_path, _VIDEO_PREFIX_BYTES)
            if not prefix:
                raise ThumbError("could not read any of the file")
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
                tmp.write(prefix)
                temp_path = Path(tmp.name)
            try:
                data = _from_video(temp_path, target)
            finally:
                temp_path.unlink(missing_ok=True)
    else:
        raw = _read_prefix(connector, rel_path, _MAX_SOURCE_BYTES)
        if not raw:
            raise ThumbError("source file not reachable")
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
