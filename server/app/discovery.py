"""Answering "where is the server?" so nobody has to type it.

Setting up a television meant entering an address on an on-screen keyboard with
a remote control — dozens of presses to spell out something like
http://192.0.2.14:8080, twice: once for the installer and once for the app. It is
the worst part of using this, and it is entirely avoidable: the server and the
screen are on the same network, so the screen can simply ask.

A UDP broadcast, not mDNS. mDNS would mean a dependency and a daemon; this is
twenty lines, works on a bare Android device with no library, and answers only on
the local network to a packet that names this application.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket

from . import lanaddr
from .config import get_settings

log = logging.getLogger("homesh.discovery")

# Chosen from the unassigned range and specific to this application, so a stray
# broadcast from something else on the network is ignored rather than answered.
PORT = 45877

# The probe a client sends. Matching on it means we never reply to unrelated
# traffic that happens to arrive on this port.
PROBE = b"HOMESH-DISCOVER-V1"


class _Responder(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # noqa: ANN001
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        if data.strip() != PROBE:
            return

        settings = get_settings()
        # The address a device on the LAN can actually reach. Configuration, as
        # everywhere else — the server does not guess its own address, and
        # localhost would mean nothing to a television.
        base = lanaddr.lan_base() or settings.public_origin

        reply = json.dumps({"service": "homesh", "url": base, "name": settings.rp_name})
        log.info("discovery probe from %s — replying %s", addr[0], base)
        if self.transport is not None:
            self.transport.sendto(reply.encode(), addr)


async def serve() -> None:
    """Listen for probes until cancelled."""
    settings = get_settings()
    if not (settings.lan_base_url or "").strip():
        # With nothing useful to answer, staying quiet is better than telling
        # every television in the house to talk to localhost.
        log.info("discovery disabled: LAN_BASE_URL is not set")
        return

    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    try:
        sock.bind(("0.0.0.0", PORT))  # noqa: S104 - a LAN responder must hear broadcasts
    except OSError as exc:
        log.warning("discovery could not bind udp/%d: %s", PORT, exc)
        sock.close()
        return

    transport, _protocol = await loop.create_datagram_endpoint(_Responder, sock=sock)
    log.info("discovery listening on udp/%d", PORT)

    try:
        await asyncio.Event().wait()
    finally:
        with contextlib.suppress(Exception):
            transport.close()
