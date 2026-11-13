# 01 - AI foundations: the worker, the queue, the model router, Settings -> AI

Read [`README.md`](README.md) first; its rules apply to everything here.

What exists today, and must be kept working:

- `server/app/ai.py` -- `ask()`, `check()`, `status()`, `history()`, the spend
  gate, and `_openrouter()`. One model, from `AI_MODEL` in `.env`.
- `server/app/ai_api.py` -- `GET /api/ai/status`, `GET /api/ai/history`.
- `ai_calls` (migration 026) and `users.may_spend_on_ai`.
- `server/tests/test_ai.py`.

This track adds: a second container that does slow work at night (A2), a router
that chooses among several models and providers (A4), and the page where an
administrator controls both.

---

## 1. Vocabulary

| Word | Meaning here |
|---|---|
| **server** | the existing `api` container (`app.main`) |
| **worker** | the new container, same code base, started as `python -m app.worker` |
| **job** | one row in `ai_jobs`: one piece of slow work on one item |
| **local model** | a model file run by the worker: embeddings, faces, speech |
| **provider** | a company answering chat requests over the network: OpenRouter, Gemini |
| **task kind** (`chat`, `bulk`, `translate`) | what a provider call is for; each has its own ordered list of models |

---

## 2. A1 - the spike's output

Not built from this document (see README section 1). The spike writes
`docs/design/SPIKE_RESULTS.md` with these sections, and later tasks read them:

```
## Embedding model
name:            <identifier stored in embeddings.model, e.g. "eg2-256">
runtime:         <python package and version>
stored_dim:      <integer; the dimension written to the database>
normalise:       yes|no
files:           <one line per file: url, sha256, bytes, licence>
encode_text:     <the exact python call, 5-10 lines>
encode_image:    <the exact python call>
encode_audio:    <the exact python call, or "not supported">
seconds_per_photo, seconds_per_clip_frame: <measured on the target machine>
query_languages: native | translate-first
relevance_floor: <cosine distance above which a hit is not shown>

## Face models
detector, recogniser: <names>, files as above
face_dim, match_threshold, suggest_threshold: <numbers>
min_face_px, min_quality: <numbers>

## Speech model
name, runtime, files, compute_type, seconds_per_second_of_audio
```

The designs below are written against an interface, so they do not change
whichever models are chosen.

---

## 3. A2 - the worker and its queue

### 3.1 Migration `ai_jobs`

```sql
-- Slow work, one row per piece. The worker leases a row, does it, and marks it.
-- A lease that lapses is simply taken again, so a restart loses nothing.
CREATE TABLE IF NOT EXISTS ai_jobs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind        TEXT NOT NULL,
    item_id     UUID REFERENCES items(id) ON DELETE CASCADE,
    -- Which model the result is for. Part of the identity: choosing a new
    -- model makes new jobs rather than marking old ones undone.
    model       TEXT NOT NULL DEFAULT '',
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    state       TEXT NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued','running','done','failed','waiting')),
    -- Lower runs first. 0 = a person is waiting; 100 = night work.
    priority    INT NOT NULL DEFAULT 100,
    attempts    INT NOT NULL DEFAULT 0,
    not_before  TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_by    TEXT,
    lease_until TIMESTAMPTZ,
    last_error  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS ai_jobs_identity
    ON ai_jobs (kind, item_id, model) WHERE item_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ai_jobs_next
    ON ai_jobs (priority, id) WHERE state = 'queued';
CREATE INDEX IF NOT EXISTS ai_jobs_state ON ai_jobs (kind, state);

-- One row. Everything an administrator sets on the AI page. In the database so
-- it travels with backups; holds no secret.
CREATE TABLE IF NOT EXISTS ai_settings (
    only_row   BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (only_row),
    data       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID REFERENCES users(id) ON DELETE SET NULL
);
INSERT INTO ai_settings (only_row) VALUES (TRUE) ON CONFLICT DO NOTHING;
```

States:

| State | Meaning | Leaves it when |
|---|---|---|
| `queued` | ready, once `not_before` has passed | leased |
| `running` | leased by a worker until `lease_until` | finished, or the lease lapses |
| `waiting` | the file's bytes are not reachable (drive off, Drive offline) | `not_before` passes (6 hours), then `queued` again; does **not** count an attempt |
| `done` | the result is stored | never (a new model makes a new row) |
| `failed` | five attempts failed | an administrator presses "Try the failed ones again" |

`ai_jobs` and `ai_settings` need no line in the test fixture: `ai_jobs` rows
cascade from `items`; add `DELETE FROM ai_jobs WHERE item_id IS NULL` and
`UPDATE ai_settings SET data = '{}'` to the `db` fixture.

### 3.2 `server/app/aijobs.py` (new; used by server and worker)

Pure queue logic, no model code, so the server can import it.

```python
@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    item_id: UUID | None
    model: str
    payload: dict
    attempts: int

LEASE = timedelta(minutes=10)
MAX_ATTEMPTS = 5
WAIT_FOR_BYTES = timedelta(hours=6)
BACKOFF = [timedelta(minutes=m) for m in (1, 5, 30, 120, 720)]

def enqueue(conn, kind, item_id, model="", payload=None, priority=100) -> None
    # INSERT ... ON CONFLICT (kind, item_id, model) WHERE item_id IS NOT NULL DO NOTHING

def lease(conn, worker: str, kinds: list[str], max_priority: int) -> Job | None
def extend(conn, job_id: int, worker: str) -> bool      # heartbeat; False if lost
def finish(conn, job_id: int, worker: str) -> None
def wait_for_bytes(conn, job_id: int, worker: str) -> None
def fail(conn, job_id: int, worker: str, error: str) -> None
def release_lapsed(conn) -> int
def counts(conn) -> dict[str, dict[str, int]]            # kind -> state -> n
def retry_failed(conn, kind: str | None) -> int
```

`lease` is one statement, so two workers can never take the same row:

```sql
UPDATE ai_jobs
SET state = 'running', lease_by = :w, lease_until = now() + interval '10 minutes',
    attempts = attempts + 1
WHERE id = (
    SELECT id FROM ai_jobs
    WHERE state = 'queued' AND not_before <= now()
      AND kind = ANY(:kinds) AND priority <= :maxp
    ORDER BY priority, id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id, kind, item_id, model, payload, attempts
```

`finish`, `fail`, `wait_for_bytes` and `extend` all carry
`WHERE id = :id AND lease_by = :w AND state = 'running'`, so a worker that lost
its lease cannot overwrite the worker that took over.

`fail` sets `state = 'failed'` when `attempts >= MAX_ATTEMPTS`, otherwise
`state = 'queued'` with `not_before = now() + BACKOFF[attempts - 1]`.
`last_error` is `f"{type(exc).__name__}: {exc}"[:400]`. It may contain a file
name; it is shown to administrators only and never logged at INFO.

`wait_for_bytes` sets `state = 'waiting'`, `not_before = now() + 6 hours`,
`attempts = attempts - 1`. `release_lapsed` turns `running` rows with a past
`lease_until` and `waiting` rows with a past `not_before` back to `queued`.

### 3.3 `server/app/worker.py` (new) -- the process

```
python -m app.worker
  |- thread 1: the internal HTTP API (section 3.6), uvicorn on :8090
  `- main thread: the loop
```

The loop, in words:

1. `os.nice(19)` once at start (ignore `OSError`).
2. Every pass: `release_lapsed`; read `ai_settings`.
3. If `HOMESH_ROLE` is `standby`: never run batch work. Sleep 60 s, repeat.
   (The standby runs the worker only for section 3.6.)
4. Decide `max_priority`: `100` if not paused and inside the night window,
   otherwise `0`. Priority-0 jobs (a person is waiting) run at any hour and
   while paused.
5. Once per hour while `max_priority == 100`, run every registered **planner**
   (below).
6. `lease(...)`. No job: sleep 30 s. A job: run its handler; while it runs, a
   timer calls `extend` every 3 minutes and sets a "stop" flag if `extend`
   returns False.
7. Map the handler's result to `finish` / `wait_for_bytes` / `fail`.
8. On SIGTERM: finish the current job if it ends within 20 s, otherwise leave
   the lease to lapse. Never mark a job done that was not finished.

Handlers and planners are registered in one dictionary so a later task adds a
kind by adding one entry:

```python
class BytesUnavailable(Exception): ...    # handler raises -> wait_for_bytes

@dataclass(frozen=True)
class Kind:
    name: str
    handler: Callable[[Job, "Context"], None]
    planner: Callable[[Connection, "Context"], int]   # returns rows enqueued
    needs: tuple[str, ...]                            # local model names

KINDS: dict[str, Kind] = {}      # filled by app/workkinds/*.py at import
```

`Context` gives a handler what it may use and nothing else: `open_item(item_id)`
(returns a connector and relative path through `stream.resolve_playable`, and
raises `BytesUnavailable` when `playable_now` says no), `models` (lazy loaders),
`settings`, and `should_stop()`.

A planner inserts jobs for items that lack a result, at most 5000 per pass,
oldest content first. Example shape, used by every embedding kind:

```sql
INSERT INTO ai_jobs (kind, item_id, model)
SELECT :kind, i.id, :model FROM items i
WHERE i.kind = 'photo'
  AND NOT EXISTS (SELECT 1 FROM ai_jobs j
                  WHERE j.kind = :kind AND j.item_id = i.id AND j.model = :model)
ORDER BY i.created_at NULLS LAST
LIMIT 5000
ON CONFLICT DO NOTHING
```

A kind whose local model file is missing is skipped by the loop and reported as
"model not installed" by the status endpoint. It never fails its jobs.

### 3.4 The night window

Stored in `ai_settings.data`: `{"timezone": "<IANA name>", "night": {"start":
"01:00", "end": "07:00"}, "paused": false}`. Defaults when absent: UTC,
01:00-07:00, not paused.

```python
def in_window(now_utc: datetime, tz: str, start: str, end: str) -> bool
```

Convert with `zoneinfo.ZoneInfo(tz)`; an unknown zone falls back to UTC. When
`start < end` the window is `[start, end)`; when `start > end` it wraps midnight;
when equal it is the whole day. This function gets its own unit tests,
including both daylight-saving changes.

The page sets `timezone` from the administrator's browser
(`Intl.DateTimeFormat().resolvedOptions().timeZone`) the first time the
settings are saved, and shows it: "Times are in <zone>."

### 3.5 Compose and image

`server/Dockerfile`: name the existing final stage `server`, and add a stage
after it:

```dockerfile
FROM server AS worker
USER root
COPY server/requirements-worker.txt ./
RUN pip install --require-hashes -r requirements-worker.txt
USER homesh
CMD ["python", "-m", "app.worker"]
```

Put `target: server` on the `api` service's build. `docker-compose.yml`:

```yaml
  worker:
    build:
      context: .
      dockerfile: server/Dockerfile
      target: worker
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    # Named one by one. The worker signs no links and reads no backups, so it
    # is not given the keys for either.
    environment:
      DATABASE_URL: ${DATABASE_URL}
      MASTER_KEY: ${MASTER_KEY}
      HOMESH_ROLE: ${HOMESH_ROLE:-primary}
      AI_PROVIDER: ${AI_PROVIDER:-}
      AI_API_KEY: ${AI_API_KEY:-}
      AI_BASE_URL: ${AI_BASE_URL:-https://openrouter.ai/api/v1}
      AI_MODEL: ${AI_MODEL:-}
      AI_MONTHLY_CAP_MICROS: ${AI_MONTHLY_CAP_MICROS:-0}
      GEMINI_API_KEY: ${GEMINI_API_KEY:-}
      GEMINI_NO_BILLING: ${GEMINI_NO_BILLING:-false}
      MEDIA_ROOTS: ${MEDIA_ROOTS:-}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
    volumes:
      - media-cache:/var/lib/homesh/cache
      - ai-models:/var/lib/homesh/models:ro
      - ${MEDIA_HOST_PATH:-./sample-media}:/media/library:ro
      - ./.secrets:/run/secrets:ro
    expose:
      - "8090"
    read_only: true
    tmpfs:
      - /tmp:size=2g
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 256
    mem_limit: ${WORKER_MEMORY:-3g}
    cpus: ${WORKER_CPUS:-2.0}
    cpu_shares: 128
```

Before writing this, check how `.env` supplies the database address today
(`DATABASE_URL`, or the `POSTGRES_*` parts); pass whatever `config.py` reads.
Add `ai-models:` to `volumes:`. Add `WORKER_MEMORY`, `WORKER_CPUS`,
`GEMINI_API_KEY`, `GEMINI_NO_BILLING` to `.env.example` with one-line ASCII
comments.

**Granted folders.** `tools/grant-folder.ps1` writes
`docker-compose.override.yml` with the granted folders under `services: api:`.
Change it to write the same `volumes:` list under `worker:` as well, read-only,
so the worker reaches exactly what the server reaches and nothing more. Keep the
file pure ASCII. `tools/start-homesh.ps1` must rewrite an override file that
lacks the `worker:` block (an install from before this change).

**Model files.** New `tools/fetch-models.ps1`: runs a one-off container
(`docker compose run --rm -v ai-models:/models --entrypoint python worker -m
app.fetch_models`) that downloads each file listed in a manifest
(`server/app/modelfiles.py`, a constant list copied from `SPIKE_RESULTS.md`:
name, url, sha256, bytes), verifies the SHA-256, and writes it under
`/models/<name>`. A file whose hash does not match is deleted and the script
exits non-zero. At load time the worker checks the size only (hashing gigabytes
on every start is waste); the hash is checked at download.

### 3.6 The worker's internal API

Some work cannot wait for night: turning a typed question into a vector, and
turning a spoken command into text. The server asks the worker over the compose
network. The port is not published; nothing outside Docker can reach it.

`server/app/worker_api.py` (new, a small FastAPI app, run only in the worker):

| Route | Body | Answers |
|---|---|---|
| `GET /health` | - | `{"ok": true, "models": {"embed": true, "faces": false, "speech": true}}` |
| `POST /embed-text` | `{"text": "..."}` (1-500 chars) | `{"model": "...", "vector": [..]}` |
| `POST /transcribe` | raw audio bytes, `Content-Type` as sent by the client, at most 2 MB | `{"text": "...", "seconds": 4.2}` |

Every route except `/health` requires `Authorization: Bearer <token>`, where

```python
token = hmac.new(base64.urlsafe_b64decode(MASTER_KEY), b"homesh-worker-v1",
                 hashlib.sha256).hexdigest()
```

compared with `hmac.compare_digest`. Both sides derive it; no new secret is
introduced. One request at a time per route (an `asyncio.Lock`); a second caller
waits up to 10 s then gets 503.

`server/app/workerclient.py` (new, used by the server):

```python
class WorkerUnavailable(Exception): ...
async def embed_text(text: str) -> tuple[str, list[float]]     # timeout 8 s
async def transcribe(audio: bytes, content_type: str) -> str   # timeout 30 s
async def health() -> dict | None                              # timeout 2 s, cached 30 s
```

`WORKER_URL` is a new setting, default `http://worker:8090`. Every caller treats
`WorkerUnavailable` as "this feature is off right now" and degrades (search
falls back to file names; the microphone button shows "Speech is not available
right now"). It is never a 500.

### 3.7 Endpoints the server adds for the queue (in `ai_api.py`)

All administrators only. All refused on the standby (the default).

| Route | Body | Answers |
|---|---|---|
| `GET /api/ai/work` | - | see below |
| `POST /api/ai/work/pause` | `{"paused": true}` | `{"paused": true}` |
| `POST /api/ai/work/retry` | `{"kind": "embed-photo"}` or `{}` for all | `{"requeued": 12}` |

```json
{
  "paused": false,
  "in_window": true,
  "night": {"start": "01:00", "end": "07:00"},
  "timezone": "Europe/Paris",
  "worker": "running",
  "kinds": [
    {"kind": "embed-photo", "label": "Photos, for search",
     "installed": true, "queued": 41200, "waiting": 310, "running": 1,
     "done": 8800, "failed": 3, "done_last_24h": 2100, "nights_left": 20,
     "last_error": "OSError: ..."}
  ]
}
```

`worker` is `"running"`, `"not answering"` (health failed) or `"not installed"`.
`nights_left` is `ceil(queued / done_last_24h)`, or `null` when nothing was done
in the last day. `label` comes from a constant table in `ai_api.py`; the page
never shows a raw kind name.

### 3.8 Tests for A2 (`server/tests/test_aijobs.py`)

- two leases in a row never return the same job;
- a lapsed lease is taken again, and the first worker's `finish` then changes
  nothing;
- `fail` backs off, and the fifth failure ends in `failed`;
- `wait_for_bytes` does not consume an attempt and returns after `not_before`;
- priority 0 is leased when `max_priority` is 0, priority 100 is not;
- `in_window` across midnight, equal start and end, an unknown zone, and both
  daylight-saving changes;
- enqueueing the same (kind, item, model) twice makes one row;
- deleting an item deletes its jobs;
- `/api/ai/work*` answer 403 to a non-administrator;
- the worker token: a wrong token gets 401 from `/embed-text` (test the app in
  `worker_api.py` with a fake embedder).

---

## 4. A4 - the model router

### 4.1 Configuration

Keys stay in `.env`, as now. New variables:

| Variable | Meaning |
|---|---|
| `AI_API_KEY` (existing) | the OpenRouter key |
| `GEMINI_API_KEY` | a Gemini API key |
| `GEMINI_NO_BILLING` | `true` when that key's Google project has no billing account. Gemini is offered **only** when this is `true`: a project with no billing account cannot be charged, which is the rule this project uses for every provider. With `false`, Gemini is not offered and the page says why |

`config.py` gains `gemini_api_key: str = ""`, `gemini_no_billing: bool = False`,
`worker_url: str = "http://worker:8090"`.

### 4.2 Migration `ai_models`

```sql
-- What each provider offers, refreshed daily. A cache of public information
-- plus this server's own experience of each model. No secret.
CREATE TABLE IF NOT EXISTS ai_models (
    id            TEXT PRIMARY KEY,          -- "<provider>:<model>"
    provider      TEXT NOT NULL,             -- openrouter | gemini
    model         TEXT NOT NULL,
    label         TEXT NOT NULL,
    free          BOOLEAN NOT NULL,
    tools         BOOLEAN NOT NULL,
    context       INT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    gone_at       TIMESTAMPTZ,
    cooling_until TIMESTAMPTZ,
    last_ok_at    TIMESTAMPTZ,
    last_error    TEXT,
    last_error_at TIMESTAMPTZ
);
ALTER TABLE ai_calls ADD COLUMN IF NOT EXISTS task TEXT;      -- chat|bulk|translate|test
ALTER TABLE ai_calls ADD COLUMN IF NOT EXISTS fallback BOOLEAN NOT NULL DEFAULT FALSE;
```

Add `DELETE FROM ai_models` to the `db` fixture.

### 4.3 `server/app/aicatalog.py` (new)

```python
async def refresh() -> dict    # {"openrouter": 312, "gemini": 9, "gone": 2, "now_paid": 1}
```

- **OpenRouter**: `GET {AI_BASE_URL}/models` (no key needed; send it if present).
  For each entry: `free` is true only when `pricing.prompt` and
  `pricing.completion` both parse to zero **and** the id ends in `:free`. Both
  conditions, deliberately: a model is treated as paid unless everything says it
  is free. `tools` is `"tools" in supported_parameters`. `context` is
  `context_length`.
- **Gemini** (only when a key is set): `GET
  https://generativelanguage.googleapis.com/v1beta/models` with the key in the
  `x-goog-api-key` header, never in the URL. Keep entries whose
  `supportedGenerationMethods` contains `generateContent`. `free` is
  `GEMINI_NO_BILLING`. `tools` is true. `context` is `inputTokenLimit`. The
  stored `model` drops the leading `models/`.
- Upsert by id, set `seen_at`. A row not seen in this refresh gets `gone_at =
  now()`; a row seen again gets `gone_at = NULL`.
- Called from the upkeep loop once a day (add a call beside the existing sweeps
  in `upkeep.py`), and by the "Refresh" button. A failed refresh changes
  nothing and is logged once.

Verify both response shapes against the providers' current documentation before
writing the parser, and keep a recorded sample of each as a test fixture.

### 4.4 Changes to `server/app/ai.py`

Keep `ask()` working exactly as it does (existing tests must pass unchanged);
re-implement it as a thin call to the new function with `task="bulk"`.

```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str            # JSON text exactly as the model sent it

@dataclass(frozen=True)
class Reply:
    text: str
    tool_calls: tuple[ToolCall, ...]
    model_id: str             # "<provider>:<model>"
    prompt_tokens: int
    completion_tokens: int
    cost_micros: int
    fallback: bool            # True when the first choice was not what answered

async def chat(*, task: str, purpose: str, messages: list[dict],
               user_id: UUID | None = None, tools: list[dict] | None = None,
               max_tokens: int = 1024) -> Reply
```

`chat` in order:

1. `candidates(task)`: the ordered ids from `ai_settings.data["tasks"][task]`.
   If that list is empty or missing, the single candidate is
   `f"openrouter:{AI_MODEL}"` -- today's behaviour, so an install that never
   opens the new page keeps working.
2. Drop a candidate when: its provider has no key; its row has `gone_at` set;
   `cooling_until` is in the future; `tools` were requested and the row says it
   has none.
3. For each remaining candidate, apply the **spend gate** (unchanged rules,
   now per candidate): a model that is not `free` needs a cap above zero, an
   account that may spend, and spend below the cap. A candidate that fails the
   gate is skipped.
4. Call the first candidate that passes. Both providers speak the same
   chat-completions format, so there is one HTTP function:

   ```python
   async def _chat_completions(base_url, key, model, messages, tools, max_tokens,
                               ask_for_cost: bool) -> Reply
   ```

   OpenRouter: `base_url = AI_BASE_URL`, `ask_for_cost=True` (adds
   `"usage": {"include": true}`). Gemini: `base_url =
   "https://generativelanguage.googleapis.com/v1beta/openai"`, cost is 0 by the
   rule in 4.1.
5. On HTTP 429, a 5xx, or a timeout: record the attempt (`outcome='error'`), set
   `cooling_until = now() + (Retry-After seconds, at least 60, at most 3600;
   600 when absent)`, and try the next candidate. On 401 or 402: record, set
   `cooling_until = now() + 1 hour` and `last_error`, try the next.
6. On success: record with `fallback = (this was not the first candidate in the
   configured list)`, set `last_ok_at`, return.
7. No candidate left: record one row with `outcome='refused'` and raise
   `Refused` with a message a person can act on:
   - "no AI provider is set up on this server" (no keys);
   - "every model in the list is busy right now. Try again in a few minutes."
     (all cooling);
   - the existing paid-model messages (gate).

`_record` gains `provider`, `task` and `fallback` parameters; stop reading the
provider from settings inside it.

**Never log `messages`, `tools`, a reply's text or a key.** Log the model id,
the task, the status code and the duration.

### 4.5 Settings shape and validation

`ai_settings.data`, complete:

```json
{
  "timezone": "UTC",
  "night": {"start": "01:00", "end": "07:00"},
  "paused": false,
  "tasks": {"chat": [], "bulk": [], "translate": []},
  "provider_folders": [],
  "describe_people": true,
  "questions_per_day": 100,
  "bulk_batches_per_night": 100,
  "tag_repair": false
}
```

`PUT /api/ai/settings` validates with a Pydantic model: times match
`^\d\d:\d\d$` and parse; `timezone` is accepted only if `ZoneInfo` loads it;
each task list has at most 8 ids, each of which exists in `ai_models`, and for
`chat` each has `tools = true`; a paid model id is accepted only when the cap is
above zero; `provider_folders` are ids of existing sources; `questions_per_day`
is 1-1000; `bulk_batches_per_night` is 0-1000; `describe_people` and
`tag_repair` are booleans (used by `04-assistant.md` and `05-music.md`; add
each key when its task is built, not before). Unknown keys are refused (`extra="forbid"`). The change is written to
`audit_log` with `security.audit(conn, "ai.settings", user.id, {"changed":
[...key names...]}, ip)`.

`provider_folders` is explained in `04-assistant.md` section 3. Its default is
"every folder whose audience is `everyone`", computed when the list is absent;
an explicit empty list means none.

### 4.6 Endpoints (in `ai_api.py`)

| Route | Who | Body | Answers |
|---|---|---|---|
| `GET /api/ai/status` (extend) | anyone signed in | - | adds `"features": {"assistant": bool, "meaning_search": bool, "voice": bool, "faces": bool}` for everyone |
| `GET /api/ai/settings` | admin | - | the JSON above, defaults filled in, plus `"providers": {"openrouter": {"key": true}, "gemini": {"key": true, "usable": false, "why": "GEMINI_NO_BILLING is not set to true"}}` and `"cap_micros"`, `"spent_micros"` |
| `PUT /api/ai/settings` | admin | any subset of the settings keys | the full settings |
| `GET /api/ai/models` | admin | query `free`, `tools` (booleans), `q` (text) | list of model rows; paid ones omitted while the cap is zero |
| `POST /api/ai/models/refresh` | admin | - | the counts from `refresh()` |
| `POST /api/ai/models/test` | admin | `{"id": "..."}` | `{"ok": true, "ms": 840}` or `{"ok": false, "why": "..."}` |
| `GET /api/ai/usage` | admin | - | per model today: `calls`, `errors`, `fallbacks`; and `month_spent_micros` |

`features` in `/status` is how the web app decides what to show: `assistant` is
true when at least one `chat` candidate would pass step 2; `meaning_search`
when the worker's health says the embedding model is loaded and at least one
embedding exists; `voice` when the speech model is loaded; `faces` when at
least one face exists.

`models/test` sends one request -- system: "Reply with the single word: ready",
`max_tokens=5`, `purpose="test"`, `task="test"` -- straight to that model
(bypassing the candidate list but **not** the spend gate) and reports the
result. Rate-limited with `throttle.hit("ai-test", str(user.id), Limit(10,
timedelta(minutes=5)))`.

All of these are refused on the standby by default; the page there is read-only
(section 5.6).

### 4.7 Tests for A4 (extend `server/tests/test_ai.py`)

Mock the HTTP layer with `httpx.MockTransport`; never call a provider in tests.

- with no settings, `chat` uses `AI_MODEL` (the old behaviour);
- first model answers 429 -> second answers; two `ai_calls` rows, the second
  with `fallback = true`; the first model is cooling;
- `Retry-After` is honoured and clamped;
- a paid model in the list is skipped while the cap is zero, and the free one
  after it is used;
- a paid model is refused for an account without `may_spend_on_ai`;
- every candidate cooling -> `Refused`, one row with `outcome='refused'`;
- Gemini is never a candidate when `GEMINI_NO_BILLING` is false;
- the Gemini key is sent in a header and appears in no URL (assert on the
  recorded request);
- catalogue: a model that is `:free` by name but priced above zero is stored as
  paid; a model missing from the next refresh gets `gone_at`;
- `PUT /api/ai/settings` refuses an unknown key, a bad time, an unknown zone, a
  model without tools in `chat`, and a paid model while the cap is zero;
- no response from any `/api/ai/*` route contains either key (set both keys to
  a marker string and search every response body for it);
- every admin route answers 403 to a non-administrator.

---

## 5. The page: "Assistant and AI" (`web/src/Ai.tsx`, `web/src/ai.ts`)

**Mockup first**: publish a mockup of this page and get the owner's answer
before building it.

Opened from the same menu that opens People and Folders, for administrators
only: add `onOpenAi?: () => void` to `Browser`'s props, passed from `App.tsx`
exactly as `onOpenPeople` is. A wide sheet. Five sections, each a
`<div className="setting">`, in this order.

### 5.1 Top: one sentence of state

```
Assistant and AI                                              [Close]
The assistant is on. Night work runs 01:00-07:00 and is 18% done.
```

One sentence, built from `/status` and `/work`. Other forms: "The assistant is
off: no provider key is set on this server. (How to set one)" -- the link opens
`docs/AI.md` on GitHub in a new tab. "Night work is paused."

### 5.2 Providers

```
Providers
  OpenRouter   Key present    [Test]   Works - 0.8 s
  Gemini       Key present    Not used: the server has not been told this
                              key's Google project has no billing account.
                              Set GEMINI_NO_BILLING=true in .env once you
                              have checked.
  Keys are set in the .env file on the PC. This page never shows them.

  Spending   No limit is set, so only free models can be used.
             (or: 0.42 of 5.00 used this month)
```

No input box for a key exists anywhere on the page: a key typed into a browser
would have to be stored in the database, and it must not be.

### 5.3 Which model does what

Three blocks, one per task, each with the same controls:

```
Answering questions and doing what you ask
Needs a model that can use tools.
  1. Gemini Flash         free   [up] [down] [Test] [Remove]
  2. <model label>        free   busy until 14:32   [up] [down] [Test] [Remove]
  [Add a model...]
  If the first is busy or over its free limit, the next one answers.
```

- Task titles: "Answering questions and doing what you ask" (`chat`),
  "Background tagging" (`bulk`), "Translating a search" (`translate`).
- "Add a model..." opens a picker in place: a search box and a list from
  `/api/ai/models` filtered for the task (`tools=1` for chat). Each row: label,
  provider, "free" or "paid", context size ("128k"). Paid rows are absent while
  the cap is zero, with one line under the list: "Paid models are hidden because
  no monthly limit is set."
- Reordering is by up and down buttons, not drag: it must work with a keyboard
  and on a phone.
- A warning row replaces a model that is gone or now paid: "This model is no
  longer offered. The next one is answering instead. [Remove]". Never remove it
  silently.
- Saving is immediate on each change (`PUT /api/ai/settings`), with "Saved"
  appearing beside the block for two seconds (`role="status"`). On failure the
  list snaps back and the server's message is shown.
- An empty list reads: "Using the model from the server's settings file
  (<AI_MODEL>). Add a model here to choose others."

### 5.4 Night work

```
Night work
Slow jobs run at night so the PC stays free in the day.
  From [01:00] to [07:00]    Times are in <zone>.
  [Pause]   (or: Paused. [Resume])

  Photos, for search        8,800 of 50,310 done   about 20 nights left
    310 are waiting for a drive that is off.
    3 could not be done.  [Show why]  [Try those again]
  Faces                      Not installed
```

- Time inputs are `<input type="time">`. Saved on blur.
- "Show why" expands the `last_error` text for that kind in place, as text.
- "Not installed" rows explain in one line: "Run tools\fetch-models.ps1 on the
  PC to install it."
- The block refreshes every 30 s while the sheet is open, and stops when it
  closes.
- When the worker is "not answering": a line at the top of the block in the
  danger colour with `role="alert"`: "The night worker is not running. Start
  Homesh again on the PC."

### 5.5 What the assistant may talk about, and what was done

"What the assistant may describe to a provider" (the folder list) and the
history and audit views are specified in `04-assistant.md` sections 3 and 6.
Until C1 is built this section shows the existing call history from
`GET /api/ai/history` as a plain list: when, who, what for, which model, the
outcome, and tokens.

### 5.6 On the standby

When `health.role === "standby"`: every control is disabled and one line sits
at the top: "These settings are changed on the main server. This is what they
were at the last backup." Night-work progress is not shown (the standby does
none).

### 5.7 States to design

- loading: "Loading..." in the muted colour under the title;
- not an administrator: the menu entry is absent (the routes refuse anyway);
- no keys at all: sections 5.3 and 5.4 are replaced by the one sentence in 5.1;
- a failed save: the server's `detail`, beside the control that failed.
