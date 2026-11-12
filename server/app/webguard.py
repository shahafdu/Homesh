"""What the browser is told, and which browser requests are believed.

Three small guards that sit in front of every route.

**Headers.** A content security policy, a framing rule, ``nosniff`` and a
referrer policy. The framing rule is the one with teeth: without it any page
could load the control tower in an invisible frame and borrow a click.

**Writes from another origin are refused.** ``SameSite=Lax`` keeps other
*sites* from sending the session cookie with a write, but a page on the same
host and a different port counts as the same site, and the cookie goes with it.
So a write is accepted only when the browser says it came from this origin, or
says nothing at all -- which is what the TV, the phone app's native calls,
ffmpeg reading through the stream endpoint and the standby's in-process replay
all do. A browser always names the origin of a cross-origin write, so silence
is never a browser being tricked.

**No query strings in the access log.** A television's socket and every
signed media link carry their credential in the query string, and uvicorn logs
the whole path. A log is the thing most likely to be copied somewhere, so the
query is cut before the line is written.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Script from this origin, and the Cast sender, which Google serves and which
# loads its framework from the same place. Everything else from here only.
#
# 'unsafe-inline' for styles because React writes style attributes and the
# print page is a srcdoc frame with its own <style>; neither is a script.
# 'wasm-unsafe-eval' because pdf.js decodes some images with WebAssembly.
# ws:/wss: spelled out because older Android WebViews -- which is what a TV box
# runs -- do not count a WebSocket to this host as 'self'.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'wasm-unsafe-eval' https://www.gstatic.com",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self' data:",
        "connect-src 'self' ws: wss: https://www.gstatic.com",
        "worker-src 'self' blob:",
        "frame-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        # 'self' rather than 'none': printing puts a file of ours in a frame.
        "frame-ancestors 'self'",
    ]
)

HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    # Signed links live in URLs; a referrer would carry one to wherever a
    # link on the page led.
    "Referrer-Policy": "same-origin",
}

_WRITES = {"POST", "PUT", "PATCH", "DELETE"}


def add_security_headers(response: Response) -> Response:
    for name, value in HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def write_is_from_elsewhere(request: Request, allowed: tuple[str, ...] = ()) -> bool:
    """True for a browser write whose page is not on this origin.

    Compared as host and port, not scheme: the tailnet address arrives through
    a proxy that ends TLS, so the server sees http for a page that was https.
    The port is what separates Homesh from an app beside it on the same host,
    and the port is kept.
    """
    if request.method not in _WRITES:
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return False
    if origin in allowed:
        return False
    if origin == "null":
        return True
    theirs = urlsplit(origin)
    netloc = (theirs.netloc or "").lower()
    if theirs.scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[: -len(":443")]
    elif theirs.scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[: -len(":80")]
    ours = {
        (request.headers.get("host") or "").lower(),
        (request.headers.get("x-forwarded-host") or "").lower(),
    } - {""}
    return netloc not in ours


def refuse_cross_origin() -> Response:
    return JSONResponse(
        {"detail": "this request came from another page and was refused"}, status_code=403
    )


class RedactQueries(logging.Filter):
    """Cut the query string from uvicorn's access lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2]
            if "?" in path:
                record.args = (*args[:2], path.split("?", 1)[0] + "?redacted", *args[3:])
        return True
