import { api, ApiError } from "./api";
import { zoneToSend, type ListedModel, type ProviderState, type TaskName, type Work } from "./aiState";

export interface AiStatus {
  configured: boolean;
  /** The settings file's model, used by a task with an empty list. */
  model: string;
  features: { assistant: boolean };
}

export interface AiSettings {
  timezone: string;
  night: { start: string; end: string };
  paused: boolean;
  tasks: Record<TaskName, string[]>;
  listed: Record<string, ListedModel>;
  providers: Record<"openrouter" | "gemini", ProviderState>;
  cap_micros: number;
  spent_micros: number;
}

export interface Model {
  id: string;
  provider: string;
  model: string;
  label: string;
  free: boolean;
  tools: boolean;
  context: number | null;
  cooling_until: string | null;
}

export interface Call {
  at: string;
  /** Null when the server asked on nobody's behalf. */
  who: string | null;
  purpose: string;
  model: string;
  outcome: string;
  detail: string | null;
  tokens: number;
}

export interface TestResult {
  ok: boolean;
  ms?: number;
  why?: string;
}

export interface RefreshCounts {
  openrouter: number;
  gemini: number;
  gone: number;
  now_paid: number;
}

export type SettingsPatch = {
  night?: { start: string; end: string };
  paused?: boolean;
  tasks?: Partial<Record<TaskName, string[]>>;
};

export const getStatus = () => api.get<AiStatus>("/api/ai/status");
export const getSettings = () => api.get<AiSettings>("/api/ai/settings");
export const listCalls = () => api.get<Call[]>("/api/ai/history?limit=50");
export const refreshModels = () => api.post<RefreshCounts>("/api/ai/models/refresh");
export const testModel = (id: string) => api.post<TestResult>("/api/ai/models/test", { id });

export function listModels(filter: { tools?: boolean; free?: boolean; q?: string } = {}) {
  const q = new URLSearchParams();
  if (filter.tools) q.set("tools", "true");
  if (filter.free) q.set("free", "true");
  if (filter.q) q.set("q", filter.q.slice(0, 80));
  const query = q.toString();
  return api.get<Model[]>(`/api/ai/models${query ? `?${query}` : ""}`);
}

export const getWork = () => api.get<Work>("/api/ai/work");
export const retryWork = (kind: string) =>
  api.post<{ requeued: number }>("/api/ai/work/retry", { kind });

/** Save part of the settings. The first save also tells the server which time
 *  zone the night hours are meant in. */
export async function saveSettings(patch: SettingsPatch, storedZone: string): Promise<AiSettings> {
  const zone = zoneToSend(storedZone, Intl.DateTimeFormat().resolvedOptions().timeZone);
  if (zone) {
    try {
      return await api.put<AiSettings>("/api/ai/settings", { ...patch, timezone: zone });
    } catch (e) {
      // A browser can name a zone the server has never heard of. That must not
      // stop every other setting from being saved.
      if (!(e instanceof ApiError && e.status === 422)) throw e;
    }
  }
  return api.put<AiSettings>("/api/ai/settings", patch);
}

/** The server's own words where it gave any. */
export function reason(e: unknown): string {
  return e instanceof Error && e.message ? e.message : "That did not work.";
}
