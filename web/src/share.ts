/** Getting a file off the server and onto someone else's phone.
 *
 * The rule this is built around: **share the bytes, never a link.** A link would
 * be a way into this server for whoever received it — forwarded, screenshotted,
 * left in a group chat — and the whole point of sending a wedding video to a
 * relative is that they get the video, not an account.
 *
 * So the file is fetched here and handed to the system share sheet as a real
 * file. WhatsApp, Mail and the rest then attach it exactly as if it had come
 * from the gallery, and nothing about this server travels with it.
 */

import { api } from "./api";
import { downloadUrl } from "./library";

/** Above this, sharing is refused in favour of a download.
 *
 * The share sheet needs the whole file in memory, and a phone asked to hold a
 * two-hour film there will be killed by the system mid-copy. Downloading streams
 * to storage instead and has no such ceiling.
 */
const MAX_SHARE_BYTES = 256 * 1024 * 1024;

export type ShareOutcome =
  | { ok: true }
  | { ok: false; reason: "cancelled" }
  | { ok: false; reason: "too-large" | "unsupported" | "failed"; detail: string };

/** Whether the system share sheet can take files at all.
 *
 * `navigator.share` is secure-context-only, so over plain http on the house
 * network it is simply absent — the same restriction that made crypto.randomUUID
 * undefined on every television. Callers use this to offer Download instead of
 * a Share button that cannot work.
 */
/** A media type from the filename, for when the server could not name one.
 *
 * Only the kinds that actually get shared out of a media library. A share sheet
 * refuses a file it cannot identify, so "unknown" is the one answer guaranteed
 * to fail.
 */
function guessType(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  const known: Record<string, string> = {
    mp3: "audio/mpeg", m4a: "audio/mp4", aac: "audio/aac", flac: "audio/flac",
    wav: "audio/wav", ogg: "audio/ogg", opus: "audio/opus",
    mp4: "video/mp4", m4v: "video/mp4", mov: "video/quicktime",
    mkv: "video/x-matroska", avi: "video/x-msvideo", wmv: "video/x-ms-wmv",
    jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif",
    webp: "image/webp", heic: "image/heic",
    pdf: "application/pdf", txt: "text/plain",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  };
  return known[ext] ?? "application/octet-stream";
}

export function canShareFiles(): boolean {
  return typeof navigator.canShare === "function" && typeof navigator.share === "function";
}

export async function shareFile(
  itemId: string,
  filename: string,
  size: number | null,
): Promise<ShareOutcome> {
  if (!canShareFiles()) {
    return {
      ok: false,
      reason: "unsupported",
      detail: "This browser cannot attach files to a share. Download it instead.",
    };
  }

  if (size !== null && size > MAX_SHARE_BYTES) {
    return {
      ok: false,
      reason: "too-large",
      detail: "Too large to share directly. Download it, then attach it from your files.",
    };
  }

  let file: File;
  try {
    const url = await downloadUrl(itemId);
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) throw new Error(`server returned ${res.status}`);
    const blob = await res.blob();
    // A generic type is refused by the share sheet, which will not attach
    // something it cannot name. The server falls back to octet-stream for any
    // extension it does not recognise, so the extension is consulted here before
    // giving up on the file.
    const type = blob.type && blob.type !== "application/octet-stream"
      ? blob.type
      : guessType(filename);
    file = new File([blob], filename, { type });
  } catch (e) {
    return { ok: false, reason: "failed", detail: e instanceof Error ? e.message : String(e) };
  }

  // Ask before sharing: some platforms accept navigator.share but refuse files,
  // and finding that out by throwing loses the download the user just waited for.
  if (!navigator.canShare({ files: [file] })) {
    return {
      ok: false,
      reason: "unsupported",
      detail: "This device will not attach that file type. Download it instead.",
    };
  }

  try {
    // Only the file. No title, no text, and above all no url — a share sheet
    // will happily send a link alongside the attachment, which is the one thing
    // this must never do.
    await navigator.share({ files: [file] });
    return { ok: true };
  } catch (e) {
    // Dismissing the sheet throws AbortError. That is a choice, not a failure.
    if (e instanceof DOMException && e.name === "AbortError") {
      return { ok: false, reason: "cancelled" };
    }
    return { ok: false, reason: "failed", detail: e instanceof Error ? e.message : String(e) };
  }
}

/** Save a copy to this device.
 *
 * Works everywhere, including where sharing does not: it is an ordinary link the
 * browser streams to storage, so a film does not have to fit in memory first.
 */
export async function downloadFile(itemId: string, filename: string): Promise<void> {
  const url = await downloadUrl(itemId);
  const link = document.createElement("a");
  link.href = url;
  // The server already sets Content-Disposition with an RFC 6266 filename, which
  // is what non-Latin names need; this is the hint for browsers that prefer it.
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}


/** Whether a file can be handed over as a Drive link, and whether it already is.
 *
 * The route for the files nothing else can carry: a two-hour video will not fit
 * through a share sheet and will not go through email at all. If the file
 * already lives in Drive, the copy is there and the link costs nothing.
 *
 * The link points at Drive, never at this server — what travels is one file,
 * served by Google and revocable, not a way into the house.
 */
export interface DriveLink {
  supported: boolean;
  url: string | null;
  reason: string | null;
}

export const driveLink = (itemId: string) =>
  api.get<DriveLink>(`/api/items/${itemId}/drive-link`);

export const createDriveLink = (itemId: string) =>
  api.post<{ url: string }>(`/api/items/${itemId}/drive-link`);

export const revokeDriveLink = (itemId: string) =>
  api.delete(`/api/items/${itemId}/drive-link`);
