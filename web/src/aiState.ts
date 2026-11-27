/*
 * What the "Assistant and AI" page says, worked out from what the server answered.
 * Kept apart from aiApi.ts and free of imports so web/test/aiState.test.ts can
 * run it under plain Node.
 */

export type TaskName = "chat" | "bulk" | "translate";
export type ProviderName = "openrouter" | "gemini";

export const PROVIDER_LABELS: Record<ProviderName, string> = {
  openrouter: "OpenRouter",
  gemini: "Gemini",
};

export interface ProviderState {
  key: boolean;
  usable: boolean;
  why?: string | null;
}

/** A model named in one of the lists, as the server knows it. */
export interface ListedModel {
  label: string;
  provider: string;
  free: boolean;
  tools: boolean;
  gone: boolean;
  cooling_until: string | null;
}

export interface WorkKind {
  kind: string;
  label: string;
  installed: boolean;
  queued: number;
  waiting: number;
  running: number;
  done: number;
  failed: number;
  done_last_24h: number;
  nights_left: number | null;
  last_error: string | null;
}

export interface Work {
  paused: boolean;
  in_window: boolean;
  night: { start: string; end: string };
  timezone: string;
  worker: "running" | "not answering" | "not installed";
  kinds: WorkKind[];
}

export function providerOf(id: string): string {
  return id.split(":", 1)[0];
}

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider as ProviderName] ?? provider;
}

/** The model's own name, without the provider in front of it. */
export function modelName(id: string): string {
  const colon = id.indexOf(":");
  return colon < 0 ? id : id.slice(colon + 1);
}

export function nightRange(night: { start: string; end: string }): string {
  return night.start === night.end ? "all day" : `${night.start}-${night.end}`;
}

export function kindTotal(k: WorkKind): number {
  return k.queued + k.waiting + k.running + k.done + k.failed;
}

/** How much of the night work is done, or null when there is none to measure.
 *  Rounded down: "100% done" must mean nothing is left. */
export function percentDone(work: Work): number | null {
  let done = 0;
  let total = 0;
  for (const k of work.kinds) {
    if (!k.installed) continue;
    done += k.done;
    total += kindTotal(k);
  }
  return total === 0 ? null : Math.floor((done / total) * 100);
}

/** The one sentence at the top of the page.
 *
 *  `work` is null while it has not been asked for or could not be read, and
 *  "absent" when this server has no night work at all. */
export function stateSentence(s: {
  onStandby: boolean;
  assistant: boolean;
  providers: Record<string, ProviderState>;
  paused: boolean;
  night: { start: string; end: string };
  work: Work | "absent" | null;
}): { text: string; howTo: boolean } {
  if (s.onStandby) return { text: "The assistant is not offered on the standby.", howTo: false };

  const providers = Object.values(s.providers);
  if (!providers.some((p) => p.key)) {
    return {
      text: "The assistant is off: no provider key is set on this server.",
      howTo: true,
    };
  }

  let first: string;
  if (s.assistant) first = "The assistant is on.";
  else if (!providers.some((p) => p.usable)) {
    first = "The assistant is off: no provider is ready to use. Providers, below, says why.";
  } else {
    first = "The assistant is off: no model in its list can answer right now.";
  }

  const range = nightRange(s.night);
  let night = "";
  if (s.paused) night = "Night work is paused.";
  else if (s.work === "absent" || (s.work && s.work.worker === "not installed")) {
    night = "Nothing runs at night on this server yet.";
  } else if (s.work && s.work.worker === "not answering") {
    night = "The night worker is not running.";
  } else if (s.work) {
    const percent = percentDone(s.work);
    night =
      percent === null
        ? `Night work runs ${range}.`
        : `Night work runs ${range} and is ${percent}% done.`;
  }
  return { text: night ? `${first} ${night}` : first, howTo: false };
}

export interface ModelRow {
  id: string;
  label: string;
  free: boolean | null;
  /** No longer offered, or now costs money while nothing may be spent. */
  withdrawn: boolean;
  /** Why it would be passed over for the moment, if it would. */
  note: string | null;
  /** Whether it would be asked if its turn came now. */
  answers: boolean;
}

/** One row of a task's list.
 *
 *  On the standby, keys and the spending limit are the standby's own, so only
 *  what the restored database says is judged there. */
export function modelRow(
  id: string,
  context: {
    task: TaskName;
    listed: Record<string, ListedModel>;
    providers: Record<string, ProviderState>;
    capMicros: number;
    onStandby: boolean;
    now: Date;
    time: (at: Date) => string;
  },
): ModelRow {
  const m = context.listed[id];
  const provider = providerOf(id);
  const unpayable = !!m && !m.free && context.capMicros <= 0 && !context.onStandby;
  const withdrawn = !m || m.gone || unpayable;
  const notes: string[] = [];
  let answers = !withdrawn;

  if (!withdrawn && !context.onStandby && !context.providers[provider]?.usable) {
    notes.push(`Not used: ${providerLabel(provider)} is not set up. See Providers, above.`);
    answers = false;
  }
  if (!withdrawn && context.task === "chat" && !m.tools) {
    notes.push("Not used here: it can no longer use tools.");
    answers = false;
  }
  if (!withdrawn && m.cooling_until) {
    const until = new Date(m.cooling_until);
    if (until.getTime() > context.now.getTime()) {
      notes.push(`busy until ${context.time(until)}`);
      answers = false;
    }
  }
  return {
    id,
    label: m?.label ?? modelName(id),
    free: m ? m.free : null,
    withdrawn,
    note: notes.length ? notes.join(" ") : null,
    answers,
  };
}

/** What replaces a model that was withdrawn. Says that another is answering
 *  only when one really is. */
export function withdrawnText(rows: ModelRow[], index: number): string {
  const start = "This model is no longer offered.";
  if (rows.slice(index + 1).some((r) => r.answers)) {
    return `${start} The next one is answering instead.`;
  }
  if (rows.some((r) => r.answers)) return `${start} Another one in the list is answering instead.`;
  return `${start} Nothing else in this list can answer: add another model.`;
}

/** A list with one entry moved up (-1) or down (+1). */
export function moved(ids: string[], index: number, by: -1 | 1): string[] {
  const to = index + by;
  if (index < 0 || index >= ids.length || to < 0 || to >= ids.length) return ids;
  const out = [...ids];
  [out[index], out[to]] = [out[to], out[index]];
  return out;
}

/** The model a provider's Test button asks. Only ever a free one: pressing
 *  Test beside a provider must not cost anything. The lists come first, then
 *  the settings file's model, then any free model the provider offers. */
export function testModelFor(
  provider: ProviderName,
  listedIds: string[],
  defaultModel: string,
  free: { id: string; provider: string; free: boolean }[],
): string | null {
  const usable = free.filter((m) => m.provider === provider && m.free);
  const known = new Set(usable.map((m) => m.id));
  return (
    listedIds.find((id) => known.has(id)) ??
    (known.has(`${provider}:${defaultModel}`) ? `${provider}:${defaultModel}` : null) ??
    usable[0]?.id ??
    null
  );
}

/** "128k", "1M". */
export function contextSize(tokens: number | null): string {
  if (!tokens || tokens < 0) return "";
  if (tokens >= 1_000_000) return `${Math.floor(tokens / 100_000) / 10}M`;
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}k`;
  return String(tokens);
}

export function spendingText(capMicros: number, spentMicros: number): string {
  if (capMicros <= 0) return "No limit is set, so only free models can be used.";
  const money = (micros: number) => (micros / 1_000_000).toFixed(2);
  return `${money(spentMicros)} of ${money(capMicros)} used this month`;
}

export function testedText(result: { ok: boolean; ms?: number; why?: string }): string {
  if (result.ok) return `Works - ${((result.ms ?? 0) / 1000).toFixed(1)} s`;
  return result.why ?? "That did not work.";
}

export function refreshedText(counts: {
  openrouter: number;
  gemini: number;
  gone: number;
  now_paid: number;
}): string {
  const found = counts.openrouter + counts.gemini;
  if (found === 0) return "No provider's list could be read. Nothing was changed.";
  const parts = [`Found ${found} model${found === 1 ? "" : "s"}.`];
  if (counts.gone > 0) {
    parts.push(`${counts.gone} ${counts.gone === 1 ? "is" : "are"} no longer offered.`);
  }
  if (counts.now_paid > 0) {
    parts.push(`${counts.now_paid} now ${counts.now_paid === 1 ? "costs" : "cost"} money.`);
  }
  return parts.join(" ");
}

export const OUTCOMES: Record<string, string> = {
  ok: "Answered",
  refused: "Refused",
  error: "Failed",
};

const PURPOSES: Record<string, string> = {
  test: "a test",
  ask: "a question",
  "tag-music": "tagging music",
};

export function purposeText(purpose: string): string {
  return PURPOSES[purpose] ?? purpose;
}

/** The time zone to send with a save, or null to leave the stored one alone.
 *  "UTC" is what the server says before anyone has saved anything. */
export function zoneToSend(stored: string, browser: string | undefined): string | null {
  return stored === "UTC" && browser && browser !== "UTC" ? browser : null;
}
