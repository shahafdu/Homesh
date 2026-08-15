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
    file = new File([blob], filename, { type: blob.type || "application/octet-stream" });
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
