"""Where this server can be reached on the house network.

`LAN_BASE_URL` is configuration, and configuration goes stale: addresses here
are DHCP, and a power cut moved this machine from .206 to .205, which broke the
address shown for installing the TV app and every address handed to a screen.
Nothing said so — the value was still there, still well-formed, and wrong.

So the configured value is a seed rather than the truth. The server also learns
its own address from requests that arrive over the house network: anything
reaching it at a private IPv4 address is telling it, in the Host header, an
address that demonstrably works from somewhere in the house. A television
already paired does this every time it reconnects.

Deliberately not detected from inside the container, which can only see its own
bridge address (172.18.x) and the gateway — neither of which any device on the
house network can use.
"""

from __future__ import annotations

import ipaddress
import logging
import time

import httpx

from .config import get_settings

log = logging.getLogger("homesh.lanaddr")

# The most recent address something on the house network used to reach us, and
# when. In memory: a stale address in a database outlives the lease that made it
# true, and re-learning costs one request.
_seen: tuple[str, float] | None = None

# How long a probe of the configured address is trusted. Long enough not to
# probe on every pairing panel; short enough that a lease change is noticed
# within the evening it happens.
_PROBE_TTL = 300.0
_probe: tuple[str | None, float] | None = None


def _is_house_host(host: str) -> bool:
    """A bare house-network IPv4, with or without a port.

    Private, but not loopback and not link-local: 127.0.0.1 is private by every
    definition and reaches nothing from another room, and it is what a health
    check inside the container reports — so without this the server confidently
    learns an address no television can use.

    Names are not addresses. A ts.net hostname is reachable, but only from the
    tailnet, which a set-top box is not on.
    """
    bare = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    try:
        address = ipaddress.ip_address(bare)
    except ValueError:
        return False
    return address.is_private and not address.is_loopback and not address.is_link_local


def note_host(host: str | None) -> None:
    """Record a Host header, if it names us by a house-network address."""
    global _seen
    if not host or not _is_house_host(host):
        return
    if not host.startswith("http"):
        host = f"http://{host}"
    if _seen is None or _seen[0] != host:
        log.info("learned a house-network address for this server: %s", host)
    _seen = (host.rstrip("/"), time.monotonic())


def _answers(base: str) -> bool:
    """Whether something is actually listening there, from in here."""
    try:
        with httpx.Client(timeout=1.5) as probe:
            return probe.get(f"{base}/api/health").status_code == 200
    except httpx.HTTPError:
        return False


def lan_base() -> str | None:
    """The best address a device in the house can use, or None.

    The configured one while it answers, because it is what somebody chose. The
    learned one when it does not, because a wrong address is worse than a
    surprising one — and being unreachable is the whole failure being fixed.

    Deliberately a plain function called from plain endpoints: the probe leaves
    this server and comes back into it, so on the event loop it would wait for a
    reply only the event loop can send.
    """
    global _probe
    configured = (get_settings().lan_base_url or "").strip().rstrip("/")

    if configured:
        now = time.monotonic()
        if _probe is None or _probe[1] < now - _PROBE_TTL or _probe[0] != configured:
            _probe = (configured if _answers(configured) else None, now)
        if _probe[0]:
            return configured
        log.warning("LAN_BASE_URL (%s) does not answer; using what devices report", configured)

    if _seen:
        return _seen[0]
    return configured or None


def forget() -> None:
    """Drop what has been learned. For tests."""
    global _seen, _probe
    _seen = None
    _probe = None
