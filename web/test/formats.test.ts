// Run with: npm test (node --test, types stripped; no test framework needed).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  AUDIO_EXTS,
  copyFormat,
  copyName,
  downloadLabel,
  route,
  SHAREABLE,
  UNDECODABLE,
} from "../src/formats.ts";

// The same table the server's tests read, so the screen never offers a copy the
// server would refuse to make.
const shared = JSON.parse(
  readFileSync(new URL("../../server/tests/send_copy_cases.json", import.meta.url), "utf-8"),
) as {
  shareable: string[];
  audio: string[];
  video_needs_conversion: string[];
  cases: [string, string, "mp4" | "m4a" | null][];
};

const file = (ext: string, kind: string) => ({ ext, kind, filename: `a clip.${ext}` });

test("the extension lists agree with the server's", () => {
  assert.deepEqual([...SHAREABLE].sort(), [...shared.shareable].sort());
  assert.deepEqual([...AUDIO_EXTS].sort(), [...shared.audio].sort());
  assert.deepEqual([...UNDECODABLE].sort(), [...shared.video_needs_conversion].sort());
});

test("route picks a copy exactly where the server makes one", () => {
  for (const [kind, ext, expected] of shared.cases) {
    assert.equal(copyFormat(ext.toLowerCase(), kind), expected, `${kind} .${ext}`);
    const plan = route(file(ext, kind));
    assert.equal(plan.via === "copy" ? plan.format : null, expected, `${kind} .${ext}`);
  }
});

test("old formats are copied, ones that open are not", () => {
  for (const [ext, kind] of [
    ["wma", "audio"], ["wmv", "video"], ["avi", "video"], ["mov", "video"], ["mkv", "video"],
  ]) {
    assert.equal(route(file(ext, kind)).via, "copy", ext);
  }
  for (const [ext, kind] of [["mp3", "audio"], ["mp4", "video"], ["m4a", "audio"]]) {
    assert.equal(route(file(ext, kind)).via, "file", ext);
  }
});

test("documents still go as PDF, and MIDI is still refused", () => {
  assert.equal(route(file("docx", "doc")).via, "pdf");
  assert.equal(route(file("mid", "other")).via, "none");
});

test("the extension comes from the name when the catalog has none", () => {
  assert.equal(route({ ext: null, filename: "Old Song.WMA", kind: "audio" }).via, "copy");
});

test("the download button says what will arrive", () => {
  const label = (ext: string, kind: string) => {
    const plan = route(file(ext, kind));
    return downloadLabel(plan.via === "copy" ? plan.format : null);
  };
  assert.equal(label("wmv", "video"), "Download as MP4");
  assert.equal(label("wma", "audio"), "Download as M4A");
  assert.equal(label("mp3", "audio"), "Download");
});

test("the copy keeps the file's name", () => {
  assert.equal(copyName("Beach day.v2.wmv", "mp4"), "Beach day.v2.mp4");
  assert.equal(copyName("song", "m4a"), "song.m4a");
  assert.equal(copyName(".wma", "m4a"), ".wma.m4a");
});
