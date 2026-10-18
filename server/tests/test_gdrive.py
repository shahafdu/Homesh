"""The Drive connector's two ways of stopping a house.

Neither is about Drive being slow. Both are about one shared thing being held
while everybody waits for it, and both end the same way from outside: a response
whose headers have gone out and whose body never arrives, for ever, with nothing
in any log.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.sources.gdrive import (
    AVAILABLE_FOR,
    REFRESH_EARLY,
    REFRESH_TIMEOUT,
    GoogleDriveConnector,
    _Credentials,
)


class FakeCreds:
    """What google-auth hands back, as far as this code is concerned."""

    def __init__(self, seconds_left: float, refresh_takes: float = 0.0):
        self.expiry = (datetime.now(UTC) + timedelta(seconds=seconds_left)).replace(tzinfo=None)
        self.valid = seconds_left > 0
        self.token = "first"  # noqa: S105 - a stand-in, not a secret
        self.refreshes = 0
        self._takes = refresh_takes

    def refresh(self, _request):
        time.sleep(self._takes)
        self.refreshes += 1
        self.token = f"refreshed-{self.refreshes}"
        self.expiry = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)
        self.valid = True


class TestARefreshDoesNotStopTheFolder:
    """The refresh happens off the lock, and that is the entire point.

    It used to happen under one: a mutex held across the network round trip that
    mints a token, on the path of every byte read from that folder. One refresh
    that did not come back stopped the folder completely, until the server was
    restarted — which is what an unreachable film looked like from the sofa.
    """

    def _creds(self, fake: FakeCreds) -> _Credentials:
        creds = _Credentials(Path("/nowhere/key.json"))
        creds._creds = fake
        return creds

    def test_a_valid_token_is_handed_back_without_a_refresh(self):
        fake = FakeCreds(seconds_left=3600)
        assert self._creds(fake).token() == "first"
        assert fake.refreshes == 0

    def test_it_mints_again_before_the_token_runs_out(self):
        """Not when it has expired: that is a stall in the middle of a film."""
        fake = FakeCreds(seconds_left=REFRESH_EARLY - 60)
        assert self._creds(fake).token() == "refreshed-1"

    def test_the_lock_is_free_while_a_refresh_is_in_flight(self):
        """The regression test, and it is about the lock rather than the clock.

        Deliberately aware of how this is built, because that is where the fault
        was: `token()` took one mutex for its whole body, refresh included, and
        every read of every byte in the folder goes through it. While a mint was
        in the air the folder was shut to everybody -- including the readers
        whose token was perfectly good -- and a mint that never came back shut
        it until the server was restarted.
        """
        creds = self._creds(FakeCreds(seconds_left=10, refresh_takes=1.0))
        free = threading.Event()

        def bystander():
            # Somebody arriving while the mint is in the air.
            time.sleep(0.3)
            if creds._lock.acquire(timeout=0.5):
                creds._lock.release()
                free.set()

        watcher = threading.Thread(target=bystander)
        watcher.start()
        creds.token()
        watcher.join(timeout=5)

        assert free.is_set(), "the lock was held across the network call"

    def test_the_mint_gives_up_rather_than_waiting_for_ever(self):
        """google-auth's own default is two minutes per attempt, and the caller
        cannot shorten it except by handing over a transport that does."""
        import inspect

        timeout = inspect.signature(_Credentials._request().__call__).parameters["timeout"]
        assert timeout.default == REFRESH_TIMEOUT


class TestExpiryIsReadCorrectly:
    def test_plenty_of_time_left_is_not_expiring(self):
        assert not _Credentials._expiring(FakeCreds(seconds_left=3600))

    def test_inside_the_early_window_is_expiring(self):
        assert _Credentials._expiring(FakeCreds(seconds_left=REFRESH_EARLY - 1))

    def test_no_expiry_falls_back_to_what_the_library_says(self):
        class NoExpiry:
            expiry = None
            valid = True

        assert not _Credentials._expiring(NoExpiry())
        NoExpiry.valid = False
        assert _Credentials._expiring(NoExpiry())


class TestAvailabilityIsNotAskedPerByte:
    """Choosing a replica asks every candidate source whether it is there, and
    that happens once per range request. Answering it with a fresh TLS
    connection and an API call meant a film paid a round trip per range -- about
    a second each, which is most of the wait before a video starts."""

    class Answer:
        status_code = 200

    class CountingClient:
        def __init__(self):
            self.calls = 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            return TestAvailabilityIsNotAskedPerByte.Answer()

    def _connector(self, monkeypatch):
        drive = GoogleDriveConnector("root", Path("/nowhere/key.json"))
        client = self.CountingClient()
        monkeypatch.setattr(drive, "_http", lambda: client)
        monkeypatch.setattr(drive.creds, "token", lambda: "token")
        return drive, client

    def test_the_answer_is_reused(self, monkeypatch):
        drive, client = self._connector(monkeypatch)
        for _ in range(40):
            assert drive.available
        assert client.calls == 1, "asked Drive once per read"

    def test_it_is_asked_again_once_the_memory_is_stale(self, monkeypatch):
        drive, client = self._connector(monkeypatch)
        assert drive.available
        drive._available_at -= AVAILABLE_FOR + 1
        assert drive.available
        assert client.calls == 2

    def test_an_unreachable_folder_answers_no_rather_than_raising(self, monkeypatch):
        drive, _ = self._connector(monkeypatch)

        class Broken:
            def get(self, *_a, **_k):
                raise RuntimeError("network down")

        monkeypatch.setattr(drive, "_http", lambda: Broken())
        assert drive.available is False

    def test_the_memory_is_short_enough_to_notice_unsharing(self):
        """A folder that stops being shared has to stop working while somebody
        is still looking at the screen."""
        assert AVAILABLE_FOR <= 300


@pytest.mark.parametrize("seconds", [0, -60])
def test_an_expired_token_is_always_replaced(seconds):
    creds = _Credentials(Path("/nowhere/key.json"))
    fake = FakeCreds(seconds_left=seconds)
    creds._creds = fake
    assert creds.token() == "refreshed-1"


class TestAReadThatBreaksOffIsResumed:
    """The headers have gone out with a length by the time Drive cuts a read, so
    a short body cannot be reported -- the browser only sees the connection
    fail, and pdf.js abandons the whole document over one missing chunk."""

    DATA = bytes(range(256)) * 4096  # 1 MiB, every byte telling where it is from

    class Response:
        def __init__(self, body: bytes, cut_after: int | None):
            self.status_code = 206
            self.body, self.cut_after = body, cut_after

        def iter_bytes(self, chunk_size: int):
            import httpx

            for i in range(0, len(self.body), chunk_size):
                if self.cut_after is not None and i >= self.cut_after:
                    raise httpx.ReadError("connection reset")
                yield self.body[i:i + chunk_size]

    class Client:
        def __init__(self, data: bytes, cuts: list[int | None]):
            self.data, self.cuts, self.ranges = data, list(cuts), []

        def stream(self, _method, _url, params=None, headers=None):
            from contextlib import contextmanager

            first, _, last = headers["Range"].removeprefix("bytes=").partition("-")
            body = self.data[int(first):(int(last) + 1) if last else None]
            self.ranges.append(headers["Range"])
            cut = self.cuts.pop(0) if self.cuts else None

            @contextmanager
            def opened():
                yield TestAReadThatBreaksOffIsResumed.Response(body, cut)

            return opened()

    def _connector(self, monkeypatch, cuts):
        import app.sources.gdrive as gdrive

        drive = GoogleDriveConnector("root", Path("/nowhere/key.json"))
        drive.remember("book.pdf", "file-id")
        client = self.Client(self.DATA, cuts)
        monkeypatch.setattr(drive, "_http", lambda: client)
        monkeypatch.setattr(drive.creds, "token", lambda: "token")
        monkeypatch.setattr(gdrive, "RESUME_PAUSE", 0)
        return drive, client

    def test_it_picks_up_from_the_byte_it_reached(self, monkeypatch):
        drive, client = self._connector(monkeypatch, cuts=[512 * 1024])
        got = b"".join(drive.open_range("book.pdf", 1000, len(self.DATA) - 1))
        assert got == self.DATA[1000:]
        assert client.ranges == [f"bytes=1000-{len(self.DATA) - 1}",
                                 f"bytes={1000 + 512 * 1024}-{len(self.DATA) - 1}"]

    def test_an_open_ended_range_stays_open_ended(self, monkeypatch):
        drive, client = self._connector(monkeypatch, cuts=[256 * 1024])
        assert b"".join(drive.open_range("book.pdf")) == self.DATA
        assert client.ranges[-1] == f"bytes={256 * 1024}-"

    def test_an_outage_is_given_up_on(self, monkeypatch):
        import httpx

        from app.sources.gdrive import RESUMES

        drive, client = self._connector(monkeypatch, cuts=[0] * (RESUMES + 1))
        with pytest.raises(httpx.ReadError):
            b"".join(drive.open_range("book.pdf"))
        assert len(client.ranges) == RESUMES + 1
