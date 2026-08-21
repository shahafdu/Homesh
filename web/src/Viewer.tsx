import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import PdfView from "./PdfView";
import { printDocument, printImage, printViaShareSheet } from "./print";
import { canShareFiles } from "./share";
import {
  canPreview,
  documentUrl,
  downloadUrl,
  type FileEntry,
  formatSize,
  needsConversion,
  liveVideoUrl,
  needsVideoConversion,
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
  // Printing goes through the browser's own dialog, which can take a moment to
  // reach: a document is rendered to PDF on the server the first time.
  const [printing, setPrinting] = useState(false);
  const [printNote, setPrintNote] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  // Set when direct play turned out not to work for this file.
  const [fellBack, setFellBack] = useState(false);
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

  // A new file gets a fresh chance at direct play; the last one's failure says
  // nothing about it.
  useEffect(() => {
    setFellBack(false);
  }, [file?.item_id]);

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
          {/* Here as well as in the file menu, because this is where somebody
              is standing when they decide to print: looking at the thing. */}
          {(file.kind === "photo" || file.kind === "doc") && (
            <button
              className="v-btn"
              disabled={printing}
              title="Print, or save as a PDF from the same dialog"
              onClick={async () => {
                setPrinting(true);
                setPrintNote(null);
                try {
                  // The same reasoning as the file menu: where a share sheet
                  // exists, Print lives inside it and works. Trying to print a
                  // PDF from a hidden frame on a phone downloads it instead.
                  const result = canShareFiles()
                    ? await printViaShareSheet(file)
                    : file.kind === "photo"
                      ? await printImage(await downloadUrl(file.item_id), file.filename)
                      : await printDocument(file.item_id, file.ext);
                  if (!result.ok) setPrintNote(result.detail);
                } catch (e) {
                  setPrintNote(e instanceof Error ? e.message : String(e));
                } finally {
                  setPrinting(false);
                }
              }}
            >
              {printing ? "Preparing…" : "Print"}
            </button>
          )}
          <button className="v-btn" onClick={save} title="Save to this device">
            Download
          </button>
          <button className="v-btn" onClick={onClose} aria-label="Close" title="Close (Esc)">
            ✕
          </button>
        </div>
      </header>

      {printNote && <div className="error v-print-note">{printNote}</div>}

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

          {url && file.kind === "video" && !needsVideoConversion(file.ext) && !fellBack && (
            // Direct play first: these are the original bytes, decoded by the
            // browser, which is free where converting is not.
            //
            // The container is a poor guide to whether that will work — an .mp4
            // can hold a codec from before H.264 was universal, and the browser
            // then plays the sound and shows nothing at all, which looks far
            // more broken than an honest failure. So the outcome is watched:
            // an error, or a decoded track with no picture, falls back to
            // converting as it plays.
            <video
              className="v-video"
              src={url}
              controls
              autoPlay
              playsInline
              onError={() => setFellBack(true)}
              onLoadedData={(e) => {
                if (e.currentTarget.videoWidth === 0) setFellBack(true);
              }}
            />
          )}

          {file.kind === "video" && (needsVideoConversion(file.ext) || fellBack) && (
            <Convertible file={file} />
          )}

          {url && file.kind === "doc" && text !== null && <pre className="v-text">{text}</pre>}

          {url && file.kind === "doc" && text === null && previewable && (
            // An office document is rendered by the server and arrives as a PDF;
            // a PDF is already one. Either way this draws it, rather than handing
            // it to the browser — a phone has no PDF viewer to hand it to, and
            // would download the file instead of showing it.
            <PdfView
              url={needsConversion(file.ext) ? documentUrl(file.item_id) : url}
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
/** Seconds as h:mm:ss, or m:ss below an hour. */
function asClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function Convertible(props: { file: FileEntry }) {
  // Where in the file to begin. A live stream has no index to seek within, so
  // moving through it restarts the encoder further in — crude, but it means a
  // two-hour tape can be watched from the middle without first converting all of
  // it and keeping the result.
  const [start, setStart] = useState(0);
  const [jump, setJump] = useState("0:00");
  const [ready, setReady] = useState(false);
  const video = useRef<HTMLVideoElement | null>(null);

  // Where you are, kept in the box you type into.
  //
  // It used to be empty with "12:30" as a placeholder, which reads as a value —
  // and a value of half past twelve, at that. Showing the real position makes
  // the field mean something before it is touched, and makes it obvious what to
  // type: the stream begins at `start`, and the element counts from there.
  useEffect(() => {
    const el = video.current;
    if (!el) return;
    const tick = () => {
      if (document.activeElement === document.getElementById("jump-to")) return;
      setJump(asClock(start + el.currentTime));
    };
    el.addEventListener("timeupdate", tick);
    return () => el.removeEventListener("timeupdate", tick);
  }, [start, ready]);

  return (
    <div className="v-live">
      {/* Twelve seconds can pass before the first frame: the file is on Drive
          and the encoder has to reach it before it can encode anything. A black
          rectangle for that long reads as broken, so it says what it is doing. */}
      {!ready && (
        <div className="v-starting">
          <span className="spinner" aria-hidden="true" />
          <p className="muted">Starting the film…</p>
          <p className="muted small">
            A large tape takes a few seconds to reach. Nothing is being downloaded.
          </p>
        </div>
      )}

      <video
        ref={video}
        className="v-video"
        style={ready ? undefined : { display: "none" }}
        src={liveVideoUrl(props.file.item_id, start)}
        controls
        autoPlay
        playsInline
        onLoadedData={() => setReady(true)}
      />

      <div className="v-live-bar">
        <span className="muted small">
          MPEG-2, converted as it plays. Nothing is downloaded or stored.
        </span>

        <span className="v-jump">
          <label className="muted small" htmlFor="jump-to">Position</label>
          <input
            id="jump-to"
            className="jump"
            value={jump}
            inputMode="numeric"
            aria-label="Position — type a time and press Enter to jump there"
            title="Type mm:ss or h:mm:ss and press Enter"
            onChange={(e) => setJump(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              // mm:ss or plain minutes, because a scrub bar cannot be offered
              // and a seconds count is not how anybody thinks about a tape.
              // h:mm:ss, mm:ss, or plain minutes. A tape runs to two hours, so
              // the hour form has to be accepted — and a seconds count is not
              // how anybody thinks about one.
              const parts = jump.split(":").map((n) => parseInt(n, 10));
              const seconds = parts.some(isNaN)
                ? NaN
                : parts.length === 3
                  ? parts[0] * 3600 + parts[1] * 60 + parts[2]
                  : parts.length === 2
                    ? parts[0] * 60 + parts[1]
                    : parts[0] * 60;
              if (!isNaN(seconds)) {
                setReady(false);
                setStart(seconds);
              }
            }}
          />
          {start > 0 && (
            <button className="compact" onClick={() => { setReady(false); setStart(0); }}>
              Back to the start
            </button>
          )}
        </span>
      </div>
    </div>
  );
}
