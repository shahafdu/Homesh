import { useLockScroll } from "./useLockScroll";
import { useCallback, useEffect, useState } from "react";
import { addPasskey, listPasskeys, passkeysSupported, removePasskey, type Passkey } from "./auth";
import { formatDate } from "./library";
import { PALETTES, type Appearance, type Palette, type Prefs } from "./prefs";
import {
  backupUrl,
  listBackups,
  removeBackup,
  restoreBackup,
  takeBackup,
  type Backup,
} from "./backups";
import { formatSize } from "./library";

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

        <button className="compact" style={{ marginTop: 18 }} onClick={onClose}>
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

  const refresh = useCallback(() => {
    listBackups().then(setBackups).catch(() => undefined);
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
        media files. Taken once a day and kept for a week, plus a fortnight and a
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

      {backups.map((backup) => (
        <div key={backup.name} className="invite-row">
          <div>
            <b>{formatDate(backup.taken_at)}</b>
            <div className="muted small">
              {formatSize(backup.size_bytes)}
              {" · "}
              {/* A backup on the same disk as the thing it is backing up is
                  half a backup. Nothing here can put a copy somewhere else for
                  you, so at least it is one tap to take one away. */}
              <a href={backupUrl(backup.name)} download>
                Download
              </a>
            </div>
          </div>
          {confirming === backup.name ? (
            <span className="confirm">
              <button
                className="compact danger"
                disabled={busy !== null}
                onClick={() =>
                  run("restoring", async () => {
                    const done = await restoreBackup(backup.name);
                    setNote(
                      `Restored ${done.rows.toLocaleString()} rows. What was here ` +
                        `first was saved as ${done.previous_state_saved_as}.`,
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
                onClick={() => setConfirming(backup.name)}
              >
                Restore
              </button>
              <button
                className="compact"
                disabled={busy !== null}
                onClick={() => run("removing", () => removeBackup(backup.name))}
              >
                Delete
              </button>
            </span>
          )}
        </div>
      ))}

      {confirming && (
        <p className="muted small">
          Restoring replaces the catalog, the accounts and the playlists with
          whatever was there when that backup was taken. A copy of the current
          state is saved first, so this can be undone.
        </p>
      )}

      {note && <p className="muted small">{note}</p>}
    </div>
  );
}
