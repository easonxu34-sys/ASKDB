import assert from "node:assert/strict";
import test from "node:test";

import { saveResultArtifact } from "../lib/thread-result-artifacts.mjs";

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  const writes = [];
  return {
    getItem(key) {
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      writes.push({ key, value });
      values.set(key, value);
    },
    read(key) {
      return values.get(key);
    },
    writes,
  };
}

test("persists a result artifact under its turn and appends to the turn", () => {
  const storage = memoryStorage({ key: JSON.stringify({ turn: [{ id: 1 }] }) });

  assert.equal(saveResultArtifact(storage, "key", "turn", { id: 2 }, 1024), true);
  assert.deepEqual(JSON.parse(storage.read("key")), { turn: [{ id: 1 }, { id: 2 }] });
});

test("evicts oldest other turns when the result artifact cache reaches its byte cap", () => {
  const storage = memoryStorage({
    key: JSON.stringify({ old: ["x".repeat(40)], newer: ["a"] }),
  });

  assert.equal(saveResultArtifact(storage, "key", "current", "b", 60), true);
  assert.deepEqual(JSON.parse(storage.read("key")), { newer: ["a"], current: ["b"] });
});

test("rejects an artifact that cannot fit without replacing the existing cache", () => {
  const original = JSON.stringify({ turn: ["saved"] });
  const storage = memoryStorage({ key: original });

  assert.equal(saveResultArtifact(storage, "key", "turn", "x".repeat(50), 24), false);
  assert.equal(storage.read("key"), original);
});

test("reports local storage quota failures", () => {
  const storage = {
    getItem() {
      return "{}";
    },
    setItem() {
      throw new Error("quota exceeded");
    },
  };

  assert.equal(saveResultArtifact(storage, "key", "turn", { id: 1 }, 1024), false);
});

test("evicts a whole old turn, including its chart override and undo history", () => {
  const overrideWithHistory = {
    kind: "chart_view_override",
    schema_version: 2,
    source_result_id: "result-old",
    view: { title: "edited" },
    undo_history: [{ view: { title: "before" }, summary: "manual edit", origin: "manual" }],
  };
  const initial = {
    oldTurn: [overrideWithHistory],
    newerTurn: [{ id: "keep" }],
  };
  const expected = JSON.stringify({
    newerTurn: [{ id: "keep" }],
    currentTurn: [{ id: "new" }],
  });
  const storage = memoryStorage({ key: JSON.stringify(initial) });

  assert.equal(
    saveResultArtifact(
      storage,
      "key",
      "currentTurn",
      { id: "new" },
      new TextEncoder().encode(expected).byteLength,
    ),
    true,
  );
  assert.deepEqual(JSON.parse(storage.read("key")), JSON.parse(expected));
  assert.equal(storage.writes.length, 1);
});
