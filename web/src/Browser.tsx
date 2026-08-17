import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import {
  browse,
  crumbs,
  hitAsFile,
  formatDate,
  formatDuration,
  formatSize,
  listSources,
  sortFiles,
  type Sort,
  type SortKey,
  search,
  type FileEntry,
  type Kind,
  type Listing,
  type SearchHit,
  tagLine,
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
  view: View;
  onViewChange: (v: View) => void;
  onOpenSettings: () => void;
  onOpenZones: () => void;
  onOpenPlaylists: () => void;
  onOpenPeople?: () => void;
  onPlay: (files: FileEntry[], index: number, folderPath: string) => void;
  onView: (files: FileEntry[], index: number) => void;
  onActions: (file: FileEntry, siblings: FileEntry[], foundAt?: string) => void;
  playingId: string | null;
  /** Set when something asked to be shown in its folder. */
  reveal: { path: string; itemId: string } | null;
  onRevealed: () => void;
}) {
  const {
    view, onViewChange, onOpenSettings, onOpenZones, onOpenPlaylists, onOpenPeople,
    onPlay, onView, onActions, playingId,
  } = props;

  // The current folder lives in the URL, so the browser's own history works and a
  // link can be shared. On a phone this is what makes the back gesture behave.
  const [path, setPath] = useState(
    () => new URLSearchParams(window.location.search).get("p") || "/",
  );
  const [listing, setListing] = useState<Listing | null>(null);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [query, setQuery] = useState("");
  // Not for administering — for naming: the breadcrumbs turn /drive/music into
  // "music", which needs the source list even though scanning now lives in
  // Settings.
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

  useEffect(() => {
    if (!props.reveal) return;
    // Leaving search is part of revealing: the point is to be standing in the
    // folder, and a results list is not one.
    setQuery("");
    setHits(null);
    setHighlight(props.reveal.itemId);
    navigate(props.reveal.path);
    props.onRevealed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.reveal]);

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
    window.history.pushState({ p }, "", `?p=${encodeURIComponent(p)}`);
  };

  // Back and forward move between folders rather than leaving the app.
  useEffect(() => {
    const onPop = () => {
      setQuery("");
      setHits(null);
      setPath(new URLSearchParams(window.location.search).get("p") || "/");
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Ordering is a property of how you are looking at a folder, so it sits
  // beside the view mode rather than in saved preferences: you sort by artist to
  // answer a question, not forever.
  const [sort, setSort] = useState<Sort>({ key: "name", desc: false });

  // The file a reveal asked to land on. Cleared once shown, so returning to the
  // folder later does not flash it again for no reason.
  const [highlight, setHighlight] = useState<string | null>(null);

  return (
    <div className="browser">
      <header className="bar">
        <span className="brand">Homesh</span>
        <input
          className="searchbox"
          type="search"
          placeholder="Search filenames and folders…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button className="iconbtn" onClick={onOpenZones} aria-label="Zones" title="Zones">
          ⧉
        </button>
        <button className="iconbtn" onClick={onOpenPlaylists} aria-label="Playlists"
                title="Playlists">
          ≡
        </button>
        {onOpenPeople && (
          <button className="iconbtn" onClick={onOpenPeople} aria-label="People" title="People">
            ☺
          </button>
        )}
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
        <Results
          hits={hits}
          query={query}
          view={view}
          onPlay={onPlay}
          onView={onView}
          onActions={onActions}
          playingId={playingId}
        />
      ) : (
        <Folder
          listing={listing}
          loading={loading}
          view={view}
          onOpen={navigate}
          onPlay={onPlay}
          onView={onView}
          onActions={onActions}
          sort={sort}
          onSort={setSort}
          playingId={playingId}
          highlight={highlight}
          onHighlightShown={() => setHighlight(null)}
        />
      )}

    </div>
  );
}

/** One clickable column heading.
 *
 * Clicking the column you are already sorted by reverses it, which is what every
 * file browser does and therefore what fingers expect.
 */
function SortHead(props: {
  label: string;
  col: SortKey;
  sort: Sort;
  onSort: (s: Sort) => void;
}) {
  const active = props.sort.key === props.col;
  return (
    <button
      className={`sorthead${active ? " active" : ""}`}
      aria-sort={active ? (props.sort.desc ? "descending" : "ascending") : "none"}
      onClick={(e) => {
        e.stopPropagation();
        props.onSort({ key: props.col, desc: active ? !props.sort.desc : false });
      }}
    >
      {props.label}
      <span className="arrow">{active ? (props.sort.desc ? "▾" : "▴") : ""}</span>
    </button>
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
function FileRow(props: {
  f: FileEntry;
  view: View;
  onPlay?: () => void;
  onActions?: () => void;
  isPlaying?: boolean;
  rowRef?: (node: HTMLLIElement | null) => void;
  revealed?: boolean;
}) {
  const { f, view, onPlay, onActions, isPlaying } = props;
  // Audio goes to the player bar; photos, video and documents open the viewer.
  // Anything else stays inert rather than pretending to be openable.
  const openable = f.available && ["audio", "photo", "video", "doc"].includes(f.kind);
  const playable = openable;
  const cls = [
    f.available ? "" : "offline",
    playable ? "playable" : "",
    isPlaying ? "nowplaying" : "",
    props.revealed ? "revealed" : "",
  ].filter(Boolean).join(" ");
  const click = playable ? onPlay : undefined;

  if (view === "columns") {
    return (
      <li className={cls} ref={props.rowRef} onClick={click}>
        <span className={`ic ${f.kind}`}>{isPlaying ? "▶" : GLYPH[f.kind]}</span>
        {f.filename}
      </li>
    );
  }

  if (view.startsWith("tiles")) {
    return (
      <li className={cls} ref={props.rowRef} onClick={click}>
        <Thumb item={f} size={view === "tiles-large" ? "large" : "small"} />
        <span className="nm" title={f.filename}>
          {f.filename}
        </span>
        <span className="sub">{f.available ? formatSize(f.size) : "offline"}</span>
      </li>
    );
  }

  const tags = tagLine(f.meta);

  return (
    <li className={cls} ref={props.rowRef} onClick={click}>
      <span className={`ic ${f.kind}`}>{isPlaying ? "▶" : GLYPH[f.kind]}</span>
      {/* The filename is never replaced by a tag, only accompanied by one — a
          corrupt tag must never leave you unable to tell what a file is. That is
          why these are separate columns rather than a title that falls back to
          the filename. */}
      <span className="nm" title={f.filename}>
        {f.filename}
        {/* The same metadata, folded under the filename. Columns need width a
            phone does not have, and dropping the tags there entirely would make
            the small screen the one that tells you least. Only one of the two is
            ever visible — see the media query. */}
        {tags && <span className="tags">{tags}</span>}
      </span>
      <span className="col" title={f.meta?.title ?? ""}>{f.meta?.title ?? ""}</span>
      <span className="col" title={f.meta?.artist ?? f.meta?.albumartist ?? ""}>
        {f.meta?.artist ?? f.meta?.albumartist ?? ""}
      </span>
      <span className="col" title={f.meta?.album ?? ""}>{f.meta?.album ?? ""}</span>
      <span className="meta dur">{formatDuration(f.duration_ms)}</span>
      <span className="meta">
        {f.available ? (
          openable && (
            <button
              className="sendbtn"
              title={`What to do with ${f.filename}`}
              aria-label="Actions"
              onClick={(e) => { e.stopPropagation(); onActions?.(); }}
            >
              ⋯
            </button>
          )
        ) : (
          <span className="badge">offline</span>
        )}
      </span>
      <span className="meta date">{formatDate(f.mtime)}</span>
    </li>
  );
}

function containerClass(view: View): string {
  if (view === "columns") return "cols";
  if (view === "tiles-small") return "tiles small";
  if (view === "tiles-large") return "tiles large";
  // The details view is the only one with columns, and its grid differs.
  return "rows details";
}

function Folder(props: {
  listing: Listing | null;
  loading: boolean;
  view: View;
  onOpen: (p: string) => void;
  onPlay: (files: FileEntry[], index: number, folderPath: string) => void;
  onView: (files: FileEntry[], index: number) => void;
  onActions: (file: FileEntry, siblings: FileEntry[], foundAt?: string) => void;
  playingId: string | null;
  sort: Sort;
  onSort: (s: Sort) => void;
  highlight: string | null;
  onHighlightShown: () => void;
}) {
  const { listing, loading, view, onOpen, onPlay, onView, onActions, playingId, sort, onSort } =
    props;

  // Scroll the revealed file into view once it exists. A ref callback rather
  // than an effect: the row is not in the DOM until the folder has loaded, and
  // this fires exactly when it arrives.
  const revealRef = useCallback(
    (node: HTMLLIElement | null) => {
      if (!node) return;
      node.scrollIntoView({ block: "center", behavior: "smooth" });
      window.setTimeout(props.onHighlightShown, 2000);
    },
    [props.onHighlightShown],
  );
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
                  <span className="col" />
                  <span className="col" />
                  <span className="col" />
                  <span className="meta" />
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
                  <span className="col" />
                  <span className="col" />
                  <span className="col" />
                  <span className="meta" />
                  <span className="meta" />
                  <span className="meta date" />
                </>
              )}
            </>
          )}
        </li>
      ))}

      {view === "details" && (
        <li className="rowhead">
          <span />
          <SortHead label="Name" col="name" sort={sort} onSort={onSort} />
          <SortHead label="Title" col="title" sort={sort} onSort={onSort} />
          <SortHead label="Artist" col="artist" sort={sort} onSort={onSort} />
          <SortHead label="Album" col="album" sort={sort} onSort={onSort} />
          <SortHead label="Time" col="duration" sort={sort} onSort={onSort} />
          <span />
          <SortHead label="Modified" col="date" sort={sort} onSort={onSort} />
        </li>
      )}

      {sortFiles(listing.files, sort).map((f, i) => (
        <FileRow
          key={f.item_id}
          f={f}
          rowRef={f.item_id === props.highlight ? revealRef : undefined}
          revealed={f.item_id === props.highlight}
          view={view}
          isPlaying={playingId === f.item_id}
          onPlay={() =>
            f.kind === "audio"
              ? onPlay(listing.files, i, listing.path)
              : onView(listing.files, i)
          }
          onActions={() => onActions(f, listing.files)}
        />
      ))}
    </ul>
  );
}

function Results(props: {
  hits: SearchHit[];
  query: string;
  view: View;
  onPlay: (files: FileEntry[], index: number, folderPath: string) => void;
  onView: (files: FileEntry[], index: number) => void;
  onActions: (file: FileEntry, siblings: FileEntry[], foundAt?: string) => void;
  playingId: string | null;
}) {
  const { hits, query, view, onPlay, onView, onActions, playingId } = props;

  // A result should do what the same file does in a folder: play, open, or offer
  // its menu. Jumping to the top of the containing folder — which is what this
  // used to do — makes you find the file a second time by hand.
  const files = hits.map(hitAsFile);
  if (hits.length === 0) return <p className="muted">Nothing matches “{query}”.</p>;

  const tiles = view.startsWith("tiles");

  return (
    <ul className={containerClass(view)}>
      {hits.map((h, i) => (
        <li
          key={h.item_id}
          className={`hit${h.available ? "" : " offline"}${
            playingId === h.item_id ? " nowplaying" : ""
          }`}
          onClick={() =>
            h.available &&
            (h.kind === "audio"
              ? onPlay(files.filter((f) => f.kind === "audio"), Math.max(0,
                  files.filter((f) => f.kind === "audio").findIndex((f) => f.item_id === h.item_id)),
                  "")
              : onView(files.filter((f) => f.kind === h.kind), Math.max(0,
                  files.filter((f) => f.kind === h.kind).findIndex((f) => f.item_id === h.item_id))))
          }
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
              <span className="meta">
                {h.available ? (
                  <button
                    className="sendbtn"
                    aria-label="Actions"
                    title={`What to do with ${h.filename}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onActions(files[i], files, h.path);
                    }}
                  >
                    ⋯
                  </button>
                ) : (
                  <span className="badge">offline</span>
                )}
              </span>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}
