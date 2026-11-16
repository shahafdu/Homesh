import { test } from "node:test";
import assert from "node:assert/strict";
import { canHandOver, PRELOAD_SECONDS, shouldPreload } from "../src/gapless.ts";

const next = { item_id: "b" };

test("preloads only inside the final seconds", () => {
  assert.equal(shouldPreload(100, 200, next), false);
  assert.equal(shouldPreload(200 - PRELOAD_SECONDS + 1, 200, next), true);
  // Strictly less than: exactly twenty seconds left is not yet inside.
  assert.equal(shouldPreload(200 - PRELOAD_SECONDS, 200, next), false);
  assert.equal(shouldPreload(200, 200, next), true);
});

test("never preloads without a following track", () => {
  assert.equal(shouldPreload(190, 200, null), false);
  assert.equal(shouldPreload(199.9, 200, null), false);
});

test("an unknown duration never triggers a preload", () => {
  assert.equal(shouldPreload(0, NaN, next), false);
  assert.equal(shouldPreload(0, 0, next), false);
  assert.equal(shouldPreload(0, Infinity, next), false);
});

test("hands over only the due track, and only once it can play", () => {
  assert.equal(canHandOver(4, "b", "b"), true);
  assert.equal(canHandOver(3, "b", "b"), true);
  assert.equal(canHandOver(2, "b", "b"), false);
  assert.equal(canHandOver(4, "b", "c"), false);
  assert.equal(canHandOver(4, "b", null), false);
  assert.equal(canHandOver(4, null, null), false);
});
