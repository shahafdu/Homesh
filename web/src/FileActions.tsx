import { useEffect, useRef, useState } from "react";
import { useLockScroll } from "./useLockScroll";
import { copyText } from "./copy";
import { AddToPlaylist } from "./Playlists";
import type { FileEntry } from "./library";
import {
  canShareFiles,
  createDriveLink,
  downloadFile,
  driveLink,
  handOver,
  prepareShare,
  revokeDriveLink,
  shareRoute,
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
  /** Present only when the file was reached by searching: from within its own
   *  folder, showing it in that folder means nothing. */
  onReveal?: () => void;
  onClose: () => void;
}) {
  useLockScroll();
  const { file } = props;
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [drive, setDrive] = useState<DriveLink | null>(null);
  const [copied, setCopied] = useState(false);
  const [addingToList, setAddingToList] = useState(false);

  const sharable = canShareFiles();
  const plan = shareRoute(file);

  // Asked for every file rather than guessed from the path: whether a copy sits
  // in Drive is the server's knowledge, not something to infer from a prefix.
  useEffect(() => {
    driveLink(file.item_id)
      .then(setDrive)
      .catch(() => setDrive({ supported: false, url: null, reason: null }));
  }, [file.item_id]);

  // A file already fetched and waiting for one more tap.
  //
  // The browser only allows a share while the tap that asked for it is still
  // live — a few seconds. Anything slow to fetch outlives that, and the share is
  // then refused with "Must be handling a user gesture". So the bytes are
  // collected first, offered immediately in case that was quick enough, and
  // otherwise held here for a tap that is unambiguously fresh.
  const [ready, setReady] = useState<File | null>(null);

  // Closing the sheet stops waiting on it. A conversion already under way is the
  // server's business and carries on; reopening rejoins it where it got to.
  const stop = useRef(new AbortController());
  useEffect(() => {
    const controller = stop.current;
    return () => controller.abort();
  }, []);

  const offer = async (f: File) => {
    const result = await handOver(f);
    if (result.ok || result.reason === "cancelled") {
      props.onClose();
      return true;
    }
    if (result.reason === "needs-tap") return false;
    setNote(result.detail);
    return true;
  };

  const share = async () => {
    // A second tap on the same button: the file is in hand, so this goes
    // straight to the share sheet with nothing awaited in between.
    if (ready) {
      if (!(await offer(ready))) setNote("Tap Send again.");
      return;
    }

    setBusy("Preparing…");
    setNote(null);
    const prepared = await prepareShare(
      file,
      (step, fraction) =>
        setBusy(fraction === null ? `${step}…` : `${step}… ${Math.round(fraction * 100)}%`),
      stop.current.signal,
    );
    setBusy(null);

    if (!prepared.ok) {
      setNote(prepared.detail);
      return;
    }
    if (await offer(prepared.file)) return;

    // Too slow for one tap. Nothing is lost — the file is downloaded and the
    // button now says so.
    setReady(prepared.file);
    setNote(null);
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

          {/* Only for the kinds a list is for. A playlist of spreadsheets is
              not a thing anybody wants, and offering it would be noise. */}
          {(file.kind === "audio" || file.kind === "video") && (
            <button className="action" onClick={() => setAddingToList(true)}>
              <span className="action-ic">≡</span>
              <span>
                Add to a playlist
                <span className="muted small">Put it in a list you can play later</span>
              </span>
            </button>
          )}

          {props.onReveal && (
            <button className="action" onClick={props.onReveal}>
              <span className="action-ic">⇥</span>
              <span>
                Show in folder
                <span className="muted small">Open where this file lives</span>
              </span>
            </button>
          )}

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

          <button
            className={ready ? "action primary" : "action"}
            disabled={busy !== null || !sharable}
            onClick={share}
          >
            <span className="action-ic">↗</span>
            <span>
              {ready ? "Send now" : "Share…"}
              <span className="muted small">
                {ready
                  ? "Ready — tap to choose WhatsApp, mail, anywhere"
                  : !sharable
                    ? "Needs a secure connection; download and share from your files"
                    : plan === "mp4"
                      ? "Converts it to MP4 first — phones will not accept the original"
                      : plan === "pdf"
                        ? "Sends it as a PDF, which any phone can open"
                        : plan === "none"
                          ? "Phones will not accept this kind of file — use a Drive link"
                          : "Sends the file itself — WhatsApp, mail, anywhere"}
              </span>
            </span>
          </button>
        </div>

        {addingToList && (
          <AddToPlaylist
            file={file}
            onDone={() => {
              setAddingToList(false);
              setNote("Added.");
            }}
            onCancel={() => setAddingToList(false)}
          />
        )}

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
