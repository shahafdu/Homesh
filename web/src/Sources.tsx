import { useCallback, useEffect, useState } from "react";
import { useLockScroll } from "./useLockScroll";
import { discoverSources, formatDate, listSources, scanSource, type Source } from "./library";

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

        <AddAFolder />

        <button className="compact" style={{ marginTop: 18 }} onClick={props.onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

/** How a folder on this PC gets into the library.
 *
 * Not a browser. The first attempt listed the machine and let you click through
 * it, which was the wrong shape of answer: a media server should not be
 * enumerating somebody's disk to be told one path. It did that because a web
 * page cannot do the obvious thing — no browser will tell a page a real path,
 * so a picker in here would return names and bytes and never "D:\Media".
 *
 * So the picking happens on the PC, where Windows already has the dialog, and
 * this only says so. It is written out here rather than left to a readme
 * because the person who needs it is usually holding a phone.
 */
function AddAFolder() {
  return (
    <div className="add-folder">
      <h3>Add a folder from this computer</h3>
      <p className="muted small">
        On the PC that runs Homesh, open PowerShell in the Homesh folder and run:
      </p>
      <pre className="cmd">.\tools\add-folder.ps1</pre>
      <p className="muted small">
        Windows asks which folder. Whatever you choose is added read-only and
        appears above; press <b>Look for new folders</b> to index it.
      </p>
      <p className="muted small">
        <code>-List</code> shows what has been added, and{" "}
        <code>-Remove &lt;name&gt;</code> takes one out again — which never
        touches the files in it.
      </p>

      <h3>Add a folder from Google Drive</h3>
      <p className="muted small">
        Share it with the Homesh account as <b>Editor</b>, then press{" "}
        <b>Look for new folders</b>. There is nothing to upload and no path to
        type.
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
