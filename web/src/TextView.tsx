import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import DocSearch, { pattern, type SearchState } from "./DocSearch";

/** Past this many matches the rest are not marked. A one-letter search in a
 *  large log would otherwise be tens of thousands of elements to find "a". */
const MARK_LIMIT = 2000;

/** A plain-text document, with the same search a PDF has. */
export default function TextView(props: { text: string; bar?: HTMLElement | null }) {
  const [query, setQuery] = useState("");
  const [at, setAt] = useState(0);
  const marks = useRef<(HTMLElement | null)[]>([]);

  // The text cut at every match, so each match can be its own <mark>.
  const pieces = useMemo(() => {
    const re = pattern(query);
    if (!re) return { parts: [props.text], found: 0 };
    const parts: (string | { match: string })[] = [];
    let last = 0;
    let found = 0;
    for (const m of props.text.matchAll(re)) {
      if (found >= MARK_LIMIT) break;
      const start = m.index ?? 0;
      parts.push(props.text.slice(last, start), { match: m[0] });
      last = start + m[0].length;
      found++;
    }
    parts.push(props.text.slice(last));
    return { parts, found };
  }, [props.text, query]);

  useEffect(() => {
    marks.current[at]?.scrollIntoView({ block: "center" });
  }, [at, pieces]);

  const state: SearchState = query ? { phase: "done", found: pieces.found, at } : { phase: "idle" };
  let n = 0;

  return (
    <div className="doc">
      {props.bar && createPortal(
        <DocSearch
          state={state}
          onSearch={(q) => {
            setQuery(q);
            setAt(0);
          }}
          onStep={(d) => pieces.found && setAt((i) => (i + d + pieces.found) % pieces.found)}
          onClear={() => setQuery("")}
        />,
        props.bar,
      )}
      <pre className="v-text">
        {pieces.parts.map((p, i) => {
          if (typeof p === "string") return p;
          const k = n++;
          return (
            <mark
              key={i}
              ref={(el) => {
                marks.current[k] = el;
              }}
              className={k === at ? "doc-hit current" : "doc-hit"}
            >
              {p.match}
            </mark>
          );
        })}
      </pre>
    </div>
  );
}
