import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";
import { formatDuration, type FileEntry } from "./library";
import {
  addToPlaylist,
  createPlaylist,
  deletePlaylist,
  getPlaylist,
  listPlaylists,
  removeFromPlaylist,
  renamePlaylist,
  reorderPlaylist,
  type Playlist,
  type PlaylistSummary,
} from "./playlists";
import { useLockScroll } from "./useLockScroll";

/** Playlists: the ones made here and the ones imported from the library.
 *
 * Forty-one of these came out of .m3u files written years ago. The ordering in
 * them is the part worth keeping — a scanner can find every track in the house
 * but not the sequence somebody chose.
 */
export default function Playlists(props: {
  onPlay: (files: FileEntry[], index: number) => void;
  onClose: () => void;
}) {
  useLockScroll();
  const [lists, setLists] = useState<PlaylistSummary[] | null>(null);
  const [open, setOpen] = useState<Playlist | null>(null);
  const [creating, setCreating] = useState(false);
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

        {lists?.map((list) => (
          <button
            key={list.id}
            className="playlist-row"
            onClick={async () => setOpen(await getPlaylist(list.id))}
          >
            <span className="pl-name">{list.name}</span>
            <span className="muted small">
              {list.playable} track{list.playable === 1 ? "" : "s"}
              {/* Said plainly rather than hidden: these lists are decades old and
                  some of what they point at is genuinely gone. */}
              {list.missing > 0 && ` · ${list.missing} missing`}
              {list.imported_from && (list.linked ? " · imported" : " · imported, edited here")}
            </span>
          </button>
        ))}

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
  onPlay: (files: FileEntry[], index: number) => void;
  onAct: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const { playlist } = props;
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(playlist.name);

  // Only the tracks that resolved can be played, and they are what the queue is
  // built from — a missing line has nothing to hand a player.
  const playable = playlist.entries.filter((e) => !e.missing && e.item_id);
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

  const move = (index: number, delta: number) => {
    const order = playlist.entries.map((e) => e.entry_id);
    const target = index + delta;
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    void props.onAct(() => reorderPlaylist(playlist.id, order));
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
              props.onPlay(asFiles(), 0);
              props.onClose();
            }}
          >
            ▶ Play
          </button>
          <button className="compact" onClick={() => { setDraft(playlist.name); setRenaming(true); }}>
            Rename
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
        </div>

        {playlist.imported_from && (
          <p className="muted small">
            Imported from <code className="nm-clip">{playlist.imported_from}</code>.
            Editing it here never changes that file — Homesh only ever reads your
            library. Your version becomes separate from it.
          </p>
        )}

        {playlist.missing > 0 && (
          <p className="muted small">
            {playlist.missing} of these could not be found in your library. They are
            kept so you can see what is gone rather than wondering what was lost.
          </p>
        )}

        <ol className="pl-entries">
          {playlist.entries.map((entry, index) => (
            <li key={entry.entry_id} className={entry.missing ? "missing" : ""}>
              <span className="pl-pos">{index + 1}</span>

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
                <button className="iconbtn tiny" title="Move up" aria-label="Move up"
                        disabled={index === 0} onClick={() => move(index, -1)}>↑</button>
                <button className="iconbtn tiny" title="Move down" aria-label="Move down"
                        disabled={index === playlist.entries.length - 1}
                        onClick={() => move(index, 1)}>↓</button>
                <button className="iconbtn tiny" title="Remove" aria-label="Remove"
                        onClick={() =>
                          void props.onAct(() => removeFromPlaylist(playlist.id, entry.entry_id))
                        }>×</button>
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
