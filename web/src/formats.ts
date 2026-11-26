/** Which file types need what done to them before they can be shown or sent.
 *
 * Extension lists and the decisions made from them, with no imports, so that
 * the unit tests can load this file in plain Node.
 */

/** Office formats the server renders to PDF on request.
 *
 * No browser can display these, and no browser ever will — they need a layout
 * engine rather than a parser. The server converts them so the file never has to
 * leave the house to be read.
 */
const CONVERTIBLE = new Set([
  "doc", "docx", "odt", "rtf", "dot", "dotx", "wpd",
  "xls", "xlsx", "ods", "csv", "xlsm", "xlt", "xltx",
  "ppt", "pptx", "odp", "pps", "ppsx", "pot", "potx",
  "abw", "sxw", "fodt", "fods",
]);

export function needsConversion(ext: string | null): boolean {
  return CONVERTIBLE.has((ext ?? "").toLowerCase());
}

/** Containers whose video no browser can decode.
 *
 * MPEG-2 — a DVD rip, an HDV camcorder tape — has no decoder in any current
 * browser. It is not a setting or a missing plugin; the codec was never shipped.
 * A television box usually decodes it in hardware, which is why sending it to a
 * room works when watching it here does not.
 */
export const UNDECODABLE: ReadonlySet<string> = new Set([
  "m2t", "mts", "vob", "mpg", "mpeg", "m1v", "m2v", "mod", "tod", "dv", "mxf",
  "wmv", "avi", "asf", "divx", "xvid", "rm", "rmvb", "flv",
  "3gp", "3g2",
]);

export function needsVideoConversion(ext: string | null): boolean {
  return UNDECODABLE.has((ext ?? "").toLowerCase());
}

/** The extensions a browser will actually let a page attach.
 *
 * Not a guess and not a preference — this is Chromium's own allowlist, from
 * `chrome/browser/webshare/share_service_impl.cc`. Anything outside it is
 * refused with `NotAllowedError`, which is the same exception a stale tap
 * raises, which is why the two were indistinguishable from the message alone.
 *
 * It has to be checked here because **`navigator.canShare` does not check it**:
 * Blink validates the media type loosely (`video/*` passes), while the browser
 * process validates the *extension* strictly. So canShare says yes to an .avi
 * and the share then fails — after the whole file has been fetched.
 *
 * Notably absent, and common in any older collection: avi, wmv, mkv, mov,
 * doc, docx, xls, wma, aac, heic. Those need converting to something on this list,
 * or a Drive link. The server holds the same list (server/app/sendcopy.py).
 */
export const SHAREABLE: ReadonlySet<string> = new Set([
  "avif", "bmp", "css", "csv", "ehtml", "flac", "gif", "htm", "html", "ico",
  "jfif", "jpeg", "jpg", "m4a", "m4v", "mp3", "mp4", "mpeg", "mpg", "oga",
  "ogg", "ogm", "ogv", "opus", "pdf", "pjp", "pjpeg", "png", "shtml", "svg",
  "svgz", "text", "tif", "tiff", "txt", "wav", "weba", "webm", "webp", "xbm",
]);

/** Sound the catalog may have filed as "other". MIDI is not here: it holds
 * notes rather than sound, so there is nothing in it to convert. */
export const AUDIO_EXTS: ReadonlySet<string> = new Set([
  "aac", "ac3", "aif", "aifc", "aiff", "alac", "amr", "ape", "au", "awb",
  "caf", "dts", "m4b", "mka", "mp2", "mpc", "ra", "tta", "wma", "wv",
]);

export type CopyFormat = "mp4" | "m4a";

export const COPY_TYPES: Record<CopyFormat, string> = {
  mp4: "video/mp4",
  m4a: "audio/mp4",
};

/** What the server would convert this into for sending, or null for no copy.
 *
 * The same decision as `target_for` in server/app/sendcopy.py.
 */
export function copyFormat(ext: string, kind: string): CopyFormat | null {
  // First, because mpg and mpeg can be attached as they are and still arrive as
  // something the receiving phone cannot play.
  if (UNDECODABLE.has(ext)) return "mp4";
  if (SHAREABLE.has(ext)) return null;
  if (kind === "video") return "mp4";
  if (kind === "audio" || AUDIO_EXTS.has(ext)) return "m4a";
  return null;
}

/** How a given file can be got onto somebody else's phone.
 *
 * `pdf` is the rendering the server already makes for reading a document.
 * `copy` is an MP4 or M4A made at the moment of sending and kept nowhere.
 */
export type Route =
  | { via: "file" }
  | { via: "pdf" }
  | { via: "copy"; format: CopyFormat }
  | { via: "none"; detail: string };

export function route(file: { ext: string | null; filename: string; kind: string }): Route {
  const suffix = (file.ext ?? file.filename.split(".").pop() ?? "").toLowerCase();

  if (needsConversion(suffix)) return { via: "pdf" };
  const format = copyFormat(suffix, file.kind);
  if (format) return { via: "copy", format };
  if (SHAREABLE.has(suffix)) return { via: "file" };

  return {
    via: "none",
    detail:
      `Phones refuse to attach ${suffix ? `.${suffix}` : "this"} files, and there is ` +
      "nothing to convert it to. Send a Drive link instead, or download it and " +
      "attach it from your files.",
  };
}

export function downloadLabel(format: CopyFormat | null): string {
  return format ? `Download as ${format.toUpperCase()}` : "Download";
}

/** The copy's name: the file's own, with the new ending. */
export function copyName(filename: string, format: CopyFormat): string {
  return `${filename.replace(/\.[^.]*$/, "") || filename}.${format}`;
}
