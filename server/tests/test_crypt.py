"""Encrypting a backup so it can be kept somewhere you do not control.

The round trip matters least here. What matters is what happens to a file that
has been *tampered with*, because the whole reason for encrypting is that the
copy will sit on somebody else's disk — so every one of these is a test that
something wrong fails loudly rather than quietly producing a plausible file.
"""

from __future__ import annotations

import base64
import os

import pytest

from app.crypt import CHUNK, KeyError_, decrypt, encrypt, key_bytes, new_key


@pytest.fixture
def key() -> bytes:
    return key_bytes(new_key())


def _written(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


class TestTheRoundTrip:
    @pytest.mark.parametrize(
        "size",
        [
            0,                    # an empty file is still a file
            1,
            CHUNK - 1,            # just under one chunk
            CHUNK,                # exactly one
            CHUNK + 1,            # just over, so there are two
            CHUNK * 2 + 17,       # several, ending mid-chunk
        ],
    )
    def test_what_went_in_comes_out(self, tmp_path, key, size):
        original = os.urandom(size)
        plain = _written(tmp_path, "plain", original)
        sealed = tmp_path / "sealed"
        back = tmp_path / "back"

        encrypt(plain, sealed, key)
        decrypt(sealed, back, key)

        assert back.read_bytes() == original

    def test_the_ciphertext_does_not_contain_the_plaintext(self, tmp_path, key):
        """The point of the exercise, stated as a test."""
        secret = b"the bedroom television" * 400
        plain = _written(tmp_path, "plain", secret)
        sealed = tmp_path / "sealed"
        encrypt(plain, sealed, key)

        assert b"bedroom" not in sealed.read_bytes()

    def test_two_files_of_the_same_thing_differ(self, tmp_path, key):
        """A fresh nonce per file, so identical backups are not identifiable as
        identical by whoever is storing them."""
        plain = _written(tmp_path, "plain", b"same content" * 1000)
        first, second = tmp_path / "a", tmp_path / "b"
        encrypt(plain, first, key)
        encrypt(plain, second, key)

        assert first.read_bytes() != second.read_bytes()


class TestThingsThatShouldFail:
    def _sealed(self, tmp_path, key, size=CHUNK * 2 + 500):
        plain = _written(tmp_path, "plain", os.urandom(size))
        sealed = tmp_path / "sealed"
        encrypt(plain, sealed, key)
        return sealed

    def test_the_wrong_key_says_so(self, tmp_path, key):
        sealed = self._sealed(tmp_path, key)
        with pytest.raises(KeyError_, match="different key"):
            decrypt(sealed, tmp_path / "out", key_bytes(new_key()))

    def test_a_truncated_file_is_refused(self, tmp_path, key):
        """The important one. A backup cut short must not decrypt to a clean
        prefix — for a database dump that is a restore that silently lost
        whatever came after the cut."""
        sealed = self._sealed(tmp_path, key)
        body = sealed.read_bytes()
        sealed.write_bytes(body[: len(body) // 2])

        with pytest.raises(KeyError_, match="cut short"):
            decrypt(sealed, tmp_path / "out", key)

    def test_a_file_missing_only_its_last_chunk_is_refused(self, tmp_path, key):
        """Cut at a chunk boundary, so nothing is obviously damaged. The last
        chunk says that it is the last; without it the file is incomplete and
        can be shown to be."""
        plain = _written(tmp_path, "plain", os.urandom(CHUNK * 3))
        sealed = tmp_path / "sealed"
        encrypt(plain, sealed, key)

        body = sealed.read_bytes()
        # Header, then three chunks of (4-byte length + CHUNK + tag).
        one = 4 + CHUNK + 16
        head = len(b"homesh-enc\x01") + 4 + 8
        sealed.write_bytes(body[: head + one * 2])

        with pytest.raises(KeyError_):
            decrypt(sealed, tmp_path / "out", key)

    def test_an_altered_byte_is_refused(self, tmp_path, key):
        sealed = self._sealed(tmp_path, key)
        body = bytearray(sealed.read_bytes())
        body[-20] ^= 0xFF
        sealed.write_bytes(bytes(body))

        with pytest.raises(KeyError_):
            decrypt(sealed, tmp_path / "out", key)

    def test_reordered_chunks_are_refused(self, tmp_path, key):
        """Each chunk carries its own position, so one moved elsewhere in the
        file will not open there."""
        plain = _written(tmp_path, "plain", os.urandom(CHUNK * 2))
        sealed = tmp_path / "sealed"
        encrypt(plain, sealed, key)

        body = sealed.read_bytes()
        head = len(b"homesh-enc\x01") + 4 + 8
        one = 4 + CHUNK + 16
        first, second = body[head : head + one], body[head + one :]
        sealed.write_bytes(body[:head] + second + first)

        with pytest.raises(KeyError_):
            decrypt(sealed, tmp_path / "out", key)

    def test_something_that_is_not_a_backup_is_refused(self, tmp_path, key):
        with pytest.raises(KeyError_, match="not an encrypted"):
            decrypt(_written(tmp_path, "junk", b"hello there"), tmp_path / "out", key)

    def test_nothing_is_left_behind_by_a_failure(self, tmp_path, key):
        """A half-written file that looks like a restore is worse than no file."""
        sealed = self._sealed(tmp_path, key)
        body = sealed.read_bytes()
        sealed.write_bytes(body[: len(body) - 40])
        out = tmp_path / "out"

        with pytest.raises(KeyError_):
            decrypt(sealed, out, key)
        assert not out.exists()


class TestTheKeyItself:
    def test_no_key_is_a_refusal_rather_than_a_default(self):
        """There is no situation in which quietly uploading the catalog in the
        clear is what somebody wanted."""
        for empty in ("", "   "):
            with pytest.raises(KeyError_, match="no BACKUP_KEY"):
                key_bytes(empty)

    def test_a_key_of_the_wrong_length_is_refused(self):
        with pytest.raises(KeyError_, match="32 bytes"):
            key_bytes(base64.urlsafe_b64encode(os.urandom(16)).decode())

    def test_nonsense_is_refused(self):
        with pytest.raises(KeyError_):
            key_bytes("not base64 at all !!!")

    def test_a_generated_key_is_accepted(self):
        assert len(key_bytes(new_key())) == 32

    def test_two_generated_keys_differ(self):
        assert new_key() != new_key()
