import { useCallback, useEffect, useState } from "react";
import { formatSize } from "./library";

/** Looking inside a file nothing can preview.
 *
 * The point is that no file is a dead end. A spreadsheet renders, a photograph
 * renders, and everything else offered "download it and open it somewhere
 * else" — which for a stray `.ini`, a subtitle file with the wrong extension,
 * or something whose name lies about what it is, means leaving the app to
 * answer a question the app could answer.
 *
 * Two ways to look, because a file is one of two things to somebody who cannot
 * preview it. Either it is text with an unfamiliar extension, in which case
 * reading it settles the matter; or it is not, in which case the bytes
 * themselves — a magic number in the first four, a run of nulls, a string in
 * the middle — are what identify it.
 */

/** How much is read at a time.
 *
 * A bound rather than a preference: files here run past a gigabyte, and this is
 * a viewer, not a download. Hex is smaller per page because it costs four
 * characters on screen for every byte, so the same window of a file makes a far
 * longer page.
 */
const PAGE = { text: 256 * 1024, hex: 64 * 1024 } as const;

type Mode = keyof typeof PAGE;

export default function RawView(props: {
  url: string;
  /** From the catalog. Used for the bounds and to say how far in you are. */
  size: number | null;
}) {
  const [mode, setMode] = useState<Mode | null>(null);
  const [bytes, setBytes] = useState<Uint8Array | null>(null);
  const [from, setFrom] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const read = useCallback(
    async (start: number, want: number) => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(props.url, {
          credentials: "same-origin",
          // Only the window being looked at. Without this, opening a 13 GB tape
          // to see what it is would try to hold 13 GB in a phone.
          headers: { Range: `bytes=${start}-${start + want - 1}` },
        });
        if (!res.ok && res.status !== 206) throw new Error(`server returned ${res.status}`);
        setBytes(new Uint8Array(await res.arrayBuffer()));
        setFrom(start);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [props.url],
  );

  // The first read decides how to show it, and shows it.
  useEffect(() => {
    let cancelled = false;
    setBytes(null);
    setMode(null);

    void (async () => {
      setLoading(true);
      try {
        const res = await fetch(props.url, {
          credentials: "same-origin",
          headers: { Range: `bytes=0-${PAGE.hex - 1}` },
        });
        if (!res.ok && res.status !== 206) throw new Error(`server returned ${res.status}`);
        const head = new Uint8Array(await res.arrayBuffer());
        if (cancelled) return;

        // Opened at whichever view answers the question. Somebody looking at an
        // unknown file wants to know what it is, and being shown a wall of hex
        // for a configuration file — or a page of replacement characters for a
        // photograph — is the wrong answer to that in both directions.
        const chosen: Mode = looksLikeText(head) ? "text" : "hex";
        setMode(chosen);
        if (chosen === "text" && (props.size ?? 0) > PAGE.hex) {
          await read(0, PAGE.text);
        } else {
          setBytes(head);
          setFrom(0);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [props.url, props.size, read]);

  if (error) {
    return (
      <div className="raw">
        <p className="error">Could not read the file — {error}</p>
      </div>
    );
  }

  if (!mode || !bytes) {
    return (
      <div className="raw">
        <p className="muted">Reading…</p>
      </div>
    );
  }

  const total = props.size ?? bytes.length;
  const shown = Math.min(from + bytes.length, total);
  const step = PAGE[mode];

  return (
    <div className="raw">
      <div className="raw-bar">
        <div className="seg" role="group" aria-label="How to show this file">
          <button
            aria-pressed={mode === "text"}
            onClick={() => {
              setMode("text");
              void read(from, PAGE.text);
            }}
          >
            Text
          </button>
          <button
            aria-pressed={mode === "hex"}
            onClick={() => {
              setMode("hex");
              void read(from, PAGE.hex);
            }}
          >
            Hex
          </button>
        </div>

        <span className="muted small nowrap">
          {formatSize(from)}–{formatSize(shown)} of {formatSize(total)}
          {loading && " · reading…"}
        </span>

        <div className="zone-controls">
          <button
            className="compact"
            disabled={loading || from === 0}
            onClick={() => void read(Math.max(0, from - step), step)}
          >
            ← Back
          </button>
          <button
            className="compact"
            disabled={loading || shown >= total}
            onClick={() => void read(from + bytes.length, step)}
          >
            More →
          </button>
        </div>
      </div>

      {/* Its own scroller. A hex dump is wider than a phone and longer than a
          page, and neither should push the viewer around it sideways. */}
      <pre className={`raw-body ${mode}`}>
        {mode === "text" ? asText(bytes) : asHex(bytes, from)}
      </pre>

      {mode === "text" && (
        <p className="muted small">
          Read as UTF-8. Anything that is not valid UTF-8 shows as
          <code> � </code>— if there are many, this is not text and the hex view
          will tell you more.
        </p>
      )}
    </div>
  );
}

/** Whether a first read looks like something worth reading rather than dumping.
 *
 * Nulls are the giveaway: text does not contain them and almost every binary
 * format does. Beyond that it is the proportion of control characters, which
 * separates prose and markup from a compressed archive that happens to have
 * avoided a null in its first sixty kilobytes.
 */
function looksLikeText(bytes: Uint8Array): boolean {
  if (bytes.length === 0) return true;

  const sample = bytes.subarray(0, 4096);
  let odd = 0;
  for (const byte of sample) {
    if (byte === 0) return false;
    // Tab, newline, carriage return and form feed are ordinary in text.
    const printable = byte >= 0x20 || byte === 9 || byte === 10 || byte === 13 || byte === 12;
    if (!printable) odd++;
  }
  return odd / sample.length < 0.05;
}

/** UTF-8, with invalid sequences shown rather than throwing.
 *
 * Deliberately lenient: the whole point of opening an unknown file is that
 * nobody knows what is in it, so a decoder that refuses tells you less than one
 * that shows you the mess.
 */
function asText(bytes: Uint8Array): string {
  return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
}

/** The classic dump: offset, sixteen bytes, then those bytes as characters.
 *
 * Built as one string rather than as elements. Sixty-four kilobytes is four
 * thousand rows, and four thousand rows of spans is a page that scrolls badly
 * on a phone for no benefit — none of it is interactive.
 */
function asHex(bytes: Uint8Array, from: number): string {
  const lines: string[] = [];
  const hex = (n: number, width: number) => n.toString(16).padStart(width, "0");

  for (let at = 0; at < bytes.length; at += 16) {
    const row = bytes.subarray(at, at + 16);
    const pairs: string[] = [];
    let glyphs = "";

    for (let i = 0; i < 16; i++) {
      if (i < row.length) {
        pairs.push(hex(row[i], 2));
        // Printable ASCII only. Anything else is a dot, which is what makes a
        // run of text inside a binary file stand out at a glance.
        glyphs += row[i] >= 0x20 && row[i] < 0x7f ? String.fromCharCode(row[i]) : ".";
      } else {
        pairs.push("  ");
        glyphs += " ";
      }
      // A gap down the middle, so a byte can be counted without counting from
      // the start of the row.
      if (i === 7) pairs.push("");
    }

    lines.push(`${hex(from + at, 8)}  ${pairs.join(" ")}  |${glyphs}|`);
  }

  return lines.join("\n");
}
