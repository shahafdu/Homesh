# The standby: Homesh when the PC is off

The PC serves Homesh when it is on: it is the stronger machine, it holds the
disks, and it is on the network the household mostly uses. When it is off, a
second copy on an Oracle Always Free machine answers instead.

This document is the design, written before building it, because the choices in
it are about what is acceptable rather than what is possible.

---

## The shape

```
 PC (primary, the only writer)              Oracle (standby, read-only)
 ─────────────────────────────              ───────────────────────────
 Homesh + Postgres + the disks              Homesh + Postgres
          │                                          ▲
          │ encrypted backup, hourly                 │ restores the newest
          ▼                                          │
     ┌──────────────────── homesh-backups bucket ────┘
     └──────────────────── (already exists, already encrypted)

 There is no network connection between the two machines. None.
```

The PC already pushes an encrypted backup to the bucket. The standby pulls the
newest one, decrypts it, and restores it into its own database — the same
restore that Settings → Backups performs, on a timer.

**The two machines never talk to each other.** That is the property worth
having, and it is the direct answer to the requirement that nothing online may
become a route into the house: the standby has no address for the PC, no
credential for it and no connection to it. Breaking into the standby reaches a
bucket, not a home.

## What the standby can and cannot do

| With the PC off | |
|---|---|
| Browse and search the whole catalog | ✅ as of the last sync |
| Play anything that lives on Drive | ✅ it reads Drive itself |
| Thumbnails already generated | ✅ synced alongside |
| Play a file that exists only on the PC or the RAID | ❌ the bytes are on a switched-off disk |
| Create or change anything — playlists, positions, sharing | ❌ read-only, and says so |

**Why read-only.** Two databases that both accept writes have to be merged when
the PC comes back, and merging is where this kind of system loses data: the same
playlist edited in two places, a deleted account that comes back. A read-only
standby has nothing to reconcile. The PC is always the truth, and the standby is
a view of it that is at most an hour old.

Writing while the PC is off could come later — changes queued on the standby
and replayed on the PC — but it is a separate piece of work with its own ways to
go wrong, and not worth taking on before the simple version has proved itself.

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
  and is reachable only from your own devices.
- **It cannot reach anything else on that network.** A Tailscale access rule
  tags it so that it may be connected *to*, and may not connect *out* to any
  other device. A machine that cannot start a connection is a dead end even if
  somebody gets onto it.

## What it costs

Nothing, on the Always Free allowance: one Ampere A1 machine with 2 cores and
12 GB, well inside the limits, and an hourly backup of about 19 MB into a bucket
that stays small because old copies are pruned.

**Never press Upgrade** on the Oracle account, and none of this can bill — see
`OFFSITE_BACKUPS.md`.

## The one thing that might not work

**Oracle frequently has no free Ampere capacity**, and Jerusalem is a small
region. Creating the machine can fail with *"Out of host capacity"*, sometimes
for days. It is not a mistake on your side and there is no fix except trying
again later; nothing here depends on the machine being created on the first
attempt.

## What building it involves

1. **You**, in the Oracle console: create the machine (exact clicks to follow),
   and paste in a public key so the PC can reach it over SSH. The SSH direction
   is PC → Oracle, never the other way.
2. **You**, in the Tailscale admin console: add the access rule that makes the
   standby a dead end, and approve it joining.
3. **Me**: set up the machine — Docker, the stack, the hourly restore — push the
   backups hourly rather than daily, sync thumbnails, add read-only mode, and
   teach Homesh Connect the third address.
