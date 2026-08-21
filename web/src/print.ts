/** Sending a file to a printer, or to a PDF.
 *
 * There is exactly one print path on the web, and it is the browser's own
 * dialog — which is also where "Save as PDF" lives on every desktop and phone.
 * So this does not need to produce a PDF itself: it needs to put the right
 * thing in front of that dialog.
 *
 * The awkward part is that `window.print()` prints the *page*, and the page is
 * a media browser: a toolbar, a file list, a player bar. Printing a photograph
 * has to mean the photograph, not the interface it was found in. So the thing
 * to be printed goes into a hidden iframe of its own and that frame is printed.
 */

import { documentUrl, needsConversion, type FileEntry } from "./library";
import { handOver, prepareShare } from "./share";

export type PrintOutcome = { ok: true } | { ok: false; detail: string };

/** How long to wait for the content to be ready before giving up.
 *
 * A document is converted on the server on first request, and a large scan
 * takes a while. Printing a blank page because the load had not finished is
 * worse than saying it timed out. */
const LOAD_TIMEOUT_MS = 60_000;

/** Print a document — the PDF the server renders for reading it.
 *
 * A PDF in a frame carries its own page size and margins, which is what makes
 * a printed spreadsheet look like the spreadsheet rather than a screenshot.
 */
export async function printDocument(itemId: string, ext: string | null): Promise<PrintOutcome> {
  const url = needsConversion(ext) ? documentUrl(itemId) : null;
  if (!url) {
    return {
      ok: false,
      detail: "This file is already open — use your browser's own Print command.",
    };
  }
  const printed = await frameAndPrint(url);
  if (printed.ok) return printed;

  // A PDF in a hidden frame is a browser plugin document, and a phone often
  // will not render one at all — the same limitation that made the viewer draw
  // documents with pdf.js instead of an iframe. Opening it gives the platform's
  // own PDF viewer, which has printing and "save to Files" in its own menu.
  const opened = window.open(url, "_blank", "noopener");
  if (opened) {
    return {
      ok: false,
      detail: "Opened the PDF in a new tab — print it from there.",
    };
  }
  return {
    ok: false,
    detail: `${printed.detail} Allow pop-ups, or open the document and print from your browser's menu.`,
  };
}

/** Print an image, sized to the paper rather than to the screen.
 *
 * The page is built here rather than pointing a frame at the image, because a
 * browser asked to print a bare image prints it at its pixel size: a 4000px
 * photograph spills over several sheets, and a small one prints postage-stamp
 * sized in a corner. `object-fit: contain` on a full-page box gives one sheet
 * with the whole picture on it, whatever the original.
 */
export async function printImage(url: string, caption: string): Promise<PrintOutcome> {
  const page = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>${escapeHtml(caption)}</title>
    <style>
      @page { margin: 12mm; }
      html, body { margin: 0; height: 100%; }
      body { display: flex; align-items: center; justify-content: center; }
      img { max-width: 100%; max-height: 100vh; object-fit: contain; }
    </style>
  </head>
  <body><img src="${escapeHtml(url)}" alt="${escapeHtml(caption)}"></body>
</html>`;

  // srcdoc, not a data: URL.
  //
  // A data: URL gets an *opaque* origin, which makes the frame cross-origin to
  // the page that created it — so reaching in for its print() is refused:
  // "Blocked a frame with origin ... from accessing a cross-origin frame". A
  // srcdoc frame inherits this document's origin, so the call is allowed and
  // the signed image URL inside it is same-origin too.
  const printed = await frameAndPrint({ srcdoc: page }, true);
  if (printed.ok) return printed;

  // A route that does not depend on this being right.
  //
  // A blob: URL inherits the origin of the page that created it — unlike a
  // data: URL, whose origin is opaque and which is what broke this the first
  // time — so the new tab can print itself even where the hidden frame could
  // not.
  const blob = new Blob([page], { type: "text/html" });
  const opened = window.open(URL.createObjectURL(blob), "_blank", "noopener");
  if (opened) return { ok: false, detail: "Opened it in a new tab — print it from there." };

  return {
    ok: false,
    detail: `${printed.detail} Allow pop-ups, or download it and print from your files.`,
  };
}

/** Put something in a hidden frame and hand that frame to the print dialog.
 *
 * Either a URL on this origin, or markup to inherit it. Anything that lands on
 * a different origin — a data: URL included, since those are opaque — cannot be
 * printed this way at all: the browser refuses to let the parent call print()
 * on it.
 */
async function frameAndPrint(
  source: string | { srcdoc: string },
  waitForImages = false,
): Promise<PrintOutcome> {
  const frame = document.createElement("iframe");
  // Off-screen rather than display:none: a frame that is not laid out has no
  // content to print, and several browsers print an empty sheet for one.
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText =
    "position:fixed;right:0;bottom:0;width:1px;height:1px;opacity:0;border:0;";
  if (typeof source === "string") frame.src = source;
  else frame.srcdoc = source.srcdoc;
  document.body.appendChild(frame);

  const cleanUp = () => {
    // Left for a moment: removing the frame while the dialog is still reading
    // from it prints a blank page in Safari.
    window.setTimeout(() => frame.remove(), 60_000);
  };

  try {
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(
        () => reject(new Error("took too long to load")),
        LOAD_TIMEOUT_MS,
      );
      frame.onload = () => {
        window.clearTimeout(timer);
        resolve();
      };
      frame.onerror = () => {
        window.clearTimeout(timer);
        reject(new Error("could not be loaded"));
      };
    });

    if (waitForImages) {
      // onload fires for the document, not necessarily for the picture in it.
      const img = frame.contentDocument?.querySelector("img");
      if (img && !img.complete) {
        await new Promise<void>((resolve) => {
          img.onload = () => resolve();
          img.onerror = () => resolve();
        });
      }
    }

    const view = frame.contentWindow;
    if (!view) throw new Error("the print frame went away");
    view.focus();
    view.print();
    cleanUp();
    return { ok: true };
  } catch (e) {
    frame.remove();
    return {
      ok: false,
      detail:
        e instanceof Error
          ? `Could not prepare it for printing — it ${e.message}.`
          : String(e),
    };
  }
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Print by handing the file to the system share sheet.
 *
 * On a phone this is the whole answer, and it is worth being blunt about why:
 * Android and iOS both put Print in that sheet, it works, and nothing this code
 * does can improve on it. A hidden frame cannot — a phone will not render a PDF
 * in one, so it downloads the file and the wait times out.
 *
 * A document arrives as the PDF the server renders, which is what makes the
 * printed page look like the document rather than a screenshot of one.
 */
export async function printViaShareSheet(file: FileEntry): Promise<PrintOutcome> {
  const prepared = await prepareShare(file);
  if (!prepared.ok) return { ok: false, detail: prepared.detail };

  const result = await handOver(prepared.file);
  if (result.ok || result.reason === "cancelled") return { ok: true };
  if (result.reason === "needs-tap") {
    return { ok: false, detail: "Tap Print again — it is ready now." };
  }
  return { ok: false, detail: result.detail };
}
