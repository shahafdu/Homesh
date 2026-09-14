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

/** A backup kept somewhere this house is not. */
export interface OffsiteBackup {
  name: string;
  id: string;
  size_bytes: number;
  taken_at: string | null;
}

export interface Offsite {
  /** False when there is no key, no credential, or no shared folder — `why`
   *  says which, in words meant for the person who has to fix it. */
  ready: boolean;
  why: string;
  backups: OffsiteBackup[];
}

export const listOffsite = () => api.get<Offsite>("/api/backups/offsite");

/** Bring one back down and decrypt it onto the local shelf. It stops there:
 *  restoring is the same button as for any other backup. */
export const retrieveOffsite = (name: string) =>
  api.post<{ name: string }>(`/api/backups/offsite/${encodeURIComponent(name)}`);
