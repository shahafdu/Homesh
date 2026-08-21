"""Viewing documents no browser can open.

PDFs, text and Markdown already display. Everything from an office suite — doc,
docx, xls, xlsx, ppt, pptx, odt, rtf — does not, and never will: those formats
need a layout engine, not a parser.

So the server renders them to PDF and serves that. It is the only approach that
keeps the file at home. Handing the document to Microsoft's or Google's online
viewer would mean uploading a private contract or a payslip to a third party in
order to read it, which is the opposite of why this server exists.

Conversions are cached on disk and keyed by content, so the second look is
instant and a file that never changes is converted exactly once.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import tempfile
from contextlib import closing
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from .access import may_access_item
from .config import get_settings
from .security import CurrentUser, require_user
from .stream import resolve_playable

log = logging.getLogger("homesh.documents")
router = APIRouter(prefix="/api", tags=["documents"])


# What LibreOffice can render faithfully. Deliberately explicit rather than
# "anything it will accept": a format that converts badly is worse than one that
# honestly says it has no preview.
CONVERTIBLE = {
    # Word processing
    "doc", "docx", "odt", "rtf", "dot", "dotx", "wpd",
    # Spreadsheets
    "xls", "xlsx", "ods", "csv", "xlsm", "xlt", "xltx",
    # Presentations
    "ppt", "pptx", "odp", "pps", "ppsx", "pot", "potx",
    # Other office-ish things it handles well
    "abw", "sxw", "fodt", "fods",
}

# A conversion is one process; several at once on four efficiency cores would
# make the whole server unresponsive for whoever is watching something.
_slot = asyncio.Semaphore(2)

CONVERT_TIMEOUT = 120.0

# Bounded because the file is copied to disk before conversion, and because a
# 300 MB spreadsheet is not something anybody is about to read on a phone.
MAX_SOURCE_BYTES = 100 * 1024 * 1024


def convertible(ext: str | None) -> bool:
    return (ext or "").lower().lstrip(".") in CONVERTIBLE


def _cache_dir() -> Path:
    path = Path(get_settings().cache_dir) / "documents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(item_id: UUID, size: int, ext: str) -> Path:
    # Keyed by size as well as id, so a file replaced in place — same path, new
    # contents — is not served from a stale rendering of the old one.
    key = hashlib.sha256(f"{item_id}:{size}:{ext}".encode()).hexdigest()[:32]
    return _cache_dir() / f"{key}.pdf"


async def _run_soffice(source: Path, out_dir: Path) -> Path:
    """Convert one file, in its own profile directory.

    LibreOffice keeps a per-user profile and refuses to run two instances that
    share one. A throwaway profile per conversion is the documented way to run it
    as a service, and it means a crashed conversion cannot poison the next.
    """
    with tempfile.TemporaryDirectory(prefix="homesh-lo-") as profile:
        proc = await asyncio.create_subprocess_exec(
            "soffice",
            f"-env:UserInstallation=file://{profile}",
            "--headless",
            "--norestore",
            "--invisible",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(source),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=CONVERT_TIMEOUT)
        except TimeoutError as exc:
            proc.kill()
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT, "that document took too long to convert"
            ) from exc

    if proc.returncode != 0:
        log.warning("soffice failed: %s", err.decode(errors="replace")[:400])
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "that document could not be converted")

    produced = list(out_dir.glob("*.pdf"))
    if not produced:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "the converter produced nothing")
    return produced[0]


@router.get("/documents/{item_id}")
async def document_pdf(item_id: UUID, user: CurrentUser = Depends(require_user)) -> FileResponse:
    """The document as a PDF, converting and caching on first request."""
    if not may_access_item(item_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")

    connector, rel_path, filename, size, ext = resolve_playable(item_id)

    if not convertible(ext):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            f".{ext} is not a document this can render")

    if size and size > MAX_SOURCE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            "that document is too large to render — download it instead")

    cached = _cache_path(item_id, size or 0, ext or "")
    if cached.is_file():
        return FileResponse(cached, media_type="application/pdf", filename=f"{filename}.pdf")

    async with _slot:
        # Re-checked inside the semaphore: several viewers opening the same file
        # would otherwise all queue up to convert something already done.
        if cached.is_file():
            return FileResponse(cached, media_type="application/pdf", filename=f"{filename}.pdf")

        with tempfile.TemporaryDirectory(prefix="homesh-doc-") as work:
            work_dir = Path(work)
            # Suffix matters: LibreOffice picks its import filter from it.
            source = work_dir / f"input.{(ext or 'bin').lower()}"

            # Through the connector, so this works for a Drive file exactly as
            # it does for a local one — and on a thread, because for a Drive file
            # that is a whole document fetched over the network, which on the
            # event loop would stop the server answering anything else.
            def fetch() -> None:
                with (
                    source.open("wb") as fh,
                    closing(connector.open_range(rel_path)) as chunks,
                ):
                    for chunk in chunks:
                        fh.write(chunk)

            await asyncio.to_thread(fetch)

            out_dir = work_dir / "out"
            out_dir.mkdir()
            produced = await _run_soffice(source, out_dir)

            # Move into place only once it is complete, so a crash mid-write
            # cannot leave a truncated PDF cached forever.
            shutil.move(str(produced), str(cached))

    log.info("converted %s to PDF", filename)
    return FileResponse(cached, media_type="application/pdf", filename=f"{filename}.pdf")
