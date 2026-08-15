import { useEffect, useState } from "react";
import { useLockScroll } from "./useLockScroll";
import { copyText } from "./copy";
import type { FileEntry } from "./library";
import {
  canShareFiles,
  createDriveLink,
  downloadFile,
  driveLink,
  revokeDriveLink,
  shareFile,
  type DriveLink,
} from "./share";

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
  useLockScroll();
  const { file } = props;
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [drive, setDrive] = useState<DriveLink | null>(null);
  const [copied, setCopied] = useState(false);

  const sharable = canShareFiles();

  // Asked for every file rather than guessed from the path: whether a copy sits
  // in Drive is the server's knowledge, not something to infer from a prefix.
  useEffect(() => {
    driveLink(file.item_id)
      .then(setDrive)
      .catch(() => setDrive({ supported: false, url: null, reason: null }));
  }, [file.item_id]);

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

        {drive?.supported && (
          <div className="drive-share">
            <div className="muted small">
              <b>This file is in Google Drive.</b> For anything too big to attach —
              a long video, a whole album — send a Drive link instead. It points at
              Drive, not at your server.
            </div>

            {drive.url ? (
              <>
                <div className="invite-link nm-clip">{drive.url}</div>
                <div className="zone-controls">
                  <button
                    className="compact primary"
                    onClick={async () => {
                      setCopied(await copyText(drive.url as string));
                      window.setTimeout(() => setCopied(false), 2500);
                    }}
                  >
                    {copied ? "Copied" : "Copy link"}
                  </button>
                  <button
                    className="compact"
                    disabled={busy !== null}
                    onClick={async () => {
                      setBusy("Stopping…");
                      setNote(null);
                      try {
                        await revokeDriveLink(file.item_id);
                        setDrive({ ...drive, url: null });
                      } catch (e) {
                        setNote(e instanceof Error ? e.message : String(e));
                      } finally {
                        setBusy(null);
                      }
                    }}
                  >
                    Stop sharing
                  </button>
                </div>
                <p className="muted small">
                  Anyone with this link can view this one file until you stop
                  sharing it. Nothing else in your Drive is exposed.
                </p>
              </>
            ) : (
              <button
                className="compact"
                disabled={busy !== null}
                onClick={async () => {
                  setBusy("Asking Drive…");
                  setNote(null);
                  try {
                    const { url } = await createDriveLink(file.item_id);
                    setDrive({ ...drive, url });
                  } catch (e) {
                    setNote(e instanceof Error ? e.message : String(e));
                  } finally {
                    setBusy(null);
                  }
                }}
              >
                Create a Drive link
              </button>
            )}
          </div>
        )}

        {drive && !drive.supported && drive.reason && (
          <p className="muted small">{drive.reason}</p>
        )}

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
