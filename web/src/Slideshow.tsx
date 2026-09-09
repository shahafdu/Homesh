import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useLockScroll } from "./useLockScroll";
import {
  DEFAULTS,
  HOLDS,
  TRANSITIONS,
  type ShowSettings,
  type Transition,
  URL_GOOD_FOR_MS,
  gatherPhotos,
  resolveTransition,
  type PhotoSet,
} from "./slideshow";

/** A folder of photographs, shown one after another.
 *
 * Two layers rather than one image whose src changes: a transition needs the
 * outgoing photograph to still be on screen while the incoming one arrives, and
 * swapping src leaves a blank frame where the old one was. The layers alternate,
 * so nothing is ever mounted or unmounted mid-transition.
 *
 * The next photograph is fetched before it is needed. Without that the hold time
 * is a lie — the picture appears when the network finishes rather than when the
 * timer says, and a slow source turns a five-second slideshow into an uneven one.
 */
export default function Slideshow(props: {
  itemIds: string[];
  settings: ShowSettings;
  /** Where the photographs came from, so an endless one can go back for more. */
  folder: string;
  onClose: () => void;
}) {
  useLockScroll();

  // Grows, when it is endless. The first page arrives with the props; the rest
  // is fetched as the end comes into view, so a slideshow can run all evening
  // over a hundred thousand photographs without ever holding them all.
  const [order, setOrder] = useState<string[]>(props.itemIds);
  // Where the next page of an in-order walk begins. Meaningless when shuffling,
  // where every fetch is an independent draw.
  const offset = useRef(props.itemIds.length);
  const fetching = useRef(false);

  const [at, setAt] = useState(0);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [paused, setPaused] = useState(false);
  const [showChrome, setShowChrome] = useState(true);
  // Which layer holds the photograph now on screen, and how it arrived.
  const [layer, setLayer] = useState(0);
  const [effect, setEffect] = useState<Exclude<Transition, "random">>("fade");
  const [error, setError] = useState<string | null>(null);

  const total = order.length;

  // Always the latest close callback, depended on by nothing. The parent passes
  // an inline arrow, so the function is new on every one of its renders and any
  // effect listing it would restart on each.
  const closeRef = useRef(props.onClose);
  closeRef.current = props.onClose;

  /** A signed URL for one photograph, fetched and remembered until it expires.
   *
   * The cache is a ref as well as state. State is what draws the picture; the
   * ref is what this function reads, so that filling the cache does not change
   * the function's identity. It did, and every effect that depended on it
   * re-ran on every fetch -- including the one that owns the history entry,
   * which then wound the browser back a step for each photograph.
   *
   * Entries expire because the URLs do, after five minutes. A slideshow that
   * ran longer than that and came back to an earlier photograph would hand the
   * browser a dead URL and draw a broken image -- and on a small folder, which
   * wraps every couple of minutes, that would be every photograph.
   */
  const cache = useRef<Record<string, { url: string; at: number }>>({});
  const urlFor = useCallback(async (itemId: string): Promise<string | null> => {
    const now = Date.now();
    const held = cache.current[itemId];
    if (held && now - held.at < URL_GOOD_FOR_MS) return held.url;
    try {
      const { url } = await api.get<{ url: string }>(`/api/items/${itemId}/url`);

      // Expired entries go now rather than accumulating. They are no use to
      // anybody -- the URL behind them is already dead -- and an endless
      // slideshow would otherwise carry every photograph it had ever shown.
      for (const [id, entry] of Object.entries(cache.current)) {
        if (now - entry.at >= URL_GOOD_FOR_MS) delete cache.current[id];
      }
      cache.current[itemId] = { url, at: now };
      setUrls(() => {
        const live: Record<string, string> = {};
        for (const [id, entry] of Object.entries(cache.current)) live[id] = entry.url;
        return live;
      });
      return url;
    } catch {
      return null;
    }
  }, []);

  // The one on screen, and the one after it, warmed in the browser's cache.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const current = await urlFor(order[at]);
      if (cancelled || !current) {
        if (!cancelled && !current) setError("That photo could not be loaded.");
        return;
      }
      const following = order[(at + 1) % total];
      if (following && following !== order[at]) {
        const next = await urlFor(following);
        if (next && !cancelled) {
          const warm = new Image();
          warm.src = next;
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [at, order, total, urlFor]);

  // More photographs, when the end is in sight.
  //
  // Endless means exactly this: go back to the folder rather than loop what is
  // already here. Shuffled, each fetch is an independent draw and repeats are
  // expected; in order, it pages on, and an empty page means the folder ran out
  // and the walk starts again — a wrap-around that never had to count anything.
  useEffect(() => {
    if (!props.settings.endless || fetching.current) return;
    // Twenty photographs of warning, which at three seconds is a minute to
    // fetch in — long enough that nobody sees the join.
    if (at < order.length - 20) return;

    fetching.current = true;
    void (async () => {
      try {
        const shuffle = props.settings.shuffle;
        let page = await gatherPhotos(props.folder, {
          shuffle,
          offset: shuffle ? 0 : offset.current,
          count: false,
        });

        if (page.item_ids.length === 0 && !shuffle) {
          page = await gatherPhotos(props.folder, { offset: 0, count: false });
          offset.current = page.item_ids.length;
        } else if (!shuffle) {
          offset.current += page.item_ids.length;
        }

        if (page.item_ids.length > 0) {
          // Appended, never trimmed. Trimming the front would shift every index
          // under the one being displayed -- the queue would jump backwards
          // after a day of running, which is a worse bug than the memory it
          // saves. The identifiers are small; what actually grows is the URL
          // cache, and that prunes itself below.
          setOrder((have) => have.concat(page.item_ids));
        }
      } catch {
        // The queue keeps playing what it has. A slideshow is not the place to
        // put a network error on the wall.
      } finally {
        fetching.current = false;
      }
    })();
  }, [at, order.length, props.settings.endless, props.settings.shuffle, props.folder]);

  const step = useCallback(
    (delta: number) => {
      setError(null);
      setEffect(resolveTransition(props.settings.transition));
      setLayer((n) => 1 - n);
      setAt((i) => {
        const next = i + delta;
        // Wrapping is for a slideshow that has an end. An endless one walks
        // forward into photographs it has not fetched yet, so stopping at the
        // last known one is right — the next page is already on its way.
        if (props.settings.endless) return Math.max(0, Math.min(next, total - 1));
        return (next + total) % total;
      });
    },
    [props.settings.transition, props.settings.endless, total],
  );

  // The clock. Restarted whenever the photograph changes, so pressing next
  // gives the new one its full time rather than the remainder of the old one's.
  useEffect(() => {
    if (paused || total < 2) return;
    const timer = window.setTimeout(() => step(1), props.settings.holdMs);
    return () => window.clearTimeout(timer);
  }, [at, paused, total, props.settings.holdMs, step]);

  // Its own history entry, so back closes the slideshow rather than the folder
  // behind it — the same bargain the viewer makes.
  //
  // Mount and unmount only, and that is load-bearing rather than tidiness. This
  // had `props` in its dependencies, and `props` is a fresh object on every
  // render: the entry was pushed and the cleanup wound the browser back once
  // per render, which on a phone walked straight out of the page. It read as
  // the slideshow crashing on the first or second photograph.
  //
  // Closing goes through a ref so the effect never needs to see a new callback.
  useEffect(() => {
    let closedByBack = false;
    window.history.pushState({ slideshow: true }, "");
    const onPop = () => {
      closedByBack = true;
      closeRef.current();
    };
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      if (!closedByBack) window.history.back();
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
      else if (e.key === "ArrowRight") step(1);
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === " ") {
        e.preventDefault();
        setPaused((p) => !p);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  // The controls get out of the way of the photographs, and come back on any
  // movement. A slideshow is watched rather than operated.
  useEffect(() => {
    if (!showChrome) return;
    const hide = window.setTimeout(() => setShowChrome(false), 3000);
    return () => window.clearTimeout(hide);
  }, [showChrome, at]);

  const swipe = useSwipe(step);

  const url = urls[order[at]];
  // The layer not currently on top keeps the previous photograph, which is what
  // there is to transition away from.
  const previous = urls[order[(at - 1 + total) % total]];

  return (
    <div
      className={`slideshow fx-${effect}`}
      onMouseMove={() => setShowChrome(true)}
      onClick={() => setShowChrome(true)}
      {...swipe}
    >
      <div className={`ss-layer ${layer === 0 ? "on" : "off"}`}>
        {(layer === 0 ? url : previous) && (
          <img src={layer === 0 ? url : previous} alt="" />
        )}
      </div>
      <div className={`ss-layer ${layer === 1 ? "on" : "off"}`}>
        {(layer === 1 ? url : previous) && (
          <img src={layer === 1 ? url : previous} alt="" />
        )}
      </div>

      {error && <div className="error ss-error">{error}</div>}

      <div className={`ss-chrome${showChrome ? "" : " hidden"}`}>
        <div className="ss-top">
          <span className="muted small">
            {at + 1} of {total}
            {props.settings.shuffle && " · shuffled"}
          </span>
          <button className="v-close" onClick={props.onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="ss-controls">
          <button onClick={() => step(-1)} aria-label="Previous">
            ‹
          </button>
          <button
            onClick={() => setPaused((p) => !p)}
            aria-label={paused ? "Play" : "Pause"}
          >
            {paused ? "▶" : "⏸"}
          </button>
          <button onClick={() => step(1)} aria-label="Next">
            ›
          </button>
        </div>
      </div>
    </div>
  );
}

/** Swipe left and right, as the buttons do.
 *
 * Deliberately strict: one finger, mostly sideways, and far enough to be meant.
 * A loose threshold turns a scroll or a pinch into a change of photograph, which
 * is worse than no gesture at all because it happens while somebody is doing
 * something else.
 */
export function useSwipe(
  step: (delta: number) => void,
  /** Whether a completed gesture is ours to act on. Absent means always.
   *
   * Asked at the *end*, with the element the touch began on and how far it
   * travelled, because the interesting cases need both. A region that scrolls
   * sideways keeps the gesture while it has somewhere left to scroll, and gives
   * it up at the edge — which cannot be known when the finger lands, only when
   * it lifts and the direction is known.
   */
  mine?: (target: Element, dx: number) => boolean,
) {
  const from = useRef<{ x: number; y: number; target: Element | null } | null>(null);

  return {
    onTouchStart: (e: React.TouchEvent) => {
      from.current =
        e.touches.length === 1
          ? {
              x: e.touches[0].clientX,
              y: e.touches[0].clientY,
              target: e.target as Element | null,
            }
          : null;
    },
    onTouchMove: (e: React.TouchEvent) => {
      // A second finger means a pinch. Abandon the swipe rather than finish it
      // when the fingers lift.
      if (e.touches.length > 1) from.current = null;
    },
    onTouchEnd: (e: React.TouchEvent) => {
      const start = from.current;
      from.current = null;
      if (!start || e.changedTouches.length !== 1) return;

      const dx = e.changedTouches[0].clientX - start.x;
      const dy = e.changedTouches[0].clientY - start.y;
      if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 2) return;

      // Asked now rather than at the start: whether a scrolling region is done
      // with the gesture depends on which way it went.
      if (mine && start.target && !mine(start.target, dx)) return;

      // Left means forward, matching the direction the content moves and every
      // gallery anybody has used.
      step(dx < 0 ? 1 : -1);
    },
  };
}

/** Asking how a folder should be shown, and where.
 *
 * A separate step because the answers are worth having: five seconds suits
 * holiday photographs and thirty suits a wall, and a folder of a thousand is a
 * different thing shuffled. Defaults are chosen so that pressing straight
 * through gives something reasonable.
 */
export function SlideshowSetup(props: {
  folder: string;
  folderName: string;
  onPlayHere: (itemIds: string[], settings: ShowSettings) => void;
  onSendToRoom: (itemIds: string[], settings: ShowSettings) => void;
  onClose: () => void;
}) {
  useLockScroll();
  const [settings, setSettings] = useState<ShowSettings>(DEFAULTS);
  const [found, setFound] = useState<PhotoSet | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-gathered when shuffle changes, because shuffling is the server's job on
  // a folder this size: it samples all 105,000 rather than reordering the ten
  // thousand that happened to be sent.
  useEffect(() => {
    let cancelled = false;
    setFound(null);
    void (async () => {
      try {
        // Counted here and only here: the setup screen is the one place the
        // size of the folder is worth knowing, and an endless slideshow never
        // asks again.
        const set = await gatherPhotos(props.folder, { shuffle: settings.shuffle });
        if (!cancelled) setFound(set);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [props.folder, settings.shuffle]);


  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label="Slideshow"
         onClick={props.onClose}>
      <div className="sheet-inner" onClick={(e) => e.stopPropagation()}>
        <h2>Slideshow</h2>
        <p className="muted small">
          {props.folderName}
          {found === null && " — counting photos…"}
          {found !== null &&
            ` — ${found.total.toLocaleString()} photo${found.total === 1 ? "" : "s"}, ` +
              "including subfolders"}
        </p>

        {found?.truncated && !settings.endless && (
          <p className="muted small">
            Showing {found.count.toLocaleString()} of them
            {found.shuffled ? ", picked at random from the whole folder" : ", from the beginning"}.
            At {Math.round(settings.holdMs / 1000)} seconds each that is still{" "}
            {Math.round((found.count * settings.holdMs) / 3_600_000)} hours.
          </p>
        )}

        {error && <div className="error">{error}</div>}
        {found !== null && found.count === 0 && (
          <p className="muted">
            There are no photos in this folder or anything below it.
          </p>
        )}

        {found !== null && found.count > 0 && (
          <>
            <div className="setting">
              <h3>Plays</h3>
              <div className="seg">
                <button
                  aria-pressed={settings.endless}
                  onClick={() => setSettings((s) => ({ ...s, endless: true }))}
                >
                  Forever
                </button>
                <button
                  aria-pressed={!settings.endless}
                  onClick={() => setSettings((s) => ({ ...s, endless: false }))}
                >
                  Once through
                </button>
              </div>
              {settings.endless && (
                <p className="muted small">
                  Keeps going, fetching more from the folder as it needs them.
                  Photos will repeat eventually, which on a wall is no bad thing.
                </p>
              )}
            </div>

            <div className="setting">
              <h3>Order</h3>
              <div className="seg">
                <button
                  aria-pressed={!settings.shuffle}
                  onClick={() => setSettings((s) => ({ ...s, shuffle: false }))}
                >
                  In order
                </button>
                <button
                  aria-pressed={settings.shuffle}
                  onClick={() => setSettings((s) => ({ ...s, shuffle: true }))}
                >
                  Shuffle
                </button>
              </div>
            </div>

            <div className="setting">
              <h3>Each photo for</h3>
              <div className="seg wrap">
                {HOLDS.map((h) => (
                  <button
                    key={h.ms}
                    aria-pressed={settings.holdMs === h.ms}
                    onClick={() => setSettings((s) => ({ ...s, holdMs: h.ms }))}
                  >
                    {h.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="setting">
              <h3>Transition</h3>
              <div className="seg wrap">
                {TRANSITIONS.map((t) => (
                  <button
                    key={t.id}
                    aria-pressed={settings.transition === t.id}
                    onClick={() => setSettings((s) => ({ ...s, transition: t.id }))}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
              {settings.transition === "random" && (
                <p className="muted small">
                  A different one for each photo, so a long slideshow does not
                  settle into a rhythm.
                </p>
              )}
            </div>

            <div className="ss-start">
              <button
                className="compact primary"
                onClick={() => props.onPlayHere(found.item_ids, settings)}
              >
                ▶ Play here
              </button>
              <button
                className="compact"
                onClick={() => props.onSendToRoom(found.item_ids, settings)}
              >
                Play in a room…
              </button>
            </div>
          </>
        )}

        <button className="compact" style={{ marginTop: 18 }} onClick={props.onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}
