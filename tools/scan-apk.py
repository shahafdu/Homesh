"""Refuse to publish an APK that describes the network it will be pointed at.

The two Android apps are downloaded from a public release page, so anything
compiled into them is published. Neither has any business carrying an address:
both are configured at runtime, or find the server themselves on the local
network.

This exists because the check that was supposed to establish that ran `strings`,
which is not installed on the machine it ran on. It printed nothing, the exit
status was swallowed, and "no matches" was reported as though it meant
something. A check that cannot fail loudly is not a check -- so this one is
Python, reads the archive itself, and exits non-zero.

Every entry is decompressed and searched, which is the point: an address would
be inside classes.dex or resources.arsc, not sitting in a loose file at the top
of the zip.

    python tools/scan-apk.py build/homesh-phone.apk build/homesh-tv.apk
"""

from __future__ import annotations

import re
import sys
import zipfile

PATTERNS: dict[str, bytes] = {
    "private IPv4": rb"(?:^|[^0-9.])(?:10|192\.168|172\.(?:1[6-9]|2[0-9]|3[01]))"
    rb"\.[0-9]{1,3}\.[0-9]{1,3}",
    # Tailscale hands out 100.64.0.0/10 for tailnet addresses.
    "tailnet address": rb"100\.(?:6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7])"
    rb"\.[0-9]{1,3}\.[0-9]{1,3}",
    "tailnet name": rb"[A-Za-z0-9-]+\.ts\.net",
    "tailnet id": rb"tail[0-9a-f]{6,}",
    "device uuid": rb"uuid:[0-9a-f]{8}-[0-9a-f]{4}",
}

# The suffix the app tests hostnames against. A comparison against ".ts.net" is
# how it knows a tailnet name implies https; it names no tailnet in particular.
ALLOWED: set[bytes] = {b".ts.net"}


def scan(path: str) -> list[str]:
    found: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for entry in archive.namelist():
            blob = archive.read(entry)
            for label, pattern in PATTERNS.items():
                for match in re.finditer(pattern, blob):
                    hit = match.group(0).strip()
                    if hit in ALLOWED:
                        continue
                    found.append(f"{path}: {label} in {entry}: {hit!r}")
    return found


def main(paths: list[str]) -> int:
    if not paths:
        print("usage: scan-apk.py <apk> [apk ...]", file=sys.stderr)
        return 2

    problems: list[str] = []
    for path in paths:
        print(f"scanning {path}")
        problems += scan(path)

    if problems:
        print()
        print("REFUSING: these APKs carry something about a real network.")
        for line in problems:
            print(f"  {line}")
        print()
        print("Addresses are configured at runtime. Nothing about the house")
        print("belongs in a file published on a release page.")
        return 1

    print(f"clean - {len(paths)} APK(s) carry no address, tailnet or device id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
