// Run with: npm test (node --test, types stripped; no test framework needed).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  contextSize,
  modelRow,
  moved,
  percentDone,
  refreshedText,
  spendingText,
  stateSentence,
  testedText,
  testModelFor,
  withdrawnText,
  zoneToSend,
  type ListedModel,
  type Work,
  type WorkKind,
} from "../src/aiState.ts";

const night = { start: "01:00", end: "07:00" };
const on = {
  openrouter: { key: true, usable: true },
  gemini: { key: false, usable: false },
};

const kind = (over: Partial<WorkKind>): WorkKind => ({
  kind: "k",
  label: "Photos, for search",
  installed: true,
  queued: 0,
  waiting: 0,
  running: 0,
  done: 0,
  failed: 0,
  done_last_24h: 0,
  nights_left: null,
  last_error: null,
  ...over,
});
const work = (kinds: WorkKind[], worker: Work["worker"] = "running"): Work => ({
  paused: false,
  in_window: true,
  night,
  timezone: "UTC",
  worker,
  kinds,
});

const base = {
  onStandby: false,
  assistant: true,
  providers: on,
  paused: false,
  night,
  work: "absent" as const,
};

test("the sentence says the assistant is on and how far night work has got", () => {
  const said = stateSentence({ ...base, work: work([kind({ done: 18, queued: 82 })]) });
  assert.deepEqual(said, {
    text: "The assistant is on. Night work runs 01:00-07:00 and is 18% done.",
    howTo: false,
  });
});

test("with no key anywhere it says so and offers how to set one", () => {
  const providers = { openrouter: { key: false, usable: false } };
  const said = stateSentence({ ...base, assistant: false, providers });
  assert.equal(said.text, "The assistant is off: no provider key is set on this server.");
  assert.equal(said.howTo, true);
});

test("a key that cannot be used is not the same as no model answering", () => {
  const unusable = stateSentence({
    ...base,
    assistant: false,
    providers: { gemini: { key: true, usable: false } },
  });
  assert.match(unusable.text, /no provider is ready/);
  const busy = stateSentence({ ...base, assistant: false });
  assert.match(busy.text, /no model in its list can answer/);
});

test("night work is never said to run when nothing runs it", () => {
  assert.match(stateSentence(base).text, /Nothing runs at night/);
  assert.match(
    stateSentence({ ...base, work: work([], "not installed") }).text,
    /Nothing runs at night/,
  );
  assert.match(
    stateSentence({ ...base, work: work([], "not answering") }).text,
    /night worker is not running/,
  );
  assert.equal(stateSentence({ ...base, work: null }).text, "The assistant is on.");
  assert.match(stateSentence({ ...base, paused: true }).text, /Night work is paused\./);
});

test("the standby says only that the assistant is not offered there", () => {
  assert.equal(
    stateSentence({ ...base, onStandby: true }).text,
    "The assistant is not offered on the standby.",
  );
});

test("done is rounded down, leaves out what is not installed, and may be unknown", () => {
  assert.equal(percentDone(work([kind({ done: 999, queued: 1 })])), 99);
  assert.equal(
    percentDone(work([kind({ done: 1, failed: 1 }), kind({ installed: false, queued: 50 })])),
    50,
  );
  assert.equal(percentDone(work([])), null);
});

const model = (over: Partial<ListedModel> = {}): ListedModel => ({
  label: "Alpha",
  provider: "openrouter",
  free: true,
  tools: true,
  gone: false,
  cooling_until: null,
  ...over,
});
const now = new Date("2026-01-01T12:00:00Z");
const row = (m: ListedModel | undefined, over: Partial<Parameters<typeof modelRow>[1]> = {}) =>
  modelRow("openrouter:vendor/alpha", {
    task: "chat",
    listed: m ? { "openrouter:vendor/alpha": m } : {},
    providers: on,
    capMicros: 0,
    onStandby: false,
    now,
    time: () => "14:32",
    ...over,
  });

test("a model that is gone, unknown or now costs money is withdrawn", () => {
  assert.equal(row(model()).withdrawn, false);
  assert.equal(row(model({ gone: true })).withdrawn, true);
  assert.equal(row(undefined).withdrawn, true);
  assert.equal(row(undefined).label, "vendor/alpha");
  assert.equal(row(model({ free: false })).withdrawn, true);
  assert.equal(row(model({ free: false }), { capMicros: 5_000_000 }).withdrawn, false);
  // The standby has its own limit, which says nothing about the list.
  assert.equal(row(model({ free: false }), { onStandby: true }).withdrawn, false);
});

test("a busy model says until when, and a past time says nothing", () => {
  const busy = row(model({ cooling_until: "2026-01-01T12:30:00Z" }));
  assert.equal(busy.note, "busy until 14:32");
  assert.equal(busy.answers, false);
  const past = row(model({ cooling_until: "2026-01-01T11:00:00Z" }));
  assert.equal(past.note, null);
  assert.equal(past.answers, true);
});

test("a provider that is not set up, or a model without tools, is passed over", () => {
  const off = { openrouter: { key: false, usable: false } };
  assert.match(row(model(), { providers: off }).note ?? "", /OpenRouter is not set up/);
  assert.match(row(model({ tools: false })).note ?? "", /use tools/);
  assert.equal(row(model({ tools: false }), { task: "bulk" }).note, null);
});

test("a withdrawn model claims another is answering only when one is", () => {
  const gone = row(model({ gone: true }));
  const fine = row(model());
  assert.match(withdrawnText([gone, fine], 0), /next one is answering/);
  assert.match(withdrawnText([fine, gone], 1), /Another one in the list/);
  assert.match(withdrawnText([gone, gone], 0), /add another model/);
});

test("moving stays inside the list", () => {
  assert.deepEqual(moved(["a", "b", "c"], 1, -1), ["b", "a", "c"]);
  assert.deepEqual(moved(["a", "b", "c"], 1, 1), ["a", "c", "b"]);
  assert.deepEqual(moved(["a", "b"], 0, -1), ["a", "b"]);
  assert.deepEqual(moved(["a", "b"], 1, 1), ["a", "b"]);
});

test("testing a provider never picks a paid model", () => {
  const known = [
    { id: "openrouter:x:free", provider: "openrouter", free: true },
    { id: "openrouter:y:free", provider: "openrouter", free: true },
    { id: "openrouter:paid", provider: "openrouter", free: false },
    { id: "gemini:g", provider: "gemini", free: true },
  ];
  assert.equal(
    testModelFor("openrouter", ["openrouter:paid", "openrouter:y:free"], "x:free", known),
    "openrouter:y:free",
  );
  assert.equal(
    testModelFor("openrouter", ["openrouter:paid"], "y:free", known),
    "openrouter:y:free",
  );
  assert.equal(testModelFor("openrouter", [], "paid", known), "openrouter:x:free");
  assert.equal(testModelFor("gemini", [], "x:free", known), "gemini:g");
  assert.equal(testModelFor("gemini", [], "x:free", []), null);
});

test("small texts", () => {
  assert.equal(contextSize(128000), "128k");
  assert.equal(contextSize(1048576), "1M");
  assert.equal(contextSize(null), "");
  assert.equal(spendingText(0, 0), "No limit is set, so only free models can be used.");
  assert.equal(spendingText(5_000_000, 420_000), "0.42 of 5.00 used this month");
  assert.equal(testedText({ ok: true, ms: 840 }), "Works - 0.8 s");
  assert.equal(testedText({ ok: false, why: "busy" }), "busy");
  assert.equal(
    refreshedText({ openrouter: 2, gemini: 1, gone: 1, now_paid: 2 }),
    "Found 3 models. 1 is no longer offered. 2 now cost money.",
  );
  assert.match(refreshedText({ openrouter: 0, gemini: 0, gone: 0, now_paid: 0 }), /Nothing was/);
});

test("the time zone is sent only while none has been saved", () => {
  assert.equal(zoneToSend("UTC", "Europe/Paris"), "Europe/Paris");
  assert.equal(zoneToSend("UTC", "UTC"), null);
  assert.equal(zoneToSend("UTC", undefined), null);
  assert.equal(zoneToSend("Europe/Paris", "Asia/Tokyo"), null);
});
