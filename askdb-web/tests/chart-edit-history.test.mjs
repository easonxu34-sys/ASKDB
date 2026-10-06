import assert from "node:assert/strict";
import test from "node:test";

import { commitChartViewChange, undoChartViewChange } from "../lib/chart-edit-history.mjs";

const cacheKey = "artifacts";
const turnId = "turn-current";
const sourceResultId = "result-current";
const sourceArtifacts = [
  { artifact: { kind: "successful_query_result", result_id: sourceResultId } },
  {
    artifact: {
      kind: "echarts_chart",
      schema_version: 1,
      source_result_id: sourceResultId,
    },
  },
];

function view(title) {
  return {
    chart_type: "bar",
    dimension_field: "region",
    metric_fields: ["revenue"],
    hidden_metric_fields: [],
    title,
    field_labels: {},
    sort: { mode: "original" },
    format_by_field: { revenue: { mode: "raw", decimal_places: "auto" } },
    show_data_labels: false,
    show_legend: false,
  };
}

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

function options(storage, overrides = {}) {
  return {
    storage,
    key: cacheKey,
    turnId,
    sourceResultId,
    before: view("AI recommendation"),
    after: view("Edited chart"),
    recommendedView: view("AI recommendation"),
    summary: "修改图表标题",
    origin: "manual",
    hasSourceArtifacts: (artifacts, resultId) =>
      artifacts.some((item) => item.artifact?.kind === "successful_query_result" && item.artifact.result_id === resultId) &&
      artifacts.some((item) => item.artifact?.kind === "echarts_chart" && item.artifact.source_result_id === resultId),
    validateView: (candidate) =>
      candidate && typeof candidate === "object" && typeof candidate.title === "string"
        ? candidate
        : undefined,
    equalViews: (left, right) => JSON.stringify(left) === JSON.stringify(right),
    ...overrides,
  };
}

function startingStorage(extra = {}) {
  return memoryStorage({
    [cacheKey]: JSON.stringify({ [turnId]: sourceArtifacts, ...extra }),
  });
}

test("commits a view and its undo record together, then undoes in one write", () => {
  const storage = startingStorage();
  const before = view("AI recommendation");
  const after = view("Edited chart");
  const committed = commitChartViewChange(
    options(storage, { before, after, summary: "标题：AI recommendation → Edited chart" }),
  );
  assert.equal(committed.view.title, "Edited chart");
  assert.equal(committed.undoHistory.length, 1);
  assert.equal(committed.undoHistory[0].view.title, "AI recommendation");
  assert.equal(storage.writes.length, 1);
  const saved = JSON.parse(storage.read(cacheKey))[turnId].at(-1);
  assert.equal(saved.schema_version, 2);
  assert.equal(saved.view.title, "Edited chart");
  assert.equal(saved.undo_history.length, 1);

  const undone = undoChartViewChange(
    options(storage, { expectedView: after }),
  );
  assert.equal(undone.view.title, "AI recommendation");
  assert.equal(undone.undoHistory.length, 0);
  assert.equal(storage.writes.length, 2);
});

test("keeps only the latest 20 history entries", () => {
  const storage = startingStorage();
  let current = view("AI recommendation");
  for (let index = 0; index < 22; index += 1) {
    const next = view(`edit ${index}`);
    const result = commitChartViewChange(
      options(storage, { before: current, after: next, summary: `编辑 ${index}` }),
    );
    assert.ok(result);
    current = next;
  }
  const saved = JSON.parse(storage.read(cacheKey))[turnId].at(-1);
  assert.equal(saved.undo_history.length, 20);
  assert.equal(saved.undo_history[0].view.title, "edit 1");
  assert.equal(saved.view.title, "edit 21");
});

test("upgrades a v1 override by preserving its current view and starting undo history", () => {
  const legacyView = view("Legacy custom view");
  const legacyOverride = {
        kind: "chart_view_override",
        schema_version: 1,
        source_result_id: sourceResultId,
        view: legacyView,
      };
  const storage = memoryStorage({
    [cacheKey]: JSON.stringify({ [turnId]: [...sourceArtifacts, legacyOverride] }),
  });
  const committed = commitChartViewChange(
    options(storage, {
      before: legacyView,
      after: view("Edited legacy view"),
      recommendedView: view("AI recommendation"),
    }),
  );
  assert.equal(committed.view.title, "Edited legacy view");
  assert.equal(committed.undoHistory.length, 1);
});

test("restoring the recommendation is a new reversible commit", () => {
  const storage = startingStorage();
  const recommendation = view("AI recommendation");
  const edited = view("Edited chart");
  commitChartViewChange(
    options(storage, { before: recommendation, after: edited }),
  );
  const restored = commitChartViewChange(
    options(storage, {
      before: edited,
      after: recommendation,
      summary: "恢复 AI 推荐配置",
      origin: "restore_recommendation",
    }),
  );
  assert.equal(restored.view.title, "AI recommendation");
  assert.equal(restored.undoHistory.at(-1).view.title, "Edited chart");
  assert.equal(restored.undoHistory.at(-1).origin, "restore_recommendation");
  assert.equal(
    undoChartViewChange(options(storage, { expectedView: recommendation })).view.title,
    "Edited chart",
  );
});

test("rejects stale before views, missing or expired results, and absent chart artifacts", () => {
  const staleStorage = startingStorage();
  const stale = commitChartViewChange(
    options(staleStorage, {
      before: view("stale local view"),
      after: view("Edited chart"),
    }),
  );
  assert.equal(stale, undefined);

  const expiredStorage = startingStorage();
  assert.equal(
    commitChartViewChange(options(expiredStorage, { hasSourceArtifacts: () => false })),
    undefined,
  );
  const onlyQueryStorage = memoryStorage({
    [cacheKey]: JSON.stringify({ [turnId]: [sourceArtifacts[0]] }),
  });
  assert.equal(commitChartViewChange(options(onlyQueryStorage)), undefined);

  const evictedStorage = memoryStorage({ [cacheKey]: JSON.stringify({ otherTurn: [] }) });
  assert.equal(commitChartViewChange(options(evictedStorage)), undefined);
});

test("keeps cache keys, turns, and result IDs isolated", () => {
  const storage = memoryStorage({
    [cacheKey]: JSON.stringify({ [turnId]: sourceArtifacts }),
    "another-thread": JSON.stringify({ [turnId]: sourceArtifacts }),
  });
  const committed = commitChartViewChange(
    options(storage, { key: "another-thread", after: view("Other thread") }),
  );
  assert.ok(committed);
  assert.equal(JSON.parse(storage.read(cacheKey))[turnId].length, 2);
  assert.equal(JSON.parse(storage.read("another-thread"))[turnId].at(-1).view.title, "Other thread");
  assert.equal(
    commitChartViewChange(
      options(storage, { sourceResultId: "other-result", after: view("Wrong result") }),
    ),
    undefined,
  );
  assert.equal(
    commitChartViewChange(
      options(storage, { turnId: "other-turn", after: view("Wrong turn") }),
    ),
    undefined,
  );
});

test("quota, byte-cap and failed undo writes leave the artifact index unchanged", () => {
  const original = JSON.stringify({ [turnId]: sourceArtifacts });
  const quotaStorage = {
    getItem: () => original,
    setItem: () => {
      throw new Error("quota exceeded");
    },
  };
  assert.equal(commitChartViewChange(options(quotaStorage)), undefined);

  const tooSmallStorage = startingStorage();
  const tooSmallOriginal = tooSmallStorage.read(cacheKey);
  assert.equal(
    commitChartViewChange(options(tooSmallStorage, { maxBytes: 20 })),
    undefined,
  );
  assert.equal(tooSmallStorage.read(cacheKey), tooSmallOriginal);

  const storage = startingStorage();
  commitChartViewChange(options(storage));
  const beforeUndo = storage.read(cacheKey);
  const failedUndo = {
    getItem: () => beforeUndo,
    setItem: () => {
      throw new Error("quota exceeded");
    },
  };
  assert.equal(
    undoChartViewChange(options(failedUndo, { expectedView: view("Edited chart") })),
    undefined,
  );
  assert.equal(storage.read(cacheKey), beforeUndo);
});

test("undo rejects stale views and corrupted history without changing the cache", () => {
  const storage = startingStorage();
  commitChartViewChange(options(storage));
  const before = storage.read(cacheKey);
  assert.equal(
    undoChartViewChange(options(storage, { expectedView: view("different current view") })),
    undefined,
  );
  assert.equal(storage.read(cacheKey), before);

  const index = JSON.parse(before);
  index[turnId].at(-1).undo_history[0].view = { title: 42 };
  storage.setItem(cacheKey, JSON.stringify(index));
  const corrupted = storage.read(cacheKey);
  assert.equal(undoChartViewChange(options(storage, { expectedView: view("Edited chart") })), undefined);
  assert.equal(storage.read(cacheKey), corrupted);
});
