import { api } from "./api";
import type { BesideApp } from "./notifyLinks";

export { ago, messageHref, validPath, type BesideApp } from "./notifyLinks";

export interface Message {
  id: number;
  at: string;
  sender: string;
  title: string;
  body: string;
  path: string;
  read: boolean;
}

export interface Phone {
  id: string;
  label: string;
  created_at: string;
  last_seen_at: string | null;
}

export const listMessages = () => api.get<Message[]>("/api/notify/messages?limit=50");
export const markRead = (upto: number) =>
  api.post<{ marked: number }>("/api/notify/messages/read", { upto });
export const listPhones = () => api.get<Phone[]>("/api/notify/devices");
export const removePhone = (id: string) => api.delete<void>(`/api/notify/devices/${id}`);
export const sendTest = () => api.post<{ queued: number }>("/api/notify/test");
export const newClaim = (label: string) =>
  api.post<{ claim: string; expires_in: number }>("/api/notify/devices", { label });
export const beside = () =>
  api.get<{ apps?: BesideApp[] }>("/tv.address").then((r) => r.apps ?? []);

/** The deep link that hands a claim code to Homesh Connect. */
export function claimLink(claim: string): string {
  const q = new URLSearchParams({ claim, origin: window.location.origin });
  return `homeshconnect://notify?${q.toString()}`;
}
