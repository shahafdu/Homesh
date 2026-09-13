"""One file in two places, recognised as one file.

The catalog could always hold several copies of an item and play whichever
answers — that is how the library is meant to keep working with the PC off. It
just never put two copies under one item, because nothing compared contents, so
a folder that exists both on the disk and on Drive produced two complete
catalogues side by side.

These tests are about the merge being *safe* as much as about it happening. It
deletes rows, and everything that points at the item it deletes has to be moved
first — a playlist entry that silently became NULL would be somebody's list
quietly losing a track.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import text

from app.dedup import fingerprint_local, merge_duplicates
from app.scanner import scan_source
from app.sources.local import LocalConnector

SONG = "01 - In the Flesh.mp3"


@pytest.fixture
def scanned(source):
    """The fixture library, catalogued once, as a local source."""
    sid, prefix, root = source
    scan_source(sid, LocalConnector(root))
    return sid, prefix, root


def _elsewhere(db, name: str, size: int, md5: bytes | None) -> tuple[uuid.UUID, uuid.UUID]:
    """The same file, catalogued again from a source that is not local.

    Stands in for the Drive copy: Drive states an MD5 in its listing, which is
    why the remote side needs no reading.
    """
    with db.begin() as conn:
        source_id = conn.execute(
            text(
                """
                INSERT INTO sources (kind, name, mount_prefix, audience, remote_id)
                VALUES ('gdrive', :n, :p, 'everyone', :r) RETURNING id
                """
            ),
            {
                "n": f"Cloud {uuid.uuid4().hex[:5]}",
                "p": f"/cloud/{uuid.uuid4().hex[:6]}",
                "r": uuid.uuid4().hex,
            },
        ).scalar_one()
        item_id = conn.execute(
            text(
                """
                INSERT INTO items (kind, size_bytes) VALUES ('audio', :s) RETURNING id
                """
            ),
            {"s": size},
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO replicas
                    (item_id, source_id, dir_path, filename, ext, available, content_md5)
                VALUES (:i, :s, 'Cloud', :n, 'mp3', TRUE, :m)
                """
            ),
            {"i": str(item_id), "s": str(source_id), "n": name, "m": md5},
        )
    return source_id, item_id


def _local(db, name: str):
    with db.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT r.id, r.item_id, i.size_bytes
                FROM replicas r JOIN items i ON i.id = r.item_id
                JOIN sources s ON s.id = r.source_id
                WHERE r.filename = :n AND s.kind = 'local'
                """
            ),
            {"n": name},
        ).one()


def _digest(root, relative: str) -> bytes:
    return hashlib.md5((root / relative).read_bytes()).digest()  # noqa: S324


class TestFingerprintingReadsOnlyWhatItMust:
    """Hashing the library means reading every byte of it. The shortlist — same
    name, same size, different source — is the difference between reading 53 GB
    and reading all of it."""

    def test_a_file_with_a_lookalike_elsewhere_is_read(self, db, scanned):
        _, _, root = scanned
        _replica, _item, size = _local(db, SONG)
        _elsewhere(db, SONG, size, b"\x00" * 16)

        assert fingerprint_local(connectors=lambda _sid: LocalConnector(root)).fingerprinted == 1

        replica_id, _, _ = _local(db, SONG)
        with db.connect() as conn:
            stored = conn.execute(
                text("SELECT content_md5 FROM replicas WHERE id = :r"), {"r": replica_id}
            ).scalar_one()
        assert bytes(stored) == _digest(root, f"Music/Pink Floyd/The Wall/{SONG}")

    def test_a_file_that_exists_nowhere_else_is_left_alone(self, db, scanned):
        """The whole library has lookalike-free files in it, and reading them
        would be reading the library for nothing."""
        _, _, root = scanned
        assert fingerprint_local(connectors=lambda _sid: LocalConnector(root)).fingerprinted == 0

    def test_a_name_that_matches_at_a_different_size_is_not_a_candidate(self, db, scanned):
        _, _, root = scanned
        _, _, size = _local(db, SONG)
        _elsewhere(db, SONG, size + 1, b"\x00" * 16)
        assert fingerprint_local(connectors=lambda _sid: LocalConnector(root)).fingerprinted == 0


class TestMerging:
    def _twinned(self, db, root):
        """The same song, catalogued locally and again from the cloud."""
        _, _, size = _local(db, SONG)
        real = _digest(root, f"Music/Pink Floyd/The Wall/{SONG}")
        _source, cloud_item = _elsewhere(db, SONG, size, real)
        fingerprint_local(connectors=lambda _sid: LocalConnector(root))
        _replica, local_item, _ = _local(db, SONG)
        return local_item, cloud_item

    def test_two_items_become_one_with_two_copies(self, db, scanned):
        _, _, root = scanned
        local_item, cloud_item = self._twinned(db, root)
        assert local_item != cloud_item

        assert merge_duplicates().merged == 1

        with db.connect() as conn:
            left = conn.execute(
                text("SELECT count(*) FROM items WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [str(local_item), str(cloud_item)]},
            ).scalar_one()
            copies = conn.execute(
                text(
                    """
                    SELECT count(*) FROM replicas
                    WHERE item_id = (
                        SELECT id FROM items WHERE id = ANY(CAST(:ids AS uuid[]))
                    )
                    """
                ),
                {"ids": [str(local_item), str(cloud_item)]},
            ).scalar_one()

        assert left == 1, "one item survives"
        assert copies == 2, "and it knows about both copies"

    def test_the_survivor_carries_the_fingerprint(self, db, scanned):
        _, _, root = scanned
        local_item, cloud_item = self._twinned(db, root)
        merge_duplicates()

        with db.connect() as conn:
            stored = conn.execute(
                text(
                    "SELECT content_hash FROM items WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [str(local_item), str(cloud_item)]},
            ).scalar_one()
        assert bytes(stored) == _digest(root, f"Music/Pink Floyd/The Wall/{SONG}")

    def test_running_it_again_changes_nothing(self, db, scanned):
        _, _, root = scanned
        self._twinned(db, root)
        assert merge_duplicates().merged == 1
        assert merge_duplicates().merged == 0

    def test_two_different_files_are_not_merged(self, db, scanned):
        """Same name, same size, different contents. It happens — two cameras
        both call it IMG_1234.jpg — and merging those would lose one."""
        _, _, root = scanned
        _, local_item, size = _local(db, SONG)
        _source, cloud_item = _elsewhere(db, SONG, size, b"\xff" * 16)
        fingerprint_local(connectors=lambda _sid: LocalConnector(root))

        assert merge_duplicates().merged == 0
        with db.connect() as conn:
            both = conn.execute(
                text("SELECT count(*) FROM items WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [str(local_item), str(cloud_item)]},
            ).scalar_one()
        assert both == 2


class TestNothingIsLostInTheMerge:
    """The merge deletes an item, and four tables cascade from that one while a
    fifth is set to NULL. Everything has to be moved before the delete, and this
    is the half of the feature that can quietly destroy something."""

    def _twinned(self, db, root):
        _, local_item, size = _local(db, SONG)
        real = _digest(root, f"Music/Pink Floyd/The Wall/{SONG}")
        _source, cloud_item = _elsewhere(db, SONG, size, real)
        fingerprint_local(connectors=lambda _sid: LocalConnector(root))
        return local_item, cloud_item

    def _survivor(self, db, a, b):
        with db.connect() as conn:
            return conn.execute(
                text("SELECT id FROM items WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [str(a), str(b)]},
            ).scalar_one()

    def test_a_playlist_keeps_its_track(self, db, user, scanned):
        _, _, root = scanned
        local_item, cloud_item = self._twinned(db, root)

        with db.begin() as conn:
            playlist = conn.execute(
                text(
                    "INSERT INTO playlists (owner_id, name) VALUES (:u, 'Mine') RETURNING id"
                ),
                {"u": str(user.id)},
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO playlist_items (playlist_id, position, item_id, original_ref)
                    VALUES (:p, 1, :i, 'whatever.mp3')
                    """
                ),
                {"p": str(playlist), "i": str(cloud_item)},
            )

        merge_duplicates()
        survivor = self._survivor(db, local_item, cloud_item)

        with db.connect() as conn:
            still = conn.execute(
                text("SELECT item_id FROM playlist_items WHERE playlist_id = :p"),
                {"p": str(playlist)},
            ).scalar_one()
        assert still == survivor, "a playlist entry was orphaned by the merge"

    def test_where_you_had_got_to_survives(self, db, user, scanned):
        _, _, root = scanned
        local_item, cloud_item = self._twinned(db, root)

        with db.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO progress (user_id, item_id, position_ms)
                    VALUES (:u, :i, 90000)
                    """
                ),
                {"u": str(user.id), "i": str(cloud_item)},
            )

        merge_duplicates()
        survivor = self._survivor(db, local_item, cloud_item)

        with db.connect() as conn:
            where = conn.execute(
                text("SELECT position_ms FROM progress WHERE item_id = :i"),
                {"i": str(survivor)},
            ).scalar_one()
        assert where == 90000

    def test_two_positions_for_one_person_do_not_break_the_merge(self, db, user, scanned):
        """Both items can carry a row for the same listener, and the primary key
        will not have both. The survivor's stands."""
        _, _, root = scanned
        local_item, cloud_item = self._twinned(db, root)

        with db.begin() as conn:
            for item, position in ((local_item, 1000), (cloud_item, 2000)):
                conn.execute(
                    text(
                        """
                        INSERT INTO progress (user_id, item_id, position_ms)
                        VALUES (:u, :i, :p)
                        """
                    ),
                    {"u": str(user.id), "i": str(item), "p": position},
                )

        assert merge_duplicates().merged == 1
        survivor = self._survivor(db, local_item, cloud_item)

        with db.connect() as conn:
            rows = conn.execute(
                text("SELECT position_ms FROM progress WHERE item_id = :i"),
                {"i": str(survivor)},
            ).scalars().all()
        assert rows == [1000], "the survivor's own position should stand"

    def test_tags_read_from_the_file_survive(self, db, scanned):
        _, _, root = scanned
        local_item, cloud_item = self._twinned(db, root)

        with db.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO item_metadata (item_id, key, value, origin, confidence)
                    VALUES (:i, 'artist', 'Pink Floyd', 'file', 1.0)
                    """
                ),
                {"i": str(cloud_item)},
            )

        merge_duplicates()
        survivor = self._survivor(db, local_item, cloud_item)

        with db.connect() as conn:
            artist = conn.execute(
                text("SELECT value FROM item_metadata WHERE item_id = :i AND key = 'artist'"),
                {"i": str(survivor)},
            ).scalar_one()
        assert artist == "Pink Floyd"
