/*
 * Where a message may lead. Kept apart from notifyApi.ts and free of imports
 * so web/test/notifyLinks.test.ts can run it under plain Node, against the
 * same cases the server's valid_path is tested with.
 */

/** An app beside Homesh, as /tv.address lists it. Only the PC's address is
 *  needed here: a link from the page opens the app where this page is. */
export interface BesideApp {
  name: string;
  url: string;
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
export function messageHref(m: { sender: string; path: string }, apps: BesideApp[]): string | null {
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
