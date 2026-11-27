"""Converting an old-format file when it is sent.

Every clip here is made by ffmpeg while the test runs, and ffmpeg reads it back
over a real HTTP server through the signed stream endpoint, which is the path a
send takes in the house.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import text

from app import ffinput, sendcopy, transcode
from app.config import get_settings
from app.library import forget_connectors
from app.main import app
from app.scanner import scan_source
from app.security import CurrentUser, require_user
from app.sources.local import LocalConnector

CASES = json.loads(
    (Path(__file__).parent / "send_copy_cases.json").read_text(encoding="utf-8")
)


# ── What is converted to what ───────────────────────────────────────────────


@pytest.mark.parametrize("kind,ext,expected", CASES["cases"])
def test_target(kind, ext, expected):
    assert sendcopy.target_for(kind, ext) == expected


def test_the_lists_agree_with_the_web_client():
    """web/test/formats.test.ts holds the other side to the same file."""
    assert sendcopy.SHAREABLE == set(CASES["shareable"])
    assert sendcopy.AUDIO_EXTS == set(CASES["audio"])
    assert transcode.NEEDS_CONVERSION == set(CASES["video_needs_conversion"])


# ── Real conversions ────────────────────────────────────────────────────────

_PICTURE = "testsrc=size={size}:rate=10:duration={seconds}"
_TONE = "sine=frequency=440:duration={seconds}"


def _ffmpeg(*args: str) -> None:
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],  # noqa: S607
        check=True,
        capture_output=True,
    )


def _video(path: Path, vcodec: str, acodec: str, seconds=2, size="320x240") -> None:
    _ffmpeg(
        "-f", "lavfi", "-i", _PICTURE.format(size=size, seconds=seconds),
        "-f", "lavfi", "-i", _TONE.format(seconds=seconds),
        "-c:v", vcodec, "-c:a", acodec, "-ar", "44100", "-shortest", str(path),
    )


def _audio(path: Path, acodec: str, seconds=2, *extra: str) -> None:
    _ffmpeg(
        "-f", "lavfi", "-i", _TONE.format(seconds=seconds),
        "-c:a", acodec, "-ar", "44100", *extra, str(path),
    )


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("clips")
    _video(root / "Beach day.wmv", "wmv2", "wmav2")
    _video(root / "tall.wmv", "wmv2", "wmav2", seconds=1, size="640x1200")
    _video(root / "tape.mpg", "mpeg2video", "mp2")
    _video(root / "long.wmv", "wmv2", "wmav2", seconds=6)
    _audio(root / "Chanson d'été.wma", "wmav2", 2, "-metadata", "title=A Song")
    _audio(root / "memo.ac3", "ac3")
    (root / "broken.wmv").write_bytes(b"this is not a video" * 100)
    (root / "plain.mp3").write_bytes(b"x" * 16)
    (root / "plain.mp4").write_bytes(b"x" * 16)
    (root / "tune.mid").write_bytes(b"x" * 16)
    return root


@pytest.fixture
def scanned(db, clips, monkeypatch):
    with db.begin() as conn:
        sid = conn.execute(
            text(
                """
                INSERT INTO sources (kind, name, mount_prefix, remote_id, audience)
                VALUES ('local', 'Clips', '/local/clips', :root, 'everyone') RETURNING id
                """
            ),
            {"root": str(clips)},
        ).scalar_one()
    monkeypatch.setenv("MEDIA_ROOTS", f"clips={clips}")
    get_settings.cache_clear()
    forget_connectors()
    scan_source(sid, LocalConnector(clips))
    yield sid
    get_settings.cache_clear()
    forget_connectors()


@pytest.fixture
def sending(stream_server, scanned, monkeypatch):
    monkeypatch.setattr(ffinput, "SOURCE_BASE", stream_server)
    # A semaphore belongs to the event loop that first waited on it, and each
    # test client has a loop of its own.
    monkeypatch.setattr(transcode, "_slot", asyncio.Semaphore(1))
    for leftover in sendcopy._dir().iterdir():
        leftover.unlink()


@pytest.fixture
def children(monkeypatch) -> list[str]:
    """The programs a request starts, by name, still really run."""
    started: list[str] = []
    real = sendcopy._run

    async def recording(args, timeout):
        started.append(args[0])
        return await real(args, timeout)

    monkeypatch.setattr(sendcopy, "_run", recording)
    return started


def _item(db, filename: str):
    with db.connect() as conn:
        return conn.execute(
            text("SELECT item_id FROM replicas WHERE filename = :f"), {"f": filename}
        ).scalar_one()


def _set_duration(db, item, ms: int) -> None:
    with db.begin() as conn:
        conn.execute(
            text("UPDATE items SET duration_ms = :d WHERE id = :id"),
            {"d": ms, "id": str(item)},
        )


def _send(client, item):
    return client.post(f"/api/items/{item}/send-copy")


def _read(tmp_path: Path, body: bytes, suffix: str) -> dict:
    """What ffprobe makes of what came back."""
    path = tmp_path / f"received.{suffix}"
    path.write_bytes(body)
    out = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "error", "-show_entries",  # noqa: S607
         "stream=codec_name,codec_type,width,height:format=format_name,duration:format_tags",
         "-of", "json", str(path)],
        check=True,
        capture_output=True,
    ).stdout
    return json.loads(out)


def _codecs(probed: dict) -> dict[str, str]:
    return {s["codec_type"]: s["codec_name"] for s in probed["streams"]}


def _left_behind() -> list[Path]:
    return list(sendcopy._dir().iterdir())


class TestTheCopy:
    def test_wmv_arrives_as_mp4(self, client, db, sending, tmp_path):
        r = _send(client, _item(db, "Beach day.wmv"))

        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "video/mp4"
        assert r.headers["cache-control"] == "no-store"
        disposition = r.headers["content-disposition"]
        assert disposition.startswith("attachment;")
        assert 'filename="Beach day.mp4"' in disposition
        probed = _read(tmp_path, r.content, "mp4")
        assert _codecs(probed) == {"video": "h264", "audio": "aac"}
        assert "mp4" in probed["format"]["format_name"]
        assert float(probed["format"]["duration"]) == pytest.approx(2, abs=0.5)
        # The index comes first, so it starts playing before it has all arrived.
        assert r.content.index(b"moov") < r.content.index(b"mdat")

    def test_no_more_than_1080_lines(self, client, db, sending, tmp_path):
        r = _send(client, _item(db, "tall.wmv"))

        assert r.status_code == 200, r.text
        picture = next(
            s for s in _read(tmp_path, r.content, "mp4")["streams"] if s["codec_type"] == "video"
        )
        assert (picture["width"], picture["height"]) == (576, 1080)

    def test_mpeg2_is_converted_although_it_could_be_attached(
        self, client, db, sending, tmp_path
    ):
        r = _send(client, _item(db, "tape.mpg"))

        assert r.status_code == 200, r.text
        assert _codecs(_read(tmp_path, r.content, "mp4")) == {"video": "h264", "audio": "aac"}

    def test_wma_arrives_as_m4a_with_its_tags(self, client, db, sending, tmp_path):
        r = _send(client, _item(db, "Chanson d'été.wma"))

        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "audio/mp4"
        named = quote("Chanson d'été.m4a")
        assert f"filename*=UTF-8''{named}" in r.headers["content-disposition"]
        probed = _read(tmp_path, r.content, "m4a")
        assert _codecs(probed) == {"audio": "aac"}
        assert probed["format"]["tags"]["title"] == "A Song"

    def test_sound_the_catalog_calls_other_is_converted_too(
        self, client, db, sending, tmp_path
    ):
        r = _send(client, _item(db, "memo.ac3"))

        assert r.status_code == 200, r.text
        assert _codecs(_read(tmp_path, r.content, "m4a")) == {"audio": "aac"}


class TestNothingIsKept:
    def test_the_copy_is_gone_once_sent(self, client, db, sending):
        assert _send(client, _item(db, "Beach day.wmv")).status_code == 200
        assert _left_behind() == []

    def test_nothing_joins_the_watching_cache(self, client, db, sending):
        cache = transcode._cache_dir()
        before = sorted(cache.iterdir())

        assert _send(client, _item(db, "Beach day.wmv")).status_code == 200

        assert sorted(cache.iterdir()) == before
        with db.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM transcodes")).scalar_one() == 0

    def test_the_sweep_removes_old_leftovers_only(self):
        old = sendcopy._dir() / f"{uuid.uuid4().hex}.mp4"
        new = sendcopy._dir() / f"{uuid.uuid4().hex}.m4a"
        old.write_bytes(b"x")
        new.write_bytes(b"x")
        two_hours_ago = time.time() - 2 * 3600
        os.utime(old, (two_hours_ago, two_hours_ago))

        try:
            sendcopy.sweep()
            assert not old.exists()
            assert new.exists()
        finally:
            new.unlink(missing_ok=True)

    def test_a_file_that_cannot_be_converted_leaves_nothing(
        self, client, db, sending, caplog
    ):
        item = _item(db, "broken.wmv")
        with caplog.at_level(logging.INFO, logger="homesh.sendcopy"):
            r = _send(client, item)

        assert r.status_code == 422
        assert r.json()["detail"] == sendcopy.FAILED
        assert _left_behind() == []
        said = " ".join(
            rec.getMessage() for rec in caplog.records if rec.name == "homesh.sendcopy"
        )
        assert str(item) in said
        # The address ffmpeg read carries a token that unlocks the file.
        assert "?t=" not in said
        assert "broken" not in said
        # The slot was given back.
        assert not transcode._slot.locked()

    def test_the_log_names_the_item_and_not_the_file(self, client, db, sending, caplog):
        item = _item(db, "Beach day.wmv")
        with caplog.at_level(logging.INFO, logger="homesh.sendcopy"):
            assert _send(client, item).status_code == 200

        said = " ".join(
            rec.getMessage() for rec in caplog.records if rec.name == "homesh.sendcopy"
        )
        assert str(item) in said
        assert "wmv to mp4" in said
        assert "Beach" not in said


class TestLimits:
    def test_too_long_is_refused_before_anything_is_fetched(
        self, client, db, sending, children
    ):
        item = _item(db, "Beach day.wmv")
        _set_duration(db, item, 601_000)

        r = _send(client, item)

        assert r.status_code == 413
        assert r.json()["detail"] == (
            "Too long to convert for sending. Share a Drive link instead."
        )
        assert children == []

    def test_an_unknown_length_is_asked_of_ffprobe_first(
        self, client, db, sending, children, monkeypatch
    ):
        monkeypatch.setattr(get_settings(), "share_convert_max_seconds", 3)

        r = _send(client, _item(db, "long.wmv"))

        assert r.status_code == 413
        assert r.json()["detail"] == sendcopy.TOO_LONG
        assert children == ["ffprobe"]

    def test_a_wrong_length_never_sends_a_file_cut_short(
        self, client, db, sending, monkeypatch
    ):
        monkeypatch.setattr(get_settings(), "share_convert_max_seconds", 2)
        item = _item(db, "long.wmv")
        _set_duration(db, item, 1000)

        r = _send(client, item)

        assert r.status_code == 413
        assert r.json()["detail"] == sendcopy.TOO_LONG
        assert _left_behind() == []

    def test_a_result_too_big_to_attach_is_refused(self, client, db, sending, monkeypatch):
        monkeypatch.setattr(sendcopy, "MAX_BYTES", 4096)

        r = _send(client, _item(db, "Beach day.wmv"))

        assert r.status_code == 413
        assert r.json()["detail"] == sendcopy.TOO_BIG
        assert _left_behind() == []

    @pytest.mark.parametrize("filename", ["plain.mp3", "plain.mp4", "tune.mid"])
    def test_no_copy_is_made_where_none_is_needed_or_possible(
        self, client, db, sending, children, filename
    ):
        r = _send(client, _item(db, filename))

        assert r.status_code == 415
        assert children == []

    def test_ten_in_ten_minutes(self, client, db, sending):
        item = _item(db, "Beach day.wmv")
        # Refused for length, so that ten attempts cost nothing to run.
        _set_duration(db, item, 601_000)
        for _ in range(10):
            assert _send(client, item).status_code == 413

        assert _send(client, item).status_code == 429


class TestOneAtATime:
    def test_waits_for_the_slot_then_says_the_server_is_busy(
        self, client, db, sending, children, monkeypatch
    ):
        item = _item(db, "Beach day.wmv")
        _set_duration(db, item, 2000)
        # Taken, as by a conversion for watching.
        monkeypatch.setattr(transcode, "_slot", asyncio.Semaphore(0))
        monkeypatch.setattr(sendcopy, "SLOT_WAIT_SECONDS", 0.3)

        started = time.monotonic()
        r = _send(client, item)

        assert time.monotonic() - started >= 0.3
        assert r.status_code == 503
        assert r.json()["detail"] == sendcopy.BUSY
        assert "retry-after" in r.headers
        assert children == []
        assert _left_behind() == []

    def test_goes_ahead_when_the_slot_comes_free_in_time(
        self, client, db, sending, monkeypatch
    ):
        slot = asyncio.Semaphore(0)
        monkeypatch.setattr(transcode, "_slot", slot)
        later = threading.Timer(0.3, lambda: client.portal.call(slot.release))
        later.start()
        try:
            r = _send(client, _item(db, "Beach day.wmv"))
        finally:
            later.join()

        assert r.status_code == 200, r.text
        assert not slot.locked()


class TestAccess:
    def test_an_account_without_access_gets_404_and_nothing_runs(
        self, client, db, sending, children
    ):
        with db.begin() as conn:
            uid = conn.execute(
                text(
                    "INSERT INTO users (handle, display_name, is_admin)"
                    " VALUES ('sam', 'Sam', FALSE) RETURNING id"
                )
            ).scalar_one()
        sam = CurrentUser(id=uid, handle="sam", display_name="Sam", is_admin=False)
        app.dependency_overrides[require_user] = lambda: sam

        r = _send(client, _item(db, "Beach day.wmv"))

        assert r.status_code == 404
        # Not told that it needs no copy either, which would say that it exists.
        assert _send(client, _item(db, "plain.mp3")).status_code == 404
        assert children == []
        assert _left_behind() == []

    def test_an_unknown_item_is_404(self, client, db):
        assert _send(client, uuid.uuid4()).status_code == 404

    def test_signed_out_is_refused(self, anon_client, db):
        assert _send(anon_client, uuid.uuid4()).status_code == 401
