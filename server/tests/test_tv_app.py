"""Handing the TV app to a set-top box.

A box has a network connection and a remote control and no other way to receive
a file, so the server serves the APK itself. These check the two things that
would waste somebody's evening on a ladder in front of a television: a wrong
content type, which makes Android refuse to install it, and a stale path that
needs a restart before a fresh build appears.
"""

from __future__ import annotations


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
