"""What ffmpeg and ffprobe may read.

The files here are disguised on purpose: named as video, and holding a list that
tells the reader to go somewhere else. "Somewhere else" is a small server in this
test that writes down every request, so that a pass means nothing was asked of
it, not merely that the conversion failed.
"""

from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

import app as server_package
from app import ffinput, metadata, sendcopy, thumbs, transcode
from app.config import get_settings
from app.library import forget_connectors
from app.scanner import scan_source
from app.signing import mint, verify
from app.sources.local import LocalConnector

# ── What every run is given ─────────────────────────────────────────────────


def _value(args: list[str], option: str) -> str:
    return args[args.index(option) + 1]


def test_a_library_file_is_read_from_our_own_endpoint_over_http_only():
    item, user = uuid4(), uuid4()
    args = ffinput.stream_input(item, user, ttl=60).args()

    assert _value(args, "-protocol_whitelist") == "http,tcp"
    url = _value(args, "-i")
    prefix = f"{ffinput.SOURCE_BASE}/api/stream/{item}?t="
    assert url.startswith(prefix)
    claim = verify(url.removeprefix(prefix), "stream")
    assert (claim.item_id, claim.user_id) == (item, user)


def test_a_path_is_read_with_no_way_to_reach_an_address(tmp_path):
    args = ffinput.file_input(tmp_path / "a:b.mp4").args()

    assert _value(args, "-protocol_whitelist") == "file"
    assert _value(args, "-i") == f"file:{tmp_path / 'a:b.mp4'}"


@pytest.mark.parametrize(
    "build",
    [
        lambda source: ffinput.ffmpeg_argv(source, ["out.mp4"], before=["-ss", "3"]),
        lambda source: ffinput.ffprobe_argv(source, ["-v", "error"]),
    ],
)
def test_both_lists_come_before_the_input(build, tmp_path):
    args = build(ffinput.file_input(tmp_path / "x.mp4"))

    assert args.index("-protocol_whitelist") < args.index("-i")
    assert args.index("-format_whitelist") < args.index("-i")
    assert _value(args, "-format_whitelist") == ",".join(ffinput.DEMUXERS)


def test_no_format_that_can_name_something_else_is_listed():
    indirect = {
        "hls", "applehttp", "dash", "concat", "ffconcat", "imf", "sdp", "rtsp", "rtp",
        "sap", "image2", "image2pipe", "tee", "data", "subfile", "vobsub", "lavfi",
        "webm_dash_manifest",
    }
    assert not indirect & set(ffinput.DEMUXERS)


def test_every_listed_format_is_one_this_ffmpeg_knows():
    """A misspelt name refuses nothing and permits nothing, silently."""
    said = subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-demuxers"],  # noqa: S607
        check=True, capture_output=True, text=True,
    ).stdout
    known = {
        name
        for line in said.splitlines()
        if line.strip().startswith("D")
        for name in line.split()[1].split(",")
    }
    assert set(ffinput.DEMUXERS) <= known, set(ffinput.DEMUXERS) - known


def test_a_complaint_loses_the_address_and_its_token():
    said = "[http @ 0x1] http://127.0.0.1:8080/api/stream/abc?t=secret.token: Invalid data"

    assert "secret" not in ffinput.redact(said)
    assert "Invalid data" in ffinput.redact(said)


# ── Real files ──────────────────────────────────────────────────────────────

_PICTURE = "testsrc=size={size}:rate=25:duration=1"
_TONE = "sine=frequency=440:sample_rate=48000:duration=1"


def _make(path: Path, *extra: str, picture: bool, size: str = "320x240") -> None:
    inputs = ["-f", "lavfi", "-i", _PICTURE.format(size=size)] if picture else []
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",  # noqa: S607
         *inputs, "-f", "lavfi", "-i", _TONE, *extra, str(path)],
        check=True, capture_output=True,
    )


# Extension -> what it takes to make one. Not every format the catalog knows has
# an encoder here (APE, Musepack, AMR); those are covered by name above.
_VIDEOS: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(
        ["mp4", "m4v", "mov", "mkv", "webm", "avi", "wmv", "asf", "flv", "mpg", "mpeg",
         "vob", "ts", "m2ts", "ogv"],
        (),
    ),
    "3gp": ("-c:v", "mpeg4", "-c:a", "aac"),
    "3g2": ("-c:v", "mpeg4", "-c:a", "aac"),
    "mts": ("-f", "mpegts"),
    "m2t": ("-f", "mpegts"),
    "tod": ("-f", "mpegts"),
    "mod": ("-f", "vob"),
    "divx": ("-f", "avi"),
    "xvid": ("-f", "avi"),
    "dv": ("-s", "720x576", "-pix_fmt", "yuv420p", "-ac", "2"),
    "mxf": ("-c:v", "mpeg2video", "-c:a", "pcm_s16le"),
    "rm": ("-c:v", "rv10", "-c:a", "ac3"),
    "m1v": ("-c:v", "mpeg1video", "-an", "-f", "mpeg1video"),
    "m2v": ("-c:v", "mpeg2video", "-an", "-f", "mpeg2video"),
}
_SOUNDS: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(
        ["mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "wma", "aiff", "wv", "ac3",
         "eac3", "au", "caf", "mka", "mp2", "tta", "w64"],
        (),
    ),
    "m4b": ("-f", "ipod"),
    "alac": ("-c:a", "alac", "-f", "ipod"),
    "dts": ("-c:a", "dca", "-strict", "-2"),
    "ra": ("-c:a", "ac3", "-f", "rm"),
}


@pytest.mark.parametrize(
    "ext,extra,picture",
    [(e, x, True) for e, x in _VIDEOS.items()] + [(e, x, False) for e, x in _SOUNDS.items()],
)
def test_the_formats_the_library_holds_are_still_read(tmp_path, ext, extra, picture):
    path = tmp_path / f"clip.{ext}"
    _make(path, *extra, picture=picture)

    done = subprocess.run(  # noqa: S603
        ffinput.ffprobe_argv(
            ffinput.file_input(path),
            ["-v", "error", "-show_entries", "stream=codec_type", "-of", "json"],
        ),
        capture_output=True, check=False,
    )

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["streams"]


# ── Disguised files ─────────────────────────────────────────────────────────


class _Bait:
    """An address a crafted file names, which writes down who came."""

    def __init__(self) -> None:
        seen = self.seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                seen.append(self.path)
                self.send_response(404)
                self.end_headers()

            do_HEAD = do_GET  # noqa: N815

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture(scope="module")
def bait():
    listening = _Bait()
    yield listening
    listening.server.shutdown()
    listening.server.server_close()


def _playlist(bait_url: str, name: str) -> str:
    """HLS. The comment keeps each file's bytes its own."""
    return (
        "#EXTM3U\n"
        f"# {name}\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXTINF:10,\n"
        f"{bait_url}/{name}.ts\n"
        "#EXT-X-ENDLIST\n"
    )


def _manifest(bait_url: str, name: str) -> str:
    """DASH, which ffmpeg recognises by its contents alone, whatever it is called."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!-- {name} -->
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"
     profiles="urn:mpeg:dash:profile:isoff-on-demand:2011"
     type="static" mediaPresentationDuration="PT2S" minBufferTime="PT1S">
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <Representation id="v" bandwidth="1000" codecs="avc1.42c00c" width="320" height="240">
        <BaseURL>{bait_url}/{name}.mp4</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


@pytest.fixture(scope="module")
def folder(tmp_path_factory, bait) -> Path:
    root = tmp_path_factory.mktemp("disguised")
    for kind, body in (("playlist", _playlist), ("manifest", _manifest)):
        for ext in ("mp4", "wmv"):
            name = f"{kind}-{ext}"
            (root / f"{name}.{ext}").write_text(body(bait.url, name), encoding="utf-8")
    _make(root / "Beach day.wmv", "-c:v", "wmv2", "-c:a", "wmav2", picture=True)
    # A list of other files rather than of addresses: no request leaves, and
    # somebody else's file is read in this one's name. ffmpeg follows such a list
    # only to a plain name, one with no space in it.
    _make(root / "other.wmv", "-c:v", "wmv2", "-c:a", "wmav2", picture=True, size="160x120")
    (root / "list.mp4").write_text("ffconcat version 1.0\nfile 'other.wmv'\n", encoding="utf-8")
    return root


@pytest.fixture
def reading(db, folder, bait, stream_server, monkeypatch):
    """The folder catalogued, and ffmpeg pointed at a server that is listening."""
    with db.begin() as conn:
        sid = conn.execute(
            text(
                """
                INSERT INTO sources (kind, name, mount_prefix, remote_id, audience)
                VALUES ('local', 'Disguised', '/local/disguised', :root, 'everyone')
                RETURNING id
                """
            ),
            {"root": str(folder)},
        ).scalar_one()
    monkeypatch.setenv("MEDIA_ROOTS", f"disguised={folder}")
    get_settings.cache_clear()
    forget_connectors()
    scan_source(sid, LocalConnector(folder))
    monkeypatch.setattr(ffinput, "SOURCE_BASE", stream_server)
    # A semaphore belongs to the event loop that first waited on it, and each
    # test client has a loop of its own.
    monkeypatch.setattr(transcode, "_slot", asyncio.Semaphore(1))
    monkeypatch.setattr(transcode, "_live", {})
    bait.seen.clear()
    yield
    get_settings.cache_clear()
    forget_connectors()


def _item(db, filename: str):
    with db.connect() as conn:
        return conn.execute(
            text("SELECT item_id FROM replicas WHERE filename = :f"), {"f": filename}
        ).scalar_one()


def _converted(client, item) -> dict:
    """Start the whole-file conversion and wait for how it ends."""
    started = client.post(f"/api/videos/{item}/conversion")
    assert started.status_code == 202, started.text
    deadline = time.monotonic() + 60
    while True:
        status = client.get(f"/api/videos/{item}/conversion").json()
        if status["state"] in ("done", "failed"):
            return status
        assert time.monotonic() < deadline, status
        time.sleep(0.05)


def _watch(client, item, user) -> bytes:
    r = client.get(f"/api/videos/{item}/live.mp4?t={mint(item, user.id, 'stream')}")
    assert r.status_code == 200, r.text
    return r.content


def _codecs(path: Path, body: bytes) -> dict[str, str]:
    path.write_bytes(body)
    out = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "error", "-show_entries",  # noqa: S607
         "stream=codec_name,codec_type", "-of", "json", str(path)],
        check=True, capture_output=True,
    ).stdout
    return {s["codec_type"]: s["codec_name"] for s in json.loads(out)["streams"]}


DISGUISES = ["playlist", "manifest"]


class TestADisguisedFileFetchesNothing:
    def test_without_the_format_list_the_address_is_fetched(
        self, db, user, reading, bait, stream_server
    ):
        """What the other tests here would be worth nothing without: this ffmpeg
        does follow such a file, even with only the protocols it needs."""
        item = _item(db, "manifest-mp4.mp4")
        url = f"{stream_server}/api/stream/{item}?t={mint(item, user.id, 'stream')}"

        subprocess.run(  # noqa: S603
            ["ffprobe", "-v", "error", "-protocol_whitelist", "http,tcp",  # noqa: S607
             "-i", url],
            capture_output=True, check=False, timeout=30,
        )

        assert bait.seen

    def test_the_format_list_alone_stops_a_playlist(self, tmp_path, bait):
        """The HLS cases below would pass on this ffmpeg without the list, since
        it declines a playlist named .mp4 by itself. Named as one, it follows it
        with the very protocols a streamed file is given -- and the list stops it."""
        playlist = tmp_path / "plain.m3u8"
        playlist.write_text(_playlist(bait.url, "plain"), encoding="utf-8")
        protocols = "file,http,tcp"
        options = ["-v", "error", "-rw_timeout", "5000000"]

        bait.seen.clear()
        subprocess.run(  # noqa: S603
            ["ffprobe", *options, "-protocol_whitelist", protocols,  # noqa: S607
             "-i", f"file:{playlist}"],
            capture_output=True, check=False, timeout=30,
        )
        assert bait.seen, "this ffmpeg no longer follows a playlist; the control is void"

        bait.seen.clear()
        guarded = subprocess.run(  # noqa: S603
            ffinput.ffprobe_argv(ffinput.Input(protocols, f"file:{playlist}"), options),
            capture_output=True, check=False, timeout=30,
        )
        assert guarded.returncode != 0
        assert bait.seen == []

    @pytest.mark.parametrize("disguise", DISGUISES)
    @pytest.mark.parametrize("ext", ["mp4", "wmv"])
    def test_watching_it_live(self, client, db, user, reading, bait, disguise, ext):
        item = _item(db, f"{disguise}-{ext}.{ext}")

        assert _watch(client, item, user) == b""
        assert bait.seen == []

    @pytest.mark.parametrize("disguise", DISGUISES)
    def test_converting_it_whole(self, client, db, reading, bait, disguise):
        item = _item(db, f"{disguise}-wmv.wmv")

        status = _converted(client, item)

        assert status["state"] == "failed"
        assert bait.seen == []
        # What ffmpeg said is shown to the viewer; the address it read carries a
        # token that unlocks the file.
        assert status["error"]
        assert "?t=" not in status["error"]
        assert not transcode._output_for(item).exists()

    @pytest.mark.parametrize("disguise", DISGUISES)
    def test_sending_it(self, client, db, reading, bait, disguise):
        r = client.post(f"/api/items/{_item(db, f'{disguise}-wmv.wmv')}/send-copy")

        assert r.status_code == 422, r.text
        assert r.json()["detail"] == sendcopy.FAILED
        assert bait.seen == []

    @pytest.mark.parametrize("disguise", DISGUISES)
    def test_making_its_thumbnail(self, db, reading, folder, bait, disguise):
        name = f"{disguise}-mp4.mp4"

        with pytest.raises(thumbs.ThumbError):
            thumbs.generate(_item(db, name), "video", LocalConnector(folder), name)

        assert bait.seen == []

    @pytest.mark.parametrize("disguise", DISGUISES)
    def test_timing_it(self, folder, bait, disguise):
        bait.seen.clear()

        assert metadata.read_video(folder / f"{disguise}-mp4.mp4") == ({}, None)
        assert bait.seen == []

    def test_a_list_of_other_files_is_not_followed_either(self, folder):
        listing = folder / "list.mp4"
        unguarded = subprocess.run(  # noqa: S603
            ["ffprobe", "-v", "error", "-show_entries", "stream=width",  # noqa: S607
             "-of", "json", str(listing)],
            capture_output=True, check=True,
        ).stdout
        # It would have been: the other file's picture, under this one's name.
        assert 160 in [s.get("width") for s in json.loads(unguarded)["streams"]]

        assert metadata.read_video(listing) == ({}, None)


class TestARealFileStillConverts:
    def test_whole(self, client, db, reading, tmp_path):
        item = _item(db, "Beach day.wmv")

        assert _converted(client, item)["state"] == "done"

        body = client.get(f"/api/videos/{item}/converted").content
        assert _codecs(tmp_path / "whole.mp4", body) == {"video": "h264", "audio": "aac"}
        transcode._output_for(item).unlink()

    def test_live_from_part_way_in(self, client, db, user, reading, tmp_path):
        item = _item(db, "Beach day.wmv")
        r = client.get(
            f"/api/videos/{item}/live.mp4?start=0.4&t={mint(item, user.id, 'stream')}"
        )

        assert r.status_code == 200, r.text
        assert _codecs(tmp_path / "live.mp4", r.content) == {"video": "h264", "audio": "aac"}

    def test_as_a_send_copy(self, client, db, reading, tmp_path):
        r = client.post(f"/api/items/{_item(db, 'Beach day.wmv')}/send-copy")

        assert r.status_code == 200, r.text
        assert _codecs(tmp_path / "sent.mp4", r.content) == {"video": "h264", "audio": "aac"}

    def test_with_a_thumbnail_and_a_length(self, db, reading, folder):
        name = "Beach day.wmv"

        picture = thumbs.generate(_item(db, name), "video", LocalConnector(folder), name)
        _tags, duration_ms = metadata.read_video(folder / name)

        assert picture.stat().st_size > 1
        assert duration_ms == pytest.approx(1000, abs=500)


# ── Nothing starts either program another way ───────────────────────────────

PROGRAMS = ("ffmpeg", "ffprobe")


def _started_by_hand(source: str) -> list[int]:
    """Lines that name either program, as an argument or a command line, or that
    make an input of their own instead of asking for one."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            named = node.value.rsplit("/", 1)[-1] in PROGRAMS
            line = node.value.startswith(tuple(f"{p} -" for p in PROGRAMS))
            if named or line:
                found.append(node.lineno)
        if isinstance(node, ast.Call):
            called = node.func
            name = getattr(called, "attr", getattr(called, "id", None))
            if name == "Input":
                found.append(node.lineno)
    return found


@pytest.mark.parametrize(
    "code",
    [
        'run(["ffmpeg", "-i", path])',
        'args = ["/usr/bin/ffprobe", path]',
        'os.system("ffmpeg -i x y")',
        'ffinput.Input("http,tcp,file", url)',
    ],
)
def test_the_check_sees_a_call_made_by_hand(code):
    assert _started_by_hand(code)


def test_every_ffmpeg_and_ffprobe_call_comes_from_the_helper():
    root = Path(server_package.__file__).parent
    offenders = {
        str(path.relative_to(root)): lines
        for path in sorted(root.rglob("*.py"))
        if path.name != "ffinput.py"
        and (lines := _started_by_hand(path.read_text(encoding="utf-8")))
    }

    assert offenders == {}, "build the command with app/ffinput.py"
