"""Google Drive connector, via a service account.

The server reads Drive as a robot identity that you have shared folders with,
rather than as an app holding a token to your whole account. Ordinary Drive
sharing does the granting, so there is no consent screen, no verification, and
nothing that expires weekly (ARCHITECTURE.md §1.2).

The practical consequence: it can see exactly the folders you shared and nothing
else, and you revoke it by unsharing.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .base import Entry

log = logging.getLogger("homesh.sources.gdrive")

API = "https://www.googleapis.com/drive/v3"

# Everything that reads your library uses this and only this. The server indexes
# and streams; it has no business writing to your media (§2).
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Creating a shareable link is the one action that cannot be done read-only —
# a link is a permission, and adding a permission is a write. It therefore gets
# its own credential, minted separately and used by nothing else, so the scope
# that can alter your Drive is reachable from exactly one code path instead of
# being handed to every listing and every byte range.
SHARE_SCOPES = ["https://www.googleapis.com/auth/drive"]

FOLDER_MIME = "application/vnd.google-apps.folder"

# Docs, Sheets and Slides have no bytes to download — they must be exported to a
# concrete format. Listing them but refusing to stream would be a worse lie than
# skipping them, so they are catalogued and marked for export later.
NATIVE_PREFIX = "application/vnd.google-apps."

FIELDS = "id,name,mimeType,size,modifiedTime,md5Checksum,trashed"
PAGE = 200
TIMEOUT = 30.0

# How long to wait for a free connection before giving up on a read.
#
# Separate from the other timeouts and much shorter, because it is not waiting
# for Drive -- it is waiting for this server. Without it the wait is unbounded:
# once every connection in the pool is held, a new read sits there for ever with
# its response already begun and no bytes behind it, which on a television is a
# film that never starts and no error anywhere. Measured: the hundredth
# simultaneous read never returned at all, while the ninety-ninth took eight
# seconds.
POOL_WAIT = 20.0

# Connections held open to Drive, per shared folder.
#
# Explicit rather than inherited, so the number that decides when reads start
# queueing is written down beside the timeout that gives up on them.
POOL = httpx.Limits(max_connections=64, max_keepalive_connections=16)

# Minted again this long before it runs out.
#
# A token lasts an hour, and refreshing it takes a network round trip that every
# read for this folder waits behind. Doing that while a film is playing is a
# stall in the middle of the film; doing it five minutes early is free.
REFRESH_EARLY = 300.0

# A refresh that has not answered in this long has failed, whatever it thinks.
# google-auth's own default is two minutes, which is two minutes of a house with
# no music.
REFRESH_TIMEOUT = 20.0

# How long the answer to "is this folder still shared with us" is reused.
#
# Long enough that it leaves the path of an ordinary read, short enough that
# unsharing a folder takes effect while you are still looking at the screen.
AVAILABLE_FOR = 60.0


class DriveError(Exception):
    pass


class _Credentials:
    """Access tokens minted from the service-account key.

    A service account signs an assertion with its private key and exchanges it
    for a one-hour token, as often as needed. There is no refresh token tied to a
    user's consent, which is precisely why nothing here expires on a weekly clock.
    """

    def __init__(self, key_path: Path, scopes: list[str] | None = None) -> None:
        self.key_path = key_path
        self.scopes = scopes or SCOPES
        self._creds: Any = None
        self._lock = threading.Lock()

    def token(self) -> str:
        """The current access token, minted again when it is nearly out.

        **The refresh does not happen under the lock**, and that is the whole
        point of the shape below. It used to: a single mutex was held across the
        network round trip that mints the token, and every read of every byte in
        this folder goes through here. One refresh that did not come back
        therefore stopped the folder -- not slowly, but completely and until the
        server was restarted, with each waiting read sitting inside a response
        whose headers had already been sent. From outside that is a film that
        never starts, a folder that never opens, and nothing in any log.

        Two threads may now mint at once, which costs one extra token request
        and is the correct trade. The token they are replacing is still valid
        for another five minutes, so neither of them is holding anything up.
        """
        with self._lock:
            creds = self._creds

        if creds is None:
            creds = self._minted()
            with self._lock:
                # Whoever got here first wins; the loser's token is simply
                # unused. Both are valid.
                self._creds = self._creds or creds
                creds = self._creds

        if self._expiring(creds):
            began = time.monotonic()
            creds.refresh(self._request())
            waited = time.monotonic() - began
            if waited > 2:
                # Named in the log, because this is the step that used to stop
                # everything and leave nothing behind to say so.
                log.warning("drive token took %.1fs to refresh", waited)

        return creds.token

    def _minted(self):
        from google.oauth2 import service_account

        if not self.key_path.is_file():
            raise DriveError(
                f"no service-account key at {self.key_path}. "
                "Download the JSON key and place it there."
            )
        return service_account.Credentials.from_service_account_file(
            str(self.key_path), scopes=self.scopes
        )

    @staticmethod
    def _expiring(creds: Any) -> bool:
        """Out of time, or close enough that a film would notice."""
        expiry = getattr(creds, "expiry", None)
        if expiry is None:
            return not getattr(creds, "valid", False)
        # google-auth keeps expiry naive and in UTC.
        left = (expiry.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
        return left < REFRESH_EARLY

    @staticmethod
    def _request():
        """google-auth's transport, but it gives up eventually.

        Its own default is two minutes per attempt, which is two minutes of a
        house with no music -- and the caller has no way to shorten it except by
        passing a transport that does.
        """
        from google.auth.transport.requests import Request

        class Bounded(Request):
            def __call__(self, url, method="GET", body=None, headers=None,
                         timeout=REFRESH_TIMEOUT, **kwargs):
                return super().__call__(url, method, body, headers, timeout, **kwargs)

        return Bounded()


def _client(creds: _Credentials) -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        headers={"Authorization": f"Bearer {creds.token()}"},
        follow_redirects=True,
    )


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def shared_folders(key_path: Path) -> list[tuple[str, str]]:
    """Top-level folders shared with the robot. These become the source roots."""
    creds = _Credentials(key_path)
    out: list[tuple[str, str]] = []
    page_token = None

    with _client(creds) as client:
        while True:
            params = {
                "q": f"sharedWithMe and mimeType = '{FOLDER_MIME}' and trashed = false",
                "fields": f"nextPageToken,files({FIELDS})",
                "pageSize": PAGE,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token

            response = client.get(f"{API}/files", params=params)
            if response.status_code != 200:
                raise DriveError(f"listing shared folders failed: {response.text[:200]}")

            body = response.json()
            out.extend((f["id"], f["name"]) for f in body.get("files", []))
            page_token = body.get("nextPageToken")
            if not page_token:
                break

    return out


class GoogleDriveConnector:
    """One shared folder, presented as a source.

    Drive identifies files by opaque id rather than path, so the tree is walked
    and paths are assembled as we go. Ids are kept alongside, because a rename in
    Drive changes the path but not the file.
    """

    def _http(self) -> httpx.Client:
        """A pooled client for this connector, built once.

        Not closed explicitly: a connector lives as long as the source it
        represents, and httpx releases the pool when it is collected.
        """
        existing = getattr(self, "_client", None)
        if existing is None:
            existing = httpx.Client(
                timeout=httpx.Timeout(TIMEOUT, pool=POOL_WAIT),
                limits=POOL,
                follow_redirects=True,
            )
            self._client = existing
        return existing

    def __init__(self, root_id: str, key_path: Path) -> None:
        self.root_id = root_id
        self.creds = _Credentials(key_path)
        # Path -> Drive id, filled during walk/list so open_range can find a file
        # again without re-walking the tree.
        self._ids: dict[str, str] = {"": root_id}
        # Answered from memory for a moment at a time; see `available`.
        self._available = False
        self._available_at = 0.0

    @property
    def available(self) -> bool:
        """Whether the folder is still shared with us, asked sparingly.

        This is on the path of **every byte served from Drive**: a replica is
        chosen per range request, and choosing it asks each candidate source
        whether it is there. It was answering by opening a new TLS connection to
        Google and making an API call, so a film that asks for forty ranges paid
        forty handshakes and forty round trips for an answer that cannot
        meaningfully change between them. Measured at roughly a second each,
        which is most of the wait before a video starts.

        A minute of memory removes it from the common path entirely while still
        noticing an unshared folder within a minute -- and unsharing is not
        something that needs to be noticed inside one second.
        """
        now = time.monotonic()
        cached = getattr(self, "_available_at", 0.0)
        if now - cached < AVAILABLE_FOR:
            return self._available
        try:
            r = self._http().get(
                f"{API}/files/{self.root_id}",
                params={"fields": "id,trashed", "supportsAllDrives": "true"},
                headers={"Authorization": f"Bearer {self.creds.token()}"},
            )
            answer = r.status_code == 200
        except Exception as exc:  # noqa: BLE001 - availability must never raise
            log.debug("drive unavailable: %s", exc)
            answer = False

        self._available = answer
        self._available_at = now
        return answer

    # ── Listing ─────────────────────────────────────────────────────────────

    def _children(self, folder_id: str) -> list[dict]:
        out: list[dict] = []
        page_token = None
        with _client(self.creds) as client:
            while True:
                params = {
                    "q": f"'{folder_id}' in parents and trashed = false",
                    "fields": f"nextPageToken,files({FIELDS})",
                    "pageSize": PAGE,
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                }
                if page_token:
                    params["pageToken"] = page_token

                r = client.get(f"{API}/files", params=params)
                if r.status_code != 200:
                    raise DriveError(f"listing failed: {r.text[:200]}")
                body = r.json()
                out.extend(body.get("files", []))
                page_token = body.get("nextPageToken")
                if not page_token:
                    return out

    @staticmethod
    def _entry(item: dict) -> Entry:
        is_dir = item.get("mimeType") == FOLDER_MIME
        size = item.get("size")
        return Entry(
            name=item["name"],
            is_dir=is_dir,
            size=None if is_dir or size is None else int(size),
            mtime=_parse_time(item.get("modifiedTime")),
            remote_id=item["id"],
            # Free: it arrives with the listing. Absent for folders and for the
            # Docs/Sheets kinds, which have no bytes to checksum.
            content_md5=item.get("md5Checksum"),
        )

    def _resolve_id(self, path: str) -> str:
        """Find a folder's Drive id, walking down from the root if unseen."""
        path = path.strip("/")
        if path in self._ids:
            return self._ids[path]

        current = self.root_id
        walked = ""
        for part in path.split("/"):
            found = next(
                (c for c in self._children(current)
                 if c["name"] == part and c.get("mimeType") == FOLDER_MIME),
                None,
            )
            if found is None:
                raise FileNotFoundError(path)
            current = found["id"]
            walked = f"{walked}/{part}".strip("/")
            self._ids[walked] = current
        return current

    def list_dir(self, path: str = "") -> list[Entry]:
        folder_id = self._resolve_id(path)
        entries: list[Entry] = []
        for item in self._children(folder_id):
            entry = self._entry(item)
            child = f"{path.strip('/')}/{entry.name}".strip("/")
            self._ids[child] = item["id"]
            entries.append(entry)

        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def stat(self, path: str) -> Entry:
        parent, _, name = path.strip("/").rpartition("/")
        for entry in self.list_dir(parent):
            if entry.name == name:
                return entry
        raise FileNotFoundError(path)

    def walk(self) -> Iterator[tuple[str, Entry]]:
        stack: list[tuple[str, str]] = [("", self.root_id)]
        while stack:
            rel, folder_id = stack.pop()
            try:
                children = self._children(folder_id)
            except DriveError as exc:
                # One unreadable folder must not abandon the whole scan.
                log.warning("skipping %s: %s", rel or "/", exc)
                continue

            for item in children:
                entry = self._entry(item)
                child = f"{rel}/{entry.name}".strip("/")
                self._ids[child] = item["id"]
                if entry.is_dir:
                    stack.append((child, item["id"]))
                else:
                    yield rel, entry

    # ── Bytes ───────────────────────────────────────────────────────────────

    def remember(self, rel_path: str, file_id: str) -> None:
        """Record where a file is, so it need not be looked for.

        The scanner learns every file's id as it walks; telling the connector
        saves it walking the tree again later to answer the same question.
        """
        self._ids[rel_path.strip("/")] = file_id

    def open_range(self, path: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        """Stream a file, honouring the requested range.

        Drive supports Range on downloads, so seeking in a film does not drag the
        whole file across first.
        """
        rel = path.strip("/")
        file_id = self._ids.get(rel)
        if file_id is None:
            # Only when nothing is known: this lists every folder from the root
            # down, which for a deep path costs seconds, and a browser asks for
            # several ranges to play one song.
            parent, _, _name = rel.rpartition("/")
            self.list_dir(parent)          # populates the id cache
            file_id = self._ids.get(rel)
        if file_id is None:
            raise FileNotFoundError(path)

        headers = {"Range": f"bytes={start}-{'' if end is None else end}"}
        # One client per connector, kept open. A new one per range request meant
        # a fresh TCP connection and TLS handshake to Google every time, and a
        # browser asks for several ranges to play one song — so the handshake was
        # a large share of the wait before any audio arrived.
        client = self._http()
        began = time.monotonic()
        try:
            with client.stream(
                "GET",
                f"{API}/files/{file_id}",
                params={"alt": "media", "supportsAllDrives": "true"},
                headers={"Authorization": f"Bearer {self.creds.token()}", **headers},
            ) as response:
                if response.status_code not in (200, 206):
                    raise DriveError(f"download failed: {response.status_code}")
                waited = time.monotonic() - began
                if waited > 5:
                    log.warning("drive took %.1fs to begin %s", waited, rel)
                yield from response.iter_bytes(chunk_size=256 * 1024)
        except httpx.PoolTimeout as exc:
            # Every connection to this folder is in use. Refusing is the right
            # answer and used to be impossible: with no pool timeout the read
            # simply waited, inside a response whose headers had already gone
            # out, so the screen showed a film that was never going to start.
            log.warning("too many reads at once for this folder; refused %s", rel)
            raise DriveError("too many reads at once from this folder") from exc

    @staticmethod
    def is_exportable_only(mime: str | None) -> bool:
        """Docs, Sheets and Slides have no bytes — they must be exported."""
        return bool(mime and mime.startswith(NATIVE_PREFIX) and mime != FOLDER_MIME)


# ── Sharing a file by link ──────────────────────────────────────────────────


class DrivePermissionError(DriveError):
    """The robot account is not allowed to share this file.

    Distinct from a transport failure because the fix is a human one: the folder
    was shared with the robot as a viewer, and a viewer cannot grant access it
    does not have.
    """


def _share_client(key_path: Path) -> httpx.Client:
    return _client(_Credentials(key_path, SHARE_SCOPES))


def link_for(key_path: Path, file_id: str) -> str | None:
    """The existing anyone-with-the-link URL, or None if it is not shared."""
    with _share_client(key_path) as http:
        r = http.get(
            f"{API}/files/{file_id}",
            params={"fields": "webViewLink,permissions(id,type,role)",
                    "supportsAllDrives": "true"},
        )
        if r.status_code == 404:
            raise DriveError("that file is no longer in Drive")
        if r.status_code == 403:
            raise DrivePermissionError(r.text)
        r.raise_for_status()
        body = r.json()

    shared = any(p.get("type") == "anyone" for p in body.get("permissions") or [])
    return body.get("webViewLink") if shared else None


def create_link(key_path: Path, file_id: str) -> str:
    """Make the file readable by anyone holding the link, and return it.

    Reader, never writer: the person receiving this is being sent a copy to
    watch, not an invitation to change the original.
    """
    with _share_client(key_path) as http:
        r = http.post(
            f"{API}/files/{file_id}/permissions",
            params={"supportsAllDrives": "true", "sendNotificationEmail": "false"},
            json={"role": "reader", "type": "anyone"},
        )
        if r.status_code in (403, 401):
            raise DrivePermissionError(r.text)
        if r.status_code == 404:
            raise DriveError("that file is no longer in Drive")
        r.raise_for_status()

        info = http.get(
            f"{API}/files/{file_id}",
            params={"fields": "webViewLink", "supportsAllDrives": "true"},
        )
        info.raise_for_status()
        link = info.json().get("webViewLink")

    if not link:
        raise DriveError("Drive granted the permission but returned no link")
    return link


def revoke_link(key_path: Path, file_id: str) -> None:
    """Withdraw the public link. Idempotent — already-gone is success."""
    with _share_client(key_path) as http:
        r = http.delete(
            f"{API}/files/{file_id}/permissions/anyoneWithLink",
            params={"supportsAllDrives": "true"},
        )
        if r.status_code in (404, 204, 200):
            return
        if r.status_code == 403:
            raise DrivePermissionError(r.text)
        r.raise_for_status()


# ── Keeping a file somewhere this house is not ──────────────────────────────
#
# Backups go to Drive, and the direction is the whole security argument. The
# house pushes; nothing online pulls, listens or holds a way back in. There is
# no server of ours out there to break into — only a folder with files in it,
# and those files are encrypted before they leave (see `app/crypt.py`), so what
# a folder holds is ciphertext and a filename.
#
# What this cannot defend against, said plainly: whoever holds the service
# account key can delete these copies as well as write them, and that key is on
# the machine being backed up. Drive's own trash is the only thing standing
# behind that. It protects against losing the machine, which is what backups are
# for; it does not protect against somebody who already owns the machine.


def _write_client(key_path: Path) -> httpx.Client:
    """A client for the one credential allowed to write to Drive."""
    return _client(_Credentials(key_path, SHARE_SCOPES))


def backup_folder_id(key_path: Path, name: str) -> str:
    """The id of the shared folder backups go into, found by name.

    By name rather than by id in the configuration, because an id is a thing
    somebody has to go and find in a URL, and the folder is one they create and
    share by hand. A name is what they will have.
    """
    with _write_client(key_path) as http:
        r = http.get(
            f"{API}/files",
            params={
                "q": (
                    "sharedWithMe and trashed = false "
                    f"and mimeType = '{FOLDER_MIME}' and name = '{name}'"
                ),
                "fields": "files(id,name,capabilities/canAddChildren)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
        )
        r.raise_for_status()
        files = r.json().get("files", [])

    if not files:
        raise DriveError(
            f"no folder named {name!r} is shared with this server. "
            "Create it in Drive and share it with the service account as Editor."
        )
    folder = files[0]
    if not (folder.get("capabilities") or {}).get("canAddChildren"):
        raise DrivePermissionError(
            f"{name!r} is shared as a viewer, so nothing can be written to it. "
            "Share it as Editor instead — a service account has no storage of "
            "its own, so it cannot put a file anywhere it has not been given."
        )
    return folder["id"]


def upload(key_path: Path, folder_id: str, filename: str, path: Path) -> str:
    """Put a file in that folder, replacing one of the same name. Returns its id.

    Resumable rather than multipart: a backup is tens of megabytes and growing,
    and the simple upload endpoint wants the whole body in one request with no
    way to recover a broken one.
    """
    size = path.stat().st_size
    with _write_client(key_path) as http:
        existing = http.get(
            f"{API}/files",
            params={
                "q": f"'{folder_id}' in parents and name = '{filename}' and trashed = false",
                "fields": "files(id)",
                "supportsAllDrives": "true",
            },
        )
        existing.raise_for_status()
        found = existing.json().get("files", [])

        if found:
            file_id = found[0]["id"]
            start = http.patch(
                f"https://www.googleapis.com/upload/drive/v3/files/{file_id}",
                params={"uploadType": "resumable", "supportsAllDrives": "true"},
                headers={"X-Upload-Content-Length": str(size)},
                json={},
            )
        else:
            start = http.post(
                "https://www.googleapis.com/upload/drive/v3/files",
                params={"uploadType": "resumable", "supportsAllDrives": "true"},
                headers={"X-Upload-Content-Length": str(size)},
                json={"name": filename, "parents": [folder_id]},
            )

        if start.status_code in (401, 403):
            raise DrivePermissionError(start.text[:300])
        start.raise_for_status()
        session = start.headers.get("location")
        if not session:
            raise DriveError("Drive did not open an upload session")

        with path.open("rb") as body:
            done = http.put(
                session,
                content=body,
                headers={"Content-Length": str(size)},
                timeout=httpx.Timeout(TIMEOUT, read=300.0, write=300.0),
            )
        done.raise_for_status()
        return done.json().get("id", "")


def list_backups(key_path: Path, folder_id: str) -> list[dict]:
    """What is in the folder, newest first."""
    with _write_client(key_path) as http:
        r = http.get(
            f"{API}/files",
            params={
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "files(id,name,size,modifiedTime)",
                "orderBy": "modifiedTime desc",
                "pageSize": PAGE,
                "supportsAllDrives": "true",
            },
        )
        r.raise_for_status()
        return r.json().get("files", [])


def fetch(key_path: Path, file_id: str, target: Path) -> int:
    """Bring one back down. Returns the bytes written."""
    written = 0
    with _write_client(key_path) as http, target.open("wb") as out:
        with http.stream(
            "GET",
            f"{API}/files/{file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
            timeout=httpx.Timeout(TIMEOUT, read=300.0),
        ) as response:
            if response.status_code != 200:
                raise DriveError(f"download failed: {response.status_code}")
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                out.write(chunk)
                written += len(chunk)
    return written
