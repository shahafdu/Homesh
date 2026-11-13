# Design for the work that is left

Written November 2026. These documents turn the roadmap into designs detailed
enough to build from without re-deciding anything: tables as SQL, endpoints with
their request and response shapes, module and function names, screens, and the
tests each task must add.

They are written for an implementer who has **not** read the rest of the
project. Everything the implementer must reuse is named with its file. Where a
choice was open, it is made here and the reason is given, so it is not reopened
halfway through a task.

| File | Track | Tasks |
|---|---|---|
| [`01-ai-foundations.md`](01-ai-foundations.md) | Worker, queue, model router, Settings -> AI | A1-A5 |
| [`02-search.md`](02-search.md) | Embeddings and search by meaning | E1-E5 |
| [`03-faces.md`](03-faces.md) | Faces | P1-P3 |
| [`04-assistant.md`](04-assistant.md) | History, audit, chat and commands, voice, tagging | C1-C4 |
| [`05-music.md`](05-music.md) | Gapless playback, loudness, tag repair | M1-M3 |
| [`06-library-upkeep.md`](06-library-upkeep.md) | Change notices, photo availability, the converter sandbox | L1-L5, A5 |

`docs/AI.md` (the rules) and `docs/AI_PLAN.md` (what was agreed) stay the
authority on *what* and *why*. These files are the *how*. If one of them
contradicts `AI.md`, `AI.md` wins and the design file has a bug.

---

## 1. Order of work

Do the tasks in this order. A task may start only when every task in its
"needs" column is merged and CI is green.

| Step | Task | Needs | Size |
|---|---|---|---|
| 1 | **A1** Spike: choose the embedding, face and speech models | the owner's sample photos and recordings | S |
| 2 | **A5** Converter sandbox | - | S |
| 3 | **A2** Worker container and job queue | A1 | M |
| 4 | **A4** Model router, catalogue, Settings -> AI | - | M |
| 5 | **A3** Embeddings table and scope-first search query | A1 | S |
| 6 | **E1** Photo embeddings | A2 A3 | M |
| 7 | **E4** The search box | E1 | M |
| 8 | **E2** Clips: frames and sound | E1 | M |
| 9 | **E3** Documents | E1 | S |
| 10 | **E5** Search on the standby | E4 | S |
| 11 | **P1** Face detection and grouping | A2 | M |
| 12 | **P2** Naming and corrections | P1 | M |
| 13 | **P3** "More of this person" | P2 | S |
| 14 | **C1** History and audit | A4 | M |
| 15 | **C2** Chat and commands | C1 E4 | L |
| 16 | **C3** Voice | C2 | M |
| 17 | **C4** Tagging | A2 A4 | S |
| 18 | **M1** Gapless playback | - | M |
| 19 | **M2** Loudness | A2 | S |
| 20 | **M3** Tag repair | A2 | M |
| 21 | **L1** Folder watcher | - | S |
| 22 | **L2** Drive change polling | - | S |
| 23 | **L4** Wake the PC | - | S |
| 24 | **L5** Takeout import | - | M |

M1, L1, L2, L4 and L5 need nothing from the AI tracks and can be done at any
point. **C1 must be finished before C2**: nothing that changes data is given to
a model before the record of what it did exists.

### A1 is not for a weaker model

The spike compares models on the owner's own photos and recordings and decides
three things every later task depends on. It needs judgement and it needs the
owner. Its output is one file, `docs/design/SPIKE_RESULTS.md`, with exactly the
fields listed in section 2 of `01-ai-foundations.md`. Every later task reads its
model names, dimensions and file checksums from that file. **If that file does
not exist, stop: do not pick a model yourself.**

---

## 2. Rules for the implementer

Each rule is here because breaking it has already cost something in this
project, or would. They apply to every task. A task's own document adds to
them; it never relaxes them.

### 2.1 Finishing

1. One task is one or a few commits. Commit messages explain **why**.
2. Every task ends with this sequence, in this order, and is not reported as
   done before the last line passes:

   ```powershell
   .\tools\run-tests.ps1
   git push origin main
   .\tools\verify-ci.ps1
   .\tools\publish-android-release.ps1   # only if an Android versionCode went up
   ```
3. Update `CLAUDE.md` when a task changes what it states: the test count, the
   migration count, the layout list, the phase table.
4. Update `docs/USER_GUIDE.md` when a task adds something a person uses.

### 2.2 The repository is public

5. **Nothing that describes this household goes into a tracked file or a commit
   message.** No real address, host name, region, key, device id, room, person's
   name, folder name, file name, file count, or language. Tests use invented
   names ("Main Room", "Study"), RFC 5737 addresses (`192.0.2.x`), and invented
   people ("Alex", "Sam").
6. Never name the apps that sit beside Homesh. Say "an app beside Homesh".
7. A value specific to this setup belongs in `.env` or `.local/`, and code reads
   it from there. Never print such a value in a chat reply either.
8. The commit hooks refuse some of this. They cannot recognise most of it. A
   refusal from a hook is never worked around by rewording until it passes, by
   `--no-verify`, or by editing `.local/private-words.txt`. Stop and ask.

### 2.3 Server code

9. **Every new route takes `user: CurrentUser = Depends(require_user)`** from
   `server/app/security.py`. There is no new unauthenticated route in any of
   these designs. An administrator-only route starts with:

   ```python
   if not user.is_admin:
       raise HTTPException(status.HTTP_403_FORBIDDEN, "administrators only")
   ```
10. **Anything that returns catalog rows applies the caller's library scope.**
    Use `access.library_scope(user.id)` and either `access.can_read(path, scope)`
    on each row, or the SQL clause from `access.scope_sql` (added in task A3).
    A count, a suggestion, a thumbnail and a "nothing found" message are catalog
    data too: a restricted account must not be able to tell that something it
    cannot open exists.
11. **Anything that returns rooms applies `access.zone_scope(user.id)`.**
12. **SQL is `sqlalchemy.text()` with bound parameters.** Never build a value
    into the string. An identifier (table, column, index name) that must vary
    comes from a constant list in the code, never from a request.
13. **A secret is never written to a table, a log line, an exception message or
    a response.** A page may say a key is present; it never shows any part of it.
14. **A child program gets `childenv.child_env()`**, never the server's own
    environment. This covers ffmpeg, ffprobe, fpcalc, soffice and anything new.
15. **A file name, a tag, a document's text and a model's answer are untrusted
    input.** They are never used to build a shell command, a file path, an SQL
    string or HTML. Pass file paths to child programs as list arguments with
    `--` before them where the program supports it.
16. **Metadata records its origin and never overwrites.** A new fact is a new
    row in `item_metadata` with its own `origin`. `origin='user'` outranks
    everything when read. Nothing computed deletes or changes a `file` or `user`
    row.
17. **The server never writes to the library.** Every mount is read-only and
    stays so. A feature that seems to need writing a file there is designed
    wrongly.
18. **No endpoint takes a host path.** A request may name a source and a path
    *relative to that source*, which is then confined under the source's root
    after symlinks resolve (`sources/local.py` shows the check).
19. **Every write route must be classified for the standby.** `standby.classify`
    refuses any write it does not list, which is the safe default. Each design
    says, per route, whether it is refused, local or replayed there. Do not add
    a route to `_REPLAYED` unless its design says so.
20. Migrations are plain SQL in `server/migrations`, numbered from `027`, one
    file per task that needs one, written so that running them twice does no
    harm (`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`). Take the next free
    number at the time you write it; the names in these documents are fixed, the
    numbers are not.
21. A new table whose rows do not disappear when `users`, `items` or `sources`
    are deleted must be emptied in the `db` fixture in
    `server/tests/conftest.py`, or tests leak into each other.
22. A new Python dependency is added to `server/pyproject.toml` and the pinned,
    hashed requirements files are regenerated with the command in the header of
    `server/requirements.txt`. Never install something in the Dockerfile that is
    not in a hashed file. The worker's heavy dependencies go in their own file
    (`requirements-worker.txt`) so the server image does not grow.
23. Python: ruff with the project's rule set must pass. Comments explain a
    decision or a constraint; they never narrate the code.

### 2.4 Web code

24. **No browser API that needs a secure context**, unless it is guarded and the
    feature is simply absent without it. Screens reach the server over plain
    http on the house network, where `crypto.randomUUID`,
    `navigator.clipboard`, `navigator.mediaDevices` and service workers do not
    exist. Use `randomId()` from `web/src/id.ts`.
25. Network calls go through `api` in `web/src/api.ts`. Types for each feature
    live in a helper file beside the component, named `<feature>Api.ts`
    (`aiApi.ts` beside `Ai.tsx`). **Never two files whose names differ only in
    letter case** (`faces.ts` beside `Faces.tsx`): Windows cannot tell them
    apart, and the build fails there while passing in CI.
26. Colours come from the tokens in `styles.css` (`--bg`, `--surface`, `--line`,
    `--ink`, `--muted`, `--accent`, `--ok`, `--danger`). Never a literal colour.
    All three palettes and both appearances must look right.
27. Text that came from a file, a tag, a person or a model is rendered as text
    (a React child). Never `dangerouslySetInnerHTML`, never a link built from
    it. File names, folder names, people's names and questions get `dir="auto"`,
    because a library may hold right-to-left names.
28. The filename is always shown. A title, a caption or an AI tag may be shown
    *with* it, never instead of it.

### 2.5 Screens

Every screen in these designs follows the same conventions. They are stated
once here.

29. **A panel is a sheet**: `<div className="sheet" role="dialog"
    aria-modal="true" aria-label="...">` holding `<div className="sheet-inner">`,
    with `useLockScroll()`, closed by Escape and by a click on the backdrop.
    `Settings.tsx` is the model to copy. Use `sheet-inner wide` for anything
    with a table.
30. **Every list has four states and all four are designed**: loading (a quiet
    line of text, no spinner graphic), empty (says what would make it non-empty
    and offers the button that does it), error (the server's `detail` text, and
    a "Try again" button), and filled.
31. **A button that starts something slow** is disabled while it runs and its
    label changes to say what is happening ("Testing..."). The result appears
    beside the button, not in an alert box.
32. **A destructive action asks once, in place**: the button turns into "Sure?
    [Yes, remove] [Cancel]" on the same row. No modal on top of a sheet.
33. **Wording**: plain sentences, sentence case, no jargon. "The assistant", not
    "the LLM" or "the model", anywhere a household member reads. "Folders", not
    "sources". "Rooms", not "zones". Say what happened and what to do next:
    "Could not reach the provider. The next model in the list was used."
34. **Phone first.** Everything works at 360 px wide with no sideways scrolling.
    Touch targets are at least 44 px tall. A table that cannot fit becomes one
    block per row.
35. **Keyboard and screen reader**: every control is reachable by Tab, has a
    visible focus ring, and has a text label or `aria-label`. Status changes are
    announced with `role="status"`. Colour is never the only signal.
36. **Nothing the feature is not set up for is shown broken.** With no provider
    configured the assistant button is absent, not greyed out with an error.
    With no models installed the search box works exactly as it does today.
37. **The television client (`web/src/tv/`) gets none of these features** unless
    a design says so. It has no keyboard and no microphone.

### 2.6 Android

38. A version bump changes `versionCode` and `versionName` together, and the
    release is published with `tools/publish-android-release.ps1`.
39. A JavaScript bridge method checks `Origins.same(...)` against the current
    page before it acts, as the existing bridges do.
40. A new permission is requested at the moment it is first needed, with one
    sentence saying why, and the feature works again after a refusal is
    reversed in system settings.

### 2.7 When to stop and ask

Stop, change nothing further, and report, when:

- a hook, a test or CI refuses something and the fix is not obviously inside the
  task;
- a design here contradicts the code as it now stands (the code moved since
  November 2026 -- say what differs);
- a task would need a new unauthenticated route, a writable library mount, a
  secret in the database, or a model that was not named in `SPIKE_RESULTS.md`;
- a migration would have to drop or rewrite rows that hold something a person
  typed.

---

## 3. Security baseline, by feature kind

A task's document lists its own threats. These apply wherever the kind of
feature appears.

| Kind | What must hold |
|---|---|
| A route returning items | Scope applied in the query *and* re-checked per row; tests with a restricted account that expect an empty or shorter result, never a 403 that reveals existence |
| A route taking an item id | `access.may_access_item(item_id, user.id)`, answering 404 (not 403) when it fails |
| A route that changes something | The cross-origin write check in `webguard.py` already covers it; do not add an exemption. Rate-limit with `throttle.hit` when a signed-in account could make it expensive |
| An upload (a voice clip) | A size limit enforced while reading, a list of accepted content types, the bytes kept only in a temporary file that is deleted in a `finally` |
| A call to a provider | Through `ai.ask` / `ai.chat` only, so the spend gate and the record cannot be skipped. Only text leaves; never a photo, a face, a voice or a vector |
| A model's reply | Treated as untrusted text. It can only request tools from a fixed list; arguments are validated by Pydantic before use; ids it mentions are checked against what its tools returned |
| A new container | No published port, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `read_only: true` with a `tmpfs` for `/tmp`, memory, CPU and pid limits, only the environment variables it needs |
| A downloaded model file | Fetched once by a script, from a URL pinned in `SPIKE_RESULTS.md`, verified against the SHA-256 there before first use, stored in a named volume, never in the image or the repository |
| Biometric data (face vectors) | Stays on the PC: not in any backup, not on the standby, not in a response, not in a log |

---

## 4. What every task hands over

A task is complete when all of these exist:

- the migration, if any, and it applies cleanly on top of a database that
  already holds data;
- the server code and the tests the design lists, plus any test you needed to
  believe your own change;
- the web or Android code, checked at 360 px and at desktop width, in the warm
  palette light and dark at least;
- the documentation changes from 2.1;
- green CI, shown by `verify-ci.ps1`.

Screens are shown to the owner as mockups before they are built when a design
says "mockup first". That is a rule of this project: the owner reviews pictures,
not a running build.

---

## 5. Building it unattended

`tools/run-build.ps1` works through the tasks without anyone watching. The
plan it follows is `tools/autobuild/tasks.json`: the order, which model builds
each task, what each task needs first, and what only the owner can supply.

```powershell
.\tools\run-build.ps1 -Status     # where every task stands
.\tools\run-build.ps1             # build until nothing more can be built
.\tools\run-build.ps1 -Tick mockup-faces    # tell it an entry of yours is done
.\tools\run-build.ps1 -Retry A5   # put a task that was set aside back
```

For each task: a session of the task's model builds it; the script itself
checks that the work was pushed and CI is green; an Opus session reviews the
commits against the rules above and fixes what it finds; the script checks CI
again. Two failed attempts hand the task to Opus once; a third failure sets it
aside and the run carries on with whatever does not depend on it.

**It asks rather than guesses.** A task that needs a file, an approved mockup
or a decision is skipped until that exists, and a session that hits a stop
condition (section 2.7) writes its question down and ends. Everything waiting
for the owner is collected in `.local\build\NEEDS-YOU.md`, rewritten on every
pass, with what to do for each entry -- and a notification is shown on the PC
when the list changes or the run can go no further.

It never starts, rebuilds or deploys the running server or the standby, and
never publishes an app: those stay with the owner, and are listed in the same
file once the task that needs them is built. Sessions run in Claude Code's
auto permission mode with the deny-list in `tools/autobuild/settings.json`, and
cannot read `.env`.

It runs on the Claude subscription the PC is signed in to; the script removes
any API key from its own environment so nothing is billed per token. When the
subscription's limit is reached it waits and carries on.
