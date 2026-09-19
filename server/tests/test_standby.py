"""The PC and the standby, and the changes that travel between them.

The standby accepts ordinary personal changes while the PC is off and the PC
carries them out when it is back. These tests are mostly about that journey
being safe: nothing refused on one side slips through on the other, nothing is
done twice, nothing made on the standby is lost to its own hourly refresh, and
the identity a change is replayed under cannot be claimed from outside.
"""

from __future__ import annotations

import base64
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import standby
from app.main import app
from app.security import CurrentUser, optional_user, require_user


@pytest.fixture
def as_role(monkeypatch):
    from app.config import get_settings

    def set_role(name: str) -> None:
        monkeypatch.setenv("HOMESH_ROLE", name)
        get_settings.cache_clear()

    yield set_role
    get_settings.cache_clear()


def _item(db) -> str:
    with db.begin() as conn:
        return str(
            conn.execute(
                text("INSERT INTO items (kind, size_bytes) VALUES ('audio', 1) RETURNING id")
            ).scalar_one()
        )


def _as(user: CurrentUser) -> TestClient:
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[optional_user] = lambda: user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_overrides_left():
    yield
    app.dependency_overrides.clear()


# ── What the standby may do ─────────────────────────────────────────────────


class TestWhatKindOfChangeThisIs:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/playlists"),
            ("GET", "/api/people"),
            ("HEAD", "/api/stream/x"),
        ],
    )
    def test_reading_is_always_allowed(self, method, path):
        assert standby.classify(method, path) == "read"

    def test_signing_in_is_handled_there(self):
        assert standby.classify("POST", "/api/auth/login/begin") == "local"
        assert standby.classify("POST", "/api/auth/login/complete") == "local"
        assert standby.classify("POST", "/api/auth/devices/claim") == "local"

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/api/auth/passkeys/begin"),
            ("POST", "/api/auth/passkeys/complete"),
            ("DELETE", "/api/auth/passkeys/0b5c7f36-1c1e-4d7a-9b1e-2f4c1f2a9e11"),
            ("POST", "/api/auth/register/begin"),
        ],
    )
    def test_passkeys_are_made_and_removed_on_the_pc(self, method, path):
        """The standby's passkeys are the PC's, restored every hour: one made
        here would vanish, one removed here would come back."""
        assert standby.classify(method, path) == "refused"

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/api/playlists"),
            ("PUT", f"/api/playlists/{uuid.uuid4()}"),
            ("DELETE", f"/api/playlists/{uuid.uuid4()}"),
            ("POST", f"/api/playlists/{uuid.uuid4()}/items"),
            ("DELETE", f"/api/playlists/{uuid.uuid4()}/items/{uuid.uuid4()}"),
            ("PUT", f"/api/playlists/{uuid.uuid4()}/order"),
            ("POST", f"/api/playlists/{uuid.uuid4()}/copy"),
            ("PUT", f"/api/playlists/{uuid.uuid4()}/share"),
            ("PUT", "/api/prefs"),
        ],
    )
    def test_personal_changes_are_kept_and_carried_back(self, method, path):
        assert standby.classify(method, path) == "replayed"

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/api/people/invites"),
            ("PUT", f"/api/people/{uuid.uuid4()}/admin"),
            ("DELETE", f"/api/people/{uuid.uuid4()}"),
            ("POST", "/api/zones"),
            ("POST", f"/api/zones/{uuid.uuid4()}/play"),
            ("POST", "/api/backups"),
            ("POST", "/api/backups/homesh-20260101-000000.sql.gz/restore"),
            ("DELETE", f"/api/sources/{uuid.uuid4()}"),
            ("POST", f"/api/items/{uuid.uuid4()}/drive-link"),
            # Importing reads the files of a folder the standby cannot reach.
            ("POST", f"/api/playlists/import/{uuid.uuid4()}"),
        ],
    )
    def test_everything_else_waits_for_the_pc(self, method, path):
        assert standby.classify(method, path) == "refused"


class TestTheStandbyChoosesIds:
    """A playlist made on the standby is named by id in every later change, so
    the PC has to create it with the same id or those changes name nothing."""

    def test_a_new_playlist_gets_ids_for_itself_and_its_tracks(self):
        body = standby.with_chosen_ids(
            "POST", "/api/playlists", json.dumps({"name": "x", "item_ids": ["a", "b"]}).encode()
        )
        data = json.loads(body)
        assert uuid.UUID(data["id"])
        assert len(data["entry_ids"]) == 2

    def test_added_tracks_get_entry_ids(self):
        body = standby.with_chosen_ids(
            "POST", f"/api/playlists/{uuid.uuid4()}/items", json.dumps({"item_ids": ["a"]}).encode()
        )
        assert len(json.loads(body)["entry_ids"]) == 1

    def test_a_copy_gets_an_id(self):
        body = standby.with_chosen_ids("POST", f"/api/playlists/{uuid.uuid4()}/copy", b"{}")
        assert uuid.UUID(json.loads(body)["id"])

    def test_ids_already_given_are_left_alone(self):
        chosen = str(uuid.uuid4())
        body = standby.with_chosen_ids(
            "POST", "/api/playlists", json.dumps({"name": "x", "id": chosen}).encode()
        )
        assert json.loads(body)["id"] == chosen

    def test_changes_that_create_nothing_are_untouched(self):
        raw = json.dumps({"name": "renamed"}).encode()
        assert standby.with_chosen_ids("PUT", f"/api/playlists/{uuid.uuid4()}", raw) == raw


class TestPlaylistsAcceptChosenIds:
    def test_a_playlist_is_made_with_the_id_it_was_given(self, db, user):
        chosen = str(uuid.uuid4())
        made = _as(user).post("/api/playlists", json={"name": "Mine", "id": chosen})
        assert made.status_code == 201
        assert made.json()["id"] == chosen

    def test_making_it_twice_makes_it_once(self, db, user):
        """A replay interrupted and run again must not leave two playlists."""
        chosen = str(uuid.uuid4())
        item = _item(db)
        body = {"name": "Mine", "id": chosen, "item_ids": [item], "entry_ids": [str(uuid.uuid4())]}
        client = _as(user)
        client.post("/api/playlists", json=body)
        client.post("/api/playlists", json=body)

        with db.connect() as conn:
            playlists = conn.execute(
                text("SELECT count(*) FROM playlists WHERE id = :p"), {"p": chosen}
            ).scalar_one()
            entries = conn.execute(
                text("SELECT count(*) FROM playlist_items WHERE playlist_id = :p"), {"p": chosen}
            ).scalar_one()
        assert (playlists, entries) == (1, 1)

    def test_somebody_elses_id_is_refused(self, db, user):
        """Choosing an id must not become a way to be handed a playlist you do
        not own."""
        chosen = str(uuid.uuid4())
        _as(user).post("/api/playlists", json={"name": "Theirs", "id": chosen})

        with db.begin() as conn:
            other = conn.execute(
                text(
                    "INSERT INTO users (handle, display_name) "
                    "VALUES ('other', 'Other') RETURNING id"
                )
            ).scalar_one()
        stranger = CurrentUser(id=other, handle="other", display_name="Other", is_admin=False)

        response = _as(stranger).post("/api/playlists", json={"name": "Mine", "id": chosen})
        assert response.status_code == 409

    def test_adding_the_same_tracks_twice_adds_them_once(self, db, user):
        client = _as(user)
        playlist = client.post("/api/playlists", json={"name": "Mine"}).json()["id"]
        body = {"item_ids": [_item(db)], "entry_ids": [str(uuid.uuid4())]}
        client.post(f"/api/playlists/{playlist}/items", json=body)
        client.post(f"/api/playlists/{playlist}/items", json=body)

        with db.connect() as conn:
            entries = conn.execute(
                text("SELECT count(*) FROM playlist_items WHERE playlist_id = :p"),
                {"p": playlist},
            ).scalar_one()
        assert entries == 1


# ── The gate on the standby ─────────────────────────────────────────────────


class TestTheGate:
    def _ops(self, db) -> list:
        with db.connect() as conn:
            return conn.execute(text("SELECT method, path, body FROM outbox_ops")).all()

    def test_on_the_pc_it_does_nothing(self, db, user, as_role, monkeypatch):
        as_role("primary")
        monkeypatch.setattr(standby, "_session_user", lambda scope: user.id)
        response = _as(user).post("/api/playlists", json={"name": "Mine"})
        assert response.status_code == 201
        assert self._ops(db) == []

    def test_an_administrative_change_is_refused_and_says_why(self, db, user, as_role):
        as_role("standby")
        response = _as(user).post("/api/people/invites", json={})
        assert response.status_code == 409
        assert response.json()["standby"] is True
        assert "main Homesh server is off" in response.json()["detail"]

    def test_a_personal_change_is_made_and_recorded(self, db, user, as_role, monkeypatch):
        as_role("standby")
        monkeypatch.setattr(standby, "_session_user", lambda scope: user.id)

        response = _as(user).post("/api/playlists", json={"name": "Made on the standby"})
        assert response.status_code == 201

        [(method, path, body)] = self._ops(db)
        assert (method, path) == ("POST", "/api/playlists")
        recorded = json.loads(bytes(body))
        assert recorded["id"] == response.json()["id"], "the recorded id is the one used here"

    def test_a_change_that_failed_here_is_not_carried_back(self, db, user, as_role, monkeypatch):
        as_role("standby")
        monkeypatch.setattr(standby, "_session_user", lambda scope: user.id)
        response = _as(user).put(f"/api/playlists/{uuid.uuid4()}", json={"name": "x"})
        assert response.status_code >= 400
        assert self._ops(db) == []

    def test_reading_on_the_standby_is_untouched(self, db, user, as_role):
        as_role("standby")
        assert _as(user).get("/api/playlists").status_code == 200


# ── Replaying on the PC ─────────────────────────────────────────────────────


def _op(user: CurrentUser, method: str, path: str, body: dict | None = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "at": "2026-01-01T00:00:00+00:00",
        "user_id": str(user.id),
        "method": method,
        "path": path,
        "body": base64.b64encode(json.dumps(body or {}).encode()).decode(),
    }


class TestReplay:
    async def test_a_playlist_made_on_the_standby_appears_on_the_pc_as_it_was(self, db, user):
        chosen, entry = str(uuid.uuid4()), str(uuid.uuid4())
        item = _item(db)

        outcome = await standby.replay(
            _op(user, "POST", "/api/playlists", {
                "name": "From the standby", "id": chosen,
                "item_ids": [item], "entry_ids": [entry],
            })
        )
        assert outcome[0] == "applied"

        with db.connect() as conn:
            row = conn.execute(
                text("SELECT name, owner_id FROM playlists WHERE id = :p"), {"p": chosen}
            ).one()
            entry_row = conn.execute(
                text("SELECT id FROM playlist_items WHERE playlist_id = :p"), {"p": chosen}
            ).scalar_one()
        assert row == ("From the standby", user.id)
        assert str(entry_row) == entry, "the entry keeps its id, so a later removal names it"

    async def test_a_later_change_names_what_the_earlier_one_made(self, db, user):
        chosen, entry = str(uuid.uuid4()), str(uuid.uuid4())
        item = _item(db)
        await standby.replay(
            _op(user, "POST", "/api/playlists",
                {"name": "x", "id": chosen, "item_ids": [item], "entry_ids": [entry]})
        )
        removed = await standby.replay(
            _op(user, "DELETE", f"/api/playlists/{chosen}/items/{entry}")
        )
        assert removed[0] == "applied"

    async def test_a_change_to_something_gone_is_skipped_not_forced(self, db, user):
        outcome = await standby.replay(
            _op(user, "PUT", f"/api/playlists/{uuid.uuid4()}", {"name": "renamed"})
        )
        assert outcome[0] == "skipped"
        assert outcome[1] == 404

    async def test_a_change_the_standby_should_have_refused_is_never_replayed(self, db, user):
        """The list in the bucket is data, and data can be wrong."""
        outcome = await standby.replay(_op(user, "POST", "/api/people/invites", {}))
        assert outcome == ("skipped", None, "not a change the standby may make")

    async def test_nobody_is_replayed_as_an_account_that_is_gone(self, db, user):
        ghost = CurrentUser(id=uuid.uuid4(), handle="gone", display_name="Gone", is_admin=True)
        outcome = await standby.replay(_op(ghost, "POST", "/api/playlists", {"name": "x"}))
        assert outcome == ("skipped", None, "that account no longer exists")

    async def test_it_is_replayed_as_them_and_nobody_else(self, db, user):
        """Access on the PC is exactly what it would have been: somebody who may
        not edit a playlist cannot edit it by way of the standby."""
        client = _as(user)
        theirs = client.post("/api/playlists", json={"name": "The owner's"}).json()["id"]
        app.dependency_overrides.clear()

        with db.begin() as conn:
            other = conn.execute(
                text(
                    "INSERT INTO users (handle, display_name) "
                    "VALUES ('guest', 'Guest') RETURNING id"
                )
            ).scalar_one()
        guest = CurrentUser(id=other, handle="guest", display_name="Guest", is_admin=False)

        outcome = await standby.replay(
            _op(guest, "PUT", f"/api/playlists/{theirs}", {"name": "taken over"})
        )
        assert outcome[0] == "skipped"
        with db.connect() as conn:
            name = conn.execute(
                text("SELECT name FROM playlists WHERE id = :p"), {"p": theirs}
            ).scalar_one()
        assert name == "The owner's"


def test_the_replay_identity_cannot_be_claimed_over_the_network(db, user):
    """It lives in the ASGI scope, which a request cannot write to. A header or a
    cookie of the same name is just a header or a cookie."""
    app.dependency_overrides.clear()
    with TestClient(app) as anonymous:
        response = anonymous.get(
            "/api/playlists",
            headers={"replay_user": str(user.id), "x-replay-user": str(user.id)},
            cookies={"replay_user": str(user.id)},
        )
    assert response.status_code == 401


# ── The trip through the bucket, and the standby's refresh ──────────────────


class Bucket:
    """Somewhere to put files, standing in for the real one."""

    def __init__(self):
        self.held: dict[str, bytes] = {}

    def configured(self, _settings):
        from app.offsite import Store

        return Store(provider="oracle", region="r", bucket="b", access_key="k",
                     secret_key="s", namespace="n")  # noqa: S106 - stand-ins

    def put(self, _store, name, path):
        self.held[name] = path.read_bytes()
        return len(self.held[name])

    def index(self, _store):
        from app.offsite import Stored

        return [Stored(name=n, size_bytes=len(b), written_at="2026-01-01T00:00:00Z")
                for n, b in sorted(self.held.items())]

    def get(self, _store, name, target):
        target.write_bytes(self.held[name])
        return len(self.held[name])

    def remove(self, _store, name):
        self.held.pop(name, None)


@pytest.fixture
def bucket(monkeypatch, tmp_path):
    from app import offsite
    from app.config import get_settings
    from app.crypt import new_key

    fake = Bucket()
    for name in ("configured", "put", "index", "get", "remove"):
        monkeypatch.setattr(offsite, name, getattr(fake, name))
    monkeypatch.setenv("BACKUP_KEY", new_key())
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    get_settings.cache_clear()
    yield fake
    get_settings.cache_clear()


class TestTheJourney:
    async def test_changes_go_out_encrypted_and_come_back_applied(self, db, user, bucket):
        chosen = str(uuid.uuid4())
        standby.record(user.id, "POST", "/api/playlists",
                       json.dumps({"name": "Made while the PC slept", "id": chosen,
                                   "item_ids": [], "entry_ids": []}).encode())

        assert standby.push_outbox() == 1
        [name] = [n for n in bucket.held if n.startswith(standby.OUTBOX_PREFIX)]
        assert b"Made while the PC slept" not in bucket.held[name], "it left in the clear"

        result = await standby.replay_outboxes()
        assert result == {"outboxes": 1, "applied": 1, "skipped": 0}
        assert not any(n.startswith(standby.OUTBOX_PREFIX) for n in bucket.held), \
            "a carried-out outbox is removed"

        with db.connect() as conn:
            assert conn.execute(
                text("SELECT outcome FROM applied_ops")
            ).scalar_one() == "applied"
            assert conn.execute(
                text("SELECT name FROM playlists WHERE id = :p"), {"p": chosen}
            ).scalar_one() == "Made while the PC slept"

    async def test_nothing_is_done_twice(self, db, user, bucket):
        standby.record(user.id, "POST", "/api/playlists",
                       json.dumps({"name": "Once", "id": str(uuid.uuid4())}).encode())
        standby.push_outbox()
        pending = dict(bucket.held)

        await standby.replay_outboxes()
        bucket.held.update(pending)          # the same outbox, arriving again
        again = await standby.replay_outboxes()

        assert again["applied"] == 0
        with db.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM playlists WHERE name = 'Once'")
            ).scalar_one() == 1

    def test_the_refresh_waits_for_the_pc_to_have_the_standbys_changes(self, db, user, bucket):
        """An hourly refresh must never throw away something somebody did."""
        from app import backups

        mine = standby.record(user.id, "PUT", "/api/prefs", b'{"view":"tiles"}')
        taken = backups.make_backup()              # a PC backup without that change
        backups.send_offsite(taken.name)

        outcome = standby.refresh_from_primary()
        assert outcome["refreshed"] is False
        assert "not in that backup yet" in outcome["why"]
        with db.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM outbox_ops WHERE id = :i"), {"i": str(mine)}
            ).scalar_one() == 1

    def test_the_refresh_happens_once_the_changes_are_in(self, db, user, bucket):
        from app import backups

        mine = standby.record(user.id, "PUT", "/api/prefs", b'{"view":"tiles"}')
        with db.begin() as conn:
            conn.execute(
                text("INSERT INTO applied_ops (id, outcome) VALUES (:i, 'applied')"),
                {"i": str(mine)},
            )
        taken = backups.make_backup()              # now the PC's backup has it
        backups.send_offsite(taken.name)
        with db.begin() as conn:
            conn.execute(text("DELETE FROM applied_ops"))

        outcome = standby.refresh_from_primary()
        assert outcome["refreshed"] is True
        with db.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM outbox_ops")).scalar_one() == 0


class TestTheStandbyKeepsWhatIsItsOwn:
    def test_the_pcs_passkeys_arrive_with_its_backup(self, db, user, bucket):
        """One passkey for both machines: made on the PC, for the tailnet's
        domain, it reaches the standby with the next backup and works there."""
        from app import backups

        with db.begin() as conn:
            conn.execute(
                text("INSERT INTO credentials (user_id, credential_id, public_key, rp_id) "
                     "VALUES (:u, 'pc-passkey', 'k', 'example.ts.net')"),
                {"u": str(user.id)},
            )
        taken = backups.make_backup()

        with db.begin() as conn:
            conn.execute(text("DELETE FROM credentials"))
            conn.execute(
                text("INSERT INTO credentials (user_id, credential_id, public_key) "
                     "VALUES (:u, 'stale-standby-passkey', 'k')"),
                {"u": str(user.id)},
            )

        backups.restore(taken.name, keep=standby.STANDBY_LOCAL)

        with db.connect() as conn:
            rows = conn.execute(text("SELECT credential_id FROM credentials")).all()
        assert {bytes(r[0]) for r in rows} == {b"pc-passkey"}

    def test_who_is_signed_in_survives_a_restore(self, db, user, bucket):
        from app import backups
        from app.security import create_session

        taken = backups.make_backup()
        with db.begin() as conn:
            create_session(conn, user.id, "phone")
        backups.restore(taken.name, keep=standby.STANDBY_LOCAL)
        with db.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM auth_sessions")).scalar_one() == 1
