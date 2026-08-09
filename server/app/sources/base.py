"""The one interface every source implements.

Local disk, Google Drive and a Takeout archive differ enormously underneath, but the
catalog only ever needs these four operations. Adding Dropbox or SMB later means a new
connector and nothing else (ARCHITECTURE.md §4).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

# Extension → media kind. Deliberately explicit rather than mimetype-guessing: the
# catalog's job is to be predictable, and an unknown extension is a fact worth keeping
# rather than a thing to hide.
_KINDS: dict[str, str] = {
    **dict.fromkeys(
        ["mp3", "flac", "m4a", "aac", "ogg", "opus", "wma", "wav", "aiff", "alac", "ape", "wv"],
        "audio",
    ),
    **dict.fromkeys(
        ["mp4", "mkv", "avi", "mov", "wmv", "m4v", "mpg", "mpeg", "webm", "flv", "ts", "m2ts"],
        "video",
    ),
    **dict.fromkeys(
        ["jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff",
         "raw", "cr2", "nef", "arw", "dng"],
        "photo",
    ),
    **dict.fromkeys(
        ["pdf", "epub", "mobi", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
         "txt", "md", "rtf", "odt", "ods", "odp", "csv"],
        "doc",
    ),
}

# Playlists are catalogued but are not media items; they become playlists (§5.2).
PLAYLIST_EXTS = {"m3u", "m3u8", "pls", "asx", "wpl"}


def classify(filename: str) -> tuple[str, str]:
    """Return (kind, extension). Unknown extensions are kept as 'other', never dropped."""
    _, _, ext = filename.rpartition(".")
    ext = ext.lower() if ext and ext != filename else ""
    return _KINDS.get(ext, "other"), ext


@dataclass(frozen=True)
class Entry:
    """One filesystem-ish node, as the catalog sees it."""

    name: str
    is_dir: bool
    size: int | None = None
    mtime: datetime | None = None
    # Opaque per-source id (Drive file id, inode, …). None where a path is the identity.
    remote_id: str | None = None


@runtime_checkable
class Connector(Protocol):
    """A mounted source. Paths are POSIX-style and relative to the source root."""

    def list_dir(self, path: str) -> list[Entry]:
        """Immediate children of a directory. Raises FileNotFoundError if absent."""
        ...

    def stat(self, path: str) -> Entry:
        """Metadata for a single node."""
        ...

    def walk(self) -> Iterator[tuple[str, Entry]]:
        """Yield (dir_path, entry) for every file beneath the root, recursively."""
        ...

    def open_range(self, path: str, start: int, end: int | None) -> Iterator[bytes]:
        """Stream bytes [start, end]. Underpins HTTP range requests and seeking."""
        ...

    @property
    def available(self) -> bool:
        """False when the backing store is unreachable — RAID off, agent down, no network."""
        ...
