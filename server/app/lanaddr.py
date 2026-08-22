"""Where this server can be reached on the house network.

`LAN_BASE_URL` is configuration, and configuration goes stale: addresses here
are DHCP, and a power cut moved this machine from .206 to .205, which broke the
address shown for installing the TV app and every address handed to a screen.
Nothing said so — the value was still there, still well-formed, and wrong.

So the configured value is a seed rather than the truth. The server also learns
its own address from requests that arrive over the house network: anything
reaching it at a private IPv4 is telling it, in the Host header, an address that
demonstrably works from somewhere in the house. A television already paired does
this every time it reconnects.

Deliberately not detected from inside the container, which can only see its own
bridge address (172.18.x) and the gateway — neither of which any device on the
house network can use.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time

import httpx

from .config import get_settings

log = logging.getLogger("homesh.lanaddr")

# Just the host — never a port.
#
# A Host header carries whichever port the caller used, so learning the whole
# thing meant the answer flip-flopped between "192.168.x.y" (port 80) and
# "192.168.x.y:8080" depending on which request happened to arrive last. A
# television stores whatever it is told, so an answer that changes between
# probes is worse than one that is merely old.
_seen: tuple[str, float] | None = None

# Whether the configured address answered, last time anything checked. Refreshed
# by a background task rather than on demand: `lan_base()` is called from the
# discovery responder, which runs on the event loop, and a probe there travels
# out of this server and back into it — waiting for a reply that only the loop
# it is blocking can send. It timed out against itself every time, and the log
# said the address did not answer while curl got 200 in 0.16s.
_configured_answers: bool | None = None

CHECK_EVERY = 60.0

# Nothing is probed for the first few seconds of a process.
#
# The address cannot have gone stale in the time it takes to start, and this is
# what keeps short-lived instances from doing network work at all: the test
# suite starts the application several hundred times, and probing on each one
# put a few hundred two-second socket waits into the default executor. One
# startup eventually failed outright — "server disconnected without sending a
# response" — which is a strange way to be told about a watchdog.
FIRST_CHECK_AFTER = 5.0


def _is_house_host(host: str) -> bool:
    """A bare house-network IPv4, with or without a port.

    Private, but not loopback and not link-local: 127.0.0.1 is private by every
    definition and reaches nothing from another room, and it is what a health
    check inside the container reports — so without this the server confidently
    learns an address no television can use.

    Names are not addresses. A ts.net hostname is reachable, but only from the
    tailnet, which a set-top box is not on.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private and not address.is_loopback and not address.is_link_local


def _split(host: str) -> str | None:
    """The bare host from a Host header, when it is one we can use."""
    bare = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return bare if _is_house_host(bare) else None


def _port() -> str:
    """The port to publish, taken from configuration.

    Not from the request: port 80 is published as a convenience for typing an
    address on a remote control, and an address learned there would drop the
    port entirely — leaving a television depending on a mapping that exists for
    somebody's typing comfort.
    """
    configured = (get_settings().lan_base_url or "").strip().rstrip("/")
    _, _, tail = configured.partition("://")
    _, colon, port = tail.partition(":")
    return port if colon and port.isdigit() else "8080"


def note_host(host: str | None) -> None:
    """Record a Host header, if it names us by a house-network address."""
    global _seen
    bare = _split(host) if host else None
    if not bare:
        return
    if _seen is None or _seen[0] != bare:
        log.info("learned a house-network address for this server: %s", bare)
    _seen = (bare, time.monotonic())


def lan_base() -> str | None:
    """The best address a device in the house can use, or None.

    Pure: no network, no blocking. Safe to call from the event loop, which is
    where the discovery responder calls it from.

    The configured one while it answers, because it is what somebody chose. The
    learned one when it does not, because a wrong address is worse than a
    surprising one — being unreachable is the whole failure being fixed.
    """
    configured = (get_settings().lan_base_url or "").strip().rstrip("/")

    if configured and _configured_answers is not False:
        return configured
    if _seen:
        return f"http://{_seen[0]}:{_port()}"
    return configured or None


def _answers(base: str) -> bool:
    """Whether something is actually listening there. Blocking; off-loop only."""
    try:
        with httpx.Client(timeout=2.0) as probe:
            return probe.get(f"{base}/api/health").status_code == 200
    except httpx.HTTPError:
        return False


async def watch() -> None:
    """Keep track of whether the configured address still works.

    On a thread, because the probe re-enters this server: it leaves, comes back
    through the host's published port, and is answered by this same process.
    """
    global _configured_answers
    await asyncio.sleep(FIRST_CHECK_AFTER)
    while True:
        configured = (get_settings().lan_base_url or "").strip().rstrip("/")
        if configured:
            try:
                ok = await asyncio.to_thread(_answers, configured)
            except Exception as exc:  # noqa: BLE001 - a watchdog must not take the server with it
                log.debug("could not check the configured address: %s", exc)
                ok = _configured_answers
            if ok != _configured_answers:
                log.info(
                    "configured address %s %s", configured, "answers" if ok else "does not answer"
                )
            _configured_answers = ok
        # Not suppressed. Swallowing CancelledError here made the loop
        # uncancellable: shutdown cancelled the task, the sleep raised, the
        # exception was eaten and `while True` went round again — so the server
        # hung on exit and the test suite stopped dead after eight tests.
        await asyncio.sleep(CHECK_EVERY)


def forget() -> None:
    """Drop what has been learned. For tests."""
    global _seen, _configured_answers
    _seen = None
    _configured_answers = None
