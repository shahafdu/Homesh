import { api } from "./api";

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

/** An app beside Homesh, as /tv.address lists it. Only the PC's address is
 *  needed here: a link from the page opens the app where this page is. */
export interface BesideApp {
  name: string;
  url: string;
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

/**
 * True when `path`, appended to an address, stays inside it. The same rules as
 * `valid_path` on the server and on the phone: a link is made only from a path
 * that passes, so a message can never send somebody to another site.
 */
export function validPath(path: unknown): path is string {
  if (typeof path !== "string" || path.length < 1 || path.length > 512) return false;
  if (!path.startsWith("/") || path.includes("//") || path.includes("\\")) return false;
  // Control and invisible formatting characters, newlines included.
  if (/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(path)) return false;
  const first = path.slice(1).split("/", 1)[0];
  return !first.includes("@") && !first.includes(":");
}

/** Where a message leads, or null when it should be shown without a link. */
export function messageHref(m: Message, apps: BesideApp[]): string | null {
  if (!validPath(m.path)) return null;
  if (m.sender === "Homesh") return m.path;
  const app = apps.find((a) => a.name === m.sender);
  if (!app) return null;
  try {
    const base = new URL(app.url);
    if (base.protocol !== "http:" && base.protocol !== "https:") return null;
    const target = new URL(base.origin + base.pathname.replace(/\/+$/, "") + m.path);
    return target.origin === base.origin ? target.href : null;
  } catch {
    return null;
  }
}

/** "just now", "5 minutes ago", "3 days ago". */
export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  const unit = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"} ago`;
  if (s < 3600) return unit(Math.floor(s / 60), "minute");
  if (s < 86400) return unit(Math.floor(s / 3600), "hour");
  if (s < 86400 * 14) return unit(Math.floor(s / 86400), "day");
  return unit(Math.floor(s / (86400 * 7)), "week");
}
