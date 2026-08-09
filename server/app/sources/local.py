"""Local filesystem connector.

In Mode A (all-in-one) this reads the disk directly. In Mode B the same interface is
satisfied by the remote agent over WireGuard — the catalog cannot tell the difference,
which is what lets the topology change without touching the catalog (ARCHITECTURE.md §3.4).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .base import Entry

log = logging.getLogger("hearth.sources.local")

# Directories that are never media and only slow a scan down.
_SKIP_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".cache", ".thumbnails",
    "$RECYCLE.BIN", "System Volume Information", ".Trash-1000", "@eaDir",
}

CHUNK = 256 * 1024


class LocalConnector:
    def __init__(self, root: str | Path) -> None:
        # Resolve once: every later path is validated against this.
        self.root = Path(root).resolve()

    # ── Path safety ─────────────────────────────────────────────────────────

    def _resolve(self, path: str) -> Path:
        """Map a source-relative path to a real one, refusing anything outside the root.

        Checked *after* symlink resolution — a symlink pointing out of the tree is
        exactly the case a naive prefix check misses (ARCHITECTURE.md §6).
        """
        candidate = (self.root / path.lstrip("/")).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise PermissionError(f"path escapes source root: {path}")
        return candidate

    # ── Connector protocol ──────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self.root.is_dir()

    def _entry(self, p: Path) -> Entry:
        st = p.stat()
        return Entry(
            name=p.name,
            is_dir=p.is_dir(),
            size=None if p.is_dir() else st.st_size,
            mtime=datetime.fromtimestamp(st.st_mtime, tz=UTC),
        )

    def list_dir(self, path: str = "") -> list[Entry]:
        target = self._resolve(path)
        if not target.is_dir():
            raise FileNotFoundError(path)

        entries: list[Entry] = []
        with os.scandir(target) as it:
            for de in it:
                if de.name.startswith(".") or de.name in _SKIP_DIRS:
                    continue
                try:
                    entries.append(self._entry(Path(de.path)))
                except OSError as exc:
                    # A single unreadable file must not abort a directory listing.
                    log.debug("skipping %s: %s", de.path, exc)

        # Directories first, then natural-ish name order — matches how a file manager
        # behaves, which is the point of the folder view (§2, principle 2).
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def stat(self, path: str) -> Entry:
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(path)
        return self._entry(target)

    def walk(self) -> Iterator[tuple[str, Entry]]:
        for dirpath, dirnames, filenames in os.walk(self.root, onerror=self._on_walk_error):
            # Prune in place so os.walk never descends into them.
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]

            rel_dir = Path(dirpath).relative_to(self.root).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir

            for name in filenames:
                if name.startswith("."):
                    continue
                try:
                    yield rel_dir, self._entry(Path(dirpath) / name)
                except OSError as exc:
                    log.debug("skipping %s: %s", name, exc)

    @staticmethod
    def _on_walk_error(exc: OSError) -> None:
        log.warning("scan could not read %s: %s", getattr(exc, "filename", "?"), exc)

    def open_range(self, path: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        target = self._resolve(path)
        size = target.stat().st_size
        last = size - 1 if end is None else min(end, size - 1)
        remaining = last - start + 1
        if remaining <= 0:
            return

        with target.open("rb") as fh:
            fh.seek(start)
            while remaining > 0:
                chunk = fh.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
