import { api } from "./api";

export type Kind = "audio" | "video" | "photo" | "doc" | "other";

export interface FileEntry {
  item_id: string;
  /** Always present, always shown. Metadata may add to it, never replace it. */
  filename: string;
  ext: string | null;
  kind: Kind;
  size: number | null;
  mtime: string | null;
  available: boolean;
}

export interface DirEntry {
  name: string;
  path: string;
}

export interface Listing {
  path: string;
  parent: string | null;
  dirs: DirEntry[];
  files: FileEntry[];
}

export interface SearchHit {
  item_id: string;
  filename: string;
  path: string;
  kind: Kind;
  size: number | null;
  available: boolean;
}

export interface Source {
  id: string;
  kind: string;
  name: string;
  mount_prefix: string;
  last_seen_at: string | null;
  files: number;
}

export const browse = (path: string) =>
  api.get<Listing>(`/api/browse?path=${encodeURIComponent(path)}`);

export const search = (q: string) =>
  api.get<SearchHit[]>(`/api/search?q=${encodeURIComponent(q)}`);

export const listSources = () => api.get<Source[]>("/api/sources");

/** A short-lived URL that saves to the device instead of displaying.
 *
 * Only the Content-Disposition differs — the bytes are the same, so this grants
 * nothing extra. It is what makes formats we cannot preview (spreadsheets,
 * presentations) still useful, and lets you keep a copy of anything.
 */
export async function downloadUrl(itemId: string): Promise<string> {
  const { url } = await api.get<{ url: string }>(`/api/items/${itemId}/url`);
  return `${url}&download=1`;
}

/** Formats the browser can display. Anything else is offered as a download. */
export function canPreview(kind: Kind, ext: string | null): boolean {
  if (kind === "photo" || kind === "video" || kind === "audio") return true;
  return ["pdf", "txt", "md"].includes((ext ?? "").toLowerCase());
}

export const scanSource = (id: string) => api.post(`/api/sources/${id}/scan`);

export function formatSize(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** Breadcrumb segments, each with the path that navigates to it.
 *
 * A source's mount prefix collapses into one crumb bearing the source's name.
 * Splitting "/local/library" into "local" then "library" would render crumbs that
 * navigate nowhere — nothing is mounted at "/local".
 */
export function crumbs(
  path: string,
  sources: Pick<Source, "name" | "mount_prefix">[],
): { label: string; path: string }[] {
  const out = [{ label: "All sources", path: "/" }];
  if (path === "/") return out;

  const src = sources.find(
    (s) => path === s.mount_prefix || path.startsWith(s.mount_prefix + "/"),
  );

  // Before sources have loaded, fall back to raw segments rather than rendering
  // nothing — they resolve correctly once the fetch lands.
  const base = src ? src.mount_prefix : "";
  if (src) out.push({ label: src.name, path: src.mount_prefix });

  let acc = base;
  for (const part of path.slice(base.length).split("/").filter(Boolean)) {
    acc += `/${part}`;
    out.push({ label: part, path: acc });
  }
  return out;
}
