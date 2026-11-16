"""Ask for a scan of one granted folder: ``python -m app.scanrequest <name>``.

The PC's watcher runs this inside the container when files in a granted folder
change. It is a command rather than a route on purpose: nothing new listens on
the network, there is no token to guard, and the only thing it accepts is the
name of a folder that is already mounted. It sets a flag and never scans.
"""

from __future__ import annotations

import re
import sys

from sqlalchemy import text

from .db import get_engine

NAME = re.compile(r"^[A-Za-z0-9 _.()-]{1,80}$")


def valid_name(name: str) -> bool:
    return bool(NAME.fullmatch(name)) and ".." not in name


def request_scan(name: str) -> bool:
    """Flag the source registered for this mounted folder. False if unknown."""
    # The same lookup library._register_mounted writes: mount_prefix is the
    # lower-cased folder name, and the source is local.
    prefix = f"/local/{name.lower()}"
    with get_engine().begin() as conn:
        found = conn.execute(
            text(
                "UPDATE sources SET scan_requested_at = now() "
                "WHERE kind = 'local' AND mount_prefix = :p RETURNING id"
            ),
            {"p": prefix},
        ).first()
    return found is not None


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not valid_name(argv[1]):
        print("not a folder name")
        return 2
    if not request_scan(argv[1]):
        print("no such granted folder")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
