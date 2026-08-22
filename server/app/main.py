"""Homesh core API.

Phase 0: the stack stands up, the schema applies, health is observable.
Auth, sources and the control tower land in the phases that follow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from . import lanaddr
from .auth import ensure_bootstrap_code
from .auth import router as auth_router
from .config import get_settings
from .db import check_connection, run_migrations
from .discovery import serve as serve_discovery
from .documents import router as documents_router
from .library import register_sources
from .library import router as library_router
from .occupancy import refresh_loop as watch_receiver
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

    # Keeps the receiver's state current so the control tower can answer from
    # memory instead of waiting on the hardware.
    watcher = asyncio.create_task(watch_receiver())

    # Notices when the configured LAN address stops answering — a DHCP lease
    # moving is the ordinary case — so the address handed to screens can fall
    # back to one that devices are demonstrably reaching us at.
    address = asyncio.create_task(lanaddr.watch())

    yield

    for task in (upkeep, finder, watcher, address):
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


@app.middleware("http")
async def learn_our_address(request, call_next):
    """Notice how devices in the house reach us.

    A DHCP lease moves and LAN_BASE_URL is silently wrong; anything arriving at
    a private address is evidence of one that works. Cheap: a header read.
    """
    lanaddr.note_host(request.headers.get("host"))
    return await call_next(request)


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


@app.get("/tv.address", include_in_schema=False)
def tv_address() -> Response:
    """Where a television can reach this server.

    Not `window.location.origin`, which is whatever the phone in your hand is
    using. On a tailnet that is a ts.net name, and a set-top box is not on the
    tailnet — so the address shown for the app was one the television could not
    resolve at all, which is exactly the ERR_NAME_NOT_RESOLVED it reported.

    A television is on the house network, so it needs the house address. That is
    configuration, never written down here.
    """
    lan = lanaddr.lan_base() or ""
    configured = (get_settings().lan_base_url or "").strip().rstrip("/")
    return JSONResponse(
        {
            "lan": lan or None,
            # Said out loud when the two differ, because the difference is the
            # bug being reported: an address that was right when it was written
            # down and is not any more.
            "stale_config": bool(configured and lan and configured != lan),
            "short": _short_address(lan),
            # Worth distinguishing: no LAN address configured is a different
            # problem from a television that cannot reach one.
            "detail": None
            if lan
            else "No house-network address known yet. Set LAN_BASE_URL, or open "
            "Homesh once from a device on your home network.",
        }
    )


@lru_cache(maxsize=4)
def _short_address(lan: str) -> str | None:
    """The same server without the port, when port 80 really does answer.

    Deliberately a plain `def`, called from a plain `def` endpoint so FastAPI
    runs the pair in its threadpool. The probe travels out to the host and back
    into this very server, so on the event loop it would be waiting for a reply
    that only the event loop can send — a request that times out against
    itself. That is not hypothetical: written as `async def` this returned null
    every time, while the identical probe from a shell returned 200 in 0.18s.

    Cached, because the answer is a property of the deployment and this is asked
    every time somebody opens the pairing panel.
    """
    if not lan.endswith(":8080"):
        return None
    bare = lan[: -len(":8080")]
    try:
        with httpx.Client(timeout=1.5) as probe:
            if probe.get(f"{bare}/api/health").status_code == 200:
                return bare
    except httpx.HTTPError:
        pass
    return None


@app.get("/apk", include_in_schema=False)
async def tv_apk_short() -> Response:
    """The same download, at an address short enough to type on a remote.

    Every character matters here: this is typed with a d-pad on an on-screen
    keyboard, and a browser handed something long and unfamiliar offers to
    search for it instead — which is what produced a Google results page where
    the app should have been.

    **Not `/tv`.** That is the TV interface itself, which is what the installed
    app loads on every launch. Putting the download there pointed every screen
    in the house at an APK instead of a page: black screen, no request the
    server could even log as wrong. The two are one character apart and could
    not be more different.
    """
    return RedirectResponse("/tv.apk", status_code=307)


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
    # The version is in the filename, and nothing may cache it.
    #
    # Downloader — the tool used to install this on a set-top box — saves to the
    # name the server gives and offers to install whatever is already there
    # under that name. With every build called homesh-tv.apk, a box that had
    # ever downloaded one could quietly reinstall the old file and report
    # success, which is a full day of testing an app that never changed. A name
    # that carries the version cannot be confused with an earlier one.
    version = "unknown"
    info = apk.with_name("homesh-tv.json")
    if info.is_file():
        with suppress(Exception):
            version = json.loads(info.read_text()).get("versionName", "unknown")

    return FileResponse(
        apk,
        media_type="application/vnd.android.package-archive",
        filename=f"homesh-tv-{version}.apk",
        headers={"Cache-Control": "no-store, must-revalidate"},
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
