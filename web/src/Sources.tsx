import { useCallback, useEffect, useState } from "react";
import { useLockScroll } from "./useLockScroll";
import {
  discoverSources,
  formatDate,
  listSources,
  removeSource,
  scanSource,
  type Source,
} from "./library";

/** Where the library comes from, and what each source is doing.
 *
 * Its own screen rather than a section inside Settings. Settings is where you
 * change how the app looks and which devices can sign in as you — preferences,
 * all of them yours and none of them consequential to anybody else. Adding a
 * folder is not that: it decides what the household can find, it runs for
 * minutes, and it is the thing somebody comes here to do rather than something
 * they adjust while passing through. Burying it four sections down under
 * "Colour" and "Appearance" made a first-class job read like a footnote.
 *
 * Also here rather than in the folder view: the root already lists these as
 * folders to open, and repeating them underneath as a panel to administer
 * showed the same things twice. Browsing and maintaining are different jobs.
 */
export default function Sources(props: { onClose: () => void }) {
  useLockScroll();
  const [sources, setSources] = useState<Source[] | null>(null);
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
  const scanning = sources?.some((s) => s.scan?.state === "running") ?? false;
  useEffect(() => {
    if (!scanning) return;
    const poll = window.setInterval(refresh, 2000);
    return () => window.clearInterval(poll);
  }, [scanning, refresh]);

  const look = async () => {
    setLooking(true);
    setFound(null);
    try {
      const result = await discoverSources();
      setFound(
        result.added.length
          ? `Added ${result.added.join(", ")}`
          : "Nothing new. Add a folder from this computer below, or share one " +
            "with the Homesh account in Drive.",
      );
    } catch (e) {
      setFound(e instanceof Error ? e.message : String(e));
    } finally {
      setLooking(false);
      refresh();
    }
  };

  return (
    <div
      className="sheet"
      role="dialog"
      aria-modal="true"
      aria-label="Sources"
      onClick={props.onClose}
      onKeyDown={(e) => e.key === "Escape" && props.onClose()}
    >
      <div className="sheet-inner wide" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h2>Sources</h2>
          <button className="compact" disabled={looking} onClick={() => void look()}>
            {looking ? "Looking…" : "Look for new folders"}
          </button>
        </div>

        {found && <p className="muted small">{found}</p>}

        {sources === null && <p className="muted">Loading…</p>}

        {sources?.length === 0 && (
          <p className="muted">
            Nothing yet. Add a folder from this computer, or share one with the
            Homesh account in Drive, then press <b>Look for new folders</b>.
          </p>
        )}

        {sources?.map((s) => (
          <div key={s.id} className="zone-card">
            <div className="zone-head">
              <span className="zone-name nm-clip">{s.name}</span>
              <span className="badge">{s.kind === "gdrive" ? "Drive" : "this computer"}</span>
            </div>
            <div className="muted small">{s.mount_prefix}</div>
            <div className="source-foot">
              <span className="muted small">{describeScan(s)}</span>
              <div className="zone-controls">
                <button
                  className="compact"
                  disabled={s.scan?.state === "running"}
                  onClick={async () => {
                    await scanSource(s.id);
                    refresh();
                  }}
                >
                  {s.scan?.state === "running" ? "Scanning…" : "Rescan"}
                </button>
                {/* Forgetting, not deleting. Nothing here can touch a file —
                    the mounts are read-only — and the folder itself stays
                    granted until it is withdrawn on the PC. */}
                <button
                  className="compact"
                  onClick={async () => {
                    if (!confirm(`Remove ${s.name} from the catalog? The files are not touched.`)) {
                      return;
                    }
                    await removeSource(s.id);
                    refresh();
                  }}
                >
                  Remove
                </button>
              </div>
            </div>
          </div>
        ))}

        <AddAFolder onAdded={refresh} />

        <button className="compact" style={{ marginTop: 18 }} onClick={props.onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

/** The button that opens Windows' own folder picker on the PC.
 *
 * A web page cannot open a folder dialog on the machine — no browser will tell
 * a page a path, and a file input returns names and bytes and never
 * "E:\music". But it can ask Windows to open something that can, which is what
 * a protocol handler is for and how a Zoom or Spotify link works.
 *
 * So the button is real. Pressing it hands "homesh://add-folder" to Windows,
 * which runs the grant script, which opens the ordinary folder dialog on the
 * PC. The page never learns the path and does not need to: the picking and the
 * granting both happen on the machine that holds the folder, which is also
 * what keeps the server's reach limited to what was actually chosen.
 *
 * Nothing comes back — a protocol launch has no result — so this watches the
 * source list instead and stops as soon as something new appears.
 */
function AddAFolder(props: { onAdded: () => void }) {
  const [waiting, setWaiting] = useState(false);

  // Poll while a picker is open on the PC. It ends on the first new source, or
  // after two minutes, because somebody who wandered off should not leave the
  // app asking the server about it for the rest of the evening.
  useEffect(() => {
    if (!waiting) return;
    let before: number | null = null;
    const stop = window.setTimeout(() => setWaiting(false), 120_000);
    const poll = window.setInterval(async () => {
      try {
        const now = (await listSources()).length;
        if (before === null) before = now;
        else if (now > before) {
          setWaiting(false);
          props.onAdded();
        }
      } catch {
        // A restart is expected: granting a folder recreates the container.
      }
    }, 2000);
    return () => {
      window.clearInterval(poll);
      window.clearTimeout(stop);
    };
  }, [waiting, props]);

  return (
    <div className="add-folder">
      <h3>Add a folder from this computer</h3>

      <button
        className="compact primary"
        onClick={() => {
          setWaiting(true);
          // Not fetch, not a new tab: handing the URL to the browser is what
          // makes Windows run the handler. A blocked or unregistered protocol
          // fails silently here, which is why the line below says what to do.
          window.location.href = "homesh://add-folder";
        }}
      >
        ＋ Choose a folder…
      </button>

      {waiting ? (
        <p className="muted small">
          Windows is asking which folder — <b>on the PC's screen</b>. Pick one
          and it appears here. The server restarts, so this page may blink.
        </p>
      ) : (
        <p className="muted small">
          Opens the Windows folder picker <b>on the PC that runs Homesh</b>,
          which is where the folders are. Nothing happens if you press it from a
          phone.
        </p>
      )}

      <p className="muted small">
        If nothing opens, double-click <b>Add a folder to Homesh</b> in the
        Homesh folder once — that also teaches this button to work.
      </p>
      <p className="muted small">
        Homesh reads the folders you have given it and nothing else on that
        machine — the same bargain as a folder shared in Drive.
      </p>

      <h3>Add a folder from Google Drive</h3>
      <p className="muted small">
        Share it with the Homesh account as <b>Editor</b> — not Viewer, which
        cannot grant the access the server needs — then press{" "}
        <b>Look for new folders</b>.
      </p>
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
