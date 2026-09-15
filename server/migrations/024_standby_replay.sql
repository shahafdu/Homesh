-- Changes made on the standby, and which of them the PC has already carried out.
--
-- The standby answers when the PC is off, and it accepts ordinary personal
-- changes — playlists, settings — so that the house does not go read-only every
-- time the PC sleeps. Those changes have to reach the PC when it wakes, and the
-- two machines never talk to each other: everything travels through the bucket,
-- encrypted.
--
-- Not by merging tables. Two copies of a database edited apart cannot be
-- reconciled reliably by comparing rows, and that is where this kind of system
-- quietly loses data. Each change is recorded as the request that made it, and
-- the PC performs that request itself, through its own code, as the person who
-- made it — so the PC's own rules decide whether it still makes sense.
--
-- Both tables exist on both machines because the schema is shared. Which one is
-- used depends on the role the machine is running as.

-- On the standby: what somebody did, waiting to be carried back.
CREATE TABLE IF NOT EXISTS outbox_ops (
    id          uuid PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    -- Who did it. Replayed as them and nobody else, so access on the PC is
    -- exactly what it would have been. No foreign key: this row must survive
    -- the standby's own restores, which replace `users` wholesale.
    user_id     uuid NOT NULL,
    method      text NOT NULL,
    path        text NOT NULL,
    body        bytea,
    -- Set once the op has been written into an outbox in the bucket. Not the
    -- same as applied: the PC may not have run it yet.
    pushed_at   timestamptz
);

CREATE INDEX IF NOT EXISTS outbox_ops_pending_idx
    ON outbox_ops (at) WHERE pushed_at IS NULL;

-- On the PC: every op it has dealt with, and how. This table travels in the PC's
-- backups, which is how the standby learns that a change of its own is now part
-- of the truth and can be let go.
CREATE TABLE IF NOT EXISTS applied_ops (
    id          uuid PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    -- 'applied' or 'skipped'. Skipped is not an error: adding a track to a
    -- playlist deleted in the meantime fails exactly as it would have failed,
    -- and the reason is kept so it can be shown rather than guessed at.
    outcome     text NOT NULL,
    status      integer,
    detail      text
);
