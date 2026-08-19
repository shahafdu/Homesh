import { useLockScroll } from "./useLockScroll";
import { useCallback, useEffect, useState } from "react";
import { addPasskey, listPasskeys, passkeysSupported, removePasskey, type Passkey } from "./auth";
import { discoverSources, formatDate, listSources, scanSource, type Source } from "./library";
import { PALETTES, type Appearance, type Palette, type Prefs } from "./prefs";

const APPEARANCES: { id: Appearance; label: string }[] = [
  { id: "auto", label: "Match system" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
];

export default function Settings(props: {
  prefs: Prefs;
  onChange: (patch: Partial<Prefs>) => void;
  isAdmin: boolean;
  onLinkDevice: () => void;
  onClose: () => void;
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

        {props.isAdmin && <Sources />}

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

        <button className="compact" style={{ marginTop: 18 }} onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}


/** Where the library comes from, and what it is doing.
 *
 * Here rather than in the folder view: the root already lists these as folders
 * to open, and repeating them underneath as a panel to administer showed the
 * same things twice over. Browsing and maintaining are different jobs.
 */
function Sources() {
  const [sources, setSources] = useState<Source[]>([]);
  const [looking, setLooking] = useState(false);
  const [found, setFound] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listSources().then(setSources).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // While anything is scanning, keep asking. A Drive folder takes minutes, and a
  // count that only moves when you happen to reload is not progress.
  const scanning = sources.some((s) => s.scan?.state === "running");
  useEffect(() => {
    if (!scanning) return;
    const poll = window.setInterval(refresh, 2000);
    return () => window.clearInterval(poll);
  }, [scanning, refresh]);

  return (
    <div className="group">
      <label>Library sources</label>
      <SourceList
        sources={sources}
        isAdmin
        onScan={async (id) => {
          await scanSource(id);
          refresh();
        }}
      />

      {/* How a folder is added: share it with the Homesh account in Drive, then
          ask here. There is nothing to upload and no path to type. */}
      <button
        className="compact"
        disabled={looking}
        onClick={async () => {
          setLooking(true);
          setFound(null);
          try {
            const result = await discoverSources();
            setFound(
              result.added.length
                ? `Added ${result.added.join(", ")}`
                : "Nothing new — share a folder with the Homesh account in Drive first.",
            );
          } catch (e) {
            setFound(e instanceof Error ? e.message : String(e));
          } finally {
            setLooking(false);
            refresh();
          }
        }}
      >
        {looking ? "Looking…" : "Look for new folders"}
      </button>
      {found && <p className="muted small">{found}</p>}
    </div>
  );
}

/** One line saying what this source is doing, or last did.
 *
 * "Never scanned" is called out because it is the state that hid a folder
 * sitting at zero files: indistinguishable, until now, from a folder that was
 * genuinely empty.
 */
function describeScan(s: Source): string {
  const scan = s.scan;
  const files = `${s.files.toLocaleString()} files`;

  if (scan?.state === "running") {
    return scan.seen > 0
      ? `Scanning — ${scan.seen.toLocaleString()} found so far`
      : "Scanning — starting…";
  }
  if (scan?.state === "failed") return `${files} · scan failed: ${scan.error ?? "unknown"}`;
  if (!scan?.state) return `${files} · never scanned`;
  return s.last_seen_at ? `${files} · scanned ${formatDate(s.last_seen_at)}` : files;
}

function SourceList(props: {
  sources: Source[];
  isAdmin: boolean;
  onScan: (id: string) => void;
}) {
  if (props.sources.length === 0) return null;
  return (
    <div className="sources">
      <h2>Sources</h2>
      {props.sources.map((s) => (
        <div key={s.id} className="source">
          <div>
            <strong>{s.name}</strong> <span className="muted">{s.mount_prefix}</span>
            <div className="muted small">{describeScan(s)}</div>
          </div>
          {props.isAdmin && (
            <button
              className="compact"
              disabled={s.scan?.state === "running"}
              onClick={() => props.onScan(s.id)}
            >
              {s.scan?.state === "running" ? "Scanning…" : "Rescan"}
            </button>
          )}
        </div>
      ))}
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
