// Run with: npm test (node --test, types stripped; no test framework needed).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { ago, messageHref, validPath } from "../src/notifyLinks.ts";

const cases = JSON.parse(
  readFileSync(new URL("../../server/tests/notify_paths.json", import.meta.url), "utf-8"),
) as { accepted: string[]; refused: string[] };

const apps = [
  { name: "AppOne", url: "http://192.0.2.10:8080" },
  { name: "AppTwo", url: "https://192.0.2.11/two/" },
  { name: "AppBad", url: "javascript:alert(1)" },
];

test("validPath agrees with the server on every shared case", () => {
  assert.deepEqual(cases.accepted.filter((p) => !validPath(p)), []);
  assert.deepEqual(cases.refused.filter((p) => validPath(p)), []);
  assert.equal(validPath(undefined), false);
  assert.equal(validPath(42), false);
});

test("a message from Homesh links to its own path on this page", () => {
  assert.equal(messageHref({ sender: "Homesh", path: "/a?b=c" }, apps), "/a?b=c");
});

test("a message from an app links under that app's address", () => {
  assert.equal(messageHref({ sender: "AppOne", path: "/inbox" }, apps), "http://192.0.2.10:8080/inbox");
  assert.equal(messageHref({ sender: "AppTwo", path: "/x#y" }, apps), "https://192.0.2.11/two/x#y");
});

test("no link for an unknown app, a bad path or a bad address", () => {
  assert.equal(messageHref({ sender: "Gone", path: "/inbox" }, apps), null);
  assert.equal(messageHref({ sender: "AppOne", path: "//evil.example" }, apps), null);
  assert.equal(messageHref({ sender: "Homesh", path: "/javascript:alert(1)" }, apps), null);
  assert.equal(messageHref({ sender: "AppBad", path: "/x" }, apps), null);
  for (const path of cases.refused) {
    assert.equal(messageHref({ sender: "AppOne", path }, apps), null, JSON.stringify(path));
    assert.equal(messageHref({ sender: "Homesh", path }, apps), null, JSON.stringify(path));
  }
});

test("ago", () => {
  const now = Date.parse("2026-01-10T12:00:00Z");
  assert.equal(ago(null, now), "never");
  assert.equal(ago("2026-01-10T11:59:30Z", now), "just now");
  assert.equal(ago("2026-01-10T11:59:00Z", now), "1 minute ago");
  assert.equal(ago("2026-01-10T10:00:00Z", now), "2 hours ago");
  assert.equal(ago("2026-01-07T12:00:00Z", now), "3 days ago");
  assert.equal(ago("2025-12-20T12:00:00Z", now), "3 weeks ago");
});
