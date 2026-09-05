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

/** The name of a folder, for a label that has to fit on a phone. */
const here = (path: string) => path.split("/").filter(Boolean).pop() ?? "this folder";

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
  // Everywhere, or just here. Everywhere is the right default — usually you do
  // not know where a thing is — but standing in a folder of 1,500 tracks it is
  // the wrong answer to "which of these is the live one".
  const [hereOnly, setHereOnly] = useState(false);
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
        setHits(await search(query.trim(), hereOnly ? path : null));
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      }
    }, 200);
    return () => window.clearTimeout(timer.current);
  }, [query, hereOnly, path]);

  const navigate = (p: string) => {
    setQuery("");
    setHits(null);
    setHereOnly(false);
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
      {/* One sticky block. Separately they each stuck to the viewport while the
          page around them scrolled, so both drifted and a gap opened between
          them on the way. */}
      <div className="chrome">
      <header className="bar">
        {/* The way home. Every app puts it behind the logo, and without it the
            only route back to the top was pressing back as many times as you had
            descended — or editing the address. */}
        <button
          className="brand"
          onClick={() => navigate("/")}
          title="All sources"
          aria-label="Go to the top of the library"
        >
          Homesh
        </button>
        <span className="bar-spacer" />
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

      {/* The search box sits with the things that describe a search — where it
          looks and what it found — rather than up in the brand row away from
          both. */}
      <div className="searchrow">
        {/* Our own clear button, not the one type="search" provides.
            The native one appears only while the box has focus, so clearing a
            search took two taps: one to focus the box, one to press the cross
            that had just appeared. This one is there whenever there is
            something to clear. */}
        <span className="searchbox-wrap">
          <input
            className="searchbox"
            type="search"
            placeholder="Search filenames and folders…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button
              className="searchclear"
              aria-label="Clear the search"
              title="Clear"
              onClick={() => setQuery("")}
            >
              ✕
            </button>
          )}
        </span>
        {/* One button that changes, not two. Where it looks is a single choice
            with two states, and a segmented pair spent a whole row saying so. */}
        {path !== "/" && (
          <button
            className={`scope${hereOnly ? " here" : ""}`}
            aria-pressed={hereOnly}
            title={
              hereOnly
                ? `Searching inside ${here(path)} — tap to search everywhere`
                : `Searching everywhere — tap to search inside ${here(path)}`
            }
            onClick={() => setHereOnly(!hereOnly)}
          >
            <span className="scope-ic" aria-hidden="true">{hereOnly ? "▣" : "◍"}</span>
            {hereOnly ? here(path) : "Everywhere"}
          </button>
        )}
      </div>

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
            {hereOnly && ` in ${here(path)}`}
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

      </div>

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
  // Audio goes to the player bar; everything else opens the viewer.
  //
  // Including the kinds nothing can preview. They used to be inert — a .MSWMM
  // or a file with no extension at all could not be clicked, so the only way to
  // find out what one was involved downloading it and opening it elsewhere. The
  // viewer now shows the bytes as text or hex, which answers that question
  // without leaving the app, so there is nothing left that is not worth
  // opening.
  const openable = f.available;
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
        <span className="nm-clip">{f.filename}</span>
        {/* Everything a file can do should be reachable from every view. It was
            only in the detailed one, which made the others feel like previews. */}
        {f.available && openable && (
          <button
            className="sendbtn"
            title={`What to do with ${f.filename}`}
            aria-label="Actions"
            onClick={(e) => { e.stopPropagation(); onActions?.(); }}
          >
            ⋯
          </button>
        )}
      </li>
    );
  }

  if (view.startsWith("tiles")) {
    return (
      <li className={cls} ref={props.rowRef} onClick={click}>
        <Thumb item={f} size={view === "tiles-large" ? "large" : "small"} />
        {f.available && openable && (
          <button
            className="sendbtn tile-actions"
            title={`What to do with ${f.filename}`}
            aria-label="Actions"
            onClick={(e) => { e.stopPropagation(); onActions?.(); }}
          >
            ⋯
          </button>
        )}
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
        {/* Its own element, because on a phone the filename is clamped to two
            lines — and with the tags inside that box, a long name filled both
            lines and clipped them away entirely. Which is exactly what happened:
            the tags stopped appearing the moment long names were allowed to
            wrap. The clamp belongs to the name alone. */}
        <span className="nm-text">{f.filename}</span>
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
      <span className="meta date">{formatDate(f.mtime)}</span>
      {/* Last, because it is a control rather than a fact about the file. Sitting
          between the length and the date it read as belonging to one of them. */}
      <span className="meta actions">
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

  // Sorted once, and everything downstream works from this array.
  //
  // It used to be sorted inline in the map while the *unsorted* listing was
  // handed to the player alongside the sorted index — so the queue started at
  // whatever happened to sit at that position in the other order. Invisible for
  // Latin filenames, where the browser's collator and the database's natural
  // sort agree; wrong for Hebrew ones on Windows, where they do not. That is
  // the whole of "clicking a song plays two songs above it".
  const rows = sortFiles(listing.files, sort);

  return (
    <ul className={containerClass(view)}>
      {/* Above everything. It sat between the folders and the files, so a
          column heading appeared halfway down the page and read as a
          divider rather than as a header. */}
      {view === "details" && (
        <li className="rowhead">
          <span />
          <SortHead label="Name" col="name" sort={sort} onSort={onSort} />
          <SortHead label="Title" col="title" sort={sort} onSort={onSort} />
          <SortHead label="Artist" col="artist" sort={sort} onSort={onSort} />
          <SortHead label="Album" col="album" sort={sort} onSort={onSort} />
          <SortHead label="Length" col="duration" sort={sort} onSort={onSort} />
          <SortHead label="Modified" col="date" sort={sort} onSort={onSort} />
          <span />
        </li>
      )}


      {listing.parent !== null && listing.path !== "/" && (
        <li className={`dir up${tiles ? " as-row" : ""}`} onClick={() => onOpen(listing.parent!)}>
          {/* Never a tile. "Up one folder" is a navigation control, and given a
              thumbnail and a caption it sat in a wall of photographs pretending
              to be one of them. */}
          {false ? (
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
              {view === "details" && !tiles && (
                <>
                  <span className="col" />
                  <span className="col" />
                  <span className="col" />
                  <span className="meta" />
                  <span className="meta date" />
                  <span className="meta actions" />
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
                  <span className="meta date" />
                  <span className="meta actions" />
                </>
              )}
            </>
          )}
        </li>
      ))}

      {rows.map((f, i) => (
        <FileRow
          key={f.item_id}
          f={f}
          rowRef={f.item_id === props.highlight ? revealRef : undefined}
          revealed={f.item_id === props.highlight}
          view={view}
          isPlaying={playingId === f.item_id}
          onPlay={() =>
            f.kind === "audio" ? onPlay(rows, i, listing.path) : onView(rows, i)
          }
          onActions={() => onActions(f, rows)}
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
                <span className="nm-text">{h.filename}</span>
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
