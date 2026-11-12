"""The environment a child program is started with.

The server's own environment carries every secret it has -- the master key, the
signing key, the backup key, the bucket keys and the database password all
arrive through ``env_file``. A child process inherits all of that by default,
and the children here are LibreOffice, ffmpeg and ffprobe: large parsers fed
files that anyone with edit rights on a shared folder can supply. A crafted
document that gets one of them to read its own environment would otherwise read
every key the house has.

So each one starts with only what it needs to run.
"""

from __future__ import annotations

import os

# Names a child genuinely needs. Nothing here is a secret.
_PASS_THROUGH = ("PATH", "LANG", "LC_ALL", "TZ")


def child_env(**extra: str) -> dict[str, str]:
    env = {k: os.environ[k] for k in _PASS_THROUGH if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("LANG", "C.UTF-8")
    # LibreOffice wants a writable home; a profile directory is passed
    # explicitly as well, so this is only a fallback.
    env["HOME"] = "/tmp"  # noqa: S108
    env.update(extra)
    return env
