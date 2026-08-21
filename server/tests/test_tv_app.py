"""Handing the TV app to a set-top box.

A box has a network connection and a remote control and no other way to receive
a file, so the server serves the APK itself. These check the two things that
would waste somebody's evening on a ladder in front of a television: a wrong
content type, which makes Android refuse to install it, and a stale path that
needs a restart before a fresh build appears.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


class TestApkDownload:
    def test_served_with_the_type_android_needs(self, anon_client, tmp_path, monkeypatch):
        apk = tmp_path / "homesh-tv.apk"
        apk.write_bytes(b"PK\x03\x04not-really-an-apk")
        monkeypatch.setenv("TV_APK_PATH", str(apk))

        r = anon_client.get("/tv.apk")
        assert r.status_code == 200
        # The wrong type here is silently fatal: the box downloads it and then
        # has no idea it is a package.
        assert r.headers["content-type"] == "application/vnd.android.package-archive"
        assert r.content == apk.read_bytes()

    def test_no_build_yet_says_so_rather_than_serving_the_web_app(
        self, anon_client, tmp_path, monkeypatch
    ):
        """The SPA catch-all would otherwise answer with HTML named .apk."""
        monkeypatch.setenv("TV_APK_PATH", str(tmp_path / "absent.apk"))

        r = anon_client.get("/tv.apk")
        assert r.status_code == 404
        assert "build" in r.json()["detail"].lower()

    def test_a_build_made_after_startup_is_picked_up(self, anon_client, tmp_path, monkeypatch):
        """Nobody should have to restart the server to publish an APK."""
        apk = tmp_path / "later.apk"
        monkeypatch.setenv("TV_APK_PATH", str(apk))
        assert anon_client.get("/tv.apk").status_code == 404

        apk.write_bytes(b"PK\x03\x04built-later")
        assert anon_client.get("/tv.apk").status_code == 200

    def test_it_needs_no_account(self, anon_client, tmp_path, monkeypatch):
        """A television cannot sign in, and the APK is public code either way.

        Deliberate, and worth a test so it is never quietly changed: installing
        it still grants nothing until the screen is paired from a signed-in
        phone.
        """
        apk = tmp_path / "homesh-tv.apk"
        apk.write_bytes(b"PK\x03\x04")
        monkeypatch.setenv("TV_APK_PATH", str(apk))

        assert anon_client.get("/tv.apk").status_code == 200


class TestUpdateOffer:
    """The version the app checks on launch.

    Without it, every change to the app means walking to each television, opening
    a downloader and typing a URL with a remote control.
    """

    def test_the_version_is_published_beside_the_apk(self, anon_client, tmp_path, monkeypatch):
        apk = tmp_path / "homesh-tv.apk"
        apk.write_bytes(b"PK")
        (tmp_path / "homesh-tv.json").write_text('{"versionCode": 7, "versionName": "0.7.0"}')
        monkeypatch.setenv("TV_APK_PATH", str(apk))

        r = anon_client.get("/tv.json")
        assert r.status_code == 200
        assert r.json()["versionCode"] == 7

    def test_no_build_means_nothing_to_offer(self, anon_client, tmp_path, monkeypatch):
        """Rather than the web app's HTML, which the catch-all would otherwise serve."""
        monkeypatch.setenv("TV_APK_PATH", str(tmp_path / "absent.apk"))
        assert anon_client.get("/tv.json").status_code == 404

    def test_it_needs_no_account(self, anon_client, tmp_path, monkeypatch):
        """A television has no session, and a version number is not a secret."""
        apk = tmp_path / "homesh-tv.apk"
        apk.write_bytes(b"PK")
        (tmp_path / "homesh-tv.json").write_text('{"versionCode": 1}')
        monkeypatch.setenv("TV_APK_PATH", str(apk))
        assert anon_client.get("/tv.json").status_code == 200


class TestTheAddressForATelevision:
    """Which address a television is given, and whether it can be reached.

    A set-top box is on the house network and nothing else. Handing it the
    address this browser happens to be using means handing it a ts.net name it
    cannot resolve — reported from the living room as ERR_NAME_NOT_RESOLVED.
    """

    def test_it_offers_the_house_address_not_the_browsers(self, anon_client, monkeypatch):
        monkeypatch.setenv("LAN_BASE_URL", "http://192.0.2.10:8080")
        monkeypatch.setenv("PUBLIC_ORIGIN", "https://example.ts.net")
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            body = anon_client.get("/tv.address").json()
            assert body["lan"] == "http://192.0.2.10:8080"
        finally:
            get_settings.cache_clear()

    def test_it_says_when_no_house_address_is_configured(self, anon_client, monkeypatch):
        """A different problem from a television that cannot reach one."""
        monkeypatch.setenv("LAN_BASE_URL", "")
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            body = anon_client.get("/tv.address").json()
            assert body["lan"] is None
            assert "LAN_BASE_URL" in body["detail"]
        finally:
            get_settings.cache_clear()

    def test_no_short_address_when_port_80_does_not_answer(self, anon_client, monkeypatch):
        """Easy to type and wrong is worse than long and correct."""
        from app.main import _short_address

        _short_address.cache_clear()
        monkeypatch.setenv("LAN_BASE_URL", "http://192.0.2.1:8080")  # TEST-NET-1, unroutable
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            assert anon_client.get("/tv.address").json()["short"] is None
        finally:
            get_settings.cache_clear()
            _short_address.cache_clear()

    def test_the_probe_does_not_block_the_server(self, anon_client, monkeypatch):
        """The probe leaves this server and comes back into it.

        On the event loop that is a request waiting for a reply only the event
        loop can send, so it times out against itself — which is exactly what
        happened, returning null while the identical probe from a shell returned
        200. The endpoint is a plain `def` so FastAPI runs it in a thread; this
        asks the server for something else while a probe is in flight.
        """
        from app.main import _short_address

        _short_address.cache_clear()
        monkeypatch.setenv("LAN_BASE_URL", "http://192.0.2.1:8080")
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                probing = pool.submit(anon_client.get, "/tv.address")
                # Served while the probe is still running, not after it.
                assert pool.submit(anon_client.get, "/api/health").result().status_code == 200
                assert probing.result().status_code == 200
        finally:
            get_settings.cache_clear()
            _short_address.cache_clear()

    def test_the_short_path_serves_the_same_app(self, anon_client, tmp_path, monkeypatch):
        """Every character counts when it is typed on a d-pad keyboard."""
        apk = tmp_path / "homesh-tv.apk"
        apk.write_bytes(b"PK\x03\x04 pretend apk")
        monkeypatch.setenv("TV_APK_PATH", str(apk))

        short = anon_client.get("/apk", follow_redirects=True)
        assert short.status_code == 200
        assert short.content == anon_client.get("/tv.apk").content


class TestTheTvPageStaysAtSlashTv:
    """`/tv` is the interface the installed app loads on every launch.

    This exists because a short download address was once put at `/tv`, one
    character from `/tv.apk` and serving something entirely different. Every
    screen in the house then loaded an APK into its WebView and went black —
    with no failing request for the server to log, because from its side
    everything had been served correctly.

    Asserted against the route table rather than over HTTP, because the built
    client is not present when these run: the suite mounts the source over
    /app, so /app/static does not exist and the page handler is never
    registered. The invariant is that nothing *claims* `/tv`, which is exactly
    what went wrong and is true either way.
    """

    @staticmethod
    def _explicit_paths() -> set[str]:
        """Routes declared in code, excluding the catch-all that serves files."""
        from app.main import app

        return {
            route.path
            for route in app.routes
            if getattr(route, "path", None) and "{" not in route.path
        }

    def test_nothing_claims_the_tv_page(self):
        assert "/tv" not in self._explicit_paths(), (
            "/tv is the TV interface. A route registered here shadows it and "
            "every screen in the house loads that instead — silently."
        )

    def test_the_download_has_its_own_short_path(self):
        """Short enough to type, and not on top of anything."""
        paths = self._explicit_paths()
        assert "/apk" in paths
        assert "/tv.apk" in paths

    def test_the_download_and_the_page_are_not_confusable(self, anon_client, tmp_path,
                                                          monkeypatch):
        apk = tmp_path / "homesh-tv.apk"
        apk.write_bytes(b"PK pretend apk")
        monkeypatch.setenv("TV_APK_PATH", str(apk))

        served = anon_client.get("/apk", follow_redirects=True)
        assert served.content.startswith(b"PK")
        # Whatever /tv answers — the page when built, a 404 when not — it is
        # never the archive.
        assert not anon_client.get("/tv", follow_redirects=True).content.startswith(b"PK")
