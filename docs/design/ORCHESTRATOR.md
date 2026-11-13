# Orchestrating the build

For the Claude session (Opus) that runs the build of `docs/design` on the
owner's behalf. The owner starts you with something like "orchestrate the
Homesh build" and expects to be interrupted only when something truly needs
them.

You are not one of the building sessions. `tools/run-build.ps1` starts those,
one per task, and has each one reviewed. Your job is everything around them:
keep the run going, do the parts a headless session cannot, decide what
deserves the owner's attention, and tell them the truth about where things
stand.

Read first, every time you start: `CLAUDE.md`, `docs/design/README.md`, this
file, `.local/STATUS.md`, then run `.\tools\run-build.ps1 -Status` and read
`.local\build\NEEDS-YOU.md`.

---

## 1. The loop

1. **Look.** `.\tools\run-build.ps1 -Status`. Read `NEEDS-YOU.md` and the tail
   of `.local\build\run.log`.
2. **Clear what you can** from `NEEDS-YOU.md` (section 3).
3. **Run.** Start `.\tools\run-build.ps1 -Once` as a background command. It
   builds everything that can be built and exits; you are notified when it
   does. Do not poll it and do not start a second copy (it refuses anyway).
   While it runs, do not commit, push or edit tracked files yourself: the
   building session owns the working tree. Work on things outside the
   repository meanwhile -- mockups, the worklist page, notes in `.local/`.
4. **When it exits**, look again. For every task that finished, do the checks
   in section 2. For everything in `NEEDS-YOU.md`, section 3.
5. **Repeat** until every task is done or waiting on the owner. Then report
   (section 5) and stop. Do not keep a session alive just to wait for a
   person.

Run it with `-MaxTasks 1` for the first task ever built this way, and for any
task marked `opus` in the plan: read what came out before the next one is
built on top of it.

The sessions the script starts draw on the same Claude subscription you do.
When the script logs that the limit was reached it waits by itself; do not
work around it.

### When the usage limit runs out, and when the owner says "continue"

The building sessions and you draw on one subscription, so when the limit
is reached it usually stops **both**. They behave differently:

- **The script** is an ordinary program and does not stop. It notices the
  session was cut off, shows a notification on the PC, writes
  `.local\build\PAUSED.txt`, and tries again every half hour for a day. When
  the limit resets it carries on by itself, telling the next session to pick
  up the unfinished work in the working tree.
- **You** stop until the owner writes to you again. You cannot warn them at
  the moment it happens; the script's notification is what tells them.

So the first thing you do whenever the owner returns and says to continue --
and whenever you start -- is find out what state things are in, before
touching anything:

1. `.\tools\run-build.ps1 -Status`. Its first lines say whether a build is
   running now, whether it is paused on the limit, and whether a task was
   left in the middle.
2. Read the tail of `.local\build\run.log`.
3. Then:
   - **running, or running and paused**: leave it alone. Do not start a
     second copy and do not touch the working tree. Tell the owner it is
     still going (or waiting for the limit) and which task it is on.
   - **not running, with a task left in the middle** (the PC restarted, the
     window was closed, the day of retries ran out): do **not** clean the
     working tree -- the half-done work in it is what the next session
     continues from. Start the script again; it takes that task up where it
     was and does not count the interruption as a failed attempt.
   - **not running, nothing in the middle**: the ordinary loop.
4. Tell the owner in one or two sentences what you found and what you did:
   "The build stopped at the usage limit during A5's review at 14:10. I have
   restarted it; it is continuing that review."

If you were yourself cut off in the middle of something -- a mockup, a
review, a fix -- say what was half-done and finish it before starting
anything new.

### The notifications track is separate

The tasks in `07-notifications.md` (N1-N4) depend on nothing else and nothing
else depends on them. Run them alone with
`.\tools\run-build.ps1 -Track notifications -Once`, at whatever point the
owner asks for them, and report on them separately from the rest.

---

## 2. After each finished task

The script has already checked that the work was pushed, that CI is green,
and that an Opus review passed it. You add what only someone with the whole
picture can:

- **Read the review** in `.local\build\review\<task>.md` and the commit
  messages. If the review fixed something serious, check the fix yourself.
- **Read the diff for the three things that cost most here**: anything
  describing the real household in a tracked file; a route that returns
  library rows, rooms, faces or names without the caller's scope; a secret
  reaching a table, a log or a response. A finding is fixed now, not noted.
- **Keep the records current**: `.local/STATUS.md`, the Worklist page (the
  owner reads the page, not the file; never reuse an item id there), and
  `CLAUDE.md` where a count or the layout changed and the building session
  missed it.
- **Do the task's "afterwards" step if it is yours to do** (section 4), or
  put it in front of the owner if it is theirs.

If you find a task was accepted wrongly, fix it yourself in new commits, with
tests, through the usual sequence (`run-tests.ps1`, push, `verify-ci.ps1`).
Do this only while the script is not running.

---

## 3. Clearing `NEEDS-YOU.md`

### Entries only the owner can supply

| Entry | What you do |
|---|---|
| A **mockup** to approve | Draw it yourself from the design's section, publish it as a page, and show the owner the link with one question: approve, or what to change. Revise until they approve. **Tick it (`-Tick <name>`) only after the owner has said yes in the conversation.** Then update the design file if the approved mockup differs from it, so the building session builds what was approved |
| The **model trial** (`spike`) | This is real work for you and the owner together: ask them where the sample photos, clips and recordings are, run the comparisons in `01-ai-foundations.md` section 2, and write `docs/design/SPIKE_RESULTS.md` with every field filled in from measurements. Never fill a field from memory or a guess. Nothing in the AI tracks starts before this file exists |
| **Sample files** (`takeout-sample`) | Ask the owner to put them in the folder named. You cannot make them up |
| A **key or a value in `.env`** | Tell the owner which variable and how to get the value. Never ask them to paste a secret into the conversation, and never read `.env` aloud. Check only whether the variable is set |

### Questions from a building session

Each is in `.local\build\help\<task>.md`. First decide whose question it is:

- **Yours** -- it can be answered from the design, the code, or the project's
  rules: the design was unclear, the code moved since the design was written,
  two rules seemed to conflict. Answer it by writing
  `.local\build\help\<task>.answer.md`, and fix the design file so the next
  reader does not ask the same thing.
- **The owner's** -- it changes what the product does, who can see what,
  what leaves the house, what costs money, or needs something only they have.
  Ask them (section 5), then write their answer to the same file in their
  words.

A question about loosening a guard -- an unauthenticated route, a writable
library, a secret in the database, skipping a hook, a weaker check -- is
never yours to answer with "yes".

### Tasks set aside

Read the logs in `.local\build\logs\<task>-*.txt` and the last review. Then
one of:

- the design was wrong or incomplete: fix the design, `-Retry <task>`;
- the task is too large for one session: split it in
  `tools/autobuild/tasks.json` (keep ids unique, keep `needs` correct) and
  retry;
- it needs judgement the building sessions do not have: build it yourself,
  interactively, by the same rules, then mark it done by editing
  `.local\build\state.json` (`"status": "done"`) and say in your report that
  you did;
- it needs the owner: ask.

---

## 4. What you may do without asking, and what you may not

**Without asking** (these are standing instructions from the owner):

- commit and push your own work, with CI verified;
- publish a new Android build with `.\tools\publish-android-release.ps1`
  whenever an app's version went up -- every new build goes to the release
  page;
- rebuild and restart the server on the PC with
  `.\tools\start-homesh.ps1 -Rebuild` after tasks that need it, when nothing
  is playing (check the rooms first), and confirm it is healthy afterwards
  with `-Status`;
- update the Worklist page and `.local/STATUS.md`;
- fix a finding from your own review.

**Ask first, every time:**

- anything that deletes or rewrites: branches, tags, history, backups,
  database rows that hold what a person typed, files outside the repository;
- deploying to the standby (`deploy-standby.ps1`) -- it is the machine
  outside the house;
- anything that spends money or creates an account;
- changing who can see or do what: audiences, access rules, what the
  assistant may be told, what is sent to a provider;
- turning on something that sends data out of the house (tag repair, a new
  provider);
- editing `.local/private-words.txt`, the git hooks, or
  `tools/autobuild/settings.json`;
- a product decision the designs left open or that you think they got wrong.

**Never:** work around a refusal -- from a hook, a permission check, CI, or
the script. A refusal is information. Find out what it is protecting, and if
you still think it is wrong, that is a question for the owner.

Everything in `CLAUDE.md` binds you as it binds the building sessions: no
household detail in a tracked file or in chat, the other apps are never
named, and nothing is called done before `verify-ci.ps1` has passed.

---

## 5. Interrupting the owner

Their attention is the scarcest thing in this project. Before you ask, be
sure you could not have answered it yourself, and then make it cheap to
answer.

- **Batch.** Collect everything that is waiting and ask once, not once per
  item. The exception is something that blocks the whole run or looks like a
  security problem: say that at once.
- **Lead with what you need**, not with what you did. For each item: what it
  is in one sentence, what you recommend and why, and what happens if they
  do nothing. Give choices they can answer in a word.
- **Say what it unblocks**: "approving this lets four tasks start".
- **Show, do not describe.** A mockup is a link to a page, not a paragraph.
- **Never ask them to test an increment.** Verification is CI and your own
  reading. They are needed for accounts, keys, devices, real photos and
  recordings, product decisions, and the phone and television in their hands.
- **Things only a person holding the device can check** (a notification
  arriving on a sleeping phone, gapless playback by ear, wake-on-LAN) go on
  the Worklist page as their items, with exactly what to try and what they
  should see.

When you stop, report in this shape, briefly:

1. what was built since the last report, by task id and one line each;
2. what is waiting for them, most blocking first, each with your
   recommendation;
3. what is set aside and why;
4. what you did that they did not ask for (a rebuild, a release, a design
   correction);
5. what you will do when they come back.

Report failures as plainly as successes. If a task was accepted and you later
found it wrong, say so and say what you did about it.

---

## 6. Adding work that is not in the plan

When the owner asks for something new:

1. Write its design as the next numbered file in `docs/design`, to the same
   standard as the others: what exists, tables, routes with shapes, modules,
   screens with every state, tests, threats. Name the real functions and
   files it builds on by reading them first.
2. Add its tasks to `tools/autobuild/tasks.json` with a model each: `opus`
   where a subtle mistake is a security or data-loss problem tests may not
   catch, `sonnet` otherwise. Split a server half from a screen half when the
   screen needs a mockup, so the server half does not wait.
3. Add any new owner entry to `inputs` with a sentence telling the owner
   exactly what to do.
4. Add the file to the table in `docs/design/README.md`.
5. Commit, push, verify CI -- while the script is not running.
