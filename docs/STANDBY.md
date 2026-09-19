# The standby: Homesh when the PC is off

The PC serves Homesh when it is on: it is the stronger machine, it holds the
disks, and it is on the network the household mostly uses. When it is off, a
second copy on an Oracle Always Free machine answers instead.

This document is the design, written before building it, because the choices in
it are about what is acceptable rather than what is possible.

---

## The shape

```
 PC (primary)                                 Oracle (standby)
 ------------                                 ----------------
 Homesh + Postgres + the disks                Homesh + Postgres
     |   ^                                         ^   |
     |   | replays the standby's changes           |   | its own changes,
     |   |                                         |   | as a list of operations
     v   |                                         |   v
   +-----+-------- homesh-backups bucket ----------+-----+
   |  encrypted backups, hourly     encrypted outboxes   |
   +-----------------------------------------------------+

 There is no network connection between the two machines. None.
```

Everything the two machines say to each other goes through the bucket, and
everything in the bucket is encrypted before it is written. The PC pushes
backups; the standby pulls them. The standby pushes the changes made on it; the
PC pulls those.

**The machines never talk to each other.** That is the property worth having,
and it is the direct answer to the requirement that nothing online may become a
route into the house: the standby has no address for the PC, no credential for
it and no connection to it. Breaking into the standby reaches a bucket, not a
home.

## What the standby can and cannot do

| With the PC off | |
|---|---|
| Browse and search the whole catalog | ✅ as of the last backup, at most an hour old |
| Play anything that lives on Drive | ✅ it reads Drive itself |
| Thumbnails already generated | ✅ synced alongside |
| Play a file that exists only on the PC or the RAID | ❌ the bytes are on a switched-off disk |
| Playlists — make, rename, reorder, add, remove, copy, share | ✅ kept, and carried back to the PC |
| Your own settings — colour, appearance, view | ✅ kept, and carried back |
| Accounts, invitations, who may see what, rooms, folders, backups | ❌ refused on the standby |

## How changes made on the standby get back

**Not by merging databases.** Two copies of a database edited apart cannot be
reconciled reliably by comparing their tables — the same playlist renamed in two
places, a track removed on one side and moved on the other — and that is where
this kind of system quietly loses data.

**By replaying what you did.** Every change on the standby is recorded as the
request that made it: *make a playlist called this*, *add this track to it*.
When the PC is back it fetches that list and performs each one itself, through
its own code, as the person who did it, in the order they happened.

What that buys:

- **The PC's own rules apply.** A change is checked by exactly the code that
  would have checked it had the PC been on — access, validity, all of it. The
  standby cannot smuggle in anything the PC would have refused.
- **A change that no longer makes sense is skipped, not forced.** Adding a track
  to a playlist that was deleted in the meantime fails the way it would have
  failed, and is reported rather than resurrecting the playlist.
- **Replaying twice does nothing twice.** Each change carries an identity and the
  PC remembers which it has applied, so a crash half way through is harmless.
- **Something made on the standby keeps its name.** A playlist is named by an
  id in every later change to it, so the standby chooses the id and the PC
  creates it with the same one. Otherwise "add a track to this playlist" would
  arrive at the PC naming a playlist it had given a different id.

**Why accounts, access and rooms are refused rather than replayed.** They are
rare, they are the changes that matter most if they go wrong, and none of them is
something anybody needs to do while the PC happens to be off. Refusing them keeps
the part that has to be carried back to the ordinary, personal things — and those
are the changes that make a standby worth having.

**When the refresh happens.** A new backup only appears while the PC is on, so
the standby replaces its catalog precisely when nobody is using it. It is never
refreshed out from under somebody browsing it with the PC off.

**What stays on the standby and is never replaced.** Its own passkeys (a passkey
belongs to an address, and the standby has a different one), who is signed in,
and the list of changes not yet carried back. Everything else is replaced from
the PC's backup — and only once that backup already contains every change the
standby made, so an hourly refresh can never throw away something you did.

## How your phone finds the right one

Homesh Connect already tries more than one address — the house address first,
then Tailscale. The standby becomes a third: **PC at home → PC over Tailscale →
standby.** The first one that answers wins, so when the PC is on you never touch
Oracle at all.

**Passkeys are tied to an address.** The standby has a different name from the
PC, so a passkey made for one does not sign in to the other. Each phone signs in
to the standby once, with **Use on another device**. It is a one-off per device.

## What changes about the risk

Worth saying plainly, because it is a real change rather than a detail.

Today, nothing online can read the catalog: the bucket holds ciphertext and the
key is in the house. **A standby has to be able to read it**, or it cannot serve
it. So the Oracle machine holds:

- the **backup key**, to decrypt what it pulls;
- the **catalog, decrypted**, in its own database;
- the **Drive read credential**, to play what lives on Drive.

If that machine were broken into, those three are what would be exposed: the
catalog — which names rooms, folders and accounts — and read access to the Drive
folders shared with Homesh. Not the house, and not anything on the PC.

Two things keep that as small as it can be:

- **The standby is not on the public internet.** It joins your Tailscale network
  and is reachable only from your own devices. Its only public door, SSH for the
  first setup, is closed once Tailscale is running.
- **It cannot reach anything else on that network.** A Tailscale access rule
  tags it so that it may be connected *to*, and may not connect *out* to any
  other device. A machine that cannot start a connection is a dead end even if
  somebody gets onto it.

## What it costs

Nothing, on the Always Free allowance: one Ampere A1 machine at whatever the
console marks *Always Free-eligible*, and hourly backups of about 19 MB into a
bucket that stays small because old copies are pruned.

**Take the eligible size, whatever it is.** The allowance has been cut twice
without notice -- 4 OCPU / 24 GB, then 2 and 12 in June 2026, and by September
2026 some regions' consoles offer exactly **1 OCPU and 6 GB** and nothing else. That is enough for what the standby does: serve the catalog and play files
that live on Drive, which is copying bytes rather than working on them. What one
core will not do is transcode, so on the standby a video that the screen cannot
decode by itself will not play. On the PC it would have been converted on the
fly; this is the same limitation as a file that lives only on the RAID, and it
applies to a smaller number of files.

**Never press Upgrade** on the Oracle account, and none of this can bill — see
`OFFSITE_BACKUPS.md`.

---

## Setting it up — your part

### 1. Create the machine

Oracle console -> **(menu) Compute -> Instances -> Create instance**. The page is
one long form; these are its sections in the order they appear, and anything not
named here is left as it came.

**Name and compartment**

- **Name:** `homesh-standby`
- **Compartment:** the one already selected. A fresh account has only the root
  one, and nothing here needs a second.

**Placement**

Leave the availability domain as it is. If creation later fails with *Out of
host capacity* and the region offers more than one domain, trying another is the
first thing to try.

**Security**

Leave both **Shielded instance** and **Confidential computing** off. This section
is not where the SSH key goes -- that has a section of its own, further down.

**Image and shape**

- **Shape -> Change shape -> Ampere -> VM.Standard.A1.Flex**, and leave the OCPU
  and memory at whatever the console marks *Always Free-eligible* -- in some
  regions that is **1 OCPU and 6 GB**, and it is the only size offered. **Never raise it
  past the eligible mark**: above it the shape is a paid one, and the whole
  arrangement rests on there being nothing here that can bill. Guides quoting
  4 OCPU / 24 GB, or 2 and 12, describe allowances Oracle has since cut.
- **Image -> Change image -> Canonical Ubuntu -> 24.04.** Choose the shape first;
  the image list then offers the `aarch64` build, which is the one that runs on
  Ampere. Not 20.04, which is old enough to be awkward, and not 26.04.

**Networking**

- **Create a new virtual cloud network.** Name it `homesh-vcn`; the wizard makes
  a **public subnet** with it, which is what is wanted.
- **Subnet:** the public one it just made.
- **Assign a public IPv4 address: Yes.** The machine has to reach the bucket,
  Drive and Tailscale, and on Oracle a public address is the free way to do that.
  It is also how I install everything before Tailscale exists.
- **Network security groups:** leave unticked. The subnet's own security list is
  enough, and it already allows SSH.

**Add SSH keys** -- its own section, below networking

The key is made on the PC, once, and kept in `.local`, which is never committed:

```powershell
ssh-keygen -t ed25519 -N '""' -C homesh -f .local\standby_ed25519
```

Choose **Paste public keys** and paste the contents of
`.local\standby_ed25519.pub` -- the **whole line**, the trailing comment
included, with no line break anywhere in the middle of it. A rejected paste is
almost always a line break in the middle of the blob. The file without `.pub` is
the private half and never leaves the PC.

**Why the key is not printed here.** It is a public key and cannot log anybody
in, but it is not anonymous either: an SSH server can be asked whether it
accepts a given key, so a key published beside the words "the standby" is a way
to find which machine on the internet is yours. This document once printed the
real one; it was replaced on the machine as soon as that was noticed, so the key
in the history opens nothing and identifies nothing.

There is no password login on these images, so this key is the only way in. If it
is wrong or missing, the machine has to be destroyed and made again.

**Boot volume**

- Tick **Specify a custom boot volume size** and set **100 GB**.

  The default is about 47 GB, which works but leaves little room: the thumbnails
  for a library this size, the Docker images and the database want space, and
  growing a boot volume later means resizing the filesystem on a running
  machine. The Always Free allowance is **200 GB of block storage in total**, and
  this is the only instance using it, so 100 GB is free and stays free.
- Leave the performance setting (**Balanced**), the backup policy and the
  encryption as they are. Oracle-managed keys are right here: what actually
  matters -- the backups in the bucket -- is encrypted by Homesh before it is
  sent, with a key Oracle never sees.

**Advanced options**, wherever a section offers them: leave every one alone. They
cover cloud-init scripts, hostnames, in-transit encryption and capacity
reservations. The machine is configured after it boots, over SSH, not here.

Then **Create**, and wait for the state to go orange *Provisioning* to green
*Running*, which takes a minute or two.

**If it says "Out of host capacity"**, Oracle has no free Ampere machines in the
region at that moment. Nothing is wrong on your side; try again later -- early
morning often works. **Do not** use the trick some guides suggest of creating an
A2 machine and changing its shape afterwards: A2 is a paid shape, and the point
is that nothing here can bill.

**When it is running**, copy its **public IP address** -- on the instance's page,
under *Instance access* -- into `C:\Scripts\Media_Server\.local\standby-ip`.
That file is never committed, and the address is only needed until Tailscale is
running.

**What is open while this is being set up.** The new subnet's default rules allow
SSH on port 22 from anywhere, which is how the machine gets installed at all. It
is a machine with no password and one key, holding nothing yet, and port 22 is
closed for good once Tailscale answers, which is my side of this.

### 2. Make it a dead end on your Tailscale network

Tailscale admin console → **Access controls**. The default policy lets every
device reach every other device; the change below keeps that for your own
devices and gives the standby nothing.

**Before replacing anything**, check whether the file has rules you added
yourself. If it is still Tailscale's default (a single rule with `"src": ["*"]`
and `"dst": ["*:*"]`), replace that rule, and add the `"tagOwners"` section, so
that the file contains:

```jsonc
{
  "tagOwners": {
    "tag:homesh-standby": ["autogroup:admin"]
  },
  "acls": [
    // Your own devices reach each other exactly as before.
    { "action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:member:*"] },

    // Your devices may reach the standby, and only its HTTPS port.
    { "action": "accept", "src": ["autogroup:member"], "dst": ["tag:homesh-standby:443"] }

    // Nothing has the standby as its source, so it cannot open a connection to
    // anything. Tailscale refuses whatever is not allowed.
  ]
}
```

If it has other rules you rely on, don't replace it — say so, and I will write
the change that keeps them.

### 3. A key for it to join with

Tailscale admin console → **Settings → Keys → Generate auth key**:

- **Reusable:** off
- **Ephemeral:** off
- **Pre-approved:** on
- **Tags:** `tag:homesh-standby`

Save it into `C:\Scripts\Media_Server\.local\tailscale-authkey` — **not** into a
chat window. I copy it from there to the machine and delete it afterwards.

## Setting it up — my part

- ✅ Hourly backups on the PC, pruned — here and in the bucket.
- ✅ The standby's refresh, which waits until a backup contains every change
  the standby made.
- ✅ The record of changes on the standby, and their replay on the PC.
- ✅ Refusing accounts, access, rooms and folders on the standby, with the
  status bar saying "PC offline - on standby" before anything is attempted.
- ✅ The machine itself: `tools/deploy-standby.ps1` installs Docker, clones this
  repository onto it, writes the configuration it needs and builds the stack.
  Running it again fetches `main` and rebuilds, which is how the standby is
  upgraded. Verified on the real machine: it came up as a standby, pulled the
  newest backup out of the bucket and restored 131,302 items from it, reads all
  five Drive folders, shows the two folders that live on the PC as offline, and
  refuses a write that is not replayable with 409.
- ⬜ Tailscale with HTTPS, and the public SSH rule closed once it answers --
  `tools/deploy-standby.ps1 -Tailscale` does it, and it is waiting for the
  access rule and the join key above.
- ⬜ Thumbnails synced alongside.
- ⬜ Homesh Connect trying the standby third.
