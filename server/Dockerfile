# Build context is the repository root (see docker-compose.yml).
# Multi-arch by construction: no arch-specific base or wheels.
# Built for linux/amd64 (PC, VPS) and linux/arm64 (Pi, Oracle Ampere).

# ── Stage 1: web client ─────────────────────────────────────────────────────
FROM node:22-slim AS web

WORKDIR /web
COPY web/package.json web/package-lock.json* ./
# `npm ci` when a lockfile is present, `npm install` on first run before one exists.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY web/ ./
RUN npm run build


# ── Stage 2: API ────────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg is used for remux and thumbnails — not transcoding (ARCHITECTURE.md §3.2).
#
# LibreOffice renders the office formats no browser can display — doc, docx, xls,
# xlsx, ppt, pptx and the rest — to PDF, which every browser can. It is a heavy
# dependency for a media server and it is still the right one: the alternatives
# are a different JavaScript library per format with worse fidelity, or handing
# the file to Microsoft's or Google's online viewer, which means uploading a
# private document to a third party in order to read it. Conversions are cached.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      libreoffice-writer-nogui \
      libreoffice-calc-nogui \
      libreoffice-impress-nogui \
      fonts-dejavu \
      fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first so code edits don't reinstall the world.
COPY server/pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY server/app ./app
COPY server/migrations ./migrations

# Serving the client from the API keeps everything same-origin, which is what
# makes session cookies and the WebAuthn RP ID straightforward.
COPY --from=web /web/dist ./static

# Never run as root.
# Both directories exist in the image, and that is load-bearing rather than
# tidy: Docker initialises an empty named volume from whatever is at the mount
# point, ownership included. Without the backups directory here, the volume is
# created owned by root and the server -- which does not run as root -- cannot
# write a backup into it. Found by taking one.
RUN useradd --create-home --uid 10001 homesh \
 && mkdir -p /var/lib/homesh/cache /var/lib/homesh/backups \
 && chown -R homesh:homesh /app /var/lib/homesh
USER homesh

# Which code this is: "2026.09.19-85df90c", the commit's date and short id,
# passed in by tools/start-homesh.ps1 and tools/standby-setup.sh. Last, so a new
# stamp does not throw away the cached layers above it.
ARG HOMESH_BUILD=
ENV HOMESH_BUILD=${HOMESH_BUILD}

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
