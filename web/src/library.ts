import { api } from "./api";

export type Kind = "audio" | "video" | "photo" | "doc" | "other";

export interface FileMeta {
  title?: string;
  artist?: string;
  album?: string;
  albumartist?: string;
}

export interface FileEntry {
  item_id: string;
  /** Always present, always shown. Metadata may add to it, never replace it. */
  filename: string;
  ext: string | null;
  kind: Kind;
  size: number | null;
  duration_ms?: number | null;
  /** Additive. Absent or partial is normal — plenty of files have no tags. */
  meta?: FileMeta;
  mtime: string | null;
  available: boolean;
}

export function formatDuration(ms: number | null | undefined): string {
  if (!ms || ms < 0) return "";
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0
    ? `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
    : `${m}:${s.toString().padStart(2, "0")}`;
}

/** The tag line shown beneath a filename: "Title · Artist · Album".
 *
 * Returns null when there is nothing worth showing, so a file with no tags gets
 * no empty second line.
 */
export function tagLine(meta: FileMeta | undefined): string | null {
  if (!meta) return null;
  const parts = [meta.title, meta.artist ?? meta.albumartist, meta.album].filter(
    (p): p is string => Boolean(p && p.trim()),
  );
  return parts.length ? parts.join(" · ") : null;
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

export interface ScanStatus {
  /** null means it has never run — which is a different thing from empty, and
   *  looked identical before. */
  state: "running" | "done" | "failed" | null;
  seen: number;
  added: number;
  error: string | null;
  started_at: string | null;
}

export interface Source {
  id: string;
  kind: string;
  name: string;
  mount_prefix: string;
  last_seen_at: string | null;
  files: number;
  scan: ScanStatus;
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

/** Office formats the server renders to PDF on request.
 *
 * No browser can display these, and no browser ever will — they need a layout
 * engine rather than a parser. The server converts them so the file never has to
 * leave the house to be read.
 */
const CONVERTIBLE = new Set([
  "doc", "docx", "odt", "rtf", "dot", "dotx", "wpd",
  "xls", "xlsx", "ods", "csv", "xlsm", "xlt", "xltx",
  "ppt", "pptx", "odp", "pps", "ppsx", "pot", "potx",
  "abw", "sxw", "fodt", "fods",
]);

export function needsConversion(ext: string | null): boolean {
  return CONVERTIBLE.has((ext ?? "").toLowerCase());
}

/** Formats the viewer can show, directly or after conversion. */
export function canPreview(kind: Kind, ext: string | null): boolean {
  if (kind === "photo" || kind === "video" || kind === "audio") return true;
  const e = (ext ?? "").toLowerCase();
  return ["pdf", "txt", "md", "log", "json", "xml", "csv"].includes(e) || needsConversion(ext);
}

/** Where the viewer should point for a document.
 *
 * Converted formats come from their own endpoint; a PDF is served as itself.
 */
export const documentUrl = (itemId: string) => `/api/documents/${itemId}`;

export const scanSource = (id: string) => api.post(`/api/sources/${id}/scan`);

/** Look for folders shared with this server since it started.
 *
 * Sharing a folder with the Homesh account is how a folder is added — there is
 * nothing to upload and no path to type. Discovery ran only at startup, so a
 * folder shared this afternoon stayed invisible with nothing to say why.
 */
export const discoverSources = () =>
  api.post<{ added: string[]; total: number }>("/api/sources/discover");

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


/** What the details view can be ordered by. */
export type SortKey = "name" | "title" | "artist" | "album" | "duration" | "size" | "date";

export interface Sort {
  key: SortKey;
  desc: boolean;
}

/** Numeric-aware, locale-aware comparison.
 *
 * "track2" before "track10", and Hebrew sorted as Hebrew — the same rule the
 * server's natsort collation applies, so re-sorting in the browser cannot
 * disagree with the order the server sent.
 */
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

function field(f: FileEntry, key: SortKey): string | number | null {
  switch (key) {
    case "name":
      return f.filename;
    case "title":
      return f.meta?.title ?? null;
    case "artist":
      return f.meta?.artist ?? f.meta?.albumartist ?? null;
    case "album":
      return f.meta?.album ?? null;
    case "duration":
      return f.duration_ms ?? null;
    case "size":
      return f.size ?? null;
    case "date":
      return f.mtime ?? null;
  }
}

export function sortFiles(files: FileEntry[], sort: Sort): FileEntry[] {
  const sorted = [...files].sort((a, b) => {
    const x = field(a, sort.key);
    const y = field(b, sort.key);

    // Files with no tag sink to the bottom whichever way the column is sorted.
    // Flipping them to the top on a reverse sort would bury everything that
    // actually has the value you asked to sort by.
    if (x === null && y === null) return collator.compare(a.filename, b.filename);
    if (x === null) return 1;
    if (y === null) return -1;

    const cmp =
      typeof x === "number" && typeof y === "number"
        ? x - y
        : collator.compare(String(x), String(y));
    return sort.desc ? -cmp : cmp;
  });
  return sorted;
}


/** A search hit as a file the rest of the app already knows how to handle.
 *
 * Search returns less than a listing does — no tags, no duration — but a result
 * you cannot play, download or send anywhere is a dead end, and finding
 * something in order to then go and find it again is not searching.
 */
export function hitAsFile(hit: SearchHit): FileEntry {
  return {
    item_id: hit.item_id,
    filename: hit.filename,
    ext: hit.filename.includes(".") ? hit.filename.split(".").pop()! : null,
    kind: hit.kind,
    size: hit.size,
    duration_ms: null,
    meta: undefined,
    mtime: null,
    available: hit.available,
  };
}


/** Containers whose video no browser can decode.
 *
 * MPEG-2 — a DVD rip, an HDV camcorder tape — has no decoder in any current
 * browser. It is not a setting or a missing plugin; the codec was never shipped.
 * A television box usually decodes it in hardware, which is why sending it to a
 * room works when watching it here does not.
 */
const UNDECODABLE = new Set([
  "m2t", "mts", "vob", "mpg", "mpeg", "m1v", "m2v", "mod", "tod", "dv", "mxf",
  "wmv", "avi", "asf", "divx", "xvid", "rm", "rmvb", "flv",
  "3gp", "3g2",
]);

export function needsVideoConversion(ext: string | null): boolean {
  return UNDECODABLE.has((ext ?? "").toLowerCase());
}

export interface Conversion {
  needed: boolean;
  state: "queued" | "running" | "done" | "failed" | null;
  progress: number;
  error?: string | null;
}

export const conversionStatus = (itemId: string) =>
  api.get<Conversion>(`/api/videos/${itemId}/conversion`);

export const startConversion = (itemId: string) =>
  api.post<Conversion>(`/api/videos/${itemId}/conversion`);

export const convertedUrl = (itemId: string) => `/api/videos/${itemId}/converted`;

/** Transcoded as it plays, so watching starts now and nothing is stored.
 *
 * The trade is that there is no index to seek in — the file does not exist yet —
 * so moving through it means starting again at a different point, which is what
 * `start` is for.
 */
export const liveVideoUrl = (itemId: string, start = 0) =>
  `/api/videos/${itemId}/live.mp4${start ? `?start=${Math.floor(start)}` : ""}`;
