"""Media streaming with HTTP range support.

Range requests are what make seeking work and what let a player start mid-file
instead of buffering from zero. Direct play means these bytes are the original
file's, untouched (ARCHITECTURE.md §3.2).
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from .access import may_access_item
from .config import get_settings
from .db import get_engine
from .security import CurrentUser, optional_user, require_user
from .signing import TokenError, mint, verify
from .thumbs import ThumbError, cache_path, generate, is_absent_marker, mark_absent

# Bounded, because the point is not to trade a frozen loop for forty
# simultaneous downloads. Enough to fill a screen of tiles briskly, few enough
# that Drive is not hammered and the machine stays responsive.
_thumb_slots = asyncio.Semaphore(4)

log = logging.getLogger("homesh.stream")
router = APIRouter(prefix="/api", tags=["stream"])

# Content types we serve. Anything unknown is sent as a download rather than
# guessed at, so a browser never tries to render something unexpected.
MIME = {
    "mp3": "audio/mpeg", "flac": "audio/flac", "m4a": "audio/mp4", "aac": "audio/aac",
    "ogg": "audio/ogg", "opus": "audio/opus", "wav": "audio/wav", "wma": "audio/x-ms-wma",
    "mp4": "video/mp4", "m4v": "video/mp4", "mkv": "video/x-matroska", "webm": "video/webm",
    "mov": "video/quicktime", "avi": "video/x-msvideo", "ts": "video/mp2t",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
    "webp": "image/webp", "heic": "image/heic", "heif": "image/heif", "bmp": "image/bmp",
    "tif": "image/tiff", "tiff": "image/tiff",
    "pdf": "application/pdf", "epub": "application/epub+zip",
    "txt": "text/plain; charset=utf-8", "md": "text/plain; charset=utf-8",
}

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _disposition(filename: str, attachment: bool) -> str:
    """Build a Content-Disposition header that survives non-ASCII filenames.

    A bare `filename="שיר בעברית.mp3"` is not valid in an HTTP header, and browsers
    handle it inconsistently. RFC 6266 gives a `filename*` form for exactly this;
    the plain parameter stays as a sanitised fallback for older clients.
    """
    kind = "attachment" if attachment else "inline"
    ascii_name = filename.encode("ascii", errors="replace").decode("ascii")
    # Quotes and backslashes would terminate or escape the quoted string.
    ascii_name = ascii_name.replace("\\", "_").replace('"', "_")
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def resolve_playable(item_id: UUID) -> tuple[object, str, str, int, str]:
    """Pick a reachable replica for an item.

    An item can exist in several places — the RAID and Drive — and preferring
    whichever answers is exactly what keeps cloud copies playing while the PC is
    off (§4). The connector kind is decided per source, so this works the same
    for a local file and a Drive one.

    Returns (connector, relative path, filename, size, extension).
    """
    from .library import connector_for

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT r.dir_path, r.filename, r.ext, i.size_bytes,
                       s.id, r.available
                FROM replicas r
                JOIN items i   ON i.id = r.item_id
                JOIN sources s ON s.id = r.source_id
                WHERE r.item_id = :id
                ORDER BY r.available DESC
                """
            ),
            {"id": str(item_id)},
        ).all()

    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    # Ordered available-first by the query, so the first reachable source wins.
    for dir_path, filename, ext, size, source_id, _available in rows:
        connector = connector_for(source_id)
        if connector is None or not connector.available:
            continue
        rel = f"{dir_path}/{filename}" if dir_path else filename
        return connector, rel, filename, size or 0, (ext or "")

    # The catalog knows the file; the machine holding it is not reachable.
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "this file is on a source that is currently offline",
    )


@router.get("/items/{item_id}/url")
async def signed_url(item_id: UUID, user: CurrentUser = Depends(require_user)) -> dict:
    """Mint a short-lived URL a player (or a Cast receiver) can fetch directly."""
    with get_engine().connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM items WHERE id = :id"), {"id": str(item_id)}
        ).first()
    if not exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    if not may_access_item(item_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    ttl = get_settings().media_url_ttl_minutes * 60
    return {
        "url": f"/api/stream/{item_id}?t={mint(item_id, user.id, 'stream')}",
        "expires_in": ttl,
    }


@router.get("/thumb/{item_id}")
async def thumbnail(
    item_id: UUID,
    size: str = Query("small", pattern="^(small|large)$"),
    t: str | None = Query(None),
    user: CurrentUser | None = Depends(optional_user),
) -> Response:
    """Serve a cached thumbnail, generating it on first request.

    Accepts either a session (the web client, whose <img> tags carry cookies) or a
    signed thumb token (a TV app or Cast receiver, which has no session).
    """
    authorised = user is not None
    if not authorised and t:
        try:
            claim = verify(t, "thumb")
            authorised = claim.item_id == item_id
        except TokenError:
            authorised = False
    if not authorised:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")

    # A thumbnail is a small copy of the thing itself, so it needs the same scope
    # check — otherwise a restricted account could browse the library in
    # miniature.
    viewer = user.id if user else (verify(t, "thumb").user_id if t else None)
    if viewer and not may_access_item(item_id, viewer):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    path = cache_path(item_id, size)

    if not path.exists():
        with get_engine().connect() as conn:
            kind = conn.execute(
                text("SELECT kind::text FROM items WHERE id = :id"), {"id": str(item_id)}
            ).scalar_one_or_none()
        if kind is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

        try:
            connector, rel, _filename, _size, _ext = resolve_playable(item_id)
            # On a thread, and only a few at a time.
            #
            # Generating a thumbnail for a Drive file fetches bytes over the
            # network, synchronously. Called directly from an async endpoint it
            # holds the event loop for the whole round trip — and a folder opened
            # in tiles view asks for forty at once, which stops the server
            # answering anything at all. That is exactly how it hung.
            async with _thumb_slots:
                await asyncio.to_thread(generate, item_id, kind, connector, rel, size)
        except HTTPException:
            # Source offline — do not cache that as "no artwork"; it may come back.
            raise
        except ThumbError as exc:
            log.debug("no thumbnail for %s: %s", item_id, exc)
            mark_absent(item_id, size)
        except Exception:  # noqa: BLE001 - a broken file must not break the listing
            log.exception("thumbnail generation failed for %s", item_id)
            mark_absent(item_id, size)

    if not path.exists() or is_absent_marker(path):
        # 404 rather than a placeholder image: the client already knows how to draw
        # a kind icon, and it can cache that decision.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no thumbnail")

    return Response(
        content=path.read_bytes(),
        media_type="image/webp",
        headers={
            # Thumbnails are immutable for a given item, so let the browser keep
            # them; private because the catalog is not public.
            "Cache-Control": "private, max-age=604800",
        },
    )


@router.get("/stream/{item_id}")
async def stream(
    item_id: UUID,
    request: Request,
    t: str = Query(...),
    download: bool = Query(False, description="Send as an attachment rather than inline"),
) -> Response:
    """Serve file bytes. Authorised by the signed token, not by session cookie.

    `download=1` only changes the Content-Disposition header. The bytes are
    identical either way, so it grants nothing extra — it just tells the browser
    to save the file rather than try to display it, which is what makes formats
    we cannot preview (spreadsheets, presentations) still useful.
    """
    try:
        claim = verify(t, "stream")
    except TokenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"invalid media token: {exc}") from exc

    if claim.item_id != item_id:
        # A token for one item must not unlock another.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "token does not match item")

    # Checked again at serving time, not only at minting time. The token names
    # the user it was issued to, so access revoked in between takes effect on the
    # next request rather than whenever the token happens to expire.
    if not may_access_item(item_id, claim.user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not available to this account")

    connector, rel, filename, size, ext = resolve_playable(item_id)
    media_type = MIME.get(ext.lower(), "application/octet-stream")

    start, end = 0, size - 1
    status_code = status.HTTP_200_OK
    headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        # Media URLs are per-user and short-lived, so they must never be cached
        # by a shared proxy.
        "Cache-Control": "private, max-age=0, no-store",
        "Content-Disposition": _disposition(filename, attachment=download),
    }

    range_header = request.headers.get("range")
    if range_header and size:
        m = _RANGE.match(range_header.strip())
        if not m:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed Range header")

        raw_start, raw_end = m.group(1), m.group(2)
        if raw_start == "":
            # "bytes=-500" means the final 500 bytes.
            length = int(raw_end or 0)
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(raw_start)
            end = int(raw_end) if raw_end else size - 1

        if start >= size:
            # Literal 416: the Starlette constant was renamed between versions.
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{size}"},
            )

        end = min(end, size - 1)
        status_code = status.HTTP_206_PARTIAL_CONTENT
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    headers["Content-Length"] = str(end - start + 1) if size else "0"

    return StreamingResponse(
        connector.open_range(rel, start, end),
        status_code=status_code,
        media_type=media_type,
        headers=headers,
    )
