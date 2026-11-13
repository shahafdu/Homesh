# 03 - Faces

Read [`README.md`](README.md) first. Needs A1 and A2 from
[`01-ai-foundations.md`](01-ai-foundations.md).

What this is for (from `docs/AI_PLAN.md` section 3): finding specific people --
close family and friends -- and "more photos of this person" from a face on
screen. Most faces in a library belong to strangers; they are detected and
grouped and otherwise stay out of the way.

Three rules shape everything below:

1. **A person outranks the model.** A confirmation or a rejection made by a
   person is never changed by any later run.
2. **Face vectors are biometric data and never leave the PC.** Not to a
   provider, not into a backup, not onto the standby, not into any response.
3. **A name is catalog data.** A person's name is shown only to an account that
   can open at least one photo that person is confirmed in.

Vocabulary: a **face** is one detected face in one photo or one video frame. A
**person** is somebody with a name. The accounts page is already called
"People" in the app and stays so; this feature is called **Faces** everywhere a
household member reads, and the table is `persons` so it is never confused with
`users`.

---

## 1. Data (migration `faces`)

```sql
CREATE TABLE IF NOT EXISTS faces (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id    UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    t_ms       BIGINT,                 -- frame time, for a clip; NULL for a photo
    -- The box, as fractions of the upright image, so it fits any rendering.
    box_x REAL NOT NULL, box_y REAL NOT NULL, box_w REAL NOT NULL, box_h REAL NOT NULL,
    quality    REAL NOT NULL,
    detector   TEXT NOT NULL,
    hidden     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS faces_item_idx ON faces (item_id);

-- Biometric. Kept apart from `faces` so that it alone can be left out of
-- backups. Recomputable from the file and the stored box.
CREATE TABLE IF NOT EXISTS face_vectors (
    face_id BIGINT PRIMARY KEY REFERENCES faces(id) ON DELETE CASCADE,
    model   TEXT NOT NULL,
    vector  vector NOT NULL
);

CREATE TABLE IF NOT EXISTS persons (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 80),
    hidden      BOOLEAN NOT NULL DEFAULT FALSE,
    cover_face  BIGINT REFERENCES faces(id) ON DELETE SET NULL,
    created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Who said a face is, or is not, somebody. One row per (face, person).
CREATE TABLE IF NOT EXISTS face_labels (
    face_id   BIGINT NOT NULL REFERENCES faces(id) ON DELETE CASCADE,
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    verdict   TEXT NOT NULL CHECK (verdict IN ('is','is_not')),
    origin    metadata_origin NOT NULL CHECK (origin IN ('user','ai')),
    by_user   UUID REFERENCES users(id) ON DELETE SET NULL,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (face_id, person_id)
);
-- A face is at most one person.
CREATE UNIQUE INDEX IF NOT EXISTS face_labels_one_is
    ON face_labels (face_id) WHERE verdict = 'is';
CREATE INDEX IF NOT EXISTS face_labels_person ON face_labels (person_id, verdict);

-- Computed, and replaced wholesale by the next run. Never treated as truth.
CREATE TABLE IF NOT EXISTS face_guesses (
    face_id   BIGINT PRIMARY KEY REFERENCES faces(id) ON DELETE CASCADE,
    person_id UUID REFERENCES persons(id) ON DELETE CASCADE,  -- "might be"
    group_id  BIGINT,                                         -- unnamed cluster
    distance  REAL
);
CREATE INDEX IF NOT EXISTS face_guesses_person ON face_guesses (person_id);
CREATE INDEX IF NOT EXISTS face_guesses_group ON face_guesses (group_id);
```

Add `DELETE FROM persons` to the `db` fixture (it does not cascade from
anything).

**Backups.** Add `"face_vectors"` to `SKIP` in `server/app/backups.py`. Read
`restore()` and make sure that after a restore `face_vectors` holds no row whose
`face_id` is absent from the restored `faces` (the foreign key does this if the
restore deletes or truncates `faces` with cascade; if it does not, add an
explicit `TRUNCATE face_vectors` to the restore). The planner for kind
`face-vector` (below) then recomputes what is missing. Add a test: back up,
restore, assert `face_labels` survived and `face_vectors` is empty.

This is the default the roadmap recommended. The owner may later decide that
face search should work on the standby; that would mean removing the table from
`SKIP`, and is a decision for the owner, not the implementer.

---

## 2. The face engine (worker side)

`server/app/faceengine.py` (new, worker only):

```python
@dataclass(frozen=True)
class Found:
    box: tuple[float, float, float, float]   # x, y, w, h as fractions
    score: float
    px: int                                  # the shorter side of the face, in pixels
    raw: object                              # whatever embed() needs back

class FaceEngine(Protocol):
    name: str
    dim: int
    def detect(self, image: "numpy.ndarray") -> list[Found]: ...
    def embed(self, image: "numpy.ndarray", found: Found) -> list[float]: ...

def load() -> FaceEngine
```

The implementation is the one recorded in `SPIKE_RESULTS.md`. Thresholds
(`min_face_px`, `min_quality`, `match_threshold`, `suggest_threshold`) are
constants in this module, copied from that file. Tests use a `FakeFaceEngine`
whose `detect` returns boxes written into the test image's file name.

---

## 3. P1 - detection, matching and grouping

### 3.1 Kind `faces` (`workkinds/faces.py`)

Planner: items of kind `photo`, and `video` up to 10 minutes long, without a
`faces` job for the current detector.

Handler:

1. Pixels: for a photo, as in `02-search.md` section 3 step 2. For a clip,
   one frame every 5 s, at most 20 frames, extracted as in section 4 there.
2. `engine.detect`. Drop anything with `px < min_face_px` or
   `score < min_quality`. Keep at most 30 faces per image.
3. For each kept face: `engine.embed`.
4. For a clip: faces from different frames that are within `match_threshold` of
   each other are one person seen several times -- keep the highest-quality one
   and drop the rest.
5. In one transaction: delete the item's `faces` rows **that have no row in
   `face_labels`**, insert the new faces and their vectors. A face somebody has
   labelled is never deleted by a re-run; if a new detection overlaps a
   labelled face's box by more than half (intersection over union above 0.5),
   the new one is dropped instead.
6. Enqueue `face-match` for the item at priority 100.

### 3.2 Kind `face-match`

For each face on the item that has no `verdict = 'is'` label:

1. Find the 5 nearest faces that have a `verdict = 'is'` label, by cosine
   distance on `face_vectors`, using an expression index on the model's
   dimension exactly like `embedstore.ensure_index`.
2. Examples whose photo was taken within three years of this one count first:
   if any of the 5 is inside that window, consider only those. (`items.created_at`
   is the content date.)
3. The candidate is the person with the most examples among those considered;
   `d` is the distance to that person's nearest example.
4. Skip the candidate entirely if `face_labels` holds `(face, candidate,
   'is_not')`. A rejection is permanent.
5. `d <= match_threshold`: insert a label `('is', origin='ai')`.
   `match_threshold < d <= suggest_threshold`: upsert `face_guesses (face_id,
   person_id, distance)`. Otherwise leave it for grouping.

An `ai` label is shown as "probably" in the interface and can be confirmed or
rejected. It is replaced by a `user` label the moment a person decides, and is
never written over a `user` label (the insert uses `ON CONFLICT DO NOTHING`).

When a person adds or removes a label, enqueue `face-rematch` (one job, no
item, payload `{"person_id": ...}`) at priority 100: the next night it re-runs
steps 1-5 for faces that are unlabelled or `ai`-labelled, so one correction
improves the rest.

### 3.3 Kind `face-group` (nightly, one job, no item)

Groups faces that have no label and no guess, so that the page can offer
"these 40 faces look like one person -- who is it?".

Incremental and simple, on purpose:

1. Take ungrouped faces in id order, 5000 per run.
2. For each: its nearest already-grouped face. Within `match_threshold` -> same
   `group_id`. Otherwise a new `group_id` (a sequence).
3. Write to `face_guesses.group_id`.

Groups are a convenience. A group may hold two people, or one person may be
split across groups; the page lets a person fix both, and nothing depends on a
group being right.

### 3.4 Kind `face-vector`

Planner: faces without a `face_vectors` row (after a restore). Handler: load
the item, crop the stored box, embed, insert. Nothing else changes.

### 3.5 Tests for P1 (`test_faces_worker.py`, fake engine)

- a photo with two faces gets two rows, vectors, and a `face-match` job;
- a re-run keeps a labelled face and does not duplicate it;
- a face nearer than the match threshold to a confirmed example gets an `ai`
  label; between thresholds, a guess; beyond, neither;
- a rejected pair is never labelled or guessed again, even at distance zero;
- an `ai` label never replaces a `user` label;
- the three-year rule prefers the nearer-in-time example;
- a clip with the same face in four frames keeps one face;
- deleting the item removes faces, vectors, labels and guesses.

---

## 4. P2 - the API (`server/app/faces.py`, router prefix `/api/faces`)

Every route takes a signed-in user. Every route that returns or touches a face
first proves the caller can open the face's item. The helper every route uses:

```python
def _visible_faces_sql(scope) -> tuple[str, dict]:
    """EXISTS(...) over f.item_id, built on access.scope_sql."""
```

### 4.1 Reading

| Route | Answers |
|---|---|
| `GET /api/faces/people` | persons with at least one confirmed (`is`) face in an item the caller can read, hidden ones excluded: `[{"id","name","count","cover":{"face_id"}}]`. `count` counts only readable items. Sorted by count, descending. Administrators may add `?hidden=1` |
| `GET /api/faces/people/{id}/items?limit=&before=` | readable items the person is in, newest first, paged by `created_at`: `[{"item_id","filename","path","kind","available","face_id","t_ms","sure"}]`. `sure` is false for an `ai` label. 404 when the caller can read none |
| `GET /api/faces/people/{id}/guesses?limit=` | faces that "might be" this person, readable ones only: `[{"face_id","item_id","t_ms"}]`, nearest first |
| `GET /api/faces/groups?limit=` | unnamed groups with at least 3 readable faces, largest first: `[{"group_id","count","faces":[face_id x6]}]` |
| `GET /api/faces/groups/{group_id}?limit=&after=` | the readable faces in one group |
| `GET /api/faces/items/{item_id}` | the faces on one item: `[{"face_id","box":[x,y,w,h],"t_ms","person":{"id","name"}\|null,"sure","might_be":{"id","name"}\|null}]`. 404 when the caller cannot open the item |
| `GET /api/faces/crop/{face_id}?size=96\|192` | a square JPEG of the face. 404 when the caller cannot open the item |
| `GET /api/faces/similar/{face_id}?limit=` | P3, section 6 |

`crop`: made from the item's `screen` thumbnail (`thumbs.generate(..., size=
"screen")`), the box widened by 30% on each side and clamped, resized to the
asked size, cached at `<cache>/faces/<face_id>_<size>.jpg`, written then
renamed as `thumbs.py` does. Response headers: `Cache-Control: private,
max-age=86400`. Only the two sizes are accepted.

A `might_be` or `person` whose name the caller would not otherwise be allowed
to see (rule 3) cannot arise here: the caller can open this item, and the
person is confirmed or guessed in it -- but for a **guess**, the person may be
confirmed only in photos the caller cannot open. So: return `might_be` only
when the person also appears in `GET /api/faces/people` for this caller;
otherwise `null`.

### 4.2 Changing

Who may do what:

| Action | Who | Why |
|---|---|---|
| Name a face, confirm, reject, undo their own label | anyone, on a face whose item they can open | agreed in the plan |
| Create a person | anyone | needed to name the first face |
| Rename a person | whoever created it, or an administrator | a name is shared by the household |
| Hide a person or a group, merge two persons, delete a person | administrators | they change what everybody sees |

| Route | Body | Does |
|---|---|---|
| `POST /api/faces/people` | `{"name"}` | creates; 201 `{"id","name"}`. Names are trimmed; a name that already exists (compared case-insensitively) returns the existing person with 200 |
| `PUT /api/faces/people/{id}` | `{"name"?, "hidden"?, "cover_face"?}` | per the table above |
| `DELETE /api/faces/people/{id}` | - | admin; labels cascade; faces stay |
| `POST /api/faces/labels` | `{"face_ids":[...], "person_id", "verdict":"is"\|"is_not"}`, at most 200 ids | upserts `origin='user'` labels. For `is`: first deletes any other `is` label on those faces (a face is one person). Faces the caller cannot open are skipped; the response is `{"applied": n, "skipped": m}` |
| `DELETE /api/faces/labels` | `{"face_id","person_id"}` | removes a label. An ordinary account may remove only a label it made; an administrator any |
| `POST /api/faces/groups/{group_id}/name` | `{"person_id"}` or `{"name"}` | labels every readable face in the group `is`, in one transaction |
| `POST /api/faces/groups/{group_id}/hide` | - | admin; sets `faces.hidden` on all of them |
| `POST /api/faces/people/{id}/merge` | `{"into": "<person id>"}` | admin; moves labels, keeping the target's on conflict, then deletes the source |

Every change writes one `audit_log` row through `security.audit`, with ids and
counts only -- never a name: `("faces.label", user.id, {"person": id,
"verdict": "is", "faces": 12}, ip)`.

Rate limit: `throttle.hit("faces-write", str(user.id), Limit(120,
timedelta(minutes=5)))` on every changing route.

**Standby**: every changing route is refused there (the default). The reading
routes work, from the restored labels; `similar` answers `{"state": "off"}`.

### 4.3 Tests for P2 (`test_faces_api.py`)

- a restricted account sees only persons confirmed in photos it can open, and
  `count` counts only those photos;
- `items/{id}` and `crop/{face}` answer 404 for an item outside the scope;
- `might_be` is null when the caller cannot see the person anywhere else;
- labelling a face `is` B removes its `is` A;
- a label on a face the caller cannot open is skipped and counted;
- an ordinary account cannot remove someone else's label, merge, hide or
  delete (403);
- creating an existing name returns the existing person;
- `crop` refuses a size that is not 96 or 192;
- a name with markup in it comes back as the same text (and the web renders it
  as text);
- every changing route writes an audit row holding no name.

---

## 5. P2 - the screens

**Mockup first.** Publish mockups of 5.1-5.3 and get the owner's answer before
building.

### 5.1 The Faces sheet (`web/src/Faces.tsx`, `web/src/faces.ts`)

Opened from the main menu as "Faces", shown to everyone when
`features.faces` is true. A wide sheet with three tabs (buttons with
`role="tab"`): **People**, **To check**, **Not named yet**.

```
Faces                                                         [Close]
[ People ]  To check (14)   Not named yet (212)

 (o)        (o)        (o)        (o)
 Alex       Sam        Robin      Kim
 1,204      860        311        97
```

- **People**: a grid of round face crops with the name and the count beneath.
  Tapping one opens that person: a header (crop, name, count, "Rename") and
  the person's photos as tiles, newest first, loading more on scroll. A tile
  with `sure: false` carries a small "?" badge with `aria-label="Probably"`.
  Tapping a tile opens the viewer on that photo, with the person's photos as
  the list the arrows move through.
- **To check**: one question at a time, large, answerable with one tap or one
  key:

  ```
  Is this Alex?
        [   face crop, 192 px   ]
        from IMG_2041.jpg   [Show the photo]
   [ Yes ]   [ No ]   [ Someone else... ]   [ Skip ]
                                             Y / N / S on a keyboard
  ```

  "Yes" and "No" post a label and move on at once; the next crop is already
  loaded. "Someone else..." opens the name picker (5.3). An "Undo" link stays
  visible for the previous answer. When none are left: "Nothing to check.
  Homesh will ask again as it finds more faces."
- **Not named yet**: groups as rows of six crops with a count: "38 faces".
  Buttons per group: "Name..." (the picker), "Not one person" (opens the
  group's faces as a grid with checkboxes: tick the ones that belong together,
  then "Name the ticked ones..."), and for administrators "Hide these".
  Groups of strangers are expected to be left alone; nothing nags about them.

Empty state for the whole sheet, before any faces exist: "Homesh has not
looked for faces yet. It does this at night; see Assistant and AI for
progress." (the last part only for administrators).

### 5.2 In the photo viewer (`Viewer.tsx`)

A "Faces" toggle button in the viewer's toolbar, present when the item has
faces (`GET /api/faces/items/{id}` is fetched when the toggle is first turned
on, not for every photo). When on:

- each face gets an outlined box positioned from `box` as percentages over the
  image, with the name beneath it; an `ai` label reads "Alex?"; an unknown face
  reads "Who is this?";
- tapping a box opens a small menu anchored to it:
  - named: "More of Alex" (P3), "Not Alex", "Someone else...";
  - probable: "Yes, Alex", "Not Alex", "Someone else...";
  - unknown: "Name...", and when `might_be`: "Yes, Alex" first;
- boxes are buttons (`<button>` with `aria-label="Alex, face 2 of 3"`), so Tab
  walks through them and Enter opens the menu;
- the toggle's state is remembered per device in `localStorage`, guarded with
  try/catch.

For a clip, faces are listed under the player as crops with a time ("Alex at
0:15"); tapping seeks there. No boxes are drawn over moving video.

### 5.3 The name picker

One component (`NamePicker` in `Faces.tsx`) used by all three places: a text
box with `dir="auto"`, and beneath it the existing people filtered as you
type, each with a crop. Enter on a name that matches nobody creates the
person: the last row reads "Create 'Robin'". Names are compared
case-insensitively and trimmed. Maximum 80 characters.

### 5.4 Wording

"Faces", "person", "Is this Alex?", "Probably Alex". Never "cluster",
"embedding", "match score" or "biometric" in the interface.

---

## 6. P3 - "More of this person"

`GET /api/faces/similar/{face_id}?limit=60`

- 404 unless the caller can open the face's item.
- If the face has an `is` label: redirect in the response body rather than by
  HTTP -- `{"state": "person", "person_id": "..."}` -- and the page opens that
  person. Confirmed labels are better than similarity.
- Otherwise: the nearest faces by vector, scope applied inside the query with
  `access.scope_sql` exactly as in `02-search.md` 1.4, within
  `suggest_threshold`, one per item: `{"state": "ok", "items": [...]}` in the
  same shape as `people/{id}/items`.
- On the standby, or with no vector for this face: `{"state": "off"}`, and the
  menu item is hidden.

The page shows the results as tiles under the heading "Looks like the same
person", with a "Name this person..." button on top: naming is the useful next
step, and it turns a similarity search into a confirmed person.

Tests: scope respected (a nearer face in a forbidden folder is not returned and
does not shorten the page); a labelled face returns `state: "person"`; unknown
face id -> 404.

---

## 7. Threats specific to this track

| Threat | What stops it |
|---|---|
| A restricted account learns who is in photos it cannot open | every route filters by scope; names only via a readable confirmed photo; counts within scope; `might_be` suppressed |
| Face vectors leave the house | separate table in `backups.SKIP`; no route returns a vector; the worker's internal API has no face route; a test asserts no `/api/faces/*` response contains a `vector` key |
| A child relabels the family | every change is in the audit log with who and when; administrators can remove any label; destructive actions are administrator-only |
| A person's name carries markup | stored as text, length-limited, rendered as a React child |
| Face crops are fetched by guessing ids | `crop` checks access to the item and answers 404 |
| Detection floods the table with crowd faces | size and quality floors, 30 faces per image, groups under 3 faces are not listed |
