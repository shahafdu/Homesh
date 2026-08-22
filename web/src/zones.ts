import { api } from "./api";

export interface ZoneRenderer {
  kind: "tvapp" | "heos" | "cast" | "browser";
  state: "ready" | "asleep" | "unavailable";
  name: string | null;
}

/** What is playing, as opposed to where it sits in a queue. */
export interface NowPlaying {
  /** Always present. A tag can be missing or wrong; the filename is neither. */
  filename: string;
  title: string | null;
  artist: string | null;
  /** What a position bar is a fraction of. Null for a live stream. */
  duration_ms: number | null;
}

export interface ZoneSession {
  state: "idle" | "playing" | "paused" | "buffering";
  queue_length: number;
  cursor: number;
  current_item: string | null;
  now: NowPlaying | null;
  position_ms: number | null;
  /** As the screen reports it. The only source for a live transcode, whose
   *  length the catalog cannot know. */
  duration_ms: number | null;
  /** Whether this room is playing its queue in a random order. */
  shuffle: boolean;
  volume: number | null;
  updated_at: string | null;
}

/** What the hardware is doing when it is not us doing it.
 *
 * A receiver plays Spotify and AirPlay without this app being involved, so the
 * tower asks it rather than trusting its own records.
 */
export interface ZoneExternal {
  /** Something is actually playing. Only this justifies calling a room in use. */
  busy?: boolean;
  unreachable?: boolean;
  /** With busy false this is a note — what the amplifier is switched to. The
   *  receiver cannot say whether anybody is listening, and one left on after the
   *  television went off looks exactly like one in use. */
  detail: string | null;
}

export interface Zone {
  id: string;
  name: string;
  renderer: ZoneRenderer | null;
  session: ZoneSession | null;
  external: ZoneExternal | null;
}

export const listZones = () => api.get<Zone[]>("/api/zones");

export interface QueueTrack {
  index: number;
  item_id: string;
  filename: string | null;
  title: string | null;
  artist: string | null;
  duration_ms: number | null;
}

/** What a room is going to play, in order. */
export const zoneQueue = (zoneId: string) =>
  api.get<{ cursor: number; tracks: QueueTrack[] }>(`/api/zones/${zoneId}/queue`);

/** Play a particular track of what the room already has. */
export const jumpInZone = (zoneId: string, index: number) =>
  api.post<{ zone: string; state: string }>(`/api/zones/${zoneId}/jump`, { index });

/** Turn shuffle on or off for a room. */
export const shuffleZone = (zoneId: string, on: boolean) =>
  api.post<{ shuffle: boolean }>(`/api/zones/${zoneId}/shuffle`, { on });

/** Move to a point in what is playing in another room. */
export const seekZone = (zoneId: string, positionMs: number) =>
  api.post<{ zone: string; position_ms: number }>(`/api/zones/${zoneId}/seek`, {
    position_ms: Math.max(0, Math.round(positionMs)),
  });

export const playInZone = (
  zoneId: string,
  itemIds: string[],
  startIndex = 0,
  takeOver = false,
) =>
  api.post<{ zone: string; state: string; pushed: boolean }>(
    `/api/zones/${zoneId}/play`,
    { item_ids: itemIds, start_index: startIndex, take_over: takeOver },
  );

export const stopZone = (zoneId: string) => api.post(`/api/zones/${zoneId}/stop`);

/** Transport for a room.
 *
 * Pausing is the renderer's job — it holds the stream. Skipping is the server's:
 * the receiver is sent one URL at a time and has no idea a queue exists, so the
 * queue lives here and the next track is pushed the same way the first was.
 */
export const pauseZone = (zoneId: string) => api.post(`/api/zones/${zoneId}/pause`);
export const resumeZone = (zoneId: string) => api.post(`/api/zones/${zoneId}/resume`);
export const nextInZone = (zoneId: string) => api.post(`/api/zones/${zoneId}/next`);
export const previousInZone = (zoneId: string) => api.post(`/api/zones/${zoneId}/previous`);

export const setZoneVolume = (zoneId: string, level: number) =>
  api.post(`/api/zones/${zoneId}/volume`, { level });

export const pairDevice = (
  code: string,
  name: string,
  audience: "everyone" | "admins" | "selected",
  grantTo: string[],
) =>
  api.post<{ renderer_id: string; name: string }>("/api/renderers/pair/claim", {
    code,
    name,
    audience,
    grant_to: grantTo,
  });

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

  // Someone else is using the room. Saying "ready" here would be a lie, and
  // acting on it would cut them off.
  if (zone.external?.busy) {
    return { label: zone.external.detail ?? "in use", tone: "live" };
  }
  if (zone.external?.unreachable) return { label: "not responding", tone: "off" };

  // The amplifier is on and switched to something else. Worth showing, but it is
  // not occupancy: nothing is playing, and claiming otherwise would refuse a
  // free room.
  if (zone.external?.detail && zone.session?.state !== "playing") {
    return { label: zone.external.detail, tone: "idle" };
  }

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

/** Looking after rooms.
 *
 * A typo at pairing time used to be permanent, and a screen taken out of the
 * house stayed on the list for good.
 */
export const renameZone = (zoneId: string, name: string) =>
  api.put<{ id: string; name: string }>(`/api/zones/${zoneId}`, { name });

export const removeZone = (zoneId: string) => api.delete(`/api/zones/${zoneId}`);
