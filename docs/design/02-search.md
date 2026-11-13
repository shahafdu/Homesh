# 02 - Search by meaning

Read [`README.md`](README.md) first. Needs A1 (models chosen), A2 (worker and
queue) from [`01-ai-foundations.md`](01-ai-foundations.md).

What exists today: `GET /api/search` in `server/app/library.py` -- file-name
search by trigram similarity, filtered by the caller's scope after the query.
The search box is in `web/src/Browser.tsx`; the client call is `search()` in
`web/src/library.ts`. The `embeddings` table from migration 001 exists and has
never been written to.

What this adds: every photo, clip and document gets vectors computed by the
worker; the search box finds things by what they show or say, beside what they
are called.

**Nothing leaves the house for this.** The question is turned into a vector by
the worker; Postgres finds the nearest vectors; no provider is involved.

---

## 1. A3 - the table and the query

### 1.1 Migration `embeddings_v2`

```sql
-- The table from migration 001 was fixed at 768 dimensions and could not say
-- where in a file a vector came from. It has never been written to; refuse to
-- continue if that is somehow untrue rather than drop somebody's work.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM embeddings LIMIT 1) THEN
        RAISE EXCEPTION 'embeddings is not empty; migrate it by hand';
    END IF;
END $$;

DROP TABLE embeddings;

CREATE TABLE embeddings (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id   UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    model     TEXT NOT NULL,
    -- What was embedded: a whole image, a video frame, a stretch of sound, or
    -- a passage of text.
    modality  TEXT NOT NULL CHECK (modality IN ('image','frame','audio','text')),
    chunk_idx INT NOT NULL DEFAULT 0,
    -- Where in the file, for frames and sound, so a result opens at that moment.
    t_ms      BIGINT,
    -- For text: the page the passage is on, where the format has pages.
    page      INT,
    -- No fixed dimension: it belongs to the model, and models change.
    vector    vector NOT NULL,
    UNIQUE (item_id, model, modality, chunk_idx)
);
CREATE INDEX embeddings_item_idx ON embeddings (item_id);
```

The migration runner applies each file exactly once, which is the only guard
this file needs. Do not add another.

### 1.2 The index, one per model

A vector index needs a fixed dimension, and the column has none. pgvector's
answer is an expression index per model. `server/app/embedstore.py` (new):

```python
# The only models the server will query or index. Filled from SPIKE_RESULTS.md.
# name -> stored dimension. An identifier is built from the name, so the name
# must match ^[a-z0-9_]+$ and that is asserted at import.
MODELS: dict[str, int] = {"<name from the spike>": 256}
CURRENT = "<name from the spike>"

def ensure_index(conn, model: str) -> None
def store(conn, item_id, model, modality, rows: list[tuple[int, int | None, int | None, list[float]]]) -> None
def nearest(conn, model, query_vec, scope, *, kinds=None, under=None, limit=60) -> list[Hit]
```

`ensure_index` runs (the name and dimension come from `MODELS`, never from a
request):

```sql
CREATE INDEX IF NOT EXISTS embeddings_<model>_hnsw
ON embeddings USING hnsw ((vector::vector(<dim>)) vector_cosine_ops)
WHERE model = '<model>'
```

It is called once by the worker when it first stores a vector for a model.
Building it over an already-filled table takes minutes and blocks writes to
that table only; that is acceptable at night.

`store` deletes the item's existing rows for (model, modality) and inserts the
new ones in one transaction, so a re-run replaces rather than duplicates.
Vectors are L2-normalised before storing when `SPIKE_RESULTS.md` says
`normalise: yes`.

Check the extension version at server start and log an error if it is below
0.8.0 (`SELECT extversion FROM pg_extension WHERE extname = 'vector'`); the
query below needs it. `ALTER EXTENSION vector UPDATE` in the migration brings an
older database up when the image has the newer library.

### 1.3 `access.scope_sql` (add to `server/app/access.py`)

Scope has to be applied **inside** the nearest-neighbour query. Filtering
afterwards, as file-name search does, would hand a restricted account a short
or empty page whenever its permitted items are not among the globally nearest.

```python
# The browsing path of a replica, built the way listings build it.
REPLICA_PATH = ("s.mount_prefix || CASE WHEN r.dir_path = '' THEN '' "
                "ELSE '/' || r.dir_path END")

def _like(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def scope_sql(scope: Scope) -> tuple[str, dict[str, str]]:
    """A WHERE fragment over aliases r (replicas) and s (sources)."""
    if scope.everything:
        return "TRUE", {}
    if scope.nothing:
        return "FALSE", {}
    parts, params = [], {}
    for n, prefix in enumerate(scope.allowed):
        params[f"sc{n}"] = prefix
        params[f"sc{n}l"] = _like(prefix) + "/%"
        parts.append(f"({REPLICA_PATH} = :sc{n} "
                     f"OR {REPLICA_PATH} LIKE :sc{n}l ESCAPE '\\')")
    return "(" + " OR ".join(parts) + ")", params
```

The fragment contains only constants and parameter names; every value is
bound. Tests must show that a prefix containing `%` or `_` matches literally.

### 1.4 The nearest-neighbour query

```sql
-- Run inside one transaction, in this order.
SET LOCAL hnsw.iterative_scan = relaxed_order;
SET LOCAL hnsw.max_scan_tuples = 40000;

SELECT e.item_id, e.modality, e.t_ms, e.page,
       e.vector::vector(<dim>) <=> CAST(:q AS vector(<dim>)) AS dist
FROM embeddings e
JOIN items i ON i.id = e.item_id
WHERE e.model = '<model>'
  AND (:kinds_all OR i.kind::text = ANY(:kinds))
  AND EXISTS (
        SELECT 1 FROM replicas r JOIN sources s ON s.id = r.source_id
        WHERE r.item_id = e.item_id
          AND <scope fragment>
          AND (:under = '' OR <REPLICA_PATH> = :under
               OR <REPLICA_PATH> LIKE :under_like ESCAPE '\')
      )
ORDER BY dist
LIMIT :n
```

- `<model>` and `<dim>` are written into the string from `MODELS`; that is what
  lets the planner use the partial expression index. Everything else is bound.
- Ask for `limit * 4` rows, then keep the best row per `item_id` in Python
  (one clip has many frames), then cut to `limit`.
- Drop rows with `dist` above `relevance_floor` from `SPIKE_RESULTS.md`. A
  search for something the library does not hold must return nothing, not the
  sixty least-unlike photos.
- After the query, look up one readable replica per item for display, and
  re-check it with `access.can_read` (README rule 10: in the query *and* per
  row).

`Hit` carries: `item_id`, `filename`, `path`, `kind`, `size`, `available`
(from `library.playable_now`), `t_ms`, `page`, `why` (`"image"`, `"frame"`,
`"audio"`, `"text"`).

### 1.5 Tests for A3 (`server/tests/test_embedstore.py`)

Use a tiny fake model (`MODELS` monkeypatched to `{"fake": 4}`) and hand-made
4-number vectors.

- nearest returns items in distance order, one row per item, best frame kept;
- **a restricted account gets a full page**: 200 vectors outside its folder all
  nearer than 10 inside it; asking for 10 returns those 10;
- an account with no access gets an empty list;
- an item with one replica inside the scope and one outside is returned, with
  the inside path;
- `under` narrows to a folder and its descendants and not to a sibling whose
  name starts the same (`/a/b` must not match `/a/bc`);
- a scope prefix containing `%` or `_` is literal;
- rows beyond the relevance floor are dropped;
- `store` twice leaves one set of rows;
- deleting the item deletes its vectors.

---

## 2. The embedder interface (worker side)

`server/app/embedder.py` (new, imported only by the worker):

```python
class Embedder(Protocol):
    name: str
    dim: int
    def text(self, text: str) -> list[float]: ...
    def image(self, image: "PIL.Image.Image") -> list[float]: ...
    def images(self, images: list["PIL.Image.Image"]) -> list[list[float]]: ...
    def audio(self, pcm16k_mono: bytes) -> list[float]: ...   # may raise NotImplementedError

def load() -> Embedder     # reads files from /var/lib/homesh/models, checks sizes
```

The body of each method is the code recorded in `SPIKE_RESULTS.md`. Everything
else in this document talks to this interface only. Tests use a `FakeEmbedder`
that hashes its input into a small vector, so no test needs a model file.

Loading happens once, lazily, the first time a job or request needs it, and the
model stays in memory. If loading fails, the kinds that need it report "not
installed" and the internal API answers 503.

---

## 3. E1 - photos (`server/app/workkinds/embed_photo.py`)

Kind `embed-photo`. Planner: every item of kind `photo` without a job for
(`embed-photo`, item, `CURRENT`).

Handler:

1. `ctx.open_item(item_id)`; unavailable raises `BytesUnavailable`.
2. Get pixels the cheapest way that works: the cached preview if
   `thumbs.cache_path(item_id, "screen")` exists; otherwise read the file
   through the connector (at most 60 MB; larger is a `fail` with "too large to
   read") and decode with Pillow, applying EXIF orientation. RAW formats that
   Pillow cannot open use the embedded preview the way `thumbs.py` already
   does -- reuse its function, do not write a second decoder.
3. Downscale so the long edge is at most the model's input size times two.
4. `embedder.image(img)` -> `store(..., modality='image', rows=[(0, None, None,
   vec)])`.

Set `Image.MAX_IMAGE_PIXELS` to 200 million and treat `DecompressionBombError`
as a permanent failure for that item (`fail`, not a crash): a crafted image must
not take the worker's memory.

Tests (`test_embed_photo.py`, with `FakeEmbedder` and the `library` fixture):
a photo gets one `image` row; an unreadable file ends in `fail` with a message
and the worker continues; a file on an unavailable source ends in `waiting`;
running the planner twice enqueues nothing new.

---

## 4. E2 - clips (`workkinds/embed_video.py`, `workkinds/embed_sound.py`)

Kind `embed-video`: items of kind `video` with `duration_ms` at most 10 minutes
(longer files are films and recordings, not clips; they are skipped, not
failed -- the planner's WHERE clause excludes them).

**Frames.** One frame every 5 s, plus scene changes, at most 60 per clip.

- Get a real file for ffmpeg, the way `thumbs.generate` does. A local source
  has `connector._resolve(rel_path)`, which is the file itself: use it and copy
  nothing. A remote source (Drive) has no path: when the item is at most
  300 MB, write it to a temporary file under `/tmp` by reading
  `connector.open_range(rel_path, 0, None)`; when it is larger, finish the job
  as done-with-nothing (store no rows, and record `payload.skipped = "remote
  clip too large to sample"`), so it is not retried every night.
- Extract with one ffmpeg call per clip, writing JPEGs into a temporary
  directory:

  ```
  ffmpeg -nostdin -v error -i <path> -vf "select='isnan(prev_selected_t)+gte(t-prev_selected_t\,5)+gt(scene\,0.4)',scale=448:-2" -vsync vfr -frame_pts 1 -frames:v 60 -q:v 4 <tmp>/f_%d.jpg
  ```

  Run with `childenv.child_env()`, a timeout of 120 s, arguments as a list.
  `-frame_pts 1` puts each frame's presentation timestamp (in the stream's
  time base) in its file name; multiply by the time base from one ffprobe call
  (`-show_entries stream=time_base`) to get milliseconds. Sort by that number.
- `embedder.images(frames)` -> `store(..., modality='frame', rows=[(n, t_ms,
  None, vec), ...])`.
- Delete the temporary directory in a `finally`.

**Sound.** Kind `embed-sound`, same items, only when the embedder supports
audio. ffmpeg decodes to 16 kHz mono PCM on stdout (`-f s16le -ac 1 -ar 16000
-`), read in 10-second chunks; a chunk whose RMS is below a quiet threshold is
skipped. Store with `modality='audio'`, `t_ms` the chunk's start.

Tests: generate a 12-second test clip with ffmpeg in the test (colour bars, the
way existing streaming tests make files -- copy their approach) and expect 3-4
frame rows with increasing `t_ms`; a 20-minute duration is not planned; the
temporary directory is gone afterwards.

---

## 5. E3 - documents (`workkinds/embed_doc.py`)

Kind `embed-doc`: items of kind `doc` whose extension is text, PDF, or anything
`documents.convertible()` accepts.

1. Text out: plain text files are read directly (at most 2 MB). PDFs through
   `pypdf` page by page. Convertible office files: get the cached PDF the
   viewer already makes (`documents.py`), converting through the converter
   sandbox (`06-library-upkeep.md` section 6) if it is not cached; never run
   soffice inside the worker.
2. Split into passages of about 200 words with 40 words of overlap, at most 200
   passages per document, remembering the page.
3. `embedder.text(passage)` each -> `store(..., modality='text', rows=[(n, None,
   page, vec)])`.

The extracted text is not stored. Only vectors are.

Tests: a three-page PDF made in the test gives passages with the right pages;
an encrypted PDF fails cleanly; a 300-page document stops at 200 passages.

---

## 6. E4 - the search box

### 6.1 Endpoint (extend `GET /api/search` in `library.py`)

New query parameter `mode`: `names` (default: today's behaviour, unchanged, so
old clients and the TV keep working), `meaning`, or `both`.

Response for `names` stays a list, exactly as now. For `meaning` and `both` a
new route keeps the shapes apart: **`GET /api/search/meaning`** with `q`,
`limit` (default 40, max 100), `under`, `kinds` (comma-separated, optional).

```json
{
  "state": "ok",
  "hits": [
    {"item_id": "...", "filename": "...", "path": "...", "kind": "video",
     "size": 123, "available": true, "t_ms": 15000, "page": null, "why": "frame"}
  ],
  "indexed": {"photo": 0.42, "video": 0.10, "doc": 1.0}
}
```

`state` is one of:

| state | when | the page shows |
|---|---|---|
| `ok` | results or none | the hits |
| `off` | the worker did not answer, or no model is installed | nothing extra: the names results stand alone, with no error |
| `empty-index` | the model is there but no vectors exist yet | "Search by meaning starts working as night work goes on." |

`indexed` is the share of the caller's *readable* items of each kind that have
a vector, computed with the scope fragment, cached for 10 minutes per account.
It must not reveal items outside the scope.

Steps: `throttle.hit("meaning-search", str(user.id), Limit(60,
timedelta(minutes=1)))`; `workerclient.embed_text(q)` (catch
`WorkerUnavailable` -> `state: "off"`); if `SPIKE_RESULTS.md` says
`translate-first`, first call `ai.chat(task="translate", ...)` with the system
prompt "Translate the user's search phrase to English. Reply with the
translation only." and embed the answer -- on any failure embed the original;
then `embedstore.nearest`.

The question text is not logged and not stored by this route.

### 6.2 The page (`Browser.tsx`)

The search box keeps its place and its behaviour. Changes:

- Placeholder becomes "Search names, or describe what you are looking for..."
  when `features.meaning_search` is true; unchanged otherwise.
- On each search the page sends both requests at once: `search()` (names) and
  `searchMeaning()`. Names results render the moment they arrive, exactly as
  now. Meaning results arrive later and appear **below** them under a heading:

  ```
  By name (12)
    ...rows as today...

  By what it shows or says (27)                     12% of photos are indexed
    [thumb] IMG_2041.jpg        /Photos/2019/Summer
    [thumb] VID_0032.mp4        /Photos/2019/Summer        at 0:15
    [doc]   notes.pdf           /Documents                 page 3
  ```
- An item found both ways appears once, in the names group, with a small
  "also matches what it shows" note.
- Meaning results render as tiles for photos and clips (reuse the tile
  component the tile view uses) and as rows for documents. The file name is
  always visible under a tile (README rule 28).
- "at 0:15": opening the clip starts playback at `t_ms`. `Viewer.tsx` gets an
  optional `startAtMs` prop, applied once when the video's metadata loads.
  "page 3": `PdfView.tsx` gets `startPage`.
- The "indexed" note appears only while some kind is under 100%. Wording: "12%
  of photos are indexed so far" -- so an administrator and a child both
  understand why something was not found.
- While meaning results load: one muted line, "Looking at what things show...",
  under the names results, `role="status"`. Never a spinner that blocks the
  names results.
- A meaning search that fails leaves the names results as they are and shows
  nothing; the failure is logged on the server.
- Typing triggers the names search as now; the meaning search fires only after
  the person stops typing for 600 ms or presses Enter, and a newer query cancels
  an older one (`AbortController`).
- The "inside this folder / everywhere" toggle applies to both.

Keyboard: arrow keys move through both groups as one list; Enter opens.

### 6.3 Tests

Server (`test_search_meaning.py`, with a fake worker client and fake vectors):
the restricted-account cases from 1.5 through the HTTP route; `state: "off"`
when the worker is unavailable, with status 200; the rate limit; `indexed`
counts only readable items; `mode` absent leaves `/api/search` byte-for-byte
as before (run the existing search tests untouched).

Web: type-check and build pass. There is no browser test harness in this
project; verify by hand at 360 px and desktop and say so in the commit message.

---

## 7. E5 - search on the standby

The standby has the vectors (they are ordinary rows in the hourly backup) and
no files. It needs only the text encoder.

- Run the `worker` service on the standby too. `HOMESH_ROLE=standby` makes the
  loop idle (section 3.3 of foundations) and the internal API load **only**
  `text()`. Limit it to `mem_limit: 1g`, `cpus: 1.0`.
- `tools/deploy-standby.ps1` and `tools/standby-setup.sh` gain the model fetch
  step for the text-encoder files only (`fetch_models --only text`).
- If the standby's memory cannot hold the encoder beside the server (the spike
  records the encoder's memory), E5 is skipped and `features.meaning_search` is
  false there; nothing else changes.
- Results on the standby show `available: false` for files that live only on
  the PC, exactly as browsing does there.

---

## 8. Threats specific to this track

| Threat | What stops it |
|---|---|
| Search reveals that something outside the caller's scope exists | scope inside the query, re-check per row, `indexed` computed within scope, tests for all three |
| A crafted image or clip crashes or exhausts the worker | pixel limit, file size limits, ffmpeg timeout, the container's memory and pid limits, a failure marks one job and the loop continues |
| A crafted file name reaches a command line | arguments passed as a list, never through a shell; temp files named by the worker, not after the item |
| A crafted document makes the converter read local files | conversion happens only in the sandbox container, which has no secrets, no library mount and no network |
| The question leaks to a provider | only when translation is configured, and then it is the question alone, through `ai.chat`, on the record |
| Search used to load the machine | per-account rate limit; one embedding request at a time in the worker |
