import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { formatSize, type FileEntry } from "./library";

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
        if (file.ext === "md" || file.ext === "txt") {
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
        <button className="v-close" onClick={onClose} aria-label="Close" title="Close (Esc)">
          ✕
        </button>
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

          {url && file.kind === "doc" && text === null && (
            <iframe className="v-frame" src={url} title={file.filename} />
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
