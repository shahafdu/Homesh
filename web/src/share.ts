/** Getting a file off the server and onto someone else's phone.
 *
 * The rule this is built around: **share the bytes, never a link.** A link would
 * be a way into this server for whoever received it — forwarded, screenshotted,
 * left in a group chat — and the whole point of sending a family video to a
 * relative is that they get the video, not an account.
 *
 * So the file is fetched here and handed to the system share sheet as a real
 * file. WhatsApp, Mail and the rest then attach it exactly as if it had come
 * from the gallery, and nothing about this server travels with it.
 */

import { api, ApiError } from "./api";
import { COPY_TYPES, copyName, route, type CopyFormat } from "./formats";
import { documentUrl, downloadUrl, type FileEntry } from "./library";

/** The extension, lowercased, for the handful of decisions that turn on it. */
const ext = (filename: string) => filename.split(".").pop()?.toLowerCase() ?? "";

/** Above this, sharing is refused in favour of a download.
 *
 * The share sheet needs the whole file in memory, and a phone asked to hold a
 * two-hour film there will be killed by the system mid-copy. Downloading streams
 * to storage instead and has no such ceiling.
 */
const MAX_SHARE_BYTES = 256 * 1024 * 1024;

/** The result of fetching the bytes, before anything is handed to the phone. */
export type Prepared =
  | { ok: true; file: File }
  | { ok: false; reason: "too-large" | "unsupported" | "failed"; detail: string };

/** The result of offering the file to the system share sheet. */
export type ShareOutcome =
  | { ok: true }
  | { ok: false; reason: "cancelled" }
  | { ok: false; reason: "needs-tap" }
  | { ok: false; reason: "unsupported" | "failed"; detail: string };

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
  const suffix = ext(filename);
  const known: Record<string, string> = {
    // Audio
    mp3: "audio/mpeg", m4a: "audio/mp4", aac: "audio/aac", flac: "audio/flac",
    wav: "audio/wav", ogg: "audio/ogg", opus: "audio/opus", wma: "audio/x-ms-wma",
    // Video
    mp4: "video/mp4", m4v: "video/mp4", mov: "video/quicktime",
    mkv: "video/x-matroska", avi: "video/x-msvideo", wmv: "video/x-ms-wmv",
    mpg: "video/mpeg", mpeg: "video/mpeg", "3gp": "video/3gpp", webm: "video/webm",
    // Images
    jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif",
    webp: "image/webp", heic: "image/heic", bmp: "image/bmp", tif: "image/tiff",
    // Documents. .doc was missing, which is why sharing one did nothing: an
    // unnamed type becomes application/octet-stream, and that is the one answer
    // a share sheet always refuses.
    pdf: "application/pdf", txt: "text/plain", csv: "text/csv", rtf: "application/rtf",
    doc: "application/msword",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xls: "application/vnd.ms-excel",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ppt: "application/vnd.ms-powerpoint",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    odt: "application/vnd.oasis.opendocument.text",
    ods: "application/vnd.oasis.opendocument.spreadsheet",
  };
  return known[suffix] ?? "application/octet-stream";
}

export function canShareFiles(): boolean {
  return typeof navigator.canShare === "function" && typeof navigator.share === "function";
}

/** Fetch the bytes and wrap them as a file the share sheet will accept.
 *
 * Deliberately separate from handing it over, because of a browser rule that is
 * easy to miss: `navigator.share` may only be called while the tap that asked
 * for it is still "live", a window of a few seconds. Fetching first spends that
 * window, and the share then fails with *"Must be handling a user gesture"* —
 * which is why a 30 MB video failed where a 4 MB song succeeded, and why a
 * document failed whenever the server was still rendering its PDF. It was never
 * about the file type; it was about how long the file took to arrive.
 *
 * @param onProgress fraction complete, or null when the length is unknown.
 */
export async function prepareShare(
  entry: FileEntry,
  onProgress?: (step: string, fraction: number | null) => void,
  signal?: AbortSignal,
): Promise<Prepared> {
  if (!canShareFiles()) {
    return {
      ok: false,
      reason: "unsupported",
      detail: "This browser cannot attach files to a share. Download it instead.",
    };
  }

  const { item_id: itemId, filename, size } = entry;
  const plan = route(entry);
  if (plan.via === "none") {
    return { ok: false, reason: "unsupported", detail: plan.detail };
  }
  if (plan.via === "copy") return fetchCopy(entry, plan.format, onProgress, signal);

  let url: string;
  let name = filename;
  let type: string | null = null;

  try {
    if (plan.via === "pdf") {
      url = documentUrl(itemId);
      name = `${filename}.pdf`;
      type = "application/pdf";
    } else {
      // The ceiling applies to the bytes actually fetched, so the original's
      // size is only asked about on the direct route — where a two-hour film
      // really would have to fit in memory.
      if (size !== null && size > MAX_SHARE_BYTES) return tooLarge();
      url = await downloadUrl(itemId);
    }

    onProgress?.("Preparing", 0);
    const res = await fetch(url, { credentials: "same-origin", signal });
    if (!res.ok) throw new Error(`server returned ${res.status}`);

    const declared = Number(res.headers.get("content-length")) || 0;
    if (declared > MAX_SHARE_BYTES) return tooLarge();

    const blob = await readWithProgress(res, size, (f) => onProgress?.("Preparing", f));
    if (blob.size > MAX_SHARE_BYTES) return tooLarge();

    // A generic type is refused by the share sheet, which will not attach
    // something it cannot name. The server falls back to octet-stream for any
    // extension it does not recognise, so the extension is consulted here before
    // giving up on the file.
    // Without its parameters.
    //
    // The server sends .txt as "text/plain; charset=utf-8", which is correct
    // HTTP and refused by the share sheet: the browser matches the media type
    // against its list of permitted ones *exactly*, and "text/plain" is on that
    // list while "text/plain; charset=utf-8" is not. It got past canShare(),
    // which only looks at the prefix, and failed at the share itself — reported
    // as "this phone refuses to attach that file", which was not the reason.
    const plain = blob.type.split(";")[0].trim();
    const media =
      type ??
      (plain && plain !== "application/octet-stream" ? plain : guessType(filename));

    return { ok: true, file: new File([blob], name, { type: media }) };
  } catch (e) {
    return failed(e);
  }
}

/** An MP4 or M4A of a file no phone would open, made as it is asked for.
 *
 * The server converts, answers with the result and keeps nothing, so there is
 * no job to poll: the one request lasts as long as the conversion does, and
 * only then is there a length to measure progress against.
 */
export async function fetchCopy(
  entry: FileEntry,
  format: CopyFormat,
  onProgress?: (step: string, fraction: number | null) => void,
  signal?: AbortSignal,
): Promise<Prepared> {
  try {
    onProgress?.("Converting for sending", null);
    const res = await api.postFile(`/api/items/${entry.item_id}/send-copy`, signal);

    // No fallback to the original's size: the copy's is unrelated to it.
    const blob = await readWithProgress(res, null, (f) => onProgress?.("Preparing", f));
    if (blob.size > MAX_SHARE_BYTES) return tooLarge();

    const name = copyName(entry.filename, format);
    return { ok: true, file: new File([blob], name, { type: COPY_TYPES[format] }) };
  } catch (e) {
    return failed(e);
  }
}

/** The server's own sentence says what to do. The reason is added so that the
 * screen knows whether a Drive link or another try could help. */
function failed(e: unknown): Prepared {
  if (e instanceof DOMException && e.name === "AbortError") {
    return { ok: false, reason: "failed", detail: "Stopped." };
  }
  const status = e instanceof ApiError ? e.status : 0;
  const reason =
    status === 413 ? "too-large" : status === 415 || status === 422 ? "unsupported" : "failed";
  return { ok: false, reason, detail: e instanceof Error ? e.message : String(e) };
}

const tooLarge = (): Prepared => ({
  ok: false,
  reason: "too-large",
  // A feature film runs past a gigabyte, and a phone cannot hold one in
  // memory to hand to a share sheet. A Drive link carries any size.
  detail:
    "Too big to attach — a phone cannot hold it in memory. Send a Drive link " +
    "instead, or download it and attach it from your files.",
});

/** Offer a prepared file to the system share sheet.
 *
 * Must be reached directly from a tap with nothing awaited in between, or the
 * browser refuses. Callers try it straight after preparing — which works
 * whenever that was quick — and fall back to a second tap when it was not.
 */
export async function handOver(file: File): Promise<ShareOutcome> {
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
    // NotAllowedError covers two unrelated refusals, and only the message
    // separates them: a tap that went stale while the file was fetched, and a
    // file type the browser will not attach at all. Treating both as the first
    // produced a "Send now" button that could never work, because tapping it
    // again was never the problem.
    if (e instanceof DOMException && e.name === "NotAllowedError") {
      if (/gesture|activation/i.test(e.message)) return { ok: false, reason: "needs-tap" };
      return {
        ok: false,
        reason: "unsupported",
        detail:
          "This phone refuses to attach that file. Send a Drive link instead, or " +
          "download it and attach it from your files.",
      };
    }
    return { ok: false, reason: "failed", detail: e instanceof Error ? e.message : String(e) };
  }
}

/** Read a response to a blob, reporting how far along it is.
 *
 * A share large enough to need a progress bar is exactly the one that will need
 * a second tap, so the wait has to be visible rather than look like a hang.
 */
async function readWithProgress(
  res: Response,
  size: number | null,
  onProgress?: (fraction: number | null) => void,
): Promise<Blob> {
  const declared = Number(res.headers.get("content-length")) || size || 0;
  if (!onProgress || !res.body) return res.blob();

  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress(declared > 0 ? Math.min(1, received / declared) : null);
  }
  return new Blob(chunks as BlobPart[], { type: res.headers.get("content-type") ?? "" });
}

/** Save a copy to this device.
 *
 * Works everywhere, including where sharing does not: it is an ordinary link the
 * browser streams to storage, so a film does not have to fit in memory first.
 */
export async function downloadFile(itemId: string, filename: string): Promise<void> {
  saveAs(await downloadUrl(itemId), filename);
}

/** Save a file already fetched — a converted copy, which has no address. */
export function saveFile(file: File): void {
  const url = URL.createObjectURL(file);
  saveAs(url, file.name);
  // Left a while: revoking at once cancels the download in some browsers.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function saveAs(url: string, filename: string): void {
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
