# 07 - Notifications on the phone

Read [`README.md`](README.md) first. Needs nothing from the other tracks.

What this adds: a **mailbox** on the Homesh server that Homesh itself and the
apps beside it can post short messages to, and **Homesh Connect** showing those
messages as Android notifications. Tapping one opens the app that sent it, on
the page the message names.

What exists today, and is built on:

- Homesh Connect (`android/`, `docs/PHONE_APP.md`): `PhoneActivity` is the
  native screen; Homesh itself opens in a **Custom Tab** (the phone's real
  browser, so there is no JavaScript bridge to it); an app beside Homesh opens
  in `AppActivity`, a WebView. Addresses are learned from `/tv.address` and
  kept encrypted by `Vault`. `Reach` decides which address answers. `Origins`
  compares addresses safely.
- `PHONE_APPS` in `.env` names the apps beside Homesh; `main.phone_apps()`
  parses it. Their names appear in no tracked file, and must not start to
  (README rule 6). This document says "an app beside Homesh" throughout, and
  tests use the invented names "AppOne" and "AppTwo".
- The app is built without Gradle or any library (`tools/build-tv-apk.sh`).
  **Everything below uses only Android's own APIs.** There is no Google push
  service in this design: it would need a library the build cannot take, an
  outside account, and every message's timing passing through a third party.

---

## 1. How it fits together

```
 an app beside Homesh ---- POST /api/notify/send (its own sending key) ---+
 Homesh itself ----------- notify.send(...) in process -------------------+
                                                                          v
                                                       notify_messages (the mailbox)
                                                                          |
 Homesh Connect <--- GET /api/notify/inbox (this phone's token) ----------+
      |  every 15 minutes in the background, or held open for "instant"
      v
 Android notification  --tap-->  the sending app, on the message's page
```

Three kinds of caller, three kinds of credential, none interchangeable:

| Caller | Credential | Can do |
|---|---|---|
| A person in the web app | the session cookie | see their own messages, manage their own phones |
| An app beside Homesh | its sending key, from `.env` | post a message; nothing else |
| A phone | its device token, made when the person turns notifications on | read that person's messages; nothing else |

**This track adds routes that do not take a session**: `send`, `inbox` and
`claim`. That is the one deliberate exception to README rule 9, and each of
them is authenticated by a key or a single-use code, rate-limited, and able to
do exactly one thing. Do not add another.

### How fast a message arrives

| Mode | When it is used | Delay while the phone sleeps |
|---|---|---|
| Open | Homesh Connect is on screen | a few seconds |
| Background (default) | always | up to about 15 minutes; longer when Android is saving battery |
| Instant (a switch the person turns on) | a small permanent "Homesh is listening" notification is shown | a few seconds |

A phone receives only while it can reach the PC or the standby: on the house
Wi-Fi, or with Tailscale connected. Otherwise messages wait in the mailbox
until it can.

---

## 2. N1 - the server

### 2.1 Configuration

`.env` (and `.env.example`, with invented values and ASCII comments):

```
# Who may post to the mailbox. Name|key pairs separated by ';'. The name must
# be exactly a name used in PHONE_APPS. Make a key with:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
NOTIFY_SENDERS=AppOne|<key>;AppTwo|<key>
```

`config.py`: `notify_senders: str = ""`. A parser beside `phone_apps()`:

```python
def notify_senders(configured: str, apps: list[dict]) -> dict[str, str]:
    """name -> key. An entry is kept only if its key is at least 32 characters
    and its name is an app in PHONE_APPS. Anything else is skipped and logged
    once, by position ("entry 2"), never by content."""
```

The sender name **"Homesh"** is reserved for the server itself and can never
be configured.

### 2.2 Migration `notify`

```sql
-- A phone that may read one person's messages. Only the hash of its token is
-- kept, as with sessions.
CREATE TABLE IF NOT EXISTS notify_devices (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label        TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 60),
    token_hash   BYTEA NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS notify_devices_user ON notify_devices (user_id)
    WHERE revoked_at IS NULL;

-- A single-use code that turns into a device token. Lives two minutes.
CREATE TABLE IF NOT EXISTS notify_claims (
    code_hash  BYTEA PRIMARY KEY,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label      TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ
);

-- The mailbox: one row per message per person.
CREATE TABLE IF NOT EXISTS notify_messages (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sender     TEXT NOT NULL,
    title      TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 80),
    body       TEXT NOT NULL DEFAULT '' CHECK (length(body) <= 300),
    -- A path inside the sending app. Never an address.
    path       TEXT NOT NULL DEFAULT '/' CHECK (length(path) <= 512),
    -- A later message with the same sender and tag replaces this one on the
    -- phone ("3 items waiting" becoming "4 items waiting").
    tag        TEXT CHECK (tag IS NULL OR length(tag) <= 64),
    expires_at TIMESTAMPTZ NOT NULL,
    read_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS notify_messages_inbox ON notify_messages (user_id, id);
```

All three cascade from `users`, so the test fixture needs nothing new.

### 2.3 `server/app/notify.py` (new)

```python
MAX_TTL = timedelta(days=7)
DEFAULT_TTL = timedelta(days=1)

def valid_path(path: str) -> bool
def send(*, sender: str, to: str, title: str, body: str = "", path: str = "/",
         tag: str | None = None, ttl: timedelta = DEFAULT_TTL) -> int
def inbox(user_id: UUID, after: int, limit: int = 50) -> list[dict]
def newest_id(user_id: UUID) -> int
def mine(user_id: UUID, limit: int = 50) -> list[dict]
def mark_read(user_id: UUID, upto: int) -> int
def prune() -> int                      # expired messages, used or expired claims
async def wait_for(user_id: UUID, seconds: float) -> None
```

- `valid_path`: starts with exactly one `/`; contains no `\`, no control
  character, no `//` anywhere, no `@` before the first `/` after the start,
  and no scheme; at most 512 characters. The point: joined to an app's address
  it can only ever stay inside that app. This function has its own tests and
  is the single most important line of defence in the track.
- `send`: `to` is an account's handle, `"everyone"` or `"admins"`. One row per
  recipient. Returns how many rows were written (0 for an unknown handle --
  not an error, so a sender cannot use the route to probe handles). After
  commit, wakes anyone waiting (below).
- `inbox`: messages for the person with `id > after` and `expires_at > now()`,
  oldest first.
- `wait_for`: an `asyncio.Event` per user id in a module-level dictionary;
  `send` sets it. Used by the long poll. **No database connection is held
  while waiting.** At most 200 waiters in the server at once; beyond that the
  wait returns at once and the phone simply polls.
- `prune` runs once a day from the upkeep loop.

### 2.4 Routes (`server/app/notify_api.py`, new; prefix `/api/notify`)

**For apps -- the sending key**

`POST /api/notify/send`, header `Authorization: Bearer <key>`:

```json
{"to": "everyone", "title": "Two things need a decision",
 "body": "Open to see them.", "path": "/inbox", "tag": "pending", "ttl_minutes": 1440}
```

- Find the sender by comparing the key against every configured key with
  `hmac.compare_digest` (compare against all of them; do not stop at the first
  match). No match: 401, after `throttle.hit("notify-key", throttle.caller(request),
  Limit(20, timedelta(minutes=5)))` -- the hit comes first so guessing is
  bounded.
- `throttle.hit("notify-send", sender_name, Limit(60, timedelta(minutes=5)))`.
- Validate with Pydantic: lengths as in the table; `path` through
  `valid_path` (422 otherwise); `ttl_minutes` 1 to 10080; `to` matching
  `^[A-Za-z0-9_.-]{1,64}$`.
- The stored `sender` is the configured name for that key. **A sender cannot
  choose its own name**, so one app cannot post as another or as Homesh.
- Answers `{"queued": 3}`.
- The cross-origin write check in `webguard.py` allows a request with no
  `Origin` header, which is what a server-side caller sends; nothing to
  change there. Confirm with a test.

**For a person -- the session**

| Route | Does |
|---|---|
| `GET /api/notify/messages?limit=` | the caller's last messages, newest first: `[{"id","at","sender","title","body","path","read"}]` |
| `POST /api/notify/messages/read` `{"upto": 123}` | marks the caller's messages up to that id as read |
| `GET /api/notify/devices` | the caller's phones: `[{"id","label","created_at","last_seen_at"}]` |
| `POST /api/notify/devices` `{"label": "My phone"}` | makes a claim code: 32 random bytes, url-safe; stores its SHA-256 with a two-minute expiry; answers `{"claim": "...", "expires_in": 120}`. Rate limit 10 per 5 minutes per account. At most 10 live phones per account (409 beyond) |
| `DELETE /api/notify/devices/{id}` | sets `revoked_at`; 404 unless it is the caller's |
| `POST /api/notify/test` | sends the caller one message from "Homesh": "Notifications are working", path `/`. Rate limit 5 per 5 minutes |

**For a phone -- the claim, then the device token**

`POST /api/notify/devices/claim` `{"claim": "..."}`, no session:

- `throttle.hit("notify-claim", throttle.caller(request), throttle.CODES)`.
- One statement, so a code works once: `UPDATE notify_claims SET used_at =
  now() WHERE code_hash = :h AND used_at IS NULL AND expires_at > now()
  RETURNING user_id, label`. Nothing returned: 404.
- Make the token (32 random bytes, url-safe), store its SHA-256 in
  `notify_devices`, answer `{"token": "...", "cursor": <newest_id for the
  person>, "who": "<display name>"}`. The token is shown exactly once, here.
  `cursor` starts the phone at "now", so turning notifications on does not
  replay a week of old messages.
- Audit: `("notify.device", user_id, {"device": id}, ip)`.

`GET /api/notify/inbox?after=<id>&wait=<0..50>`, header
`Authorization: Bearer <device token>`:

- Look the device up by the token's hash, not revoked. None: 401, after a
  `throttle.hit("notify-token", caller, Limit(30, timedelta(minutes=5)))`.
- Update `last_seen_at` at most once a minute.
- If nothing is newer than `after` and `wait > 0`: `await notify.wait_for(
  user_id, wait)`, then look again.
- Answers:

  ```json
  {"role": "primary", "cursor": 131,
   "messages": [{"id": 131, "at": "...", "sender": "AppOne", "title": "...",
                 "body": "...", "path": "/inbox", "tag": "pending"}]}
  ```

  `role` is `standby.role()`. Message ids are per machine, so the phone keeps
  one cursor for the PC and one for the standby.
- Never returns more than 50 messages; the phone asks again.

Device tokens and sending keys are never logged. `RedactQueries` in
`webguard.py` already strips query strings from the access log; both
credentials travel in a header anyway.

### 2.5 The standby

Apps beside Homesh have a copy on the standby and may post from there while
the PC is off, so the mailbox works on both machines:

- `standby.py`: add `"notify_messages"` to `STANDBY_LOCAL` (the standby's own
  messages must survive its hourly restore), and add
  `re.compile(r"^/api/notify/(send|messages/read)$")` to `_LOCAL` so those
  writes are handled there and not refused.
- `notify_devices` is **not** local: phones are registered on the PC and
  arrive on the standby with each backup, so one token works on both.
- `devices` (create, delete), `devices/claim` and `test` stay refused on the
  standby, by the default rule.
- `NOTIFY_SENDERS` must be set in the standby's environment too;
  `tools/deploy-standby.ps1` carries it over the way it carries the other
  values the standby needs.
- A message posted on the standby is not copied to the PC. It was delivered
  from where it was posted, and it expires.

### 2.6 What Homesh itself sends

One message in this version, to `"admins"`, from `"Homesh"`:

| When | Title | Path | Tag |
|---|---|---|---|
| The newest regular backup is more than 26 hours old (checked hourly in the upkeep loop) | "Backups have stopped" | `/` | `backup` |

Send the backup message once per day at most (remember the day in
`upkeep_state`, or in a module variable if that table does not exist yet).
More uses are added later, one at a time; a notification nobody needed is the
fastest way to get them all switched off.

### 2.7 Tests (`server/tests/test_notify.py`)

- `valid_path`: accepts `/`, `/a/b?c=d#e`; refuses `//evil.example`,
  `/\evil`, `http://x`, `/a//b`, a path with a newline, an empty string, one
  not starting with `/`, and 513 characters;
- `send` with a wrong key: 401, and the 21st wrong key in five minutes: 429;
- a sender cannot set its own name (a body containing `"sender": "Homesh"` is
  refused by `extra="forbid"`);
- a key whose name is not in `PHONE_APPS` is not accepted; "Homesh" cannot be
  configured;
- `to: "everyone"` writes one row per account; `"admins"` only to
  administrators; an unknown handle answers `{"queued": 0}`;
- the claim: works once; a second use answers 404; an expired one answers
  404; the token comes back only from the claim;
- `inbox` with a revoked or unknown token: 401; with a good one: only that
  person's messages, only those after the cursor, none expired;
- the long poll returns within a second of a `send`, and returns empty after
  `wait` seconds when nothing arrives (use a short wait in the test);
- a session cookie alone cannot read `inbox`, and a device token cannot read
  `/api/notify/messages` or any other route (try three);
- deleting a phone: its token stops working; another account cannot delete it;
- no response and no log line contains a sending key or a device token
  (marker strings, captured logs at DEBUG);
- `standby.classify` for each route matches 2.5;
- `prune` removes expired messages and spent claims.

---

## 3. N2 - Homesh Connect

Read `docs/PHONE_APP.md` and `PhoneActivity`, `AppActivity`, `Vault`, `Reach`
and `Origins` first. Target and minimum SDK versions are in the manifest;
check each API below against Android's current documentation for those
versions before using it. All of it is platform API; add no library.

### 3.1 Turning it on

Homesh runs in a Custom Tab, so the page cannot hand the app a token through a
bridge. It hands over a single-use claim code through a link instead.

1. In the web app (section 4), the person presses "Turn on for this phone".
   The page calls `POST /api/notify/devices` and then navigates to:

   ```
   homeshconnect://notify?claim=<code>&origin=<the page's own origin>
   ```

2. Manifest: `PhoneActivity` gets an intent filter for `android.intent.action.VIEW`,
   categories `DEFAULT` and `BROWSABLE`, scheme `homeshconnect`, host `notify`.
3. `PhoneActivity` handles the intent (in `onCreate` and `onNewIntent`):
   - `origin` must match, by `Origins.same`, an address the app **already
     holds** in `Vault` for the PC (house or outside address). Anything else:
     ignore the link and show "This link is not from your Homesh." -- any web
     page can produce such a link, and this check is what stops one from
     pointing the phone at somebody else's mailbox.
   - Show a confirmation before doing anything: "Turn on Homesh notifications
     on this phone?" [Turn on] [Cancel].
   - On Android 13 and later, request `POST_NOTIFICATIONS` now.
   - `POST <origin>/api/notify/devices/claim` on a background thread. Store
     the token and the cursor in `Vault` (`notify.token`, `notify.cursor.primary`,
     `notify.cursor.standby`). Schedule the background check (3.3).
   - Show "Notifications are on for <who>." Failure: "That did not work. Try
     again from Homesh's settings." (a claim lives two minutes).

A "Notifications" row is added to `PhoneActivity`'s screen: its state (off /
on / on, instant), a switch for **Instant** (3.4), a switch **Show message
text on the lock screen** (off by default), and "Turn off on this phone",
which deletes the token from `Vault` and cancels the job and the service. To
turn it on, the row opens Homesh's settings page in the Custom Tab.

### 3.2 `Mailbox.java` (new) -- fetching and showing

```java
final class Mailbox {
    /** Fetch once from whichever machine answers. Returns how many arrived. */
    static int check(Context context, int waitSeconds);
    static void show(Context context, Message m);
}
```

`check`:

1. No token in `Vault`: return 0.
2. Choose the address with the same order the app uses everywhere (`Reach`):
   the PC's house address, the PC's outside address, the standby.
3. `GET <address>/api/notify/inbox?after=<cursor for that role>&wait=<n>` with
   the token in the `Authorization` header, `HttpURLConnection`, read timeout
   `wait + 10` seconds. Use the cursor stored for the role the *response*
   reports; if the first request used the wrong role's cursor, repeat once
   with the right one.
4. HTTP 401: the phone was removed. Delete the token, cancel everything, show
   one notification: "Homesh notifications were turned off for this phone."
5. For each message, `show`; then store the new cursor.

`show`:

- A notification channel per sender, created on first use: id `app.<sender>`,
  name the sender's name, default importance. A person can then silence one
  app in Android's own settings.
- Notification id: a stable hash of `sender + "/" + tag` when there is a tag
  (so a newer message replaces the older one), else the message id.
- Title and text as given, as plain text. `setAutoCancel(true)`.
- Lock screen: `setVisibility(VISIBILITY_PRIVATE)` with a public version
  reading "New message from <sender>", unless the lock-screen switch is on.
- The tap: a `PendingIntent.getActivity` (flags `IMMUTABLE | UPDATE_CURRENT`)
  for `PhoneActivity` with action `com.homesh.tv.OPEN_MESSAGE` and extras
  `sender` and `path`. It must open an activity directly; Android 12 and
  later refuse a notification that starts one through a service or receiver.

Opening (`PhoneActivity`, action `OPEN_MESSAGE`):

- Re-validate `path` with the same rules as the server's `valid_path`
  (a Java copy, with a unit test using the same cases). Invalid: open the
  sender's front page.
- Sender `"Homesh"`: open the Custom Tab at `<reachable Homesh address> + path`.
- Otherwise: find the app with that exact name in the stored apps list and
  start `AppActivity` with its usual extras plus a new `EXTRA_PATH`.
  `AppActivity` loads `<address> + path` (and navigates there in
  `onNewIntent` when that app is already open). Check the final address with
  `Origins.under(...)` against the app's own address before loading it.
- A sender that is not in the apps list any more: open the phone app's own
  screen. Never open an address taken from a message.

### 3.3 Background checks (the default)

`NotifyJob extends JobService`, scheduled with `JobScheduler`:
periodic, every 15 minutes (the platform's minimum), requires any network,
`setPersisted(true)` so it survives a restart (needs
`RECEIVE_BOOT_COMPLETED`). The job runs `Mailbox.check(context, 0)` on a
thread and calls `jobFinished`. It is scheduled when notifications are turned
on and whenever `PhoneActivity` starts (scheduling twice with the same job id
is harmless).

Also call `Mailbox.check(context, 0)` whenever `PhoneActivity` comes to the
foreground.

### 3.4 Instant (a switch)

`NotifyService`, a foreground service that keeps asking with a long wait:

```
loop:  n = Mailbox.check(context, 50)        // returns when a message arrives, or after 50 s
       on failure: wait 5 s, 15 s, 60 s, then 5 minutes between tries, reset on success
```

- It shows the required ongoing notification on its own low-importance channel
  ("Listening"): "Homesh is listening for messages", with a "Turn off instant"
  action.
- Manifest: declare the service with a foreground service type, and the
  matching permission. Choose the type from Android's documentation for the
  app's target SDK (a type meant for message delivery between a person's own
  devices fits; a "data sync" type is time-limited on recent Android and does
  not). Record which type was chosen and why in a comment.
- Started when the switch is turned on, and on boot if it was on
  (`BOOT_COMPLETED` receiver). Returns `START_STICKY`.
- Android suspends network access for sleeping apps unless the app is exempt
  from battery optimisation. When the switch is turned on, explain in one
  sentence and open the system screen that lets the person exempt the app
  ("So messages arrive at once, allow Homesh Connect to run in the
  background."). If they decline, instant still works while the phone is
  awake and falls back to the 15-minute checks while it sleeps. Say so.
- The background job stays scheduled as the safety net.

### 3.5 Tests and release

- `android/test/`: unit tests for the Java `validPath` (the server's cases),
  the notification-id hash (same sender and tag gives the same id), cursor
  selection by role, and the claim link's origin check (a link naming an
  unknown origin is refused). Run by `build-tv-apk.sh` like the existing ones.
- The services and notifications cannot be tested in CI. The implementer
  states that in the commit message, and the owner checks on a real phone:
  turn on, send a test, tap it, lock-screen text hidden, instant on, phone
  asleep for an hour then a message, remove the phone from settings.
- Bump the phone app's `versionCode` and `versionName`. The owner (or the
  orchestrator) publishes the release.
- `docs/PHONE_APP.md` gains a "Notifications" section: what is stored on the
  phone (the token, encrypted by `Vault`), the two modes, and how to turn it
  off.

---

## 4. N3 - the web app

**Mockup first** for the bell and the settings block.

### 4.1 Settings (`Settings.tsx`, helper `web/src/notifyApi.ts`)

A new `setting` block for everyone:

```
Notifications on your phone
Homesh Connect can show a notification when Homesh or one of your other
apps has something for you.

  My phone            last checked 2 minutes ago      [Remove]
  Old tablet          last checked 3 weeks ago        [Remove]

  [ Turn on for this phone ]     [ Send a test ]
  Open this page inside Homesh Connect to turn it on there.
```

- "Turn on for this phone" asks for a label (prefilled "My phone", 60
  characters at most), calls `POST /api/notify/devices`, then sets
  `window.location.href` to the `homeshconnect://` link. If the page is still
  visible two seconds later, the app did not take the link: show "That only
  works inside the Homesh Connect app on a phone."
- "Remove" asks once in place (README rule 32).
- "Send a test" is disabled until there is at least one phone; afterwards:
  "Sent. It should arrive within 15 minutes, or at once if Instant is on."
- On the standby the buttons are disabled with the usual line about the main
  server.

### 4.2 The bell (`web/src/Inbox.tsx`)

A bell button in the browser's top bar with a dot when there are unread
messages (`aria-label="Messages, 2 unread"`). It opens a small sheet listing
the last 50 messages, newest first: sender, title, text, how long ago.

- A message from "Homesh" is a link to its `path` inside the app. A message
  from an app beside Homesh is a link to that app's address plus the path,
  built from the apps list the page already has; if the app is not in that
  list, the message is shown without a link. The path is validated with a
  TypeScript copy of `valid_path` before any link is made.
- Opening the sheet marks everything shown as read.
- Empty: "No messages."
- The list refreshes when the sheet opens and once a minute while it is open.
  No polling while it is closed beyond one check when the app loads.

Title, text and sender are rendered as text (README rule 27), `dir="auto"`.

---

## 5. What the apps beside Homesh do

Outside this repository; described here so the contract is written down. Each
app needs:

- its key from `NOTIFY_SENDERS`, given to it through its own configuration;
- one HTTP call when it has something to say:

  ```
  POST http://<the Homesh server>/api/notify/send
  Authorization: Bearer <its key>
  Content-Type: application/json

  {"to": "everyone", "title": "...", "body": "...", "path": "/the/page", "tag": "optional"}
  ```

- on the standby, the same call to the standby's Homesh.

`docs/PHONE_APP.md` documents this contract in general terms, with the
invented names.

---

## 6. Threats specific to this track

| Threat | What stops it |
|---|---|
| Somebody on the house network posts fake notifications | a sending key of at least 32 random characters per app, compared in constant time, with failed attempts rate-limited |
| One app posts as another, or as Homesh | the sender's name comes from the key, never from the request; "Homesh" cannot be configured |
| A notification sends the person to a hostile site | a message carries a path, never an address; the path is validated on the server, again on the phone, and again in the web app; the phone opens only an app from its own stored list and checks the final address is under that app's address |
| A web page tricks the phone app into registering with someone else's server | the claim link is honoured only for an address the app already holds, and only after the person confirms |
| A stolen phone keeps receiving | the token is stored encrypted on the phone; "Remove" in settings revokes it at once; the phone clears itself on the next 401 |
| A device token is used to read more than messages | it is accepted by exactly one route and is not a session |
| Message text is read from a locked phone | hidden on the lock screen unless the person turns it on |
| The mailbox is used to flood a phone | per-sender rate limit, lengths capped, messages expire, a channel per app that the person can silence |
| Sending keys or tokens leak through logs or backups | never logged; keys live in `.env`; tokens are stored only as hashes |
| The household's other apps are named in the public repository | names come from `.env` at run time; tests and docs use invented names; the commit hooks refuse the real ones |
