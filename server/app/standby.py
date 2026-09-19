"""Two machines, one catalog, and no connection between them.

The PC is the primary: the stronger machine, the one with the disks, on the
network the house mostly uses. When it is off, a standby on an Oracle Always
Free machine answers instead (docs/STANDBY.md is the design and the reasoning).

Everything the two say to each other travels through the backup bucket, and
everything written there is encrypted first:

- the PC pushes a backup every hour; the standby restores the newest one
- the standby records each change made on it, pushes the list; the PC replays it

**The machines never talk to each other.** The standby has no address for the
PC, no credential for it and no connection to it. That is the answer to the
requirement that nothing online may become a route into the house.

**Changes are replayed, not merged.** Two copies of a database edited apart
cannot be reconciled reliably by comparing their rows, and that is where this
kind of system quietly loses data. Each change on the standby is recorded as the
request that made it, and the PC performs the same request through its own code,
as the same person — so the PC's own rules decide whether it still makes sense.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import text

from . import crypt, offsite
from .config import get_settings
from .db import get_engine

log = logging.getLogger("homesh.standby")

OUTBOX_PREFIX = "outbox/"

# Tables that belong to the machine they are on and survive a standby's restore.
#
# Sessions are who is signed in, here. Device links are sign-ins in progress. And
# the outbox is the one thing a restore must never throw away: changes not yet
# carried back.
#
# Passkeys are not on the list, and used to be. A passkey belongs to a name, and
# the PC's belonged to the PC's own host name, so they were useless here and the
# standby kept its own -- which meant setting every device up twice. They now
# belong to the tailnet's domain, which both machines sit under, so the PC's
# passkeys arrive with its backup and work here as they are.
STANDBY_LOCAL = ("auth_sessions", "device_links", "outbox_ops")


def role() -> str:
    """"primary" or "standby". Anything else is treated as primary.

    Primary is the safe default: a machine that believes it is the primary does
    everything it always did, while one that wrongly believes it is a standby
    would refuse most changes and restore itself over its own data.
    """
    return "standby" if get_settings().homesh_role.strip().lower() == "standby" else "primary"


def is_standby() -> bool:
    return role() == "standby"


# ── What may change on the standby ──────────────────────────────────────────
#
# Three kinds of write, and the list of which is which is written out rather than
# inferred, because the failure mode of getting it wrong is a change silently
# lost or silently applied.

_UUID = r"[0-9a-fA-F-]{36}"

# Handled by the standby itself and not carried back. Signing in there is about
# being there.
_LOCAL = [re.compile(r"^/api/auth/")]

# ...except making and removing passkeys. The standby's passkeys are the PC's,
# restored with every backup, so one made here would vanish within the hour and
# one removed here would come back. Both are done on the PC, and reach the
# standby with its next backup.
_NOT_HERE = [re.compile(r"^/api/auth/(passkeys|register)(/|$)")]

# Ordinary personal changes: kept on the standby, and replayed on the PC.
_REPLAYED = [
    ("POST", re.compile(r"^/api/playlists/?$")),
    ("PUT", re.compile(rf"^/api/playlists/{_UUID}$")),
    ("DELETE", re.compile(rf"^/api/playlists/{_UUID}$")),
    ("POST", re.compile(rf"^/api/playlists/{_UUID}/items$")),
    ("DELETE", re.compile(rf"^/api/playlists/{_UUID}/items/{_UUID}$")),
    ("PUT", re.compile(rf"^/api/playlists/{_UUID}/order$")),
    ("POST", re.compile(rf"^/api/playlists/{_UUID}/copy$")),
    ("PUT", re.compile(rf"^/api/playlists/{_UUID}/share$")),
    ("PUT", re.compile(r"^/api/prefs/?$")),
]

_WRITES = {"POST", "PUT", "PATCH", "DELETE"}

REFUSAL = (
    "The main Homesh server is off, so this can't be changed right now. "
    "Playlists and your own settings still can; accounts, access, rooms and "
    "folders wait for the main server."
)


def classify(method: str, path: str) -> str:
    """"read", "local", "replayed" or "refused"."""
    method = method.upper()
    if method not in _WRITES:
        return "read"
    if any(p.match(path) for p in _NOT_HERE):
        return "refused"
    if any(p.match(path) for p in _LOCAL):
        return "local"
    if any(m == method and p.match(path) for m, p in _REPLAYED):
        return "replayed"
    return "refused"


def with_chosen_ids(method: str, path: str, body: bytes) -> bytes:
    """Add the ids the standby picks, to anything that creates something.

    A playlist made on the standby is named by its id in every later change to
    it. If the PC gave it a new id when replaying the creation, every one of those
    later changes would name a playlist that does not exist. So the ids are chosen
    here, recorded in the change, and used again unchanged on the PC.
    """
    if method.upper() != "POST":
        return body
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return body
    if not isinstance(data, dict):
        return body

    if re.match(r"^/api/playlists/?$", path):
        data.setdefault("id", str(uuid4()))
        data.setdefault("entry_ids", [str(uuid4()) for _ in data.get("item_ids") or []])
    elif re.match(rf"^/api/playlists/{_UUID}/items$", path):
        data.setdefault("entry_ids", [str(uuid4()) for _ in data.get("item_ids") or []])
    elif re.match(rf"^/api/playlists/{_UUID}/copy$", path):
        data.setdefault("id", str(uuid4()))
    else:
        return body
    return json.dumps(data).encode()


class StandbyGate:
    """ASGI middleware that decides what a write on the standby may do.

    ASGI rather than Starlette's `@app.middleware`: recording a change needs the
    request body, and a replayed creation needs that body *changed* before the
    endpoint reads it, which only the raw receive channel allows.

    Does nothing at all on the primary.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not is_standby():
            await self.app(scope, receive, send)
            return

        method, path = scope["method"], scope["path"]
        kind = classify(method, path)

        if kind in ("read", "local"):
            await self.app(scope, receive, send)
            return

        if kind == "refused":
            await _refuse(send)
            return

        body = await _read_body(receive)
        body = with_chosen_ids(method, path, body)
        user_id = _session_user(scope)

        status_holder: dict[str, int] = {}

        async def replay_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def watching_send(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        await self.app(scope, replay_receive, watching_send)

        # Recorded only once it has actually happened here, and only with somebody
        # to replay it as. A change refused on the standby is not carried back to
        # be refused again; an anonymous one never reached an endpoint that could
        # make it.
        if user_id is not None and 200 <= status_holder.get("status", 0) < 300:
            record(user_id, method, path, body)


async def _read_body(receive) -> bytes:
    chunks = []
    while True:
        message = await receive()
        chunks.append(message.get("body", b""))
        if not message.get("more_body"):
            return b"".join(chunks)


async def _refuse(send) -> None:
    payload = json.dumps({"detail": REFUSAL, "standby": True}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 409,
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-homesh-standby", b"1"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def _session_user(scope) -> UUID | None:
    from .security import SESSION_COOKIE, _lookup

    for name, value in scope.get("headers", []):
        if name != b"cookie":
            continue
        for part in value.decode("latin-1").split(";"):
            key, _, token = part.strip().partition("=")
            if key == SESSION_COOKIE and token:
                user = _lookup(token)
                return user.id if user else None
    return None


def record(user_id: UUID, method: str, path: str, body: bytes) -> UUID:
    op_id = uuid4()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO outbox_ops (id, user_id, method, path, body) "
                "VALUES (:id, :u, :m, :p, :b)"
            ),
            {"id": str(op_id), "u": str(user_id), "m": method.upper(), "p": path, "b": body},
        )
    return op_id


# ── The standby: sending its changes, and taking the PC's backups ───────────


def _seal(data: bytes) -> Path:
    key = crypt.key_bytes(get_settings().backup_key)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as plain:
        plain.write(data)
        plain_path = Path(plain.name)
    sealed = plain_path.with_suffix(".json.enc")
    try:
        crypt.encrypt(plain_path, sealed, key)
    finally:
        plain_path.unlink(missing_ok=True)
    return sealed


def _unseal(sealed: Path) -> bytes:
    key = crypt.key_bytes(get_settings().backup_key)
    plain = sealed.with_suffix(".plain")
    try:
        crypt.decrypt(sealed, plain, key)
        return plain.read_bytes()
    finally:
        plain.unlink(missing_ok=True)


def push_outbox() -> int:
    """Send the changes not yet sent. Returns how many."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, at, user_id, method, path, body FROM outbox_ops "
                "WHERE pushed_at IS NULL ORDER BY at"
            )
        ).all()
    if not rows:
        return 0

    ops = [
        {
            "id": str(r[0]),
            "at": r[1].isoformat(),
            "user_id": str(r[2]),
            "method": r[3],
            "path": r[4],
            "body": base64.b64encode(bytes(r[5] or b"")).decode(),
        }
        for r in rows
    ]
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = f"{OUTBOX_PREFIX}{stamp}-{ops[0]['id']}.json.enc"

    sealed = _seal(json.dumps({"format": 1, "ops": ops}).encode())
    try:
        offsite.put(_store(), name, sealed)
    finally:
        sealed.unlink(missing_ok=True)

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE outbox_ops SET pushed_at = now() WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [o["id"] for o in ops]},
        )
    log.info("sent %d change(s) made on the standby", len(ops))
    return len(ops)


def _store():
    store = offsite.configured(get_settings())
    if store is None:
        raise offsite.OffsiteError("no off-site store is configured")
    return store


def _newest_backup() -> str | None:
    names = [
        item.name
        for item in offsite.index(_store())
        if re.match(r"^homesh-\d{8}-\d{6}(-\d+)?\.sql\.gz\.enc$", item.name)
    ]
    return max(names) if names else None


def refresh_from_primary() -> dict:
    """Replace this standby's catalog with the PC's newest backup — when that is safe.

    Safe means the backup already contains every change made here. Until then
    it waits: replacing the database with a backup taken before the PC replayed
    this standby's changes would throw those changes away, and an hourly refresh
    that could lose what somebody did is worse than a standby an hour stale.
    """
    from . import backups

    newest = _newest_backup()
    if newest is None:
        return {"refreshed": False, "why": "no backup in the bucket yet"}

    plain_name = newest[: -len(".enc")]
    marker = backups.backup_dir() / ".standby-restored"
    if marker.is_file() and marker.read_text().strip() == plain_name:
        return {"refreshed": False, "why": "already on the newest backup"}

    landed = backups.bring_back(newest)
    absorbed = backups.column_values(landed, "applied_ops")

    with get_engine().connect() as conn:
        mine = {str(r[0]) for r in conn.execute(text("SELECT id FROM outbox_ops")).all()}
    waiting = mine - absorbed
    if waiting:
        return {
            "refreshed": False,
            "why": f"{len(waiting)} change(s) made here are not in that backup yet",
        }

    backups.restore(landed, keep=STANDBY_LOCAL)
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM outbox_ops WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": sorted(mine)},
        )
    marker.write_text(plain_name)
    log.info("standby refreshed from %s", plain_name)
    return {"refreshed": True, "from": plain_name}


# ── The PC: carrying the standby's changes out ──────────────────────────────


def _as_user(user_id: str):
    from .security import CurrentUser

    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT id, handle, display_name, is_admin FROM users WHERE id = :u"),
            {"u": user_id},
        ).first()
    if row is None:
        return None
    return CurrentUser(id=row[0], handle=row[1], display_name=row[2], is_admin=row[3])


async def replay(op: dict) -> tuple[str, int | None, str]:
    """Perform one change on this machine, as the person who made it.

    Through the application itself rather than by writing rows, so every rule
    the PC would have applied — who may edit what, what is still there — applies
    now. Outcome is ("applied" | "skipped", status, detail).
    """
    import httpx

    from .main import app

    if classify(op["method"], op["path"]) != "replayed":
        # Never replay anything the standby should not have accepted, whatever
        # the list in the bucket says: it is data, and data can be wrong.
        return "skipped", None, "not a change the standby may make"

    user = _as_user(op["user_id"])
    if user is None:
        return "skipped", None, "that account no longer exists"

    async def as_them(scope, receive, send):
        # Identity handed over in the ASGI scope, which only code in this process
        # can set. Nothing arriving over the network can put it there.
        scope = dict(scope)
        scope["state"] = {**scope.get("state", {}), "replay_user": user}
        await app(scope, receive, send)

    transport = httpx.ASGITransport(app=as_them)
    async with httpx.AsyncClient(transport=transport, base_url="http://replay") as client:
        response = await client.request(
            op["method"],
            op["path"],
            content=base64.b64decode(op["body"]),
            headers={"content-type": "application/json"},
        )

    if 200 <= response.status_code < 300:
        return "applied", response.status_code, ""
    detail = ""
    try:
        detail = str(response.json().get("detail", ""))[:300]
    except ValueError:
        detail = response.text[:300]
    return "skipped", response.status_code, detail


async def replay_outboxes() -> dict:
    """Fetch every outbox the standby has sent and carry each change out.

    In the order the changes were made, each exactly once: an op already listed
    in `applied_ops` is passed over, so a replay interrupted half way is simply
    run again. An outbox is deleted from the bucket only once every change in it
    has an outcome.
    """
    store = _store()
    names = sorted(i.name for i in offsite.index(store) if i.name.startswith(OUTBOX_PREFIX))
    result = {"outboxes": 0, "applied": 0, "skipped": 0}

    for name in names:
        with tempfile.TemporaryDirectory() as directory:
            sealed = Path(directory) / "outbox.json.enc"
            offsite.get(store, name, sealed)
            ops = json.loads(_unseal(sealed)).get("ops", [])

        for op in sorted(ops, key=lambda o: o["at"]):
            with get_engine().connect() as conn:
                done = conn.execute(
                    text("SELECT 1 FROM applied_ops WHERE id = :id"), {"id": op["id"]}
                ).first()
            if done:
                continue

            outcome, status_code, detail = await replay(op)
            with get_engine().begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO applied_ops (id, outcome, status, detail) "
                        "VALUES (:id, :o, :s, :d) ON CONFLICT (id) DO NOTHING"
                    ),
                    {"id": op["id"], "o": outcome, "s": status_code, "d": detail},
                )
            result[outcome] += 1
            if outcome == "skipped":
                log.warning("a change from the standby was skipped: %s %s -> %s %s",
                            op["method"], op["path"], status_code, detail)

        offsite.remove(store, name)
        result["outboxes"] += 1

    if result["outboxes"]:
        log.info("replayed the standby's changes: %s", result)
    return result
