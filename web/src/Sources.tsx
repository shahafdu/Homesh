import { useCallback, useEffect, useState } from "react";
import { useLockScroll } from "./useLockScroll";
import {
  addLocalFolder,
  browseLocal,
  discoverSources,
  formatDate,
  listSources,
  scanSource,
  type Browsing,
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

/** Browsing this machine for a folder to add.
 *
 * The drives are mounted read-only and the server lists them, which is what
 * every other media server does and the only design that works from a phone: no
 * browser will hand a web page a real path, so a file picker here returns names
 * and bytes and never "D:\Media".
 *
 * The first version of this refused to list the host at all and told people to
 * run a script on the PC instead. That is purity at the expense of the person
 * using it — no help whatsoever to somebody holding a phone in another room —
 * and it was rightly rejected. Read-only mounts, admin-only, and nothing read
 * beyond folder names until a folder is picked is where the safety actually
 * lives.
 */
function AddAFolder(props: { onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<Browsing | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const go = useCallback(async (at: string) => {
    setBusy(true);
    setNote(null);
    try {
      setView(await browseLocal(at));
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (open && !view) void go("");
  }, [open, view, go]);

  if (!open) {
    return (
      <div className="add-folder">
        <button className="compact primary" onClick={() => setOpen(true)}>
          ＋ Add a folder from this computer
        </button>
        <p className="muted small">
          Or share a folder with the Homesh account in Google Drive as{" "}
          <b>Editor</b>, then press <b>Look for new folders</b>.
        </p>
      </div>
    );
  }

  return (
    <div className="add-folder">
      <div className="sheet-head">
        <h3>Choose a folder</h3>
        <button className="compact" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>

      {note && <p className="error">{note}</p>}

      {view && (
        <>
          {/* Where you are, and every step back. On a phone this is longer than
              the screen, so it scrolls rather than wrapping into three lines. */}
          <nav className="crumbs browse-crumbs">
            <button className="crumb" onClick={() => void go("")}>
              This computer
            </button>
            {view.crumbs.map((crumb) => (
              <span key={crumb.path}>
                <span className="sep">/</span>
                <button className="crumb" onClick={() => void go(crumb.path)}>
                  {crumb.name}
                </button>
              </span>
            ))}
          </nav>

          {view.at && (
            <div className="browse-here">
              <span className="muted small">
                {view.files} file{view.files === 1 ? "" : "s"} in this folder
              </span>
              {view.added ? (
                <span className="badge">already added</span>
              ) : (
                <button
                  className="compact primary"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    setNote(null);
                    try {
                      const added = await addLocalFolder(view.at);
                      setNote(`Added ${added.name}. Rescan it to index what is in it.`);
                      setOpen(false);
                      setView(null);
                      props.onAdded();
                    } catch (e) {
                      setNote(e instanceof Error ? e.message : String(e));
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Add this folder
                </button>
              )}
            </div>
          )}

          {view.unmounted && (
            <p className="error">
              Docker has not attached this drive, so it looks empty from in
              here. On the PC, run <code>.\tools\mount-drives.ps1</code> — it
              attaches removable drives, which Windows never does by itself,
              and restarts the server. Needed again after a reboot.
            </p>
          )}

          {view.folders.length > 0 ? (
            <ul className="folder-list">
              {view.folders.map((folder) => (
                <li key={folder.path}>
                  <button className="folder-open" onClick={() => void go(folder.path)}>
                    <span className="folder-ic" aria-hidden="true">▸</span>
                    <span className="folder-name nm-clip">{folder.name}</span>
                  </button>
                  {folder.added && <span className="badge">added</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">
              No folders in here{view.files > 0 ? ` — just ${view.files} file(s)` : ""}.
            </p>
          )}
        </>
      )}

      {!view && busy && <p className="muted">Reading…</p>}
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
