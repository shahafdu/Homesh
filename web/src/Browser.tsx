import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import {
  browse,
  crumbs,
  formatDate,
  formatSize,
  listSources,
  scanSource,
  search,
  type FileEntry,
  type Kind,
  type Listing,
  type SearchHit,
  type Source,
} from "./library";
import { VIEWS, type View } from "./prefs";

const GLYPH: Record<Kind, string> = {
  audio: "♪",
  video: "▶",
  photo: "◈",
  doc: "▤",
  other: "•",
};

export default function Browser(props: {
  isAdmin: boolean;
  view: View;
  onViewChange: (v: View) => void;
  onOpenSettings: () => void;
}) {
  const { isAdmin, view, onViewChange, onOpenSettings } = props;

  const [path, setPath] = useState("/");
  const [listing, setListing] = useState<Listing | null>(null);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [query, setQuery] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (p: string) => {
    setLoading(true);
    setError(null);
    try {
      setListing(await browse(p));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!hits) void load(path);
  }, [path, hits, load]);

  useEffect(() => {
    listSources().then(setSources).catch(() => undefined);
  }, []);

  // Debounced so typing doesn't fire a query per keystroke.
  const timer = useRef<number>();
  useEffect(() => {
    window.clearTimeout(timer.current);
    if (query.trim().length === 0) {
      setHits(null);
      return;
    }
    timer.current = window.setTimeout(async () => {
      try {
        setHits(await search(query.trim()));
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      }
    }, 200);
    return () => window.clearTimeout(timer.current);
  }, [query]);

  const navigate = (p: string) => {
    setQuery("");
    setHits(null);
    setPath(p);
  };

  const rescan = async (id: string) => {
    await scanSource(id);
    // The scan runs in the background; give it a moment before re-reading.
    window.setTimeout(() => void load(path), 1500);
  };

  const atRoot = hits === null && listing?.path === "/";

  return (
    <div className="browser">
      <header className="bar">
        <span className="brand">Hearth</span>
        <input
          className="searchbox"
          type="search"
          placeholder="Search filenames and folders…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button className="iconbtn" onClick={onOpenSettings} aria-label="Settings" title="Settings">
          ⚙
        </button>
      </header>

      <div className="toolbar">
        {hits === null && listing ? (
          <nav className="crumbs">
            {crumbs(listing.path, sources).map((c, i, all) => (
              <span key={c.path}>
                <button className="crumb" onClick={() => navigate(c.path)}>
                  {c.label}
                </button>
                {i < all.length - 1 && <span className="sep">/</span>}
              </span>
            ))}
          </nav>
        ) : (
          <span className="muted small">
            {hits?.length ?? 0} result{hits?.length === 1 ? "" : "s"}
          </span>
        )}

        <div className="seg" role="group" aria-label="View mode">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              aria-pressed={view === v.id}
              title={v.hint}
              onClick={() => onViewChange(v.id)}
            >
              {v.label}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {hits !== null ? (
        <Results hits={hits} query={query} view={view} onOpen={navigate} />
      ) : (
        <Folder listing={listing} loading={loading} view={view} onOpen={navigate} />
      )}

      {atRoot && <SourceList sources={sources} isAdmin={isAdmin} onScan={rescan} />}
    </div>
  );
}

/** A thumbnail, falling back to the kind icon.
 *
 * Many files legitimately have no artwork — a track with no embedded cover, a
 * document, an unreadable file. The server answers 404 for those, so a failed load
 * is an expected outcome rather than an error worth surfacing.
 */
function Thumb(props: { item: { item_id: string; kind: Kind; available: boolean }; size: "small" | "large" }) {
  const { item, size } = props;
  const [failed, setFailed] = useState(false);

  // No point requesting artwork for a file whose source is unreachable; the server
  // would answer 503 and we would show the icon anyway.
  const showImage = !failed && item.available && item.kind !== "doc" && item.kind !== "other";

  return (
    <div className="thumb">
      {showImage ? (
        <img
          src={`/api/thumb/${item.item_id}?size=${size}`}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
        />
      ) : (
        <span className={`ic ${item.kind}`}>{GLYPH[item.kind]}</span>
      )}
    </div>
  );
}

/** One file, rendered per view mode. Kept in one place so the four modes cannot
 *  drift apart in what they show. */
function FileRow(props: { f: FileEntry; view: View }) {
  const { f, view } = props;
  const cls = `${f.available ? "" : "offline"}`;

  if (view === "columns") {
    return (
      <li className={cls}>
        <span className={`ic ${f.kind}`}>{GLYPH[f.kind]}</span>
        {f.filename}
      </li>
    );
  }

  if (view.startsWith("tiles")) {
    return (
      <li className={cls}>
        <Thumb item={f} size={view === "tiles-large" ? "large" : "small"} />
        <span className="nm" title={f.filename}>
          {f.filename}
        </span>
        <span className="sub">{f.available ? formatSize(f.size) : "offline"}</span>
      </li>
    );
  }

  return (
    <li className={cls}>
      <span className={`ic ${f.kind}`}>{GLYPH[f.kind]}</span>
      <span className="nm" title={f.filename}>
        {f.filename}
      </span>
      <span className="meta">
        {f.available ? "" : <span className="badge">offline</span>}
      </span>
      <span className="meta date">{formatDate(f.mtime)}</span>
    </li>
  );
}

function containerClass(view: View): string {
  if (view === "columns") return "cols";
  if (view === "tiles-small") return "tiles small";
  if (view === "tiles-large") return "tiles large";
  return "rows";
}

function Folder(props: {
  listing: Listing | null;
  loading: boolean;
  view: View;
  onOpen: (p: string) => void;
}) {
  const { listing, loading, view, onOpen } = props;
  if (!listing) return <p className="muted">{loading ? "Loading…" : ""}</p>;

  if (listing.dirs.length === 0 && listing.files.length === 0) {
    return <p className="muted">This folder is empty.</p>;
  }

  const tiles = view.startsWith("tiles");

  return (
    <ul className={containerClass(view)}>
      {listing.parent !== null && listing.path !== "/" && (
        <li className="dir" onClick={() => onOpen(listing.parent!)}>
          {tiles ? (
            <>
              <div className="thumb">
                <span className="ic dir">↑</span>
              </div>
              <span className="nm">..</span>
            </>
          ) : (
            <>
              <span className="ic dir">↑</span>
              <span className="nm">..</span>
              {view === "details" && (
                <>
                  <span className="meta" />
                  <span className="meta date" />
                </>
              )}
            </>
          )}
        </li>
      )}

      {listing.dirs.map((d) => (
        <li key={d.path} className="dir" onClick={() => onOpen(d.path)}>
          {tiles ? (
            <>
              <div className="thumb">
                <span className="ic dir">▸</span>
              </div>
              <span className="nm">{d.name}</span>
              <span className="sub">folder</span>
            </>
          ) : (
            <>
              <span className="ic dir">▸</span>
              <span className="nm">{d.name}</span>
              {view === "details" && (
                <>
                  <span className="meta" />
                  <span className="meta date" />
                </>
              )}
            </>
          )}
        </li>
      ))}

      {listing.files.map((f) => (
        <FileRow key={f.item_id} f={f} view={view} />
      ))}
    </ul>
  );
}

function Results(props: {
  hits: SearchHit[];
  query: string;
  view: View;
  onOpen: (p: string) => void;
}) {
  const { hits, query, view, onOpen } = props;
  if (hits.length === 0) return <p className="muted">Nothing matches “{query}”.</p>;

  const tiles = view.startsWith("tiles");

  return (
    <ul className={containerClass(view)}>
      {hits.map((h) => (
        <li
          key={h.item_id}
          className={`hit${h.available ? "" : " offline"}`}
          onClick={() => onOpen(h.path)}
        >
          {tiles ? (
            <>
              <Thumb item={h} size={view === "tiles-large" ? "large" : "small"} />
              <span className="nm" title={h.filename}>
                {h.filename}
              </span>
              <span className="sub">{h.path.split("/").pop()}</span>
            </>
          ) : view === "columns" ? (
            <>
              <span className={`ic ${h.kind}`}>{GLYPH[h.kind]}</span>
              {h.filename}
            </>
          ) : (
            <>
              <span className={`ic ${h.kind}`}>{GLYPH[h.kind]}</span>
              <span className="nm">
                {h.filename}
                {/* Where it lives matters as much as what it is called. */}
                <span className="where">{h.path}</span>
              </span>
              <span className="meta">{formatSize(h.size)}</span>
              <span className="meta date">
                {h.available ? "" : <span className="badge">offline</span>}
              </span>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

function SourceList(props: {
  sources: Source[];
  isAdmin: boolean;
  onScan: (id: string) => void;
}) {
  if (props.sources.length === 0) return null;
  return (
    <div className="sources">
      <h2>Sources</h2>
      {props.sources.map((s) => (
        <div key={s.id} className="source">
          <div>
            <strong>{s.name}</strong> <span className="muted">{s.mount_prefix}</span>
            <div className="muted small">
              {s.files.toLocaleString()} files
              {s.last_seen_at && ` · scanned ${formatDate(s.last_seen_at)}`}
            </div>
          </div>
          {props.isAdmin && (
            <button className="compact" onClick={() => props.onScan(s.id)}>
              Rescan
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
