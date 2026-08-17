import { useEffect, useRef, useState } from "react";

/** A PDF drawn into the page, rather than handed to the browser.
 *
 * An <iframe> works on a desktop and fails on a phone: mobile browsers have no
 * built-in PDF viewer, so they treat one as a file and download it. That is why
 * opening a document on a phone appeared to do nothing and then quietly saved a
 * copy — the opposite of the point, which is to read it here.
 *
 * Rendering the pages ourselves works the same everywhere, and keeps the file on
 * this server: no third-party viewer, nothing uploaded to be looked at.
 */
export default function PdfView(props: { url: string; title: string }) {
  const host = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pages, setPages] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const container = host.current;
    if (!container) return;

    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        // The worker ships beside the library; Vite resolves it at build time so
        // nothing is fetched from a CDN — a strict CSP would block that anyway.
        pdfjs.GlobalWorkerOptions.workerSrc = (
          await import("pdfjs-dist/build/pdf.worker.mjs?url")
        ).default;

        const doc = await pdfjs.getDocument({ url: props.url, withCredentials: true }).promise;
        if (cancelled) return;
        setPages(doc.numPages);

        container.replaceChildren();
        for (let n = 1; n <= doc.numPages; n++) {
          const page = await doc.getPage(n);
          if (cancelled) return;

          // Rendered at the width available, and at the device's pixel ratio so
          // small type on a phone is legible rather than soft.
          const unscaled = page.getViewport({ scale: 1 });
          const ratio = Math.min(window.devicePixelRatio || 1, 2);
          const scale = (container.clientWidth || 800) / unscaled.width;
          const viewport = page.getViewport({ scale: scale * ratio });

          const canvas = document.createElement("canvas");
          canvas.className = "pdf-page";
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.style.width = "100%";
          container.append(canvas);

          const context = canvas.getContext("2d");
          if (context) await page.render({ canvasContext: context, viewport }).promise;
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [props.url]);

  return (
    <div className="pdf">
      {error && <div className="error">Could not display this document: {error}</div>}
      {!error && pages === 0 && <p className="muted">Opening {props.title}…</p>}
      <div ref={host} className="pdf-pages" />
    </div>
  );
}
