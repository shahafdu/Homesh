"""The converter: office documents in, PDFs out, in a container of its own.

LibreOffice has a long record of bugs in which a crafted document reads local
files or runs code, and anyone with edit rights on a shared Drive folder can put
a document in the library. So it does not run beside the server's keys. It runs
here, in a container with no secrets, no library, no database and no route to
the internet (docker-compose.yml), and the server sends it bytes over a network
that only the server can reach.

What a hostile document can still do is described in
docs/design/06-library-upkeep.md section 6.6: tamper with the one PDF it
returns. The process group is killed after every conversion, so nothing it
starts survives to see the next document.

This module imports nothing from the application except the environment and
format list, both of which import nothing themselves. Run with
``python -m app.converter``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import Response

from .childenv import child_env
from .docformats import CONVERTIBLE

log = logging.getLogger("homesh.converter")

PORT = 8091

# The largest document accepted. The server refuses larger ones before sending
# them, with a message saying to download instead; this is the second lock.
MAX_INPUT = 60 * 1024 * 1024
# A PDF this large from a 60 MB document is something having gone wrong.
MAX_OUTPUT = 300 * 1024 * 1024

CONVERT_TIMEOUT = 120.0
# How long a document waits for the one before it. Past this the server is told
# to try again later, rather than holding its request open indefinitely.
WAIT_FOR_TURN = 60.0

# One at a time: the container has one CPU and a gigabyte, and LibreOffice
# refuses two instances sharing a profile in any case.
_turn = asyncio.Lock()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _command(source: Path, out_dir: Path, profile: Path) -> list[str]:
    """The command line. Every part is ours: the input is always input.<ext>.

    A throwaway profile per conversion is the documented way to run LibreOffice
    as a service, and it means a crashed conversion cannot poison the next.
    """
    return [
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
    ]


def _kill_group(pid: int) -> None:
    # The group, not the process: soffice starts children of its own, and a
    # document that has taken one over must not leave it running.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


async def run_soffice(source: Path, out_dir: Path, profile: Path) -> str:
    """Convert one file. Returns "ok", "failed" or "timeout"."""
    proc = await asyncio.create_subprocess_exec(
        *_command(source, out_dir, profile),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=child_env(HOME=str(profile)),
        start_new_session=True,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=CONVERT_TIMEOUT)
    except TimeoutError:
        return "timeout"
    finally:
        _kill_group(proc.pid)
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
    return "ok" if proc.returncode == 0 else "failed"


def _refuse() -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "could not convert this document")


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_INPUT:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "document too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_INPUT:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "document too large")
    return bytes(body)


async def _convert(data: bytes, ext: str) -> bytes:
    work = Path(tempfile.mkdtemp(prefix="convert-"))
    try:
        source = work / f"input.{ext}"
        source.write_bytes(data)
        out_dir = work / "out"
        out_dir.mkdir()
        profile = work / "profile"
        profile.mkdir()

        outcome = await run_soffice(source, out_dir, profile)
        if outcome != "ok":
            raise _OutcomeError(outcome)

        produced = out_dir / "input.pdf"
        if not produced.is_file():
            raise _OutcomeError("no output")
        if produced.stat().st_size > MAX_OUTPUT:
            raise _OutcomeError("output too large")
        return produced.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)


class _OutcomeError(Exception):
    pass


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/convert")
async def convert(request: Request, ext: str = Query(..., max_length=8)) -> Response:
    ext = ext.lower()
    if ext not in CONVERTIBLE:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "not a format this converts")

    data = await _read_body(request)

    try:
        await asyncio.wait_for(_turn.acquire(), timeout=WAIT_FOR_TURN)
    except TimeoutError as exc:
        log.info("convert %s: %d bytes, busy", ext, len(data))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "busy, try again") from exc

    started = time.monotonic()
    try:
        pdf = await _convert(data, ext)
    except _OutcomeError as exc:
        log.info("convert %s: %d bytes, %.1fs, %s", ext, len(data),
                 time.monotonic() - started, exc)
        raise _refuse() from None
    finally:
        _turn.release()

    log.info("convert %s: %d bytes -> %d bytes, %.1fs, ok", ext, len(data), len(pdf),
             time.monotonic() - started)
    return Response(pdf, media_type="application/pdf")


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # Every interface inside its container, which sits on an internal network
    # that only the server is on; nothing publishes this port.
    uvicorn.run(app, host="0.0.0.0", port=PORT, access_log=False)  # noqa: S104
