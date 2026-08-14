import { api } from "./api";

export interface ZoneRenderer {
  kind: "tvapp" | "heos" | "cast" | "browser";
  state: "ready" | "asleep" | "unavailable";
  name: string | null;
}

export interface ZoneSession {
  state: "idle" | "playing" | "paused" | "buffering";
  queue_length: number;
  cursor: number;
  current_item: string | null;
  position_ms: number | null;
  volume: number | null;
  updated_at: string | null;
}

export interface Zone {
  id: string;
  name: string;
  renderer: ZoneRenderer | null;
  session: ZoneSession | null;
}

export const listZones = () => api.get<Zone[]>("/api/zones");

export const playInZone = (zoneId: string, itemIds: string[], startIndex = 0) =>
  api.post<{ zone: string; state: string; pushed: boolean }>(
    `/api/zones/${zoneId}/play`,
    { item_ids: itemIds, start_index: startIndex },
  );

export const stopZone = (zoneId: string) => api.post(`/api/zones/${zoneId}/stop`);

export const setZoneVolume = (zoneId: string, level: number) =>
  api.post(`/api/zones/${zoneId}/volume`, { level });

export const pairDevice = (code: string, name: string) =>
  api.post<{ renderer_id: string; name: string }>("/api/renderers/pair/claim", { code, name });

/** What a zone can be sent, so the UI never offers something that will fail.
 *
 * The receiver is audio-only — ZONE2 carries no picture — so offering it a film
 * would be an invitation to a failure we already know about.
 */
export function zoneAccepts(zone: Zone, kind: string): boolean {
  if (!zone.renderer) return false;
  if (zone.renderer.kind === "heos") return kind === "audio";
  return ["audio", "video", "photo"].includes(kind);
}

export function zoneStatus(zone: Zone): { label: string; tone: "live" | "idle" | "off" } {
  if (!zone.renderer) return { label: "no device", tone: "off" };
  if (zone.renderer.state === "unavailable") {
    // A screen that is not connected is a different thing from a receiver that is
    // merely idle: one needs the app opened, the other is simply not playing.
    return {
      label: zone.renderer.kind === "tvapp" ? "app not open" : "unavailable",
      tone: "off",
    };
  }
  const state = zone.session?.state;
  if (state === "playing") return { label: "playing", tone: "live" };
  if (state === "paused") return { label: "paused", tone: "idle" };
  if (state === "buffering") return { label: "starting…", tone: "idle" };
  return { label: "ready", tone: "idle" };
}
