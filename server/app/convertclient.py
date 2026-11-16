"""The server's side of the converter (app/converter.py).

The document's bytes go to the converter container and a PDF comes back. There
is deliberately no fallback to converting inside the server: the point of the
converter is that LibreOffice never runs beside the server's keys, and a
fallback would put it back there the first time the converter was down.
"""

from __future__ import annotations

import httpx

from .config import get_settings

# The converter gives up on a document after 120 seconds, so this covers one
# conversion with room to spare. One that also waited long for its turn can be
# cut off here; it reads as "try again in a minute", which by then is true.
TIMEOUT = httpx.Timeout(150.0, connect=5.0)

# Replaced by tests with an httpx.MockTransport.
_transport: httpx.AsyncBaseTransport | None = None


class ConverterUnavailable(Exception):
    """The converter could not be reached, or was too busy to take this one."""


class NotConvertible(Exception):
    """The converter tried and this document cannot be rendered."""


async def to_pdf(data: bytes, ext: str) -> bytes:
    url = get_settings().converter_url.rstrip("/") + "/convert"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport) as http:
            r = await http.post(
                url,
                params={"ext": ext},
                content=data,
                headers={"Content-Type": "application/octet-stream"},
            )
    except httpx.HTTPError as exc:
        raise ConverterUnavailable(type(exc).__name__) from None

    if r.status_code == 200 and r.content.startswith(b"%PDF"):
        return r.content
    if r.status_code in (200, 413, 415, 422):
        raise NotConvertible(str(r.status_code))
    raise ConverterUnavailable(str(r.status_code))
