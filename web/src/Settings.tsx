import { useLockScroll } from "./useLockScroll";
import { useCallback, useEffect, useState } from "react";
import { addPasskey, listPasskeys, passkeysSupported, removePasskey, type Passkey } from "./auth";
import { formatDate } from "./library";
import { PALETTES, type Appearance, type Palette, type Prefs } from "./prefs";
import {
  backupUrl,
  listBackups,
  listOffsite,
  removeBackup,
  restoreBackup,
  retrieveOffsite,
  takeBackup,
  type Backup,
  type Offsite,
} from "./backups";
import { formatSize } from "./library";

/** "today at 14:03", or the date for anything older. Relative because the only
 *  question being asked is "is this the build I think it is". */
function formatBuilt(iso: string): string {
  const at = new Date(iso);
  if (isNaN(at.getTime())) return "at an unknown time";
  const time = at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const days = Math.floor((Date.now() - at.getTime()) / 86400000);
  if (days < 1 && at.getDate() === new Date().getDate()) return `today at ${time}`;
  if (days < 2) return `yesterday at ${time}`;
  return `${at.toLocaleDateString()} at ${time}`;
}

const APPEARANCES: { id: Appearance; label: string }[] = [
  { id: "auto", label: "Match system" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
];

export default function Settings(props: {
  prefs: Prefs;
  onChange: (patch: Partial<Prefs>) => void;
  onLinkDevice: () => void;
  onClose: () => void;
  /** Backups are an administrator's business, and the server refuses anyone
   *  else — so the section is simply absent rather than present and failing. */
  isAdmin?: boolean;
}) {
  useLockScroll();
  const { prefs, onChange, onLinkDevice, onClose } = props;

  return (
    <div
      className="sheet"
      role="dialog"
      aria-modal="true"
      aria-label="Settings"
      // Clicking the backdrop closes; clicks inside the panel must not bubble out.
      onClick={onClose}
      onKeyDown={(e) => e.key === "Escape" && onClose()}
    >
      <div className="sheet-inner" onClick={(e) => e.stopPropagation()}>
        <h2>Settings</h2>

        <div className="setting">
          <h3>Colour</h3>
          <p>Applies everywhere you sign in — phone, desktop and TV.</p>
          <div className="palettes">
            {PALETTES.map((p) => (
              <button
                key={p.id}
                className="pal"
                aria-pressed={prefs.palette === p.id}
                title={p.blurb}
                onClick={() => onChange({ palette: p.id as Palette })}
              >
                <span className="swatch">
                  {p.swatch.map((c) => (
                    <span key={c} style={{ background: c }} />
                  ))}
                </span>
                <span className="label">{p.name}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="setting">
          <h3>Appearance</h3>
          <p>“Match system” follows your device's light or dark setting.</p>
          <div className="seg">
            {APPEARANCES.map((a) => (
              <button
                key={a.id}
                aria-pressed={prefs.appearance === a.id}
                onClick={() => onChange({ appearance: a.id })}
              >
                {a.label}
              </button>
            ))}
          </div>
        </div>

        <div className="group">
          <label>This account</label>
          <Passkeys />
          <button className="compact" onClick={onLinkDevice}>
            Use on another device
          </button>
          {/* Named for the problem rather than the mechanism: what somebody
              wants is Homesh on their phone, and the reason a passkey will not
              do it there is not their concern until they get there. */}
          <p className="muted small">
            Sign in on a phone or tablet that cannot create a passkey.
          </p>
        </div>

        {props.isAdmin && <Backups />}

        {/* Which build you are looking at.
            A phone can sit on a page loaded days ago -- a tab never closed, an
            app resumed from the background -- and a fix that is live then looks
            like one that was never made. This turns "is it stale?" into a fact.
            Pull down to reload if it is older than you expect. */}
        <p className="muted small" style={{ marginTop: 18 }}>
          This page was built {formatBuilt(__BUILT__)}.
        </p>

        <button className="compact" style={{ marginTop: 8 }} onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}


/** The devices that can sign in as you.
 *
 * A passkey belongs both to the device that made it and to the address it was
 * made against, so a household needs several and a server that changes address
 * needs a way to enrol a new one.
 */
function Passkeys() {
  const [keys, setKeys] = useState<Passkey[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listPasskeys().then(setKeys).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="passkeys">
      {keys.map((key) => (
        <div key={key.id} className="invite-row">
          <div>
            <b>{key.label ?? "A device"}</b>
            <div className="muted small">
              added {formatDate(key.created_at)}
              {key.last_used_at && ` · last used ${formatDate(key.last_used_at)}`}
            </div>
          </div>
          <button
            className="compact"
            onClick={async () => {
              setNote(null);
              try {
                await removePasskey(key.id);
              } catch (e) {
                setNote(e instanceof Error ? e.message : String(e));
              }
              refresh();
            }}
          >
            Remove
          </button>
        </div>
      ))}

      <button
        className="compact"
        disabled={busy || !passkeysSupported()}
        onClick={async () => {
          setBusy(true);
          setNote(null);
          try {
            await addPasskey();
            setNote("Added.");
          } catch (e) {
            setNote(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
            refresh();
          }
        }}
      >
        {busy ? "Waiting for passkey…" : "Add a passkey to this device"}
      </button>

      {!passkeysSupported() && (
        <p className="muted small">
          This connection cannot create passkeys — they need https, or the server
          opened on the machine it runs on.
        </p>
      )}
      {note && <p className="muted small">{note}</p>}
    </div>
  );
}


/** Backups kept somewhere this house is not.
 *
 * The only kind that survives the house. Encrypted here before they leave, with
 * a key that stays here — so whoever stores them holds ciphertext and a
 * filename, and nothing that reaches back.
 */
function OffsiteCopies(props: {
  state: Offsite | null;
  busy: boolean;
  onChange: (label: string, work: () => Promise<unknown>) => Promise<void>;
}) {
  const { state } = props;
  const [picked, setPicked] = useState<string | null>(null);
  if (!state) return null;

  const copies = state.backups;
  const chosen = copies.find((b) => b.name === picked) ?? copies[0] ?? null;

  return (
    <>
      <p className="muted small" style={{ marginTop: 14 }}>
        <b>Off the machine.</b>{" "}
        {state.ready
          ? "Encrypted here and sent to your storage bucket after each backup, and " +
            "kept by the same rule. Whoever stores them cannot read them."
          : state.why}
      </p>

      {chosen && (
        <div className="backup-pick">
          <select
            value={chosen.name}
            disabled={props.busy}
            onChange={(e) => setPicked(e.target.value)}
            aria-label="Choose an off-site copy"
          >
            {copies.map((b) => (
              <option key={b.id} value={b.name}>
                {b.taken_at ? backupWhen(b.taken_at) : b.name}
              </option>
            ))}
          </select>
          <div className="muted small">{formatSize(chosen.size_bytes)} · encrypted</div>
          <button
            className="compact"
            disabled={props.busy}
            onClick={() => props.onChange("fetching", () => retrieveOffsite(chosen.name))}
          >
            Bring back
          </button>
        </div>
      )}
    </>
  );
}


/** "Fri 19 Sep, 11:00" -- the weekday first, because "which day" is the
 *  question, and the time because a day can hold more than one. */
function backupWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const isBeforeRestore = (b: Backup) => b.name.includes("-before-restore");

function backupLabel(b: Backup): string {
  return isBeforeRestore(b) ? `${backupWhen(b.taken_at)} · before a restore` : backupWhen(b.taken_at);
}

/** In the order somebody looks for one: recent days, then the weeks behind
 *  them, with the copies taken before a restore kept apart, since those are
 *  the "undo" rather than a point in time anybody chose. */
function backupGroups(backups: Backup[]): { label: string; backups: Backup[] }[] {
  const week = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const groups = [
    { label: "This week", backups: [] as Backup[] },
    { label: "Weekly, further back", backups: [] as Backup[] },
    { label: "Taken before a restore", backups: [] as Backup[] },
  ];
  for (const b of backups) {
    if (isBeforeRestore(b)) groups[2].backups.push(b);
    else if (new Date(b.taken_at).getTime() >= week) groups[0].backups.push(b);
    else groups[1].backups.push(b);
  }
  return groups.filter((g) => g.backups.length > 0);
}


/** Copies of the database, and putting one back.
 *
 * Not the media: that is your own files on your own disks, and copying
 * terabytes somewhere else is a different job. This is everything the server
 * knows *about* them — accounts and their passkeys, who may see what,
 * playlists, where everybody had got to, and a catalog whose tags took hours of
 * reading to work out. None of it exists anywhere else.
 */
function Backups() {
  const [backups, setBackups] = useState<Backup[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [offsite, setOffsite] = useState<Offsite | null>(null);
  const [picked, setPicked] = useState<string | null>(null);

  // What the dropdown shows: the one chosen, or the newest until somebody
  // chooses. Falls back when the chosen one is deleted or pruned away.
  const chosen = backups.find((b) => b.name === picked) ?? backups[0] ?? null;

  const refresh = useCallback(() => {
    listBackups().then(setBackups).catch(() => undefined);
    listOffsite().then(setOffsite).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const run = async (label: string, work: () => Promise<unknown>) => {
    setBusy(label);
    setNote(null);
    try {
      await work();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    }
    setBusy(null);
    setConfirming(null);
    refresh();
  };

  return (
    <div className="group">
      <label>Backups</label>
      <p className="muted small">
        The catalog, accounts, playlists and everything you have marked — not your
        media files. Taken every hour and kept one a day for a week, then one a week
        for five weeks, so there is always one from about a fortnight and about a
        month back.
      </p>

      <button
        className="compact"
        disabled={busy !== null}
        onClick={() =>
          run("taking", async () => {
            const made = await takeBackup();
            setNote(`Backed up — ${made.name}`);
          })
        }
      >
        {busy === "taking" ? "Backing up…" : "Back up now"}
      </button>

      {backups.length === 0 && (
        <p className="muted small">Nothing yet. The first one is taken within the hour.</p>
      )}

      {/* One control and the actions for what it has chosen, rather than a row
          per backup. A list of a dozen near-identical dates with three buttons
          each was a wall, and the question it answers -- "which point in time"
          -- is a choice of one. */}
      {chosen && (
        <div className="backup-pick">
          <select
            value={chosen.name}
            disabled={busy !== null}
            onChange={(e) => {
              setPicked(e.target.value);
              setConfirming(null);
            }}
            aria-label="Choose a backup"
          >
            {backupGroups(backups).map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.backups.map((b) => (
                  <option key={b.name} value={b.name}>
                    {backupLabel(b)}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>

          <div className="muted small">
            {formatSize(chosen.size_bytes)}
            {" · "}
            {/* A backup on the same disk as the thing it is backing up is half a
                backup. Nothing here can put a copy somewhere else for you, so at
                least it is one tap to take one away. */}
            <a href={backupUrl(chosen.name)} download>
              Download
            </a>
          </div>

          {confirming === chosen.name ? (
            <span className="confirm">
              <button
                className="compact danger"
                disabled={busy !== null}
                onClick={() =>
                  run("restoring", async () => {
                    const done = await restoreBackup(chosen.name);
                    setNote(
                      `Restored ${done.rows.toLocaleString()} rows. What was here ` +
                        "first was saved as a backup marked “before a restore”.",
                    );
                  })
                }
              >
                {busy === "restoring" ? "Restoring…" : "Yes, replace everything"}
              </button>
              <button className="compact" onClick={() => setConfirming(null)}>
                Cancel
              </button>
            </span>
          ) : (
            <span className="confirm">
              <button
                className="compact"
                disabled={busy !== null}
                onClick={() => setConfirming(chosen.name)}
              >
                Restore this
              </button>
              <button
                className="compact"
                disabled={busy !== null}
                onClick={() => run("removing", () => removeBackup(chosen.name))}
              >
                Delete
              </button>
            </span>
          )}
        </div>
      )}

      {confirming && (
        <p className="muted small">
          Restoring replaces the catalog, the accounts and the playlists with
          whatever was there when that backup was taken. A copy of the current
          state is saved first, so this can be undone.
        </p>
      )}

      {note && <p className="muted small">{note}</p>}

      <OffsiteCopies state={offsite} busy={busy !== null} onChange={run} />
    </div>
  );
}
