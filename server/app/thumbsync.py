"""Thumbnails, carried from the PC to the standby.

The standby can make a thumbnail for anything on Drive, because it reads Drive.
It cannot make one for a file that lives only on the PC or the RAID -- the bytes
are on a disk it will never see -- so with the PC off those showed as blank
tiles, a folder of photographs as a grid of placeholders. The PC has already
made them; this carries them across.

**Through the bucket, like everything else between the two machines,** and
encrypted with the backup key before they leave (docs/STANDBY.md). A thumbnail
of a private photograph is a private photograph.

**In packs, never one object per thumbnail.** The free tier allows 50,000
requests a month, and a folder of photographs is thousands of thumbnails. The PC
sends what is new since last time as one archive; the standby fetches the packs
it has not applied yet. When the packs pile up, the PC writes one complete set
and removes the rest, so the bucket holds about one copy of the cache rather
than every increment ever sent.

Only the sizes a phone or a browser shows. "screen" exists for televisions, and
rooms do not run on the standby.
"""

from __future__ import annotations

import logging
import re
import secrets
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from . import crypt, offsite
from .config import get_settings
from .thumbs import cache_root

log = logging.getLogger("homesh.thumbsync")

THUMBS_PREFIX = "thumbs/"
SYNCED_SIZES = ("small", "large")

# One archive holds at most this much. A first push of a cache that has grown
# for months should not need one temporary file the size of all of it.
PACK_LIMIT = 150 * 1024 * 1024

# More packs than this in the bucket and the next push writes a complete set
# instead, then removes the others.
COMPACT_AFTER = 40

# The only names a pack may carry. Checked on the standby before anything is
# written: a pack is ciphertext from our own bucket, but "it came from us" is
# the assumption that makes an archive a way to write anywhere on the disk.
_MEMBER = re.compile(r"^(small|large)/[0-9a-f]{2}/[0-9a-f]{32}\.webp$")
_MAX_MEMBER_BYTES = 5 * 1024 * 1024
_PACK_NAME = re.compile(r"^thumbs/\d{8}-\d{6}-\d{3}-[0-9a-f]{6}(-full)?\.tar\.enc$")


def _state(name: str) -> Path:
    return cache_root() / f".{name}"


def _store():
    store = offsite.configured(get_settings())
    if store is None:
        raise offsite.OffsiteError("no off-site store is configured")
    return store


def _key() -> bytes:
    return crypt.key_bytes(get_settings().backup_key)


def _cached() -> list[tuple[Path, float, int]]:
    """Every synced thumbnail as (path, modified, size)."""
    root = cache_root()
    found = []
    for size in SYNCED_SIZES:
        for path in (root / size).glob("*/*.webp"):
            try:
                st = path.stat()
            except OSError:
                continue  # removed while listing
            found.append((path, st.st_mtime, st.st_size))
    return found


def _packs() -> list[str]:
    return sorted(
        item.name for item in offsite.index(_store()) if _PACK_NAME.match(item.name)
    )


# -- The PC: sending ----------------------------------------------------------


def push() -> dict:
    """Send the thumbnails made since the last push. Returns what was sent."""
    marker = _state("thumbs-pushed")
    try:
        since = float(marker.read_text())
    except (OSError, ValueError):
        since = 0.0

    # Taken before looking, and recorded only once everything has been sent. A
    # thumbnail written while this runs is then picked up next time rather than
    # missed; one sent twice is harmless, because applying a pack only overwrites.
    started = time.time()

    existing = _packs()
    full = len(existing) >= COMPACT_AFTER
    everything = _cached()
    chosen = everything if full else [f for f in everything if f[1] > since]
    if not chosen:
        return {"sent": 0, "packs": 0}

    # The time orders the packs; the random part keeps two pushes in the same
    # second from writing the same name, which in a bucket is not an error but
    # a silent overwrite -- the first pack simply disappears. The tests found it.
    stamp = f"{datetime.now(UTC):%Y%m%d-%H%M%S}"
    run = secrets.token_hex(3)
    root = cache_root()
    key = _key()
    sent_packs: list[str] = []

    batch: list[tuple[Path, float, int]] = []
    batch_bytes = 0

    def send(files: list[tuple[Path, float, int]]) -> None:
        name = (
            f"{THUMBS_PREFIX}{stamp}-{len(sent_packs):03d}-{run}"
            f"{'-full' if full else ''}.tar.enc"
        )
        with tempfile.TemporaryDirectory() as work:
            plain = Path(work) / "pack.tar"
            sealed = Path(work) / "pack.tar.enc"
            with tarfile.open(plain, "w") as tar:
                for path, _mtime, _size in files:
                    try:
                        tar.add(path, arcname=path.relative_to(root).as_posix())
                    except OSError:
                        continue  # removed since it was listed
            crypt.encrypt(plain, sealed, key)
            offsite.put(_store(), name, sealed)
        sent_packs.append(name)

    for entry in sorted(chosen, key=lambda f: f[1]):
        if batch and batch_bytes + entry[2] > PACK_LIMIT:
            send(batch)
            batch, batch_bytes = [], 0
        batch.append(entry)
        batch_bytes += entry[2]
    if batch:
        send(batch)

    if full:
        # Only after the complete set is up. Removing first would leave a
        # standby that refreshed in between with nothing to fetch.
        for name in existing:
            offsite.remove(_store(), name)

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(repr(started))
    log.info(
        "sent %d thumbnail(s) to the standby in %d pack(s)%s",
        len(chosen), len(sent_packs), ", replacing the rest" if full else "",
    )
    return {"sent": len(chosen), "packs": len(sent_packs), "compacted": full}


# -- The standby: receiving -----------------------------------------------------


def pull() -> dict:
    """Apply the packs not applied yet. Returns how many files landed."""
    record = _state("thumbs-applied")
    try:
        applied = set(record.read_text().split())
    except OSError:
        applied = set()

    present = _packs()
    todo = [name for name in present if name not in applied]
    landed = refused = 0
    key = _key()

    for name in todo:
        with tempfile.TemporaryDirectory() as work:
            sealed = Path(work) / "pack.tar.enc"
            plain = Path(work) / "pack.tar"
            offsite.get(_store(), name, sealed)
            crypt.decrypt(sealed, plain, key)
            ok, bad = _unpack(plain)
            landed += ok
            refused += bad
        applied.add(name)
        # Written after each pack, so a pull interrupted half way does not start
        # again from the first.
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("\n".join(sorted(applied & set(present))))

    if refused:
        log.warning("refused %d file(s) in thumbnail packs that were not thumbnails", refused)
    if todo:
        log.info("applied %d thumbnail pack(s): %d file(s)", len(todo), landed)
    return {"packs": len(todo), "files": landed, "refused": refused}


def _unpack(archive: Path) -> tuple[int, int]:
    """Write what is a thumbnail, refuse the rest. Returns (written, refused)."""
    root = cache_root()
    written = refused = 0
    with tarfile.open(archive, "r") as tar:
        for member in tar:
            # A regular file, named exactly like a thumbnail, of a sane size.
            # Anything else -- a link, a device, a path with "..", an absolute
            # path -- fails the name pattern or the type check and is skipped
            # without touching the disk.
            if (
                not member.isreg()
                or not _MEMBER.match(member.name)
                or member.size > _MAX_MEMBER_BYTES
            ):
                refused += 1
                continue
            source = tar.extractfile(member)
            if source is None:
                refused += 1
                continue
            data = source.read(_MAX_MEMBER_BYTES + 1)
            target = root / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            # Write then rename, as thumbs.generate does: a request for this
            # thumbnail must never read half a file.
            tmp = target.with_suffix(".sync.tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
            written += 1
    return written, refused
