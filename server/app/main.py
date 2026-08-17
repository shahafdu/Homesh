"""Homesh core API.

Phase 0: the stack stands up, the schema applies, health is observable.
Auth, sources and the control tower land in the phases that follow.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .auth import ensure_bootstrap_code
from .auth import router as auth_router
from .config import get_settings
from .db import check_connection, run_migrations
from .discovery import serve as serve_discovery
from .documents import router as documents_router
from .library import register_sources
from .library import router as library_router
from .people import router as people_router
from .playlists import router as playlists_router
from .prefs import router as prefs_router
from .renderers import router as renderers_router
from .sharing import router as sharing_router
from .stream import router as stream_router
from .transcode import router as transcode_router
from .upkeep import run_forever as scan_forever
from .zones import router as zones_router

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("homesh")


class _SuppressHealthProbes(logging.Filter):
    """Drop successful health-check access lines.

    Compose probes /api/health every 10s. Left alone it buries everything else;
    failures still get through, which is the part worth seeing.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            path, status = args[2], args[4]
            if path == "/api/health" and status == 200:
                return False
        return True


logging.getLogger("uvicorn.access").addFilter(_SuppressHealthProbes())


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Homesh starting — origin=%s rp_id=%s", settings.public_origin, settings.rp_id)

    if not settings.is_configured:
        # Loud, but not fatal: the operator gets a clear health response rather
        # than a container that crash-loops with a stack trace.
        log.error(
            "MASTER_KEY and/or SECRET_KEY are unset. Generate them with:\n"
            '  python -c "import secrets,base64;'
            'print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"'
        )
    else:
        applied = run_migrations()
        if applied:
            log.info("applied migrations: %s", ", ".join(applied))
        else:
            log.info("schema up to date")

        register_sources()

        code = ensure_bootstrap_code()
        if code:
            log.warning(
                "\n"
                "  ┌──────────────────────────────────────────────┐\n"
                "  │  No users yet. First-run bootstrap code:      │\n"
                "  │                                              │\n"
                "  │      %-8s                                │\n"
                "  │                                              │\n"
                "  │  Open %-38s│\n"
                "  │  and register a passkey with this code.      │\n"
                "  └──────────────────────────────────────────────┘",
                code,
                settings.public_origin,
            )

    # Keeps the catalog current on its own. Held so shutdown can cancel it
    # rather than leaving a scan writing into a closing connection pool.
    upkeep = asyncio.create_task(scan_forever())

    # Answers "where is the server?" on the LAN, so a television never has to be
    # told an address with a remote control.
    finder = asyncio.create_task(serve_discovery())

    yield

    for task in (upkeep, finder):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    log.info("Homesh stopped")


app = FastAPI(
    title="Homesh",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# The web client is same-origin in production; this only opens the dev server.
if not settings.secure_cookies:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(library_router)
app.include_router(people_router)
app.include_router(playlists_router)
app.include_router(prefs_router)
app.include_router(sharing_router)
app.include_router(renderers_router)
app.include_router(stream_router)
app.include_router(transcode_router)
app.include_router(zones_router)


@app.get("/api/health")
async def health() -> JSONResponse:
    db_ok = check_connection()
    configured = settings.is_configured

    problems = []
    if not configured:
        problems.append("MASTER_KEY/SECRET_KEY not set")
    if not db_ok:
        problems.append("database unreachable")

    body = {
        "status": "ok" if not problems else "degraded",
        "version": app.version,
        "database": "ok" if db_ok else "unreachable",
        "configured": configured,
        "problems": problems,
    }
    # 200 even when degraded: the endpoint is for humans and Compose alike, and
    # a running-but-misconfigured service is a different thing from a dead one.
    return JSONResponse(body)


# ── The TV app ──────────────────────────────────────────────────────────────
# A set-top box has no easy way to receive a file. It has a network connection to
# this server and a remote control, so the server hands out the APK itself and
# the box fetches it with a URL short enough to type on a D-pad.
#
# Unauthenticated, deliberately: the box has no account and no keyboard worth the
# name, and the payload is the same open-source APK published in the repository.
# It carries no secret, names no address, and grants nothing — installing it
# still requires pairing from a signed-in phone before the screen can play
# anything.

def _tv_apk_path() -> Path:
    """Resolved per request, not at import.

    The APK is built outside the container and appears in a mounted directory.
    Reading it once at startup would mean a server that had to be restarted
    before it would hand out a build made a minute after it booted.
    """
    return Path(os.environ.get("TV_APK_PATH", "/app/tv-app/homesh-tv.apk"))


@app.get("/tv.json", include_in_schema=False)
async def tv_version() -> Response:
    """What build the server is offering.

    The app reads this on launch and updates itself. Unauthenticated for the same
    reason the APK is: a television has no account, and a version number is not a
    secret.
    """
    info = _tv_apk_path().with_name("homesh-tv.json")
    if not info.is_file():
        return JSONResponse({"detail": "no TV app build present"}, status_code=404)
    return FileResponse(info, media_type="application/json")


@app.get("/tv.apk", include_in_schema=False)
async def tv_apk() -> Response:
    apk = _tv_apk_path()
    if not apk.is_file():
        return JSONResponse(
            {
                "detail": "No TV app build present. Run tools/build-tv-apk.sh, "
                "or download it from the repository's releases."
            },
            status_code=404,
        )
    return FileResponse(
        apk,
        media_type="application/vnd.android.package-archive",
        filename="homesh-tv.apk",
    )


# ── Web client ──────────────────────────────────────────────────────────────
# Mounted last so it never shadows /api. Absent in local dev, where Vite serves
# the client on :5173 and proxies /api here.

_STATIC = Path(__file__).resolve().parent.parent / "static"

if _STATIC.is_dir():

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        """Serve built assets, falling back to index.html for client-side routes."""
        # An unmatched /api/* path is a genuine 404, not a client-side route. Without
        # this the catch-all answers unknown API calls with the HTML shell, turning
        # every typo'd endpoint into a confusing 200.
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        candidate = (_STATIC / path).resolve()
        # Reject traversal: the resolved path must stay inside the static root.
        if path and candidate.is_file() and candidate.is_relative_to(_STATIC):
            return FileResponse(candidate)

        # The TV is a second interface to the same system, served from the same
        # origin so it shares cookies and the WebSocket host.
        if path == "tv" or path.startswith("tv/"):
            tv = _STATIC / "tv.html"
            if tv.is_file():
                return FileResponse(tv)

        return FileResponse(_STATIC / "index.html")

else:
    log.warning("no built client at %s — run the web dev server on :5173", _STATIC)
