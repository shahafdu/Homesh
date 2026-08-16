import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import {
  canPreview,
  type Conversion,
  conversionStatus,
  convertedUrl,
  documentUrl,
  downloadUrl,
  type FileEntry,
  formatSize,
  needsConversion,
  needsVideoConversion,
  startConversion,
} from "./library";

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

          {url && file.kind === "video" && !needsVideoConversion(file.ext) && (
            // Direct play: these are the original bytes, decoded by the browser.
            <video className="v-video" src={url} controls autoPlay playsInline />
          )}

          {file.kind === "video" && needsVideoConversion(file.ext) && (
            <Convertible file={file} />
          )}

          {url && file.kind === "doc" && text !== null && <pre className="v-text">{text}</pre>}

          {url && file.kind === "doc" && text === null && previewable && (
            // An office document is rendered by the server and arrives as a PDF;
            // a PDF is already one. Either way the browser shows a PDF, so there
            // is a single viewer rather than one per format.
            <iframe
              className="v-frame"
              src={needsConversion(file.ext) ? documentUrl(file.item_id) : url}
              title={file.filename}
            />
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


/** A video in a format no browser decodes.
 *
 * Rather than an empty black player, it says what the file is and what can be
 * done about it. Converting is offered, not started: an hour of tape takes tens
 * of minutes on this hardware, and it is only worth doing for someone who
 * actually wants to watch it here rather than send it to a screen that can
 * already decode it.
 */
function Convertible(props: { file: FileEntry }) {
  const [status, setStatus] = useState<Conversion | null>(null);
  const [error, setError] = useState<string | null>(null);

  const read = useCallback(() => {
    conversionStatus(props.file.item_id).then(setStatus).catch(() => undefined);
  }, [props.file.item_id]);

  useEffect(() => {
    read();
  }, [read]);

  // Only while something is happening. Polling a finished conversion for the
  // rest of the evening would be asking a question already answered.
  const working = status?.state === "queued" || status?.state === "running";
  useEffect(() => {
    if (!working) return;
    const poll = window.setInterval(read, 3000);
    return () => window.clearInterval(poll);
  }, [working, read]);

  if (status?.state === "done") {
    return <video className="v-video" src={convertedUrl(props.file.item_id)} controls autoPlay playsInline />;
  }

  return (
    <div className="v-nopreview">
      <p className="muted">
        This is <b>MPEG-2</b> video — a DVD or a camcorder tape. No browser can
        decode it.
      </p>

      {working ? (
        <>
          <p className="muted small">
            Converting… {status?.progress ?? 0}%. It keeps going if you close this.
          </p>
          <div className="bar wide">
            <i style={{ width: `${status?.progress ?? 0}%` }} />
          </div>
        </>
      ) : (
        <>
          <p className="muted small">
            Send it to a television and it will play as it is — a set-top box
            decodes this in hardware. To watch it here, it has to be converted
            once, which takes a while for a long tape.
          </p>
          <button
            className="v-btn"
            onClick={async () => {
              setError(null);
              try {
                setStatus(await startConversion(props.file.item_id));
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              }
            }}
          >
            Convert for playback here
          </button>
        </>
      )}

      {status?.state === "failed" && (
        <div className="error">Conversion failed: {status.error}</div>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}
