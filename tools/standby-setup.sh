#!/usr/bin/env bash
# Provision the Oracle standby: Docker, the repository, the stack.
#
# Run on the standby itself, by tools/deploy-standby.ps1, which puts the
# configuration in place first. It is idempotent -- running it again updates the
# checkout and rebuilds, which is also how the standby is upgraded.
#
# Nothing here knows any address or secret. The .env and the Drive credential
# are uploaded separately, so this file can live in a public repository.
set -euo pipefail

REPO=https://github.com/shahafdu/Homesh.git
DIR=/opt/homesh
COMPOSE="docker compose -f docker-compose.standby.yml"

say() { printf '\n== %s\n' "$*"; }

# A freshly created instance is still running cloud-init, which holds the apt
# lock. Without this the first install fails on a machine that is otherwise fine.
say "Waiting for first boot to finish"
sudo cloud-init status --wait >/dev/null 2>&1 || true

say "Installing Docker and git"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq docker.io docker-compose-v2 git
sudo systemctl enable --now docker
# So that a later login can run docker without sudo. This shell does not have
# the new group yet, which is why everything below still says sudo.
sudo usermod -aG docker "$USER"

say "Fetching Homesh"
if [ -d "$DIR/.git" ]; then
    sudo git -C "$DIR" fetch --quiet origin main
    sudo git -C "$DIR" reset --quiet --hard origin/main
else
    sudo mkdir -p "$DIR"
    sudo git clone --quiet "$REPO" "$DIR"
fi
sudo git -C "$DIR" log --oneline -1

say "Installing the configuration"
# Uploaded to /tmp by the deploy script, which is the only thing that knows
# either of them. Moved rather than copied, so they do not sit in /tmp.
sudo install -m 600 -o root -g root /tmp/homesh.env "$DIR/.env"
sudo rm -f /tmp/homesh.env
sudo mkdir -p "$DIR/.secrets"
if [ -f /tmp/homesh-gdrive.json ]; then
    sudo install -m 600 -o root -g root /tmp/homesh-gdrive.json "$DIR/.secrets/gdrive.json"
    sudo rm -f /tmp/homesh-gdrive.json
fi
sudo chmod 700 "$DIR/.secrets"

say "Building and starting"
# Building here rather than pulling an image: the repository is public, this
# machine has the cores to spare while it is idle, and it means the standby runs
# the same commit as the PC with nothing to publish in between.
cd "$DIR"
sudo $COMPOSE up -d --build

say "Waiting for it to answer"
for _ in $(seq 1 60); do
    if body=$(curl -fsS --max-time 5 http://127.0.0.1:8080/api/health 2>/dev/null); then
        echo "$body"
        case "$body" in
            *'"role":"standby"'*)
                say "The standby is up, and knows it is the standby."
                exit 0
                ;;
            *)
                echo "It answered, but not as a standby -- check HOMESH_ROLE in .env." >&2
                exit 1
                ;;
        esac
    fi
    sleep 5
done

echo "It never answered. Logs:" >&2
sudo $COMPOSE logs --tail 40 api >&2
exit 1
