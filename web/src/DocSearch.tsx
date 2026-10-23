import { useEffect, useRef, useState } from "react";

/** What a query means, the same way in every kind of document.
 *
 * Case does not matter, and any run of spaces matches any run of whitespace:
 * a PDF's text arrives in pieces with line breaks wherever the page wrapped,
 * so "player's handbook" split across two lines is still found. Nothing else
 * is special -- a full stop is a full stop, not "any character". */
export function pattern(query: string): RegExp | null {
  const words = query.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return null;
  const escaped = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(escaped.join("\\s+"), "giu");
}

/** Where a search has got to, said in the bar. */
export type SearchState =
  | { phase: "idle" }
  | { phase: "searching"; found: number; done: number; of: number }
  | { phase: "done"; found: number; at: number };

/** The search box and its next/previous buttons, shared by every document view.
 *
 * A browser's own find cannot see a PDF -- the pages are pictures drawn into a
 * canvas -- and a phone's browser hides find behind a menu if it has one at
 * all. So a document carries its own, and Ctrl+F opens it rather than the
 * browser's, which would find nothing. */
export default function DocSearch(props: {
  state: SearchState;
  onSearch: (query: string) => void;
  onStep: (delta: number) => void;
  onClear: () => void;
}) {
  const box = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState("");
  const { state } = props;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
        e.preventDefault();
        box.current?.focus();
        box.current?.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const found = state.phase === "idle" ? 0 : state.found;
  const status =
    state.phase === "searching"
      ? `${found} found · page ${state.done} of ${state.of}…`
      : state.phase === "done"
        ? found === 0
          ? "Not found"
          : `${state.at + 1} of ${found}`
        : "";

  return (
    <form
      className="doc-search"
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        // Enter again on the same words is "next", as in every find bar.
        if (state.phase === "done" && found > 0 && box.current?.dataset.last === query) {
          props.onStep(1);
          return;
        }
        if (box.current) box.current.dataset.last = query;
        props.onSearch(query);
      }}
    >
      <input
        ref={box}
        type="search"
        className="doc-search-box"
        placeholder="Search in this document"
        aria-label="Search in this document"
        value={query}
        enterKeyHint="search"
        onChange={(e) => {
          setQuery(e.target.value);
          if (e.target.value === "") {
            if (box.current) box.current.dataset.last = "";
            props.onClear();
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && e.shiftKey) {
            e.preventDefault();
            props.onStep(-1);
          }
        }}
      />
      {status && (
        <span className="doc-search-status" aria-live="polite">
          {status}
        </span>
      )}
      {found > 0 && state.phase === "done" && (
        <>
          <button type="button" className="doc-btn" onClick={() => props.onStep(-1)}
            aria-label="Previous match" title="Previous (Shift+Enter)">
            ‹
          </button>
          <button type="button" className="doc-btn" onClick={() => props.onStep(1)}
            aria-label="Next match" title="Next (Enter)">
            ›
          </button>
        </>
      )}
    </form>
  );
}
