import { useState } from "react";
import type { FileEntry } from "./library";
import { canShareFiles, downloadFile, shareFile } from "./share";

/** What you can do with one file, besides open it.
 *
 * A sheet rather than more icons in the row: these rows are already dense, this
 * is a phone, and the set will grow.
 */
export default function FileActions(props: {
  file: FileEntry;
  onSendTo: () => void;
  onClose: () => void;
}) {
  const { file } = props;
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const sharable = canShareFiles();

  const share = async () => {
    setBusy("Preparing…");
    setNote(null);
    const result = await shareFile(file.item_id, file.filename, file.size);
    setBusy(null);

    if (result.ok || result.reason === "cancelled") {
      props.onClose();
      return;
    }
    setNote(result.detail);
  };

  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label={file.filename}
         onClick={props.onClose}>
      <div className="sheet-inner" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h2 className="nm-clip">{file.filename}</h2>
        </div>

        <div className="actions">
          <button className="action" onClick={props.onSendTo}>
            <span className="action-ic">⧉</span>
            <span>
              Send to a room
              <span className="muted small">Play it on a screen or the receiver</span>
            </span>
          </button>

          <button
            className="action"
            disabled={busy !== null}
            onClick={async () => {
              setBusy("Saving…");
              setNote(null);
              try {
                await downloadFile(file.item_id, file.filename);
                props.onClose();
              } catch (e) {
                setNote(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(null);
              }
            }}
          >
            <span className="action-ic">↓</span>
            <span>
              Download
              <span className="muted small">Keep a copy on this device</span>
            </span>
          </button>

          <button className="action" disabled={busy !== null || !sharable} onClick={share}>
            <span className="action-ic">↗</span>
            <span>
              Share…
              <span className="muted small">
                {sharable
                  ? "Sends the file itself — WhatsApp, mail, anywhere"
                  : "Needs a secure connection; download and share from your files"}
              </span>
            </span>
          </button>
        </div>

        {/* Worth saying plainly on the screen where it matters: what leaves this
            house is a copy of the file, not a way back to the server. */}
        <p className="muted small">
          Sharing sends the file, never a link to your server. Nobody you send it
          to gains access to anything else.
        </p>

        {busy && <div className="muted small">{busy}</div>}
        {note && <div className="error">{note}</div>}

        <button className="compact" style={{ marginTop: 12 }} onClick={props.onClose}>
          Close
        </button>
      </div>
    </div>
  );
}
