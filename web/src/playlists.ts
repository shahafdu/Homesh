import { api } from "./api";

/** One track in a list.
 *
 * `missing` is a first-class state, not an error: an imported list keeps every
 * line it came with, including the ones whose file could not be found. A list
 * that quietly loses four tracks is worse than one that says four are missing.
 */
export interface PlaylistEntry {
  entry_id: string;
  item_id: string | null;
  filename: string | null;
  raw_path: string | null;
  raw_title: string | null;
  title: string | null;
  artist: string | null;
  duration_ms: number | null;
  available: boolean;
  missing: boolean;
}

export interface PlaylistSummary {
  id: string;
  name: string;
  owner: string | null;
  mine: boolean;
  imported_from: string | null;
  /** Which of the four kinds this is, which decides how it may be treated.
   *
   *  storage — from a .m3u in the library. Read-only for everyone, because the
   *            file is: this server never writes to your library, and the next
   *            import would undo an edit without saying so.
   *  mine    — yours to change.
   *  shared  — somebody else's, offered to the house. Play only.
   *  others  — somebody else's and not shared. Administrators only. */
  kind: "mine" | "shared" | "others" | "storage";
  shared: boolean;
  /** Decided by the server, so the button shown and the answer given agree. */
  read_only: boolean;
  updated_at: string;
  entries: number;
  missing: number;
  playable: number;
}

export interface Playlist extends Omit<PlaylistSummary, "entries" | "playable"> {
  entries: PlaylistEntry[];
  missing: number;
}

export const listPlaylists = () => api.get<PlaylistSummary[]>("/api/playlists");

export const getPlaylist = (id: string) => api.get<Playlist>(`/api/playlists/${id}`);

export const createPlaylist = (name: string, itemIds: string[] = []) =>
  api.post<{ id: string; name: string }>("/api/playlists", { name, item_ids: itemIds });

export const renamePlaylist = (id: string, name: string) =>
  api.put(`/api/playlists/${id}`, { name });

export const deletePlaylist = (id: string) => api.delete(`/api/playlists/${id}`);

export const addToPlaylist = (id: string, itemIds: string[]) =>
  api.post<{ added: number }>(`/api/playlists/${id}/items`, { item_ids: itemIds });

export const removeFromPlaylist = (id: string, entryId: string) =>
  api.delete(`/api/playlists/${id}/items/${entryId}`);

/** The whole order, not a move.
 *
 * The server rejects an ordering that does not account for every entry, so a
 * stale copy cannot silently drop the rows it had not heard about.
 */
export const reorderPlaylist = (id: string, entryIds: string[]) =>
  api.put(`/api/playlists/${id}/order`, { entry_ids: entryIds });

/** A copy you own and can edit — the way to change a list that is not yours. */
export const copyPlaylist = (id: string, name?: string) =>
  api.post<{ id: string; name: string }>(`/api/playlists/${id}/copy`, { name });

/** Sharing grants playing, never editing. */
export const setPlaylistShared = (id: string, shared: boolean) =>
  api.put(`/api/playlists/${id}/share`, { shared });

export const importPlaylists = (sourceId: string) =>
  api.post<{ playlists: number; matched: number; missing: number }>(
    `/api/playlists/import/${sourceId}`,
  );
