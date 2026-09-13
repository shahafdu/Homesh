import { useCallback, useEffect, useRef, useState } from "react";

/** A PDF drawn into the page, rather than handed to the browser.
 *
 * An <iframe> works on a desktop and fails on a phone: mobile browsers have no
 * built-in PDF viewer, so they treat one as a file and download it. That is why
 * opening a document on a phone appeared to do nothing and then quietly saved a
 * copy — the opposite of the point, which is to read it here.
 *
 * Rendering the pages ourselves works the same everywhere, and keeps the file on
 * this server: no third-party viewer, nothing uploaded to be looked at.
 *
 * **Only the pages you are looking at.** It drew every page at once and kept
 * every canvas, which is fine for a two-page letter and fatal for a book: a
 * hundred pages at a television's resolution is something like two gigabytes of
 * backing store, so a 2.4 MB rulebook killed the app about a minute in — long
 * enough to look like a timeout rather than what it was. Pages are drawn as they
 * come into view and released as they leave, so what is held is a handful of
 * pages regardless of how long the document is.
 */

/** How far outside the window to draw ahead. One screenful, so scrolling
 *  steadily finds the next page already there rather than blank. */
const AHEAD = "150% 0px";

/** Pages kept drawn at once.
 *
 * A backstop rather than the mechanism: pages are released as they leave, and
 * this catches the case where several are in view at once on a wide screen.
 * Enough to scroll through without flicker, few enough that a long book costs
 * the same as a short one. */
const KEEP = 6;

export default function PdfView(props: { url: string; title: string }) {
  const host = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pages, setPages] = useState(0);

  // Everything the observer callback needs, out of React's way: it runs far
  // more often than anything should re-render.
  const doc = useRef<{ getPage(n: number): Promise<unknown> } | null>(null);
  const observer = useRef<IntersectionObserver | null>(null);
  const drawn = useRef<Map<number, HTMLCanvasElement>>(new Map());
  const drawing = useRef<Set<number>>(new Set());
  // Renders still running, so one can be called off rather than finishing into
  // a canvas nobody is looking at any more. Scrolling fast through a long book
  // starts more of these than it finishes.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const tasks = useRef<Map<number, any>>(new Map());

  /** Give back everything one page is holding.
   *
   * Three things, and the obvious one is the least of them. Removing the
   * element does nothing on its own: the canvas is still referenced until it is
   * collected, and its backing store -- which at a television's pixel ratio is
   * several megabytes for one page -- goes only when the dimensions are set to
   * zero. The render task has to be called off or it draws into it anyway, and
   * pdf.js keeps its own copy of the page's operator list until cleanup() is
   * called.
   */
  const release = useCallback((n: number) => {
    const task = tasks.current.get(n);
    if (task) {
      try {
        task.cancel();
      } catch {
        // Already finished. Nothing to call off.
      }
      tasks.current.delete(n);
    }
    const canvas = drawn.current.get(n);
    if (canvas) {
      canvas.width = 0;
      canvas.height = 0;
      canvas.remove();
      drawn.current.delete(n);
    }
  }, []);

  /** Draw one page into its slot, unless it is already drawn or being drawn. */
  const draw = useCallback(async (slot: HTMLElement, n: number) => {
    if (!doc.current || drawn.current.has(n) || drawing.current.has(n)) return;
    drawing.current.add(n);

    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const page: any = await doc.current.getPage(n);
      const unscaled = page.getViewport({ scale: 1 });

      // At the width available, and at the device's pixel ratio so small type
      // is legible rather than soft -- but capped. A television reports a ratio
      // that would make each page four times the area for no visible gain, and
      // the memory is the whole problem here.
      const width = slot.clientWidth || 800;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const scale = (width / unscaled.width) * ratio;
      const viewport = page.getViewport({ scale });

      const canvas = document.createElement("canvas");
      canvas.className = "pdf-page";
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = "100%";

      const context = canvas.getContext("2d");
      if (!context) return;
      const task = page.render({ canvasContext: context, viewport });
      tasks.current.set(n, task);
      await task.promise;
      tasks.current.delete(n);

      // What pdf.js built in order to draw this, which it holds on to for the
      // next time the page is asked for. There is no next time here: the page
      // is re-fetched if it is scrolled back to, and a book's worth of retained
      // operator lists is the other half of the memory.
      page.cleanup();

      slot.replaceChildren(canvas);
      drawn.current.set(n, canvas);

      // A backstop, for the case where more pages are in view at once than the
      // observer will report leaving.
      if (drawn.current.size > KEEP) {
        const furthest = [...drawn.current.keys()].sort(
          (a, b) => Math.abs(b - n) - Math.abs(a - n),
        )[0];
        if (furthest !== n) release(furthest);
      }
    } catch {
      // One page that will not draw is not the document failing. It stays
      // blank at its right height rather than taking the rest down with it.
    } finally {
      drawing.current.delete(n);
      tasks.current.delete(n);
    }
  }, [release]);

  useEffect(() => {
    let cancelled = false;
    const container = host.current;
    if (!container) return;

    const slots = drawn.current;
    const pending = drawing.current;

    void (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        // The worker ships beside the library; Vite resolves it at build time so
        // nothing is fetched from a CDN — a strict CSP would block that anyway.
        pdfjs.GlobalWorkerOptions.workerSrc = (
          await import("pdfjs-dist/build/pdf.worker.mjs?url")
        ).default;

        const opened = await pdfjs.getDocument({
          url: props.url,
          withCredentials: true,
          // Fetch the parts being read, rather than the whole book.
          //
          // Drawing lazily was only half of it, and on its own it changed
          // nothing for a 140 MB rulebook: pdf.js downloads the entire file in
          // the background by default, even where the server offers ranges,
          // so nothing could be read until all of it had arrived. Over Drive
          // that is minutes.
          disableAutoFetch: true,
          // A megabyte at a time instead of the default sixty-four kilobytes.
          // These live on Drive, where each request costs about 1.4 seconds of
          // latency regardless of size -- so 140 MB in 64 KB pieces is two
          // thousand round trips, and the chunk size matters far more than the
          // bytes do.
          rangeChunkSize: 1 << 20,
        }).promise;
        if (cancelled) return;

        doc.current = opened;
        setPages(opened.numPages);

        // The shape of page one, used to give every slot its height before it
        // is drawn. Without that the whole document is zero-high, everything is
        // "in view" at once, and lazy drawing becomes eager drawing again.
        const first = await opened.getPage(1);
        const shape = first.getViewport({ scale: 1 });
        const aspect = shape.height / shape.width;
        if (cancelled) return;

        container.replaceChildren();
        const watcher = new IntersectionObserver(
          (entries) => {
            for (const entry of entries) {
              const slot = entry.target as HTMLElement;
              const n = Number(slot.dataset.page);
              // Both directions. Drawing on the way in and waiting for a count
              // to be exceeded on the way out meant pages were freed only as a
              // side effect of drawing more of them -- so scrolling down a long
              // book held a little more with every page, which is the shape of
              // a crash rather than of a cap. A page that has left is released
              // whether or not anything else is being drawn.
              if (entry.isIntersecting) void draw(slot, n);
              else release(n);
            }
          },
          // The viewport, not the .pdf box. Which element actually scrolls
          // differs by where this is used -- in the viewer .pdf is given
          // height:100% inside a parent with no definite height, so it does not
          // scroll at all and its ancestor does. Watching it therefore saw
          // nothing move: the first pages drew, and scrolling produced no more
          // for ever. Against the viewport it works wherever it is put.
          { rootMargin: AHEAD },
        );

        for (let n = 1; n <= opened.numPages; n++) {
          const slot = document.createElement("div");
          slot.className = "pdf-slot";
          slot.dataset.page = String(n);
          // Its height before it has one of its own, so the document is the
          // right length from the start and scrolling does not jump.
          slot.style.aspectRatio = `1 / ${aspect}`;
          container.append(slot);
          watcher.observe(slot);
        }

        if (cancelled) watcher.disconnect();
        else observer.current = watcher;
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();

    return () => {
      cancelled = true;
      observer.current?.disconnect();
      observer.current = null;
      // Everything drawn, released. A viewer moving between documents would
      // otherwise keep every page of every one it had opened.
      for (const n of [...slots.keys()]) release(n);
      slots.clear();
      pending.clear();
      // The document itself, and the worker thread behind it. Without this a
      // reader who opens six books has six pdf.js workers alive, each holding
      // what it has fetched.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const held = doc.current as any;
      doc.current = null;
      void held?.destroy?.();
    };
  }, [props.url, draw, release]);

  return (
    <div className="pdf">
      {error && <div className="error">Could not display this document: {error}</div>}
      {!error && pages === 0 && <p className="muted">Opening {props.title}…</p>}
      <div ref={host} className="pdf-pages" />
    </div>
  );
}
