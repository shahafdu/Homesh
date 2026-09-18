#!/usr/bin/env bash
# Put the standby on the tailnet, and serve HTTPS there.
#
# Run on the standby by tools/deploy-standby.ps1 -Tailscale, which uploads the
# one-off key first. The key is read from a file and deleted immediately: a key
# on a command line is a key in the shell history and in the process list.
set -euo pipefail

KEYFILE=/tmp/homesh-authkey
say() { printf '\n== %s\n' "$*"; }

[ -f "$KEYFILE" ] || { echo "No join key at $KEYFILE." >&2; exit 1; }

say "Installing Tailscale"
if ! command -v tailscale >/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi

say "Joining the tailnet"
# --ssh is deliberately off: Tailscale SSH would be a second way in, and the
# point of the tagged access rule is that this machine is a dead end.
#
# The tag is what the access rule names, and it is also what stops this machine
# opening a connection to anything of yours.
sudo tailscale up \
    --auth-key "file:$KEYFILE" \
    --hostname homesh-standby \
    --advertise-tags tag:homesh-standby \
    --ssh=false
sudo rm -f "$KEYFILE"

say "Serving HTTPS on the tailnet"
# Tailscale terminates TLS with a certificate it gets itself, and forwards to
# the stack on the loopback address. Nothing is published on the public
# address, which is why the security list can close SSH and leave nothing open.
sudo tailscale serve --bg http://127.0.0.1:8080

say "What it is called"
tailscale status --json | python3 -c "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))"
