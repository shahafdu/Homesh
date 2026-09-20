"""Attempt limits on the doors that anybody can knock on.

Sign-in, first-run setup and code claiming are reachable without an account, and
each one does real work: a WebAuthn check costs CPU, a challenge costs memory,
an attempt costs a row in the audit log. Without a limit, one machine can spend
all three, and can guess a short code at whatever rate the server answers.

**What a key is here, honestly.** Attempts are counted per caller address, and on
this deployment that address is nearly always the same one: the containers reach
the outside through a port mapping that rewrites the source, so a phone on the
LAN, a laptop on the tailnet and the server itself all arrive looking alike.
`X-Forwarded-For` is not consulted, because nothing between the network and the
app sets it and anybody on the LAN could send one -- trusting it would hand out
a fresh allowance per forged header, which is worse than counting coarsely.

So in this house the limits are effectively per server, not per caller, and the
numbers are chosen for that: a household signs in a handful of times a day, so
twenty failed sign-ins in five minutes is far beyond ordinary use and far below
what guessing needs. Where the real address is visible -- a deployment without
that port mapping -- the same limits apply per address and are more generous
still. What this cannot do is stop one machine on your own network from
exhausting an allowance and making sign-in wait; that is a nuisance from inside
the house rather than a way in, and it is bounded by the window.

Counted in memory, deliberately: the state is worth nothing after a few minutes
and a database round trip per knock would itself be the cost an attacker wants.
"""

from __future__ import annotations

import logging
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, status

log = logging.getLogger("homesh.throttle")


@dataclass(frozen=True)
class Limit:
    """How many attempts, and over how long."""

    allowed: int
    per: timedelta


# Sign-in itself. Twenty failures in five minutes is a household having a bad
# day with a fingerprint reader; it is nothing like a search for a credential.
SIGN_IN = Limit(20, timedelta(minutes=5))

# Asking for a challenge. Cheap, and needed twice per sign-in while devices hold
# passkeys under two names, so the allowance is wide -- it exists to bound the
# memory a stranger can make the server hold, not to ration sign-ins.
CHALLENGE = Limit(120, timedelta(minutes=5))

# Claiming a code: a device link, or a first-run code. These are the short
# secrets, so this is the one that matters for guessing.
CODES = Limit(10, timedelta(minutes=5))

# Looking up an invitation. The code carries 96 bits, so this is about the cost
# of the lookup rather than the odds of a hit.
INVITES = Limit(30, timedelta(minutes=5))

# How many distinct keys are remembered at once. Nothing but a flood from many
# addresses reaches it, and then the oldest are forgotten -- which loosens the
# limit for whoever has been quiet longest, never for whoever is flooding.
MAX_KEYS = 4096

_seen: OrderedDict[tuple[str, str], deque[datetime]] = OrderedDict()
# One line per key per window, so a flood cannot also flood the log.
_announced: dict[tuple[str, str], datetime] = {}


def caller(request: Request) -> str:
    """Who is knocking, as far as this can be known. See the note above."""
    return request.client.host if request.client else "unknown"


def hit(bucket: str, key: str, limit: Limit, now: datetime | None = None) -> None:
    """Record an attempt. Raises 429 when the allowance is spent.

    Called for the attempt, not for the failure, wherever the work happens
    before the outcome is known -- a WebAuthn check costs the same whether it
    passes.
    """
    now = now or datetime.now(UTC)
    at = (bucket, key)

    tried = _seen.get(at)
    if tried is None:
        tried = deque()
        _seen[at] = tried
    _seen.move_to_end(at)

    while tried and now - tried[0] > limit.per:
        tried.popleft()

    if len(tried) >= limit.allowed:
        last = _announced.get(at)
        if last is None or now - last > limit.per:
            _announced[at] = now
            log.warning(
                "%s: too many attempts (%d in %s) -- refusing for now",
                bucket, len(tried), limit.per,
            )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many attempts -- wait a few minutes",
            headers={"Retry-After": str(int((tried[0] + limit.per - now).total_seconds()) + 1)},
        )

    tried.append(now)

    if len(_seen) > MAX_KEYS:
        # Forget whoever has been quiet longest, never the busiest.
        old, _ = _seen.popitem(last=False)
        _announced.pop(old, None)


def forgive(bucket: str, key: str) -> None:
    """Forget a key's attempts -- what success does, so that somebody who got in
    is not left carrying the count of their own fumbles."""
    _seen.pop((bucket, key), None)
    _announced.pop((bucket, key), None)


def clear() -> None:
    """For tests, and for nothing else."""
    _seen.clear()
    _announced.clear()
