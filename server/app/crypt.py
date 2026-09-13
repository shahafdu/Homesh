"""Encrypting a file so it can be kept somewhere you do not control.

A backup that only exists on the machine it was taken from is half a backup, and
the fix — keeping a copy online — is only acceptable if the copy is useless to
whoever ends up holding it. That is a stronger requirement than it first sounds,
because of *what* is in a Homesh backup: not your media, but the catalog, and the
catalog names the rooms in your house, the screens in them, the folders on your
disks and the accounts that reach them. In the clear, that is a map.

So the file is encrypted here, before it goes anywhere, with a key that never
leaves the house. The service that stores it holds ciphertext and a filename.

**AES-256-GCM, in chunks**, rather than one pass over the whole file: a backup
grows with the library and holding all of it in memory to encrypt it is a
limitation with no upside. Each chunk is sealed independently, which introduces
the two failures that chunking always introduces — and closes both:

- **Reordering.** Each chunk's position is bound into it as associated data, so
  a chunk moved elsewhere in the file fails to open rather than opening in the
  wrong place.
- **Truncation.** The last chunk says that it is the last, also as associated
  data. A file cut short therefore fails at the end rather than decrypting to a
  clean-looking prefix — which for a database dump would be a restore that
  silently lost whatever came after the cut.

The nonce is an eight-byte random prefix chosen per file plus a four-byte
counter, so no two chunks share one and no two files share a sequence.

A four-byte check value derived from the key is written in the header. It proves
nothing to an attacker — it is a hash of a hash — and it means the wrong key
gives "that is not the right key" instead of an unintelligible failure at chunk
zero.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"homesh-enc\x01"

# Plaintext per chunk. A megabyte keeps the sealing overhead under a thousandth
# and the memory flat regardless of how large the library grows.
CHUNK = 1024 * 1024

# GCM's tag, appended to each chunk's ciphertext by this library.
TAG = 16


class KeyError_(Exception):
    """No key, or the wrong one."""


def key_bytes(configured: str) -> bytes:
    """The raw key from its configured form, or a clear refusal.

    Refusing is the right behaviour for an empty key: the alternative is
    uploading the catalog in the clear, and there is no situation in which
    silently doing that is what somebody wanted.
    """
    if not configured.strip():
        raise KeyError_(
            "no BACKUP_KEY is set, so nothing can be encrypted. "
            "Generate one with tools/new-backup-key.ps1 and put it in .env."
        )
    try:
        raw = base64.urlsafe_b64decode(configured.strip() + "=" * (-len(configured.strip()) % 4))
    except Exception as exc:  # noqa: BLE001
        raise KeyError_("BACKUP_KEY is not valid base64") from exc
    if len(raw) != 32:
        raise KeyError_(f"BACKUP_KEY must decode to 32 bytes, not {len(raw)}")
    return raw


def _check(key: bytes) -> bytes:
    """Four bytes that identify a key without revealing it."""
    return hashlib.sha256(b"homesh-backup-key-check" + key).digest()[:4]


def _nonce(prefix: bytes, counter: int) -> bytes:
    return prefix + struct.pack(">I", counter)


def _aad(counter: int, final: bool) -> bytes:
    return MAGIC + struct.pack(">I", counter) + (b"\x01" if final else b"\x00")


def encrypt(source: Path, target: Path, key: bytes) -> int:
    """Encrypt `source` into `target`. Returns the bytes written."""
    cipher = AESGCM(key)
    prefix = os.urandom(8)
    written = 0

    with source.open("rb") as plain, target.open("wb") as out:
        out.write(MAGIC)
        out.write(_check(key))
        out.write(prefix)
        written += len(MAGIC) + 4 + 8

        counter = 0
        # One chunk of lookahead, so the last one can be told it is the last.
        block = plain.read(CHUNK)
        while True:
            following = plain.read(CHUNK)
            final = not following
            sealed = cipher.encrypt(_nonce(prefix, counter), block, _aad(counter, final))
            out.write(struct.pack(">I", len(sealed)))
            out.write(sealed)
            written += 4 + len(sealed)
            if final:
                break
            block = following
            counter += 1

    return written


def decrypt(source: Path, target: Path, key: bytes) -> int:
    """Decrypt `source` into `target`. Returns the bytes written.

    Raises on anything wrong, and refuses to leave a half-written file behind: a
    truncated restore that looks complete is the failure this is guarding
    against, so a failure here must not produce a plausible file.
    """
    cipher = AESGCM(key)

    with source.open("rb") as sealed:
        if sealed.read(len(MAGIC)) != MAGIC:
            raise KeyError_("that file is not an encrypted Homesh backup")
        if sealed.read(4) != _check(key):
            raise KeyError_(
                "that backup was encrypted with a different key. "
                "Without the key it was made with, it cannot be read at all."
            )
        prefix = sealed.read(8)
        if len(prefix) != 8:
            raise KeyError_("the file ends in the middle of its header")

        written = 0
        try:
            with target.open("wb") as plain:
                counter = 0
                while True:
                    header = sealed.read(4)
                    if not header:
                        raise KeyError_(
                            "the file ends without its last chunk — it was cut short"
                        )
                    (size,) = struct.unpack(">I", header)
                    body = _exactly(sealed, size)

                    final = _open_chunk(cipher, prefix, counter, body, plain)
                    written += len(body) - TAG
                    if final:
                        break
                    counter += 1
        except BaseException:
            target.unlink(missing_ok=True)
            raise

    return written


def _exactly(handle: BinaryIO, size: int) -> bytes:
    body = handle.read(size)
    if len(body) != size:
        raise KeyError_("the file ends in the middle of a chunk — it was cut short")
    return body


def _open_chunk(cipher, prefix: bytes, counter: int, body: bytes, out: BinaryIO) -> bool:
    """Open one chunk. Returns whether it was the last.

    Tried as an ordinary chunk first and as a final one second, because only the
    associated data distinguishes them and there is no third possibility: if
    neither opens, this chunk is not what it claims to be.
    """
    for final in (False, True):
        try:
            out.write(cipher.decrypt(_nonce(prefix, counter), body, _aad(counter, final)))
            return final
        except InvalidTag:
            continue
    raise KeyError_(
        f"chunk {counter} would not open. The file has been altered, reordered "
        "or damaged."
    )


def new_key() -> str:
    """A fresh key, in the form the configuration expects."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()
