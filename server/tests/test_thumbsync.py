"""Thumbnails carried from the PC to the standby, through the bucket.

What matters: they arrive, they leave encrypted, each is sent once, the request
count stays small, and a pack cannot write anything that is not a thumbnail.
"""

from __future__ import annotations

import io
import os
import tarfile
import uuid
from pathlib import Path

import pytest

from app import thumbsync
from app.thumbs import cache_path, cache_root

from .test_standby import Bucket

# Bytes no encryption would ever leave in place, so finding them in the bucket
# means a thumbnail went out in the clear.
TELLTALE = b"RIFF-a-private-photograph-WEBP"


@pytest.fixture
def cache(monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield cache_root()
    get_settings.cache_clear()


@pytest.fixture
def bucket(monkeypatch, cache):
    from app import offsite
    from app.config import get_settings
    from app.crypt import new_key

    fake = Bucket()
    for name in ("configured", "put", "index", "get", "remove"):
        monkeypatch.setattr(offsite, name, getattr(fake, name))
    monkeypatch.setenv("BACKUP_KEY", new_key())
    get_settings.cache_clear()
    return fake


def _thumb(size: str = "small", body: bytes = TELLTALE, age: float = 0) -> Path:
    path = cache_path(uuid.uuid4(), size)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    if age:
        when = path.stat().st_mtime - age
        os.utime(path, (when, when))
    return path


def _packs(bucket) -> list[str]:
    return sorted(n for n in bucket.held if n.startswith(thumbsync.THUMBS_PREFIX))


def _wipe(root: Path) -> None:
    for size in ("small", "large", "screen"):
        for path in (root / size).glob("*/*.webp"):
            path.unlink()


class TestTheTrip:
    def test_they_arrive_and_leave_encrypted(self, bucket, cache):
        made = [_thumb("small"), _thumb("large")]
        contents = {p.relative_to(cache): p.read_bytes() for p in made}

        assert thumbsync.push()["sent"] == 2
        [pack] = _packs(bucket)
        assert TELLTALE not in bucket.held[pack], "a thumbnail went out in the clear"

        # The standby's cache is empty until it pulls.
        _wipe(cache)
        assert thumbsync.pull() == {"packs": 1, "files": 2, "refused": 0}
        for rel, body in contents.items():
            assert (cache / rel).read_bytes() == body

    def test_many_thumbnails_are_one_request(self, bucket, cache):
        """The free tier allows 50,000 requests a month; a folder of
        photographs is thousands of thumbnails."""
        for _ in range(200):
            _thumb()
        assert thumbsync.push() == {"sent": 200, "packs": 1, "compacted": False}
        assert len(_packs(bucket)) == 1

    def test_televisions_size_stays_behind(self, bucket, cache):
        """Rooms do not run on the standby, and "screen" is 1920 pixels a photo."""
        _thumb("screen")
        _thumb("small")
        assert thumbsync.push()["sent"] == 1


def test_two_pushes_in_one_second_do_not_overwrite_each_other(bucket, cache):
    """In a bucket a second write to the same name is not an error but a
    silent overwrite: the first pack would simply be gone."""
    _thumb(age=60)
    thumbsync.push()
    fresh = _thumb()
    future = fresh.stat().st_mtime + 5
    os.utime(fresh, (future, future))
    thumbsync.push()
    packs = _packs(bucket)
    assert len(packs) == 2 and len(set(packs)) == 2


class TestOnlyWhatIsNew:
    def test_a_second_push_with_nothing_new_sends_nothing(self, bucket, cache):
        _thumb(age=60)
        thumbsync.push()
        assert thumbsync.push() == {"sent": 0, "packs": 0}
        assert len(_packs(bucket)) == 1

    def test_a_new_thumbnail_goes_in_the_next_pack_alone(self, bucket, cache):
        _thumb(age=60)
        thumbsync.push()
        fresh = _thumb()
        # Ahead of the recorded time, whatever the clock's resolution.
        future = fresh.stat().st_mtime + 5
        os.utime(fresh, (future, future))

        assert thumbsync.push()["sent"] == 1
        assert len(_packs(bucket)) == 2

    def test_each_pack_is_applied_once(self, bucket, cache):
        _thumb()
        thumbsync.push()
        assert thumbsync.pull()["packs"] == 1
        assert thumbsync.pull() == {"packs": 0, "files": 0, "refused": 0}


class TestAPackCannotWriteAnywhereElse:
    """A pack is ciphertext from our own bucket, and that is exactly the
    assumption that turns an archive into a way to write anywhere."""

    def _hostile(self, bucket, cache, members: list[tuple[tarfile.TarInfo, bytes]]):
        from app import crypt
        from app.config import get_settings

        cache.mkdir(parents=True, exist_ok=True)
        plain = cache.parent / "hostile.tar"
        sealed = cache.parent / "hostile.tar.enc"
        with tarfile.open(plain, "w") as tar:
            for info, data in members:
                tar.addfile(info, io.BytesIO(data) if data else None)
        crypt.encrypt(plain, sealed, crypt.key_bytes(get_settings().backup_key))
        bucket.held["thumbs/20260101-000000-000-abcdef.tar.enc"] = sealed.read_bytes()

    @staticmethod
    def _file(name: str, data: bytes = b"x") -> tuple[tarfile.TarInfo, bytes]:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        return info, data

    def test_only_thumbnail_shaped_files_land(self, bucket, cache):
        good = f"small/ab/{'ab' + 'c' * 30}.webp"
        link = tarfile.TarInfo(f"small/cd/{'cd' + 'e' * 30}.webp")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        self._hostile(bucket, cache, [
            self._file(good),
            self._file("../../escaped.webp"),
            self._file("/etc/absolute.webp"),
            self._file("small/ab/not-a-thumbnail.sh"),
            self._file(f"screen/ab/{'ab' + 'f' * 30}.webp"),
            (link, b""),
        ])

        assert thumbsync.pull() == {"packs": 1, "files": 1, "refused": 5}
        assert (cache / good).read_bytes() == b"x"
        assert not (cache.parent.parent / "escaped.webp").exists()
        assert not (cache.parent / "escaped.webp").exists()
        assert not (cache / "small" / "cd").exists(), "a link was followed"

    def test_an_absurdly_large_member_is_refused(self, bucket, cache):
        name = f"large/ab/{'ab' + '0' * 30}.webp"
        self._hostile(bucket, cache, [self._file(name, b"0" * (6 * 1024 * 1024))])
        assert thumbsync.pull()["refused"] == 1
        assert not (cache / name).exists()


class TestTheBucketStaysSmall:
    def test_many_packs_become_one_complete_set(self, bucket, cache, monkeypatch):
        monkeypatch.setattr(thumbsync, "COMPACT_AFTER", 3)
        made = []
        for n in range(3):
            path = _thumb(age=300 - n * 60)
            made.append(path)
            thumbsync.push()
            # The next thumbnail must look newer than the recorded push.
            (cache / ".thumbs-pushed").write_text(repr(path.stat().st_mtime))
        assert len(_packs(bucket)) == 3

        result = thumbsync.push()
        assert result["compacted"] is True
        assert result["sent"] == 3
        packs = _packs(bucket)
        assert len(packs) == 1 and packs[0].endswith("-full.tar.enc")

        # A standby starting from nothing gets everything from the one set.
        _wipe(cache)
        (cache / ".thumbs-applied").unlink(missing_ok=True)
        assert thumbsync.pull()["files"] == 3
        assert all(p.exists() for p in made)


def test_the_standby_looks_hourly_not_every_pass():
    """It is the PC's sending that is hourly; looking every ten minutes would
    spend requests finding nothing."""
    from app.upkeep import SYNC_EVERY, THUMBS_EVERY_PASSES

    assert SYNC_EVERY * THUMBS_EVERY_PASSES == SYNC_EVERY * 6
