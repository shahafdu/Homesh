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
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        with self._lock:
            if self._creds is None:
                if not self.key_path.is_file():
                    raise DriveError(
                        f"no service-account key at {self.key_path}. "
                        "Download the JSON key and place it there."
                    )
                self._creds = service_account.Credentials.from_service_account_file(
                    str(self.key_path), scopes=self.scopes
                )
            if not self._creds.valid:
                self._creds.refresh(Request())
            return self._creds.token


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
            existing = httpx.Client(timeout=TIMEOUT, follow_redirects=True)
            self._client = existing
        return existing

    def __init__(self, root_id: str, key_path: Path) -> None:
        self.root_id = root_id
        self.creds = _Credentials(key_path)
        # Path -> Drive id, filled during walk/list so open_range can find a file
        # again without re-walking the tree.
        self._ids: dict[str, str] = {"": root_id}

    @property
    def available(self) -> bool:
        try:
            with _client(self.creds) as client:
                r = client.get(
                    f"{API}/files/{self.root_id}",
                    params={"fields": "id,trashed", "supportsAllDrives": "true"},
                )
            return r.status_code == 200
        except Exception as exc:  # noqa: BLE001 - availability must never raise
            log.debug("drive unavailable: %s", exc)
            return False

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
        with client.stream(
            "GET",
            f"{API}/files/{file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
            headers={"Authorization": f"Bearer {self.creds.token()}", **headers},
        ) as response:
            if response.status_code not in (200, 206):
                raise DriveError(f"download failed: {response.status_code}")
            yield from response.iter_bytes(chunk_size=256 * 1024)

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
