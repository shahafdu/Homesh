import { api } from "./api";

/** A copy of the database, taken at a moment. */
export interface Backup {
  name: string;
  taken_at: string;
  size_bytes: number;
}

export const listBackups = () =>
  api.get<{ backups: Backup[] }>("/api/backups").then((r) => r.backups);

export const takeBackup = () => api.post<Backup>("/api/backups");

export const removeBackup = (name: string) =>
  api.delete(`/api/backups/${encodeURIComponent(name)}`);

export interface Restored {
  restored: string;
  rows: number;
  previous_state_saved_as: string;
}

export const restoreBackup = (name: string) =>
  api.post<Restored>(`/api/backups/${encodeURIComponent(name)}/restore`);

/** Where to fetch a copy from, to keep somewhere this machine is not.
 *
 * The one thing that makes a backup worth having is a copy on different
 * hardware, and nothing here can put one there — so at least it is one tap to
 * take one away. */
export const backupUrl = (name: string) =>
  `/api/backups/${encodeURIComponent(name)}`;
