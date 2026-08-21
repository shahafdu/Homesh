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

import { documentUrl, needsConversion } from "./library";

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
  return frameAndPrint(url);
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

  return frameAndPrint(`data:text/html;charset=utf-8,${encodeURIComponent(page)}`, true);
}

/** Put a URL in a hidden frame and hand that frame to the print dialog. */
async function frameAndPrint(url: string, waitForImages = false): Promise<PrintOutcome> {
  const frame = document.createElement("iframe");
  // Off-screen rather than display:none: a frame that is not laid out has no
  // content to print, and several browsers print an empty sheet for one.
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText =
    "position:fixed;right:0;bottom:0;width:1px;height:1px;opacity:0;border:0;";
  frame.src = url;
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
