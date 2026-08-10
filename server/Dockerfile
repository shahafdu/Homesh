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
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
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
RUN useradd --create-home --uid 10001 homesh \
 && mkdir -p /var/lib/homesh/cache \
 && chown -R homesh:homesh /app /var/lib/homesh
USER homesh

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
