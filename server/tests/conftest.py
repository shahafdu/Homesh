"""Shared fixtures.

Tests run against a real Postgres with pgvector, not a stub. The schema leans on
enums, ICU collations and trigram indexes, and none of those behave the same
against SQLite — a passing test on the wrong engine would prove nothing.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://homesh:homesh@localhost:5432/homesh_test"
)
os.environ.setdefault("MASTER_KEY", "0" * 43 + "=")
os.environ.setdefault("SECRET_KEY", "1" * 43 + "=")
os.environ.setdefault("MEDIA_ROOTS", "")
# Thumbnails are written to disk; give the suite its own directory rather than
# the container path, which does not exist on a CI runner or a dev machine.
os.environ.setdefault("CACHE_DIR", tempfile.mkdtemp(prefix="homesh-test-cache-"))


def _guard_target_database() -> None:
    """Refuse to run against anything but a database named for testing.

    The fixtures below truncate `users` and `sources`. Pointed at a working
    database that destroys real accounts and credentials — which is exactly what
    happened once during development. A name check is crude but it is checked
    before a single fixture runs, and it cannot be forgotten.
    """
    url = os.environ["DATABASE_URL"]
    name = urlsplit(url).path.lstrip("/")
    if "test" not in name.lower():
        raise SystemExit(
            f"\nRefusing to run tests against database {name!r}.\n"
            "These tests truncate users and sources. Point DATABASE_URL at a\n"
            "database whose name contains 'test', e.g. homesh_test.\n"
        )


_guard_target_database()

from app.db import get_engine, run_migrations  # noqa: E402
from app.main import app  # noqa: E402
from app.security import CurrentUser, optional_user, require_user  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    run_migrations()


@pytest.fixture
def db():
    engine = get_engine()
    yield engine
    # Each test starts from a clean catalog. Cascades handle items, replicas and
    # play_sessions; zones and renderers need naming because a leaked zone makes
    # the next test's "create zone" fail on the unique name rather than on
    # anything it was actually testing.
    with engine.begin() as conn:
        # Playlists outlive both their source and their owner on purpose — the
        # foreign keys are ON DELETE SET NULL, so that losing a drive does not
        # lose the ordering somebody made. That is right in the house and wrong
        # in a test, where it means every later test sees the last one's lists.
        conn.execute(text("DELETE FROM playlists"))
        conn.execute(text("DELETE FROM zones"))
        conn.execute(text("DELETE FROM renderers"))
        conn.execute(text("DELETE FROM sources"))
        conn.execute(text("DELETE FROM users"))


@pytest.fixture
def user(db) -> CurrentUser:
    with db.begin() as conn:
        uid = conn.execute(
            text(
                """
                INSERT INTO users (handle, display_name, is_admin, is_owner,
                                   all_library, all_zones)
                VALUES ('tester', 'Tester', TRUE, TRUE, TRUE, TRUE) RETURNING id
                """
            )
        ).scalar_one()
    return CurrentUser(id=uid, handle="tester", display_name="Tester", is_admin=True)


@pytest.fixture
def client(user) -> TestClient:
    """Authenticated client.

    The dependency is overridden rather than driving a real passkey handshake:
    WebAuthn needs an authenticator, and these tests are about what sits behind
    the door, not the lock itself.
    """
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[optional_user] = lambda: user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A small library exercising the cases that broke in practice."""
    files = [
        "Music/Pink Floyd/The Wall/01 - In the Flesh.mp3",
        "Music/Pink Floyd/The Wall/02 - The Thin Ice.flac",
        "Music/Pink Floyd/The Wall/10 - Goodbye Cruel World.mp3",
        "Music/Unsorted/track2.mp3",
        "Music/Unsorted/track10.mp3",
        "Music/Unsorted/שיר בעברית.mp3",
        "Music/Unsorted/oldies.m3u",
        "Videos/Holidays 2019/DSC_0042.MOV",
        "Videos/Holidays 2019/beach.mkv",
        "Docs/Manuals/Denon AVR-X1600H manual.pdf",
        "Docs/notes.md",
        "Photos/2019/Greece/IMG_1234.jpg",
        "Photos/2019/Greece/IMG_1235.heic",
        "Photos/2019/Greece/weird.xyz",
    ]
    for rel in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 16)

    # One genuinely decodable image, so thumbnail generation has something real to
    # work on. The rest are placeholder bytes, which is itself worth testing: a
    # corrupt file must not break a folder listing.
    from PIL import Image

    real = tmp_path / "Photos" / "2019" / "Greece" / "real.png"
    Image.new("RGB", (320, 240), (70, 110, 90)).save(real)

    # A real audio file carrying real tags, so metadata extraction is exercised
    # rather than mocked. WAVE carries ID3 frames just as MP3 does, and the wave
    # module builds one without shelling out to an encoder.
    import struct
    import wave as wave_module

    from mutagen.id3 import TALB, TIT2, TPE1, TRCK
    from mutagen.wave import WAVE

    tagged = tmp_path / "Music" / "Pink Floyd" / "The Wall" / "03 - Tagged Track.wav"
    with wave_module.open(str(tagged), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(8000)))  # 1 second

    audio = WAVE(tagged)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text="Another Brick in the Wall"))
    audio.tags.add(TPE1(encoding=3, text="Pink Floyd"))
    audio.tags.add(TALB(encoding=3, text="The Wall"))
    audio.tags.add(TRCK(encoding=3, text="3/26"))
    audio.save()

    return tmp_path


@pytest.fixture
def source(db, library: Path):
    """A registered local source pointed at the fixture library."""
    prefix = f"/local/test{uuid.uuid4().hex[:6]}"
    with db.begin() as conn:
        sid = conn.execute(
            text(
                """
                INSERT INTO sources (kind, name, mount_prefix, audience)
                VALUES ('local', 'Test', :p, 'everyone') RETURNING id
                """
            ),
            {"p": prefix},
        ).scalar_one()
    return sid, prefix, library
