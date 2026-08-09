import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { canPreview, downloadUrl, formatSize, type FileEntry } from "./library";

/** Full-screen viewer for the kinds that need a viewport rather than a player bar.
 *
 * Photos, video and documents share one shell so that arrow-key navigation, the
 * close affordance and the filename caption behave identically across all three.
 */
export default function Viewer(props: {
  files: FileEntry[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
}) {
  const { files, index, onIndex, onClose } = props;
  const file = files[index];
  const previewable = file ? canPreview(file.kind, file.ext) : false;

  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const step = useCallback(
    (delta: number) => {
      const next = index + delta;
      if (next >= 0 && next < files.length) onIndex(next);
    },
    [index, files.length, onIndex],
  );

  // The viewer gets its own history entry so that back — the natural gesture on a
  // phone — closes it rather than leaving the folder behind it.
  useEffect(() => {
    let closedByBack = false;
    window.history.pushState({ viewer: true }, "");

    const onPop = () => {
      closedByBack = true;
      onClose();
    };
    window.addEventListener("popstate", onPop);

    return () => {
      window.removeEventListener("popstate", onPop);
      // Closed with the button or Escape, so our entry is still on the stack;
      // drop it, or the next back press would appear to do nothing.
      if (!closedByBack) window.history.back();
    };
  }, [onClose]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") step(1);
      else if (e.key === "ArrowLeft") step(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, step]);

  useEffect(() => {
    let cancelled = false;
    setUrl(null);
    setText(null);
    setError(null);

    if (!file) return;

    (async () => {
      try {
        const { url } = await api.get<{ url: string }>(`/api/items/${file.item_id}/url`);
        if (cancelled) return;

        // Plain text renders inline; anything binary is handed to the browser.
        if (previewable && (file.ext === "md" || file.ext === "txt")) {
          const res = await fetch(url, { credentials: "same-origin" });
          if (!cancelled) setText(await res.text());
        }
        setUrl(url);
      } catch {
        if (!cancelled) setError("Could not open this file.");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [file]);

  const save = async () => {
    // A transient anchor rather than window.open: popup blockers treat a
    // programmatic open as suspicious, and this keeps the filename the server sent.
    const href = await downloadUrl(file.item_id);
    const a = document.createElement("a");
    a.href = href;
    a.download = file.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  if (!file) return null;

  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label={file.filename}>
      <header className="v-bar">
        <div className="v-title">
          <span className="v-name">{file.filename}</span>
          <span className="v-meta">
            {formatSize(file.size)}
            {files.length > 1 && ` · ${index + 1} of ${files.length}`}
          </span>
        </div>
        <div className="v-actions">
          <button className="v-btn" onClick={save} title="Save to this device">
            Download
          </button>
          <button className="v-btn" onClick={onClose} aria-label="Close" title="Close (Esc)">
            ✕
          </button>
        </div>
      </header>

      <div className="v-stage">
        {files.length > 1 && (
          <button
            className="v-nav prev"
            onClick={() => step(-1)}
            disabled={index === 0}
            aria-label="Previous"
          >
            ‹
          </button>
        )}

        <div className="v-content">
          {error && <div className="error">{error}</div>}
          {!error && !url && <p className="muted">Loading…</p>}

          {url && file.kind === "photo" && (
            <img className="v-img" src={url} alt={file.filename} />
          )}

          {url && file.kind === "video" && (
            // Direct play: these are the original bytes, decoded by the browser.
            <video className="v-video" src={url} controls autoPlay playsInline />
          )}

          {url && file.kind === "doc" && text !== null && <pre className="v-text">{text}</pre>}

          {url && file.kind === "doc" && text === null && previewable && (
            <iframe className="v-frame" src={url} title={file.filename} />
          )}

          {url && !previewable && (
            <div className="v-nopreview">
              <p className="muted">
                No preview for <code>.{file.ext}</code> files.
              </p>
              <p className="muted small">
                Download it to open in another app, or to keep a copy.
              </p>
              <button className="v-btn" onClick={save}>
                Download {file.filename}
              </button>
            </div>
          )}
        </div>

        {files.length > 1 && (
          <button
            className="v-nav next"
            onClick={() => step(1)}
            disabled={index === files.length - 1}
            aria-label="Next"
          >
            ›
          </button>
        )}
      </div>
    </div>
  );
}
