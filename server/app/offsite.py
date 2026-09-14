"""Putting a file somewhere this house is not.

S3, in the sense that everybody means it: Oracle Object Storage, Cloudflare R2,
Backblaze B2 and AWS itself all speak the same request signing, so this talks to
one protocol rather than to one company. Which provider is a setting, and moving
between them is six lines of `.env` rather than a rewrite — that matters more
than it sounds, because the first choice here was Google Drive and it turned out
to be impossible (a service account owns no storage, so it cannot create a file
even in a folder shared with it).

**Signature Version 4, written out rather than imported.** It is about eighty
lines of hashing with no branches, and the alternative is boto3 — fifty
megabytes of AWS SDK, a transitive dependency tree, and a second HTTP client in
an image that already has one — to sign one PUT a day. The specification for
this has not changed since 2012.

Nothing here decides *what* to send or *when*. It receives a file that is
already encrypted (see `app/crypt.py`) and puts it in a bucket. The direction is
the security argument and it lives in `app/backups.py`: the house pushes,
nothing out there pulls, and what arrives is ciphertext.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

log = logging.getLogger("homesh.offsite")

# Long enough for a backup of a large catalog on a domestic connection.
TIMEOUT = httpx.Timeout(30.0, read=600.0, write=600.0, pool=20.0)

UNSIGNED = "UNSIGNED-PAYLOAD"

# How much of a listing to read. A thousand entries of a hundred bytes is a
# generous ceiling for a shelf that holds about ten, and a response that wants
# to be larger than this is not a listing.
_LISTING_CAP = 512 * 1024


def _field(entry: str, tag: str) -> str | None:
    """One element's text, from a document whose shape is fixed and known."""
    found = re.search(rf"<{tag}>(.*?)</{tag}>", entry, re.S)
    return found.group(1).strip() if found else None


class OffsiteError(Exception):
    """Something the person who set this up needs to read."""


@dataclass(frozen=True)
class Store:
    """Where copies go, and how to prove we may put them there."""

    provider: str
    region: str
    bucket: str
    access_key: str
    secret_key: str
    # Oracle's S3 endpoint is per-tenancy, and the tenancy is named by an opaque
    # string it calls a namespace. Nobody else needs one.
    namespace: str = ""

    @property
    def host(self) -> str:
        if self.provider == "oracle":
            if not self.namespace:
                raise OffsiteError(
                    "Oracle needs the Object Storage namespace as well - it is on "
                    "the bucket's own page in the console."
                )
            return f"{self.namespace}.compat.objectstorage.{self.region}.oraclecloud.com"
        if self.provider == "aws":
            return f"s3.{self.region}.amazonaws.com"
        if self.provider == "backblaze":
            return f"s3.{self.region}.backblazeb2.com"
        raise OffsiteError(f"unknown off-site provider {self.provider!r}")

    @property
    def base(self) -> str:
        return f"https://{self.host}"


def configured(settings) -> Store | None:
    """The configured store, or None when off-site copies are not set up.

    None rather than an exception: not having arranged somewhere to send copies
    is an ordinary state, and the local backup still happens.
    """
    if not settings.offsite_provider.strip():
        return None
    missing = [
        name
        for name, value in (
            ("OFFSITE_REGION", settings.offsite_region),
            ("OFFSITE_BUCKET", settings.offsite_bucket),
            ("OFFSITE_ACCESS_KEY", settings.offsite_access_key),
            ("OFFSITE_SECRET_KEY", settings.offsite_secret_key),
        )
        if not value.strip()
    ]
    if missing:
        raise OffsiteError(f"off-site storage is half configured: {', '.join(missing)} is empty")

    return Store(
        provider=settings.offsite_provider.strip().lower(),
        region=settings.offsite_region.strip(),
        bucket=settings.offsite_bucket.strip(),
        access_key=settings.offsite_access_key.strip(),
        secret_key=settings.offsite_secret_key.strip(),
        namespace=settings.offsite_namespace.strip(),
    )


# ── Signature Version 4 ─────────────────────────────────────────────────────


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, stamp: str, region: str, service: str) -> bytes:
    """One key per day, per region, per service. That is the whole point of the
    scheme: a leaked signing key is useless tomorrow and useless elsewhere."""
    key = _sign(f"AWS4{secret}".encode(), stamp)
    key = _sign(key, region)
    key = _sign(key, service)
    return _sign(key, "aws4_request")


def _canonical(method: str, path: str, query: str, headers: dict[str, str], payload: str) -> str:
    signed = ";".join(sorted(h.lower() for h in headers))
    lines = "\n".join(f"{h.lower()}:{headers[h].strip()}" for h in sorted(headers, key=str.lower))
    return "\n".join([method, path, query, lines, "", signed, payload])


def _authorise(
    store: Store,
    method: str,
    path: str,
    query: str = "",
    payload: str = UNSIGNED,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Headers that prove this request is ours, for this minute, for this bucket."""
    now = datetime.now(UTC)
    stamp, moment = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
    service = "s3"

    headers = {
        "Host": store.host,
        "x-amz-content-sha256": payload,
        "x-amz-date": moment,
        **(extra or {}),
    }

    scope = f"{stamp}/{store.region}/{service}/aws4_request"
    to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            moment,
            scope,
            _sha256(_canonical(method, path, query, headers, payload).encode()),
        ]
    )
    signature = hmac.new(
        _signing_key(store.secret_key, stamp, store.region, service),
        to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()

    signed = ";".join(sorted(h.lower() for h in headers))
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={store.access_key}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}"
    )
    return headers


def _key_path(store: Store, name: str) -> str:
    return f"/{quote(store.bucket, safe='')}/{quote(name, safe='')}"


def _explain(response: httpx.Response, doing: str) -> OffsiteError:
    """Turn a provider's XML into something worth reading.

    The status alone is not enough here: 403 means "the key is wrong", "the key
    is right and may not do this", and "the bucket is somebody else's", and the
    three have completely different fixes.
    """
    body = response.text[:400]
    if response.status_code in (401, 403):
        hint = (
            "the key was refused. Check OFFSITE_ACCESS_KEY and OFFSITE_SECRET_KEY, "
            "and that the key belongs to a user allowed to write to this bucket."
        )
    elif response.status_code == 404:
        hint = (
            "not found. Check OFFSITE_BUCKET, OFFSITE_REGION, and - on Oracle - "
            "OFFSITE_NAMESPACE, which is on the bucket's page in the console."
        )
    else:
        hint = "the store refused it."
    return OffsiteError(f"{doing}: {hint} ({response.status_code}) {body}")


# ── The four things this needs to do ────────────────────────────────────────


def put(store: Store, name: str, path: Path) -> int:
    """Upload a file under `name`. Returns the bytes sent.

    Streamed from disk rather than read into memory: a backup grows with the
    library, and this runs on a machine that is also playing films.

    The payload is not hashed. Signing it would mean reading the whole file
    twice -- once to hash, once to send -- and it is already sealed and
    authenticated by its own encryption. The transport is TLS; what the
    signature has to establish is *who is asking*, and it still does.
    """
    size = path.stat().st_size
    headers = _authorise(
        store,
        "PUT",
        _key_path(store, name),
        extra={"Content-Length": str(size), "Content-Type": "application/octet-stream"},
    )

    with httpx.Client(timeout=TIMEOUT) as http, path.open("rb") as body:
        response = http.put(f"{store.base}{_key_path(store, name)}", content=body, headers=headers)

    if response.status_code not in (200, 201):
        raise _explain(response, f"could not upload {name}")
    log.info("sent %s off-site (%.1f MB)", name, size / 1e6)
    return size


@dataclass(frozen=True)
class Stored:
    name: str
    size_bytes: int
    written_at: str | None


def index(store: Store) -> list[Stored]:
    """What is in the bucket, newest first."""
    path = f"/{quote(store.bucket, safe='')}"
    query = "list-type=2"
    headers = _authorise(store, "GET", path, query=query)

    with httpx.Client(timeout=TIMEOUT) as http:
        response = http.get(f"{store.base}{path}?{query}", headers=headers)
    if response.status_code != 200:
        raise _explain(response, "could not list the bucket")

    # Read with a narrow pattern rather than an XML parser.
    #
    # Not laziness: `xml.etree` is documented as vulnerable to entity expansion
    # -- a short response can be made to consume the machine -- and the
    # alternative is adding a hardened XML library to the image to read three
    # fields out of a document with a fixed shape. The store is ours and the
    # transport is TLS, but "we trust the other end" is exactly the assumption
    # that makes a parser a liability, and nothing here needs one.
    body = response.text[:_LISTING_CAP]
    found = [
        Stored(
            name=_field(entry.group(1), "Key") or "",
            size_bytes=int(_field(entry.group(1), "Size") or 0),
            written_at=_field(entry.group(1), "LastModified"),
        )
        for entry in re.finditer(r"<Contents>(.*?)</Contents>", body, re.S)
    ]
    return sorted(found, key=lambda s: s.written_at or "", reverse=True)


def get(store: Store, name: str, target: Path) -> int:
    """Bring one back down. Returns the bytes written."""
    path = _key_path(store, name)
    headers = _authorise(store, "GET", path)
    written = 0

    with httpx.Client(timeout=TIMEOUT) as http, target.open("wb") as out:
        with http.stream("GET", f"{store.base}{path}", headers=headers) as response:
            if response.status_code != 200:
                response.read()
                raise _explain(response, f"could not fetch {name}")
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                out.write(chunk)
                written += len(chunk)
    return written


def remove(store: Store, name: str) -> None:
    """Delete one.

    Used only by the retention pass. A key that is allowed to write and not to
    delete is a better arrangement and is worth setting up -- see
    `docs/OFFSITE_BACKUPS.md` -- and when it is, this fails and pruning
    off-site copies becomes Oracle's job through a lifecycle rule. That is a
    reason to refuse loudly rather than to swallow the error.
    """
    path = _key_path(store, name)
    headers = _authorise(store, "DELETE", path)

    with httpx.Client(timeout=TIMEOUT) as http:
        response = http.delete(f"{store.base}{path}", headers=headers)
    if response.status_code not in (200, 202, 204):
        raise _explain(response, f"could not delete {name}")
