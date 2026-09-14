"""Signing a request the way S3 expects, and reading what comes back.

The signing is the part worth testing hard: it is eighty lines of hashing with
no branches, written out rather than pulled in as fifty megabytes of AWS SDK,
and if it is wrong the only symptom is a 403 with no clue in it.

So it is checked against Amazon's own published example rather than against a
second copy of the same arithmetic — which would only prove that two of my
mistakes agree.
"""

from __future__ import annotations

import httpx
import pytest

from app import offsite
from app.offsite import OffsiteError, Store


def _store(**over) -> Store:
    return Store(
        provider=over.get("provider", "oracle"),
        region=over.get("region", "il-jerusalem-1"),
        bucket=over.get("bucket", "homesh-backups"),
        access_key=over.get("access_key", "AKIDEXAMPLE"),
        secret_key=over.get("secret_key", "SECRETEXAMPLE"),
        namespace=over.get("namespace", "examplenamespace"),
    )


class TestSigning:
    def test_the_signing_key_matches_amazons_published_example(self):
        """The one external check in here.

        Amazon documents this derivation with a worked example; if this line
        passes, the four-step key schedule is right, and the rest of the
        signature is a hash of a string this code also builds.
        """
        # https://docs.aws.amazon.com/general/latest/gr/signature-v4-examples.html
        key = offsite._signing_key(
            "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
            "20120215",
            "us-east-1",
            "iam",
        )
        assert key.hex() == "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d"

    def test_a_request_carries_what_s3_requires(self):
        headers = offsite._authorise(_store(), "PUT", "/homesh-backups/x.enc")

        assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
        assert "SignedHeaders=" in headers["Authorization"]
        assert "Signature=" in headers["Authorization"]
        assert headers["x-amz-content-sha256"] == offsite.UNSIGNED
        assert headers["x-amz-date"].endswith("Z")

    def test_every_header_sent_is_a_header_signed(self):
        """A header in the request and not in the signature is a 403 that says
        nothing about which one."""
        headers = offsite._authorise(
            _store(), "PUT", "/b/x", extra={"Content-Length": "10", "Content-Type": "text/plain"}
        )
        declared = headers["Authorization"].split("SignedHeaders=")[1].split(",")[0]
        signed = set(declared.split(";"))
        sent = {h.lower() for h in headers if h.lower() != "authorization"}
        assert signed == sent

    def test_the_signature_covers_the_path(self):
        """Two objects must not share a signature, or one key would authorise
        writing over any file in the bucket."""
        first = offsite._authorise(_store(), "PUT", "/b/one.enc")["Authorization"]
        second = offsite._authorise(_store(), "PUT", "/b/two.enc")["Authorization"]
        assert first.split("Signature=")[1] != second.split("Signature=")[1]

    def test_the_signature_covers_the_method(self):
        get = offsite._authorise(_store(), "GET", "/b/one.enc")["Authorization"]
        delete = offsite._authorise(_store(), "DELETE", "/b/one.enc")["Authorization"]
        assert get.split("Signature=")[1] != delete.split("Signature=")[1]

    def test_a_name_with_a_space_is_escaped_the_same_way_twice(self):
        """The path is signed and then requested. Escape them differently and
        the signature is for a different object than the one being written."""
        path = offsite._key_path(_store(), "homesh 2026.sql.gz.enc")
        assert " " not in path
        assert path == "/homesh-backups/homesh%202026.sql.gz.enc"


class TestWhereItSends:
    def test_oracle_needs_its_namespace(self):
        with pytest.raises(OffsiteError, match="namespace"):
            assert _store(namespace="").host

    def test_each_provider_has_its_own_endpoint(self):
        assert _store().host.startswith("examplenamespace.compat.objectstorage.")
        assert _store(provider="aws").host == "s3.il-jerusalem-1.amazonaws.com"
        assert _store(provider="backblaze").host == "s3.il-jerusalem-1.backblazeb2.com"

    def test_an_unknown_provider_says_so(self):
        with pytest.raises(OffsiteError, match="unknown"):
            assert _store(provider="dropbox").host


class TestConfiguration:
    class Fake:
        offsite_provider = ""
        offsite_region = ""
        offsite_bucket = ""
        offsite_access_key = ""
        offsite_secret_key = ""
        offsite_namespace = ""

    def test_nothing_configured_is_not_an_error(self):
        """Not having arranged somewhere to send copies is an ordinary state.
        The local backup still happens."""
        assert offsite.configured(self.Fake()) is None

    def test_half_configured_says_which_half(self):
        settings = self.Fake()
        settings.offsite_provider = "oracle"
        settings.offsite_region = "il-jerusalem-1"
        settings.offsite_bucket = "homesh-backups"
        # No keys.
        with pytest.raises(OffsiteError, match="OFFSITE_ACCESS_KEY"):
            offsite.configured(settings)


class TestTalkingToAStore:
    """With the network replaced, so these test this code rather than Oracle."""

    def _client(self, handler):
        transport = httpx.MockTransport(handler)
        original = httpx.Client

        def build(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        return build

    def test_a_listing_is_read_out_of_the_response(self, monkeypatch):
        body = """<?xml version="1.0" encoding="UTF-8"?>
        <ListBucketResult>
          <Contents>
            <Key>homesh-20260101-000000.sql.gz.enc</Key>
            <Size>18600000</Size>
            <LastModified>2026-01-01T00:00:00.000Z</LastModified>
          </Contents>
          <Contents>
            <Key>homesh-20260102-000000.sql.gz.enc</Key>
            <Size>18700000</Size>
            <LastModified>2026-01-02T00:00:00.000Z</LastModified>
          </Contents>
        </ListBucketResult>"""
        monkeypatch.setattr(
            httpx, "Client", self._client(lambda request: httpx.Response(200, text=body))
        )

        found = offsite.index(_store())
        assert [f.name for f in found] == [
            "homesh-20260102-000000.sql.gz.enc",
            "homesh-20260101-000000.sql.gz.enc",
        ], "newest first"
        assert found[0].size_bytes == 18700000

    def test_a_refusal_says_what_to_check(self, monkeypatch):
        """A 403 means the key is wrong, or right and not allowed, or for
        another bucket entirely. The status alone sends somebody to the wrong
        place."""
        monkeypatch.setattr(
            httpx, "Client", self._client(lambda request: httpx.Response(403, text="<Error/>"))
        )
        with pytest.raises(OffsiteError, match="OFFSITE_ACCESS_KEY"):
            offsite.index(_store())

    def test_a_missing_bucket_points_at_the_right_settings(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "Client", self._client(lambda request: httpx.Response(404, text="<Error/>"))
        )
        with pytest.raises(OffsiteError, match="OFFSITE_NAMESPACE"):
            offsite.index(_store())

    def test_an_upload_sends_the_file_to_the_right_place(self, monkeypatch, tmp_path):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization", "")
            seen["body"] = request.read()
            return httpx.Response(200)

        monkeypatch.setattr(httpx, "Client", self._client(handler))
        payload = tmp_path / "homesh-20260101-000000.sql.gz.enc"
        payload.write_bytes(b"sealed bytes")

        sent = offsite.put(_store(), payload.name, payload)

        assert sent == len(b"sealed bytes")
        assert seen["method"] == "PUT"
        assert seen["url"].endswith("/homesh-backups/homesh-20260101-000000.sql.gz.enc")
        assert seen["body"] == b"sealed bytes"
        assert seen["auth"].startswith("AWS4-HMAC-SHA256")

    def test_what_comes_down_is_written_out(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            httpx,
            "Client",
            self._client(lambda request: httpx.Response(200, content=b"sealed bytes")),
        )
        target = tmp_path / "back"
        assert offsite.get(_store(), "x.enc", target) == len(b"sealed bytes")
        assert target.read_bytes() == b"sealed bytes"
