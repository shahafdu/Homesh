# Keeping the backups somewhere the house is not

A backup on the same disk as the database survives a mistake and nothing else.
The fire, the theft and the failed drive — the whole category backups exist for
— take both copies together.

This is the setup for the copy that leaves. It is the one piece of Homesh that
needs an account somewhere, and the one that needs a decision from you, so it is
written out rather than assumed.

---

## What is already true

- Backups are taken hourly and kept locally: everything from the last day, one a
  day for a week, and one a week for five weeks -- so there is always a point
  about two weeks back and one about a month back. **Settings → Backups.**
- They are **encrypted here, before they leave**, with a key that never goes
  with them. `tools/new-backup-key.ps1` generates it and prints it once.
- The database is the whole of it — accounts and their passkeys, who may see
  what, playlists, positions, and the catalog. Not your media: those are your
  own files on your own disks, and copying terabytes is a different job.

## Why not Google Drive

It was tried and Google refuses it. A service account has **no storage quota of
its own**, so it cannot create a file in Drive even inside a folder shared with
it as Editor:

    403  "Service Accounts do not have storage quota. Leverage shared drives,
         or use OAuth delegation instead."

Both remedies are Google Workspace features. Reading your shared folders is
unaffected and always was — this is only about putting something *new* there.

---

## Oracle Cloud, and why this one

Two things at once, which is the reason to prefer it over Cloudflare R2 or
Backblaze B2:

1. **Object storage**, Always Free, S3-compatible — where the encrypted backups
   go.
2. **A small always-on machine**, Always Free — which is phase 0.5 of this
   project: the node that keeps the catalog, search and thumbnails answering
   when the PC is off. Same account, later, no second provider.

The free allowance has been cut twice and is still ample for this: an ARM
machine at whatever size the console marks eligible (1 OCPU and 6 GB in
Jerusalem as of September 2026), 200 GB of block storage, 10 TB of outbound
traffic a month, and 20 GB of object storage across the tiers. Backups are
hourly and about 19 MB each, and old ones are pruned.

### What to do

**1. Create the account** at <https://www.oracle.com/cloud/free/>.

Pick the **home region** carefully: it cannot be changed afterwards, and the
always-on machine will have to live there too. Somewhere near you.

Oracle asks for a card to verify identity even on the free tier and places a
small temporary authorisation. Always Free resources stay free when the trial
credits expire — but check that what you create is labelled **Always Free**
rather than merely included in the trial.

**2. Make a bucket.** Menu → **Storage** → **Buckets** → **Create Bucket**.

- Name: `homesh-backups`
- Default storage tier: **Standard**
- Leave visibility **private**. Nothing here should ever be public.

**3. Note the namespace.** It is on the bucket's own page, as *Namespace*. A
short string of letters, unique to your tenancy and unchangeable.

**4. Create a key the server can use.** It is called a **Customer secret key**,
and on an older console it is called an **Amazon S3 Compatibility API key** —
same thing, and the second name is the more descriptive one.

There are two routes to it, because the console moved this when it introduced
identity domains and which one you get depends on the account:

*The short way.* Profile icon (top right) → **My profile**. Then look for
**Customer secret keys** — as a tab across the top, or in a list down the left
under *Resources*, depending on the version.

*The way that always works*, if that page does not offer it:

    Menu (hamburger, top left)
      → Identity & Security  →  Identity  →  Domains
      → Default  (the domain)
      → Users    (left sidebar)
      → your own user
      → Customer secret keys  (left sidebar, under "Resources")

or go straight to <https://cloud.oracle.com/identity/domains> and start from
**Default → Users**.

Then **Generate secret key**:

- Name it `homesh-backups`, so it is obvious later what it is for.
- The **secret** is shown **once**, in a dialog. Copy it before closing it —
  there is no way to see it again, only to delete the key and make another.
- The **Access key** appears in the list afterwards, so that half can be fetched
  whenever.

**5. Put them in `.env`** on the machine running Homesh, and not into a chat
window, an email or a note — this is a credential:

```
OFFSITE_PROVIDER=oracle
OFFSITE_REGION=eu-frankfurt-1        # your home region
OFFSITE_NAMESPACE=xxxxxxxxxxxx       # from step 3
OFFSITE_BUCKET=homesh-backups
OFFSITE_ACCESS_KEY=...               # from step 4
OFFSITE_SECRET_KEY=...               # from step 4, shown once
```

**6. Restart:** `.\tools\start-homesh.ps1 -Rebuild`

**Settings → Backups** then shows the off-site copies, and one is sent after
each daily backup. There is a **Back up now** button to prove it without
waiting, and **Bring back** on any off-site copy fetches and decrypts it onto
the local shelf, where it can be restored like any other.

Measured on the real bucket, the first time it ran: a backup of 566,257 rows
took 12 seconds, encrypting and uploading 18.6 MB took 4, and bringing it back
down and decrypting it took 6 -- byte-identical to what was sent.

---

## Not being billed

A worry worth taking seriously: this software takes backups by itself, on a
timer, and will later call AI providers the same way. Software that spends money
on its own is software that can spend it on its own when it has a bug.

**The hard limit is not upgrading.** A Free Tier account that is never upgraded
to Pay As You Go cannot be billed, because there is no payment relationship to
bill against. When the 30-day trial credits expire:

- Always Free resources — including this bucket, up to 20 GB — carry on
  indefinitely. The account stays active as long as it is used within any 60-day
  period.
- Paid resources are reclaimed after a grace period.
- **New paid resources cannot be created at all** until the account is upgraded.

So the platform refuses the spend rather than charging for it. Do not press
**Upgrade**, and there is nothing to go wrong.

Two things worth knowing rather than assuming:

- **Budgets in OCI alert; they do not cap.** A budget will email when a
  threshold is passed and will not stop anything. Set one anyway — it is free
  and it is a smoke alarm — but do not mistake it for a limit.
- **During the trial there are $300 of credits**, so a runaway in the first 30
  days costs credits rather than money. After that, nothing.

## Which encryption to choose on the bucket

**Oracle-managed keys.** The choice matters much less here than it normally
would, because of what arrives in the bucket: the file is already encrypted
before it leaves the house, with a key Oracle has never seen. Bucket encryption
is a second layer underneath that, protecting against Oracle's own disks being
mishandled — not against Oracle, who cannot read the contents either way.

Customer-managed keys would mean an OCI Vault, which is a **paid** service
rather than an Always Free one, and would add a way to lose the backups: lose
the vault key and the bucket is unreadable, on top of the key that already has
to be kept safe. Two keys to lose instead of one, for a layer that is already
redundant.

## What this protects against, and what it does not

**It protects against losing the machine.** Fire, theft, a dead disk, a botched
upgrade: the copies are elsewhere, and they are encrypted, so whoever holds them
holds ciphertext and a filename.

**The direction of travel is the security property.** The house pushes; nothing
out there pulls. There is no server of ours online to break into, nothing
listening, and no credential anywhere else that reaches back into this network.

**It does not protect against somebody who already has this machine.** The key
that writes to the bucket lives here, so whoever takes the machine can also
delete what was written from it.

That one is worth closing, and Oracle can: a key can be given permission to
*write* objects without permission to read or delete them. Then even this server
cannot undo what it has sent. It needs an IAM policy rather than a tick-box, so
it is a second step rather than part of the first — ask for it once the simple
version is running.

**The key is the other half of the backup.** It is in `.env`, on the machine
being backed up. Keep a copy somewhere else — a password manager, a piece of
paper in a drawer at work. Without it the copies that survive the house cannot
be read by anybody, including you. That is the property that makes storing them
on somebody else's disk acceptable, and it cuts both ways.
