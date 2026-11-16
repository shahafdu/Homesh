"""Viewing documents no browser can open.

PDFs, text and Markdown already display. Everything from an office suite — doc,
docx, xls, xlsx, ppt, pptx, odt, rtf — does not, and never will: those formats
need a layout engine, not a parser.

So the server renders them to PDF and serves that. It is the only approach that
keeps the file at home. Handing the document to Microsoft's or Google's online
viewer would mean uploading a private contract or a payslip to a third party in
order to read it, which is the opposite of why this server exists.

The rendering itself happens in the converter container (converter.py), never
in this process: LibreOffice is a large parser fed documents that anyone with
edit rights on a shared folder can supply, and this process holds every key the
house has. Here the document is only fetched, sent, and the PDF cached.

Conversions are cached on disk and keyed by content, so the second look is
instant and a file that never changes is converted exactly once.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from . import convertclient
from .access import may_access_item
from .config import get_settings
from .docformats import convertible
from .security import CurrentUser, optional_user
from .signing import TokenError, verify
from .stream import resolve_playable

log = logging.getLogger("homesh.documents")
router = APIRouter(prefix="/api", tags=["documents"])


# A conversion holds the whole document in memory, here and in the converter,
# which takes one at a time anyway; two waiting is plenty.
_slot = asyncio.Semaphore(2)

# The converter refuses anything larger (MAX_INPUT in converter.py). Checked
# here too so that a person is told to download it, rather than that it failed.
MAX_SOURCE_BYTES = 60 * 1024 * 1024

UNAVAILABLE = "Documents can't be shown right now. Try again in a minute."
FAILED = "that document could not be converted"
TOO_LARGE = "that document is too large to render — download it instead"


def _cache_dir() -> Path:
    path = Path(get_settings().cache_dir) / "documents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(item_id: UUID, size: int, ext: str) -> Path:
    # Keyed by size as well as id, so a file replaced in place — same path, new
    # contents — is not served from a stale rendering of the old one.
    key = hashlib.sha256(f"{item_id}:{size}:{ext}".encode()).hexdigest()[:32]
    return _cache_dir() / f"{key}.pdf"


@router.get("/documents/{item_id}")
async def document_pdf(
    item_id: UUID,
    t: str | None = Query(None),
    user: CurrentUser | None = Depends(optional_user),
) -> FileResponse:
    """The document as a PDF, converting and caching on first request.

    Takes either a session or a signed token, the same bargain /stream and
    /thumb already make. A television has no session -- it authenticates as a
    renderer and fetches media by signed URL -- so requiring one here is what
    made a document the one kind of file that could not be sent to a room.
    """
    viewer = user.id if user else None
    if viewer is None and t:
        try:
            claim = verify(t, "stream")
            if claim.item_id == item_id:
                viewer = claim.user_id
        except TokenError:
            viewer = None

    if viewer is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")

    # The same scope check either way: a signed URL carries the account it was
    # minted for, so a token cannot reach further than the person it was made
    # for could reach themselves.
    if not may_access_item(item_id, viewer):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    connector, rel_path, filename, size, ext = resolve_playable(item_id)

    if not convertible(ext):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            f".{ext} is not a document this can render")

    if size and size > MAX_SOURCE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LARGE)

    cached = _cache_path(item_id, size or 0, ext or "")
    if cached.is_file():
        return FileResponse(cached, media_type="application/pdf", filename=f"{filename}.pdf")

    async with _slot:
        # Re-checked inside the semaphore: several viewers opening the same file
        # would otherwise all queue up to convert something already done.
        if cached.is_file():
            return FileResponse(cached, media_type="application/pdf", filename=f"{filename}.pdf")

        try:
            await convertclient.ensure_available()
        except convertclient.ConverterUnavailable as exc:
            log.warning("the converter is unavailable (%s)", exc)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None

        # Through the connector, so this works for a Drive file exactly as it
        # does for a local one -- and on a thread, because for a Drive file that
        # is a whole document fetched over the network, which on the event loop
        # would stop the server answering anything else.
        def fetch() -> bytes:
            data = bytearray()
            with contextlib.closing(connector.open_range(rel_path)) as chunks:
                for chunk in chunks:
                    data += chunk
                    if len(data) > MAX_SOURCE_BYTES:
                        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                                            TOO_LARGE)
            return bytes(data)

        data = await asyncio.to_thread(fetch)

        try:
            pdf = await convertclient.to_pdf(data, (ext or "").lower())
        except convertclient.ConverterUnavailable as exc:
            log.warning("the converter is unavailable (%s)", exc)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None
        except convertclient.NotConvertible as exc:
            log.info("the converter could not render a .%s (%s)", ext, exc)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, FAILED) from None

        # Written beside the cache and moved into place only once complete, so
        # a crash mid-write cannot leave a truncated PDF cached forever.
        fd, partial = tempfile.mkstemp(dir=cached.parent, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(pdf)
            os.replace(partial, cached)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(partial)
            raise

    log.info("converted %s to PDF", filename)
    return FileResponse(cached, media_type="application/pdf", filename=f"{filename}.pdf")
