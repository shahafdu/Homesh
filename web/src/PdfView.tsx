import { useCallback, useEffect, useRef, useState } from "react";

import DocSearch, { pattern, type SearchState } from "./DocSearch";

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
 *
 * **Drawing ourselves also means finding ourselves.** The pages are pictures
 * in a canvas, so the browser's own find sees nothing on them. Search reads
 * each page's text through pdf.js and marks the matches over the picture.
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

/** One run of a page's text, and where it sits on the page as fractions of the
 *  page's width and height -- so a mark lands in the same place however large
 *  the page is drawn. */
type Run = { start: number; end: number; left: number; top: number; width: number; height: number };
type PageText = { text: string; runs: Run[] };
type Hit = { page: number; start: number; end: number };

/** Matches kept for one search. Enough for any real question; a one-letter
 *  search of a rulebook would otherwise mark every page solid. */
const HIT_LIMIT = 2000;

/** `tools` is the page number and search above the document. Off on a
 *  television, where the only input is a remote's direction keys: a search box
 *  there is a focus trap, not a feature. */
export default function PdfView(props: { url: string; title: string; tools?: boolean }) {
  const tools = props.tools ?? true;
  const host = useRef<HTMLDivElement | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pages, setPages] = useState(0);
  // Bumped to open the document again. A document over Drive is opened with a
  // handful of range requests, and one that fails takes the whole document
  // with it -- so a dropped connection for a few seconds left a book that
  // could only be reopened by leaving it and coming back.
  const [attempt, setAttempt] = useState(0);

  /** The page taking up most of the screen, for "Page 12 of 300". */
  const [current, setCurrent] = useState(1);
  /** What is being typed into the page box, or null when it shows `current`. */
  const [jump, setJump] = useState<string | null>(null);

  // Search. Text is read page by page and kept: it is small beside the pages
  // themselves, and a second search of the same book should not fetch it again.
  const texts = useRef<Map<number, PageText>>(new Map());
  const [hits, setHits] = useState<Hit[]>([]);
  const [hitAt, setHitAt] = useState(0);
  const [search, setSearch] = useState<SearchState>({ phase: "idle" });
  /** Bumped by every new search, so an older one still reading pages stops. */
  const searchRun = useRef(0);

  // Everything the observer callback needs, out of React's way: it runs far
  // more often than anything should re-render.
  const doc = useRef<{ getPage(n: number): Promise<unknown> } | null>(null);
  const observer = useRef<IntersectionObserver | null>(null);
  const reader = useRef<IntersectionObserver | null>(null);
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

      // Under the marks rather than replacing everything in the slot: a page
      // drawn again after scrolling back must keep its search highlights.
      slot.querySelector("canvas")?.remove();
      slot.prepend(canvas);
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
    texts.current = new Map();
    searchRun.current++;
    setHits([]);
    setSearch({ phase: "idle" });
    setCurrent(1);

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
          // The viewport, not the scrolling box. Which element actually
          // scrolls has differed by where this is used, and watching the wrong
          // one saw nothing move: the first pages drew, and scrolling produced
          // no more for ever. Against the viewport it works wherever it is put.
          { rootMargin: AHEAD },
        );

        // Which page is being read: the one taking up most of the screen. A
        // second observer, because the first one's margin reaches a screen
        // ahead and would call the next page current before it is visible.
        const visible = new Map<number, number>();
        const watching = new IntersectionObserver(
          (entries) => {
            for (const entry of entries) {
              const n = Number((entry.target as HTMLElement).dataset.page);
              if (entry.isIntersecting) visible.set(n, entry.intersectionRatio);
              else visible.delete(n);
            }
            let best = 0;
            let most = -1;
            for (const [n, share] of visible) {
              if (share > most || (share === most && n < best)) {
                best = n;
                most = share;
              }
            }
            if (best) setCurrent(best);
          },
          { threshold: [0, 0.1, 0.25, 0.5, 0.75, 1] },
        );

        for (let n = 1; n <= opened.numPages; n++) {
          const slot = document.createElement("div");
          slot.className = "pdf-slot";
          slot.dataset.page = String(n);
          // Its height before it has one of its own, so the document is the
          // right length from the start and scrolling does not jump.
          slot.style.aspectRatio = `1 / ${aspect}`;
          // Where search marks go: over the drawing, and kept when the page
          // is released and drawn again.
          const marks = document.createElement("div");
          marks.className = "pdf-marks";
          slot.append(marks);
          container.append(slot);
          watcher.observe(slot);
          watching.observe(slot);
        }

        if (cancelled) {
          watcher.disconnect();
          watching.disconnect();
        } else {
          observer.current = watcher;
          reader.current = watching;
        }
      } catch (e) {
        if (cancelled) return;
        // Once by itself, quietly: the usual cause is a moment's loss of
        // connection, and the second try simply works.
        if (attempt === 0) {
          setAttempt(1);
          return;
        }
        const said = e instanceof Error ? e.message : String(e);
        setError(
          /fetch|network/i.test(said)
            ? "the connection dropped while it was being read."
            : said,
        );
      }
    })();

    return () => {
      cancelled = true;
      observer.current?.disconnect();
      observer.current = null;
      reader.current?.disconnect();
      reader.current = null;
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
  }, [props.url, attempt, draw, release]);

  const slotFor = (n: number) =>
    host.current?.querySelector<HTMLElement>(`.pdf-slot[data-page="${n}"]`) ?? null;

  /** Bring a page into view: its top, or a point a fraction of the way down
   *  it placed a third of the way down the screen. */
  const showPage = useCallback((n: number, fraction = 0) => {
    const slot = slotFor(n);
    const box = scroller.current;
    if (!slot || !box) return;
    const lead = fraction ? box.clientHeight / 3 : 0;
    box.scrollTop = slot.offsetTop + slot.offsetHeight * fraction - lead;
  }, []);

  /** A page's text, with every run's place on the page. */
  const textOf = useCallback(async (n: number): Promise<PageText | null> => {
    const known = texts.current.get(n);
    if (known) return known;
    if (!doc.current) return null;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const page: any = await doc.current.getPage(n);
    const viewport = page.getViewport({ scale: 1 });
    const content = await page.getTextContent();
    let text = "";
    const runs: Run[] = [];
    for (const item of content.items) {
      if (typeof item.str !== "string") continue;
      const start = text.length;
      text += item.str;
      const x = item.transform[4];
      const y = item.transform[5];
      const tall = item.height || Math.hypot(item.transform[2], item.transform[3]);
      const [x1, y1, x2, y2] = viewport.convertToViewportRectangle([x, y, x + item.width, y + tall]);
      runs.push({
        start,
        end: text.length,
        left: Math.min(x1, x2) / viewport.width,
        top: Math.min(y1, y2) / viewport.height,
        width: Math.abs(x2 - x1) / viewport.width,
        height: Math.abs(y2 - y1) / viewport.height,
      });
      // A line break in the page is a space in the text, so the words either
      // side of it stay two words and a phrase across it is still found.
      if (item.hasEOL) text += " ";
    }
    page.cleanup();
    const read = { text, runs };
    texts.current.set(n, read);
    return read;
  }, []);

  /** Draw the marks for every hit, the current one stronger. */
  const mark = useCallback((list: Hit[], at: number) => {
    host.current?.querySelectorAll(".pdf-marks").forEach((layer) => layer.replaceChildren());
    list.forEach((hit, i) => {
      const layer = slotFor(hit.page)?.querySelector(".pdf-marks");
      const page = texts.current.get(hit.page);
      if (!layer || !page) return;
      for (const run of page.runs) {
        const a = Math.max(hit.start, run.start);
        const b = Math.min(hit.end, run.end);
        if (a >= b) continue;
        // Where in the run the match falls, by its share of the characters:
        // not exact for proportional type, and near enough to see.
        const length = run.end - run.start || 1;
        const box = document.createElement("span");
        box.className = i === at ? "pdf-hit current" : "pdf-hit";
        box.style.left = `${(run.left + (run.width * (a - run.start)) / length) * 100}%`;
        box.style.width = `${((run.width * (b - a)) / length) * 100}%`;
        box.style.top = `${run.top * 100}%`;
        box.style.height = `${run.height * 100}%`;
        layer.append(box);
      }
    });
  }, []);

  const goTo = useCallback((list: Hit[], at: number) => {
    const hit = list[at];
    if (!hit) return;
    setHitAt(at);
    mark(list, at);
    const run = texts.current.get(hit.page)?.runs.find((r) => r.end > hit.start);
    showPage(hit.page, run?.top ?? 0);
  }, [mark, showPage]);

  /** Read the pages in order from the one being read, wrapping round, and go
   *  to the first match the moment there is one. A book on Drive arrives a
   *  piece at a time, so waiting for all of it before showing anything would
   *  be the very wait that fetching lazily exists to avoid. */
  const find = useCallback(async (query: string) => {
    const run = ++searchRun.current;
    const re = pattern(query);
    mark([], 0);
    setHits([]);
    if (!re || !doc.current || pages === 0) {
      setSearch({ phase: "idle" });
      return;
    }
    const order = Array.from({ length: pages }, (_, i) => ((current - 1 + i) % pages) + 1);
    const found: Hit[] = [];
    let shown = false;
    for (let i = 0; i < order.length && found.length < HIT_LIMIT; i++) {
      const n = order[i];
      setSearch({ phase: "searching", found: found.length, done: i, of: pages });
      let page: PageText | null = null;
      try {
        page = await textOf(n);
      } catch {
        // A page whose text cannot be read is skipped, not the whole search.
      }
      if (run !== searchRun.current) return;
      if (!page) continue;
      for (const m of page.text.matchAll(re)) {
        if (found.length >= HIT_LIMIT) break;
        const start = m.index ?? 0;
        found.push({ page: n, start, end: start + m[0].length });
      }
      if (!shown && found.length) {
        shown = true;
        goTo(found, 0);
      }
    }
    // Into page order now that all of it is known, keeping the place reached.
    const first = found[0];
    found.sort((a, b) => a.page - b.page || a.start - b.start);
    const at = first ? found.indexOf(first) : 0;
    setHits(found);
    setHitAt(at);
    mark(found, at);
    setSearch({ phase: "done", found: found.length, at });
  }, [pages, current, textOf, goTo, mark]);

  const step = (delta: number) => {
    if (!hits.length) return;
    const at = (hitAt + delta + hits.length) % hits.length;
    goTo(hits, at);
    setSearch({ phase: "done", found: hits.length, at });
  };

  return (
    <div className="pdf">
      {tools && pages > 0 && (
        <div className="doc-bar">
          <form
            className="doc-page"
            onSubmit={(e) => {
              e.preventDefault();
              const n = Math.round(Number(jump));
              if (n >= 1 && n <= pages) showPage(n);
              setJump(null);
              (document.activeElement as HTMLElement | null)?.blur();
            }}
          >
            Page{" "}
            <input
              className="doc-page-box"
              inputMode="numeric"
              enterKeyHint="go"
              aria-label={`Page number, of ${pages}`}
              value={jump ?? String(current)}
              size={Math.max(2, String(pages).length)}
              onFocus={(e) => {
                setJump(String(current));
                e.target.select();
              }}
              onChange={(e) => setJump(e.target.value.replace(/\D/g, ""))}
              onBlur={() => setJump(null)}
            />{" "}
            of {pages}
          </form>
          <DocSearch
            state={search}
            onSearch={(q) => void find(q)}
            onStep={step}
            onClear={() => {
              searchRun.current++;
              setHits([]);
              mark([], 0);
              setSearch({ phase: "idle" });
            }}
          />
        </div>
      )}
      {error && (
        <div className="error">
          Could not display this document: {error}{" "}
          <button
            type="button"
            onClick={() => {
              setError(null);
              setPages(0);
              setAttempt((n) => n + 1);
            }}
          >
            Try again
          </button>
        </div>
      )}
      {!error && pages === 0 && <p className="muted">Opening {props.title}…</p>}
      <div ref={scroller} className="pdf-scroll">
        <div ref={host} className="pdf-pages" />
      </div>
    </div>
  );
}
