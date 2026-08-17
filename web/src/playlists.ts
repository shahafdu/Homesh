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
  /** Still following its .m3u, or taken over by an edit here.
   *
   *  The file is never written to — this server reads your library. So the
   *  first edit detaches the list, and a later import of the same file makes a
   *  fresh playlist beside it instead of overwriting your version. */
  linked?: boolean;
  updated_at: string;
  entries: number;
  missing: number;
  playable: number;
}

export interface Playlist extends Omit<PlaylistSummary, "entries" | "playable"> {
  entries: PlaylistEntry[];
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

export const importPlaylists = (sourceId: string) =>
  api.post<{ playlists: number; matched: number; missing: number }>(
    `/api/playlists/import/${sourceId}`,
  );
