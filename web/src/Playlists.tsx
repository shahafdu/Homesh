import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";
import { formatDuration, type FileEntry } from "./library";
import type { QueueOrigin } from "./player";
import {
  addToPlaylist,
  copyPlaylist,
  createPlaylist,
  deletePlaylist,
  getPlaylist,
  listPlaylists,
  removeFromPlaylist,
  renamePlaylist,
  reorderPlaylist,
  setPlaylistShared,
  type Playlist,
  type PlaylistSummary,
} from "./playlists";
import { useLockScroll } from "./useLockScroll";

const SECTIONS: { kind: string; title: string; blurb?: string }[] = [
  { kind: "mine", title: "Mine" },
  { kind: "shared", title: "Shared with the house", blurb: "Made by somebody else. Copy one to change it." },
  {
    kind: "storage",
    title: "From your library",
    blurb:
      "Imported from .m3u files. Read-only, because the files are — Homesh never " +
      "writes to your library. Copy one to make a version you can edit.",
  },
  { kind: "others", title: "Other people's", blurb: "Not shared. Visible to you as an administrator." },
];

/** Playlists: the ones made here and the ones imported from the library.
 *
 * Forty-one of these came out of .m3u files written years ago. The ordering in
 * them is the part worth keeping — a scanner can find every track in the house
 * but not the sequence somebody chose.
 */
export default function Playlists(props: {
  /** A playlist to open straight away — the player bar points here. */
  openId?: string | null;
  /** What is playing, so the list can mark it. Opening a playlist while it is
   *  playing and finding no sign of which track is on makes the list useless
   *  for the one thing it is opened for: choosing the next one. */
  playingId?: string | null;
  onPlay: (files: FileEntry[], index: number, origin: QueueOrigin) => void;
  /** Send the whole list to a room, using the same picker a file uses. */
  onSendTo: (files: FileEntry[]) => void;
  onClose: () => void;
}) {
  useLockScroll();
  const [lists, setLists] = useState<PlaylistSummary[] | null>(null);
  const [open, setOpen] = useState<Playlist | null>(null);
  const [creating, setCreating] = useState(false);
  // Only your own are open to begin with: forty-one imported lists above two of
  // yours is a wall to scroll past every time.
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ mine: true });
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLists(await listPlaylists());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Opened directly at one list, which is what the player bar asks for: while a
  // playlist is playing, the way back to it should land in it rather than in a
  // list of everything with the one you want somewhere inside.
  useEffect(() => {
    if (!props.openId) return;
    void getPlaylist(props.openId).then(setOpen).catch(() => undefined);
  }, [props.openId]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
    await refresh();
    if (open) setOpen(await getPlaylist(open.id).catch(() => null));
  };

  if (open) {
    return (
      <PlaylistDetail
        playlist={open}
        error={error}
        onBack={() => setOpen(null)}
        onClose={props.onClose}
        onPlay={props.onPlay}
        onSendTo={props.onSendTo}
        playingId={props.playingId ?? null}
        onAct={act}
      />
    );
  }

  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label="Playlists"
         onClick={props.onClose}>
      <div className="sheet-inner wide" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h2>Playlists</h2>
          <button className="compact" onClick={() => setCreating(true)}>＋ New</button>
        </div>

        {error && <div className="error">{error}</div>}
        {lists === null && <p className="muted">Loading…</p>}

        {creating && (
          <div className="zone-card">
            <label htmlFor="pl-name">Name</label>
            <input
              id="pl-name"
              value={draft}
              autoFocus
              placeholder="Saturday morning"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && draft.trim()) {
                  void act(() => createPlaylist(draft.trim()));
                  setDraft("");
                  setCreating(false);
                }
              }}
            />
            <div className="zone-controls" style={{ marginTop: 10 }}>
              <button
                className="compact primary"
                disabled={!draft.trim()}
                onClick={() => {
                  void act(() => createPlaylist(draft.trim()));
                  setDraft("");
                  setCreating(false);
                }}
              >
                Create
              </button>
              <button className="compact" onClick={() => setCreating(false)}>Cancel</button>
            </div>
          </div>
        )}

        {lists?.length === 0 && !creating && (
          <p className="muted">
            No playlists yet. Make one here, or import the .m3u files already in your
            library from Settings.
          </p>
        )}

        {/* Four kinds, governed differently. A single flat list hid that, and
            with forty-one imported lists it also made your own two impossible to
            find. */}
        {SECTIONS.map(({ kind, title, blurb }) => {
          const group = (lists ?? []).filter((l) => l.kind === kind);
          if (group.length === 0) return null;
          const isOpen = expanded[kind] ?? kind === "mine";

          return (
            <section key={kind} className="pl-section">
              <button
                className="pl-section-head"
                aria-expanded={isOpen}
                onClick={() => setExpanded({ ...expanded, [kind]: !isOpen })}
              >
                <span className="pl-caret">{isOpen ? "▾" : "▸"}</span>
                {title}
                <span className="muted small">{group.length}</span>
              </button>

              {isOpen && (
                <>
                  {blurb && <p className="muted small pl-blurb">{blurb}</p>}
                  {group.map((list) => (
                    <button
                      key={list.id}
                      className="playlist-row"
                      onClick={async () => setOpen(await getPlaylist(list.id))}
                    >
                      <span className="pl-name">
                        {list.name}
                        {list.kind === "mine" && list.shared && (
                          <span className="badge">shared</span>
                        )}
                        {list.read_only && <span className="badge">read-only</span>}
                      </span>
                      <span className="muted small">
                        {list.playable} track{list.playable === 1 ? "" : "s"}
                        {/* Said plainly: these lists are decades old and some of
                            what they point at is genuinely gone. */}
                        {list.missing > 0 && ` · ${list.missing} missing`}
                        {list.kind !== "mine" && list.owner && ` · ${list.owner}`}
                      </span>
                    </button>
                  ))}
                </>
              )}
            </section>
          );
        })}

        <button className="compact" style={{ marginTop: 16 }} onClick={props.onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

function PlaylistDetail(props: {
  playlist: Playlist;
  error: string | null;
  onBack: () => void;
  onClose: () => void;
  onPlay: (files: FileEntry[], index: number, origin: QueueOrigin) => void;
  onSendTo: (files: FileEntry[]) => void;
  playingId: string | null;
  onAct: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const { playlist } = props;
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(playlist.name);

  // Only the tracks that resolved can be played, and they are what the queue is
  // built from — a missing line has nothing to hand a player.
  const playable = playlist.entries.filter((e) => !e.missing && e.item_id);
  const origin = (): QueueOrigin => ({
    kind: "playlist",
    id: playlist.id,
    label: playlist.name,
  });
  const asFiles = (): FileEntry[] =>
    playable.map((e) => ({
      item_id: e.item_id as string,
      filename: e.filename ?? e.raw_title ?? "",
      ext: null,
      kind: "audio",
      size: null,
      duration_ms: e.duration_ms,
      meta: { title: e.title ?? undefined, artist: e.artist ?? undefined },
      mtime: null,
      available: e.available,
    }));

  // ── Reordering by dragging ────────────────────────────────────────────────
  //
  // Pointer events rather than HTML5 drag-and-drop, which does not fire on
  // touch at all — and this list is mostly used on a phone. The handle is what
  // starts a drag, so an ordinary swipe still scrolls the page.
  //
  // `dragging` is the local order while a drag is in progress. The server is
  // told once, on release: a reorder per crossed row would be a request per
  // frame of a gesture.
  const [dragging, setDragging] = useState<string[] | null>(null);
  const [held, setHeld] = useState<string | null>(null);

  const order = dragging ?? playlist.entries.map((e) => e.entry_id);
  const byId = new Map(playlist.entries.map((e) => [e.entry_id, e]));
  const shown = order.map((id) => byId.get(id)).filter((e) => e !== undefined);

  const startDrag = (entryId: string) => (event: React.PointerEvent) => {
    if (playlist.read_only) return;
    event.preventDefault();
    const handle = event.currentTarget as HTMLElement;
    handle.setPointerCapture(event.pointerId);

    const list = handle.closest("ol") as HTMLOListElement | null;
    if (!list) return;

    setHeld(entryId);
    let current = [...order];

    const onMove = (move: PointerEvent) => {
      // Which row is under the finger, by geometry rather than by hit-testing:
      // the row being dragged sits under the pointer and would swallow it.
      const rows = [...list.children] as HTMLElement[];
      const over = rows.findIndex((row) => {
        const box = row.getBoundingClientRect();
        return move.clientY >= box.top && move.clientY <= box.bottom;
      });
      const from = current.indexOf(entryId);
      if (over < 0 || over === from) return;

      const next = [...current];
      next.splice(over, 0, ...next.splice(from, 1));
      current = next;
      setDragging(next);
    };

    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      setHeld(null);

      const before = playlist.entries.map((e) => e.entry_id);
      if (current.join() === before.join()) {
        setDragging(null);
        return;
      }
      // Kept until the reload lands, so the list does not snap back to the old
      // order for the moment the round trip takes.
      void props.onAct(() => reorderPlaylist(playlist.id, current)).then(() =>
        setDragging(null),
      );
    };

    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  };

  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label={playlist.name}
         onClick={props.onClose}>
      <div className="sheet-inner wide" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <button className="compact" onClick={props.onBack}>← All playlists</button>
        </div>

        <div className="zone-head">
          {renaming ? (
            <input
              className="zone-rename"
              value={draft}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && draft.trim()) {
                  void props.onAct(() => renamePlaylist(playlist.id, draft.trim()));
                  setRenaming(false);
                }
                if (e.key === "Escape") setRenaming(false);
              }}
            />
          ) : (
            <h2 className="pl-title">{playlist.name}</h2>
          )}
        </div>

        {props.error && <div className="error">{props.error}</div>}

        <div className="zone-controls">
          <button
            className="compact primary"
            disabled={playable.length === 0}
            onClick={() => {
              props.onPlay(asFiles(), 0, origin());
              props.onClose();
            }}
          >
            ▶ Play
          </button>
          {/* The same picker a single file uses, given the whole list. A
              playlist that can only be played in the room you are standing in
              is half a playlist — and this receiver is the reason the feature
              exists at all. */}
          <button
            className="compact"
            disabled={playable.length === 0}
            onClick={() => {
              props.onSendTo(asFiles());
              props.onClose();
            }}
          >
            ⧉ Play in a room
          </button>
          {/* Copying is always available, and is the answer whenever the rest
              is not: a list you cannot change is one you can take a copy of. */}
          <button
            className="compact"
            onClick={async () => {
              await props.onAct(() => copyPlaylist(playlist.id));
              props.onBack();
            }}
          >
            Make a copy
          </button>

          {!playlist.read_only && (
            <>
              <button
                className="compact"
                onClick={() => { setDraft(playlist.name); setRenaming(true); }}
              >
                Rename
              </button>
              <button
                className="compact"
                aria-pressed={playlist.shared}
                onClick={() =>
                  void props.onAct(() => setPlaylistShared(playlist.id, !playlist.shared))
                }
              >
                {playlist.shared ? "Stop sharing" : "Share with the house"}
              </button>
              <button
                className="compact"
                onClick={async () => {
                  await props.onAct(() => deletePlaylist(playlist.id));
                  props.onBack();
                }}
              >
                Delete
              </button>
            </>
          )}
        </div>

        {playlist.read_only && (
          <p className="muted small">
            {playlist.imported_from ? (
              <>
                Imported from <code className="nm-clip">{playlist.imported_from}</code> and
                read-only, because Homesh never writes to your library. Make a copy to
                change it.
              </>
            ) : (
              <>Shared by {playlist.owner}. Make a copy to change it.</>
            )}
          </p>
        )}

        {playlist.missing > 0 && (
          <p className="muted small">
            {playlist.missing} of these could not be found in your library. They are
            kept so you can see what is gone rather than wondering what was lost.
          </p>
        )}

        <ol className="pl-entries">
          {shown.map((entry, index) => (
            <li
              key={entry.entry_id}
              className={`${entry.missing ? "missing" : ""}${
                held === entry.entry_id ? " held" : ""
              }${entry.item_id && entry.item_id === props.playingId ? " nowplaying" : ""}`}
            >
              {/* Marked, not merely coloured: a glyph is readable to everyone
                  and survives whatever palette is chosen. */}
              <span className="pl-pos">
                {entry.item_id && entry.item_id === props.playingId ? "▶" : index + 1}
              </span>

              <span className="pl-track">
                {entry.missing ? (
                  <>
                    <span className="nm-clip">{entry.raw_title ?? entry.raw_path}</span>
                    <span className="muted small">not found</span>
                  </>
                ) : (
                  <>
                    <button
                      className="pl-play"
                      onClick={() =>
                        props.onPlay(
                          asFiles(),
                          Math.max(0, playable.findIndex((p) => p.entry_id === entry.entry_id)),
                          origin(),
                        )
                      }
                    >
                      {entry.title ?? entry.filename}
                    </button>
                    {entry.artist && <span className="muted small">{entry.artist}</span>}
                  </>
                )}
              </span>

              <span className="muted small">{formatDuration(entry.duration_ms)}</span>

              <span className="pl-actions">
                {playlist.read_only ? null : (
                  <>
                    {/* The handle, not the row: dragging anywhere would make the
                        list impossible to scroll on a phone. Also a keyboard
                        control, because a drag handle alone cannot be operated
                        without a pointer. */}
                    <button
                      className="pl-grip"
                      title="Drag to reorder — or use the arrow keys"
                      aria-label={`Reorder ${entry.title ?? entry.filename ?? "track"}, position ${
                        index + 1
                      } of ${shown.length}`}
                      onPointerDown={startDrag(entry.entry_id)}
                      onKeyDown={(e) => {
                        const delta = e.key === "ArrowUp" ? -1 : e.key === "ArrowDown" ? 1 : 0;
                        if (!delta) return;
                        e.preventDefault();
                        const target = index + delta;
                        if (target < 0 || target >= order.length) return;
                        const next = [...order];
                        next.splice(target, 0, ...next.splice(index, 1));
                        setDragging(next);
                        void props
                          .onAct(() => reorderPlaylist(playlist.id, next))
                          .then(() => setDragging(null));
                      }}
                    >
                      ☰
                    </button>
                    <button className="iconbtn tiny" title="Remove" aria-label="Remove"
                            onClick={() =>
                              void props.onAct(() => removeFromPlaylist(playlist.id, entry.entry_id))
                            }>×</button>
                  </>
                )}
              </span>
            </li>
          ))}
        </ol>

        <button className="compact" style={{ marginTop: 16 }} onClick={props.onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

/** Choosing a playlist to add a file to, from the file's own menu. */
export function AddToPlaylist(props: {
  file: FileEntry;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [lists, setLists] = useState<PlaylistSummary[] | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listPlaylists().then(setLists).catch(() => setLists([]));
  }, []);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      props.onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  return (
    <div className="zone-card">
      <div className="zone-head"><span className="zone-name">Add to a playlist</span></div>

      <div className="ticks">
        {lists?.map((list) => (
          <button
            key={list.id}
            className="tick"
            onClick={() => run(() => addToPlaylist(list.id, [props.file.item_id]))}
          >
            {list.name}
          </button>
        ))}
        {lists?.length === 0 && <span className="muted small">No playlists yet.</span>}
      </div>

      <label htmlFor="new-pl">Or start a new one</label>
      <input
        id="new-pl"
        value={name}
        placeholder="Saturday morning"
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && name.trim()) {
            void run(() => createPlaylist(name.trim(), [props.file.item_id]));
          }
        }}
      />

      {error && <div className="error">{error}</div>}

      <div className="zone-controls" style={{ marginTop: 10 }}>
        <button
          className="compact primary"
          disabled={!name.trim()}
          onClick={() => run(() => createPlaylist(name.trim(), [props.file.item_id]))}
        >
          Create and add
        </button>
        <button className="compact" onClick={props.onCancel}>Cancel</button>
      </div>
    </div>
  );
}
