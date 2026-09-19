"""A one-time sign-in code, issued from a shell on the machine itself.

    python -m app.signin_code            # for the owner
    python -m app.signin_code <handle>   # for someone else in the household

Exists because of the standby. A passkey belongs to the address it was made
for, so the standby keeps passkeys of its own and starts with none; the
first-run code is switched off there, because whoever reached it first would
have made an owner account on a machine outside the house; and the "sign in on
another device" button needs somebody already signed in on that same server.
Without this, nobody could ever get in.

The code is the ordinary device-link code -- eight characters, ten minutes,
used once -- typed into "Use a code" on the sign-in screen. What makes it safe
to offer is where it comes from: a shell on the machine, which on the standby
means the PC's SSH key over the tailnet and nothing else. It does not open a
door that is not already open to whoever holds that.

Once in, add a passkey from Settings, and the code is never needed again.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from .auth import LINK_TTL, issue_device_link
from .db import get_engine


def main(argv: list[str]) -> int:
    handle = argv[1] if len(argv) > 1 else None
    with get_engine().begin() as conn:
        if handle:
            row = conn.execute(
                text("SELECT id, display_name FROM users WHERE handle = :h"), {"h": handle}
            ).first()
        else:
            row = conn.execute(
                text("SELECT id, display_name FROM users WHERE is_owner")
            ).first()
        if row is None:
            who = f"no account called {handle!r}" if handle else "no owner account yet"
            print(f"Cannot issue a code: {who}.", file=sys.stderr)
            return 1
        code = issue_device_link(conn, row[0], via="shell")

    minutes = int(LINK_TTL.total_seconds() // 60)
    print(f"Sign-in code for {row[1]}: {code[:4]} {code[4:]}")
    print(f"Valid for {minutes} minutes, once. Enter it under 'Use a code' on the sign-in screen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
