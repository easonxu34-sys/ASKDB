import assert from "node:assert/strict";
import test from "node:test";

import {
  applyChartEditIntent,
  normalizeChartViewChange,
} from "../lib/chat-output.ts";
import {
  buildChartEditRequest,
  isChartEditContextCurrent,
  confirmChartEditQuery,
  completeStagedChartEdit,
  interpretChartEdit,
  publishChartEditTurnCompletion,
  publishChartEditTurnStart,
} from "../lib/chart-edit-flow.mjs";

function query(overrides = {}) {
  return {
    resultId: "result-1",
    sql: "SELECT region, revenue",
    columns: ["region", "revenue"],
    columnTypes: ["string", "double"],
    rows: [
      { region: "east", revenue: 5 },
      { region: "west", revenue: 2 },
    ],
    ...overrides,
  };
}

function view(overrides = {}) {
  return {
    chart_type: "bar",
    dimension_field: "region",
    metric_fields: ["revenue"],
    hidden_metric_fields: [],
    bar_orientation: "vertical",
    title: "Revenue",
    field_labels: { region: "地区" },
    sort: { mode: "original" },
    format_by_field: { revenue: { mode: "raw", decimal_places: "auto" } },
    show_data_labels: false,
    show_legend: false,
    ...overrides,
  };
}

function intent(overrides = {}) {
  return {
    status: "apply",
    patch: null,
    current_result_operation: null,
    category_color_operations: [],
    query_proposal: null,
    clarification: null,
    ...overrides,
  };
}

test("builds a minimal interpreter request without SQL, rows, category values or pie colors", () => {
  const input = buildChartEditRequest({
    threadId: "thread-1",
    modelProfileId: "profile-1",
    instruction: "把饼图标题改成地区收入",
    chartArtifact: { source_result_id: "result-1" },
    queryArtifact: query({
      columns: ["region", "revenue"],
      columnTypes: ["string", "double"],
      rows: Array.from({ length: 1002 }, (_, index) => ({ region: `r${index}`, revenue: index })),
      rowCount: 2000,
    }),
    view: view({
      chart_type: "pie",
      pie_category_colors: {
        dimension_field: "region",
        by_category_key: { '["string","east"]': "blue" },
      },
      current_result_top_n: { field: "revenue", count: 5, direction: "desc" },
    }),
  });
  assert.deepEqual(Object.keys(input).sort(), [
    "column_types",
    "columns",
    "instruction",
    "model_profile_id",
    "row_count",
    "source_result_id",
    "thread_id",
    "truncated",
    "view",
  ]);
  assert.equal(input.row_count, 1000);
  assert.equal(input.truncated, true);
  assert.equal(input.view.pie_category_colors, undefined);
  assert.equal(input.view.current_result_top_n.count, 5);
  assert.equal(JSON.stringify(input).includes("SELECT"), false);
  assert.equal(JSON.stringify(input).includes("r999"), false);
  assert.equal(JSON.stringify(input).includes("data_source_id"), false);
});

test("apply and clarify dispatch only their own handlers; query proposal does not send chat", async () => {
  const calls = [];
  const result = await interpretChartEdit(intent(), {
    preview: (value) => calls.push(["preview", value]),
    clarify: (code) => calls.push(["clarify", code]),
    showQueryProposal: (value) => calls.push(["proposal", value]),
  });
  assert.equal(result.status, "preview");
  assert.equal(calls[0][0], "preview");

  calls.length = 0;
  await interpretChartEdit(
    intent({
      status: "clarify",
      patch: null,
      clarification: { code: "field_not_in_result" },
    }),
    {
      preview: () => calls.push(["preview"]),
      clarify: (code) => calls.push(["clarify", code]),
      showQueryProposal: () => calls.push(["proposal"]),
    },
  );
  assert.deepEqual(calls, [["clarify", "field_not_in_result"]]);

  calls.length = 0;
  await interpretChartEdit(
    intent({
      status: "query_required",
      query_proposal: { operation: "aggregation" },
      patch: null,
    }),
    {
      preview: () => calls.push(["preview"]),
      clarify: () => calls.push(["clarify"]),
      showQueryProposal: (value) => calls.push(["proposal", value]),
    },
  );
  assert.equal(calls[0][0], "proposal");
  assert.equal(calls.some(([name]) => name === "preview"), false);
});

test("confirmed query message preserves the original instruction and uses a fixed chart marker", async () => {
  let sent = "";
  const original = "统计去年各地区的销售额";
  await confirmChartEditQuery(
    { operation: "aggregation" },
    original,
    async (message) => {
      sent = message;
      return { accepted: true };
    },
  );
  assert.equal(
    sent,
    `${original}\n\n请根据以上原始指令查询数据，并生成图表。`,
  );
  assert.match(sent, /生成图表/u);
  assert.equal(sent.includes("SELECT"), false);
});

test("applies strict display edits by merging nested maps and defers no-op or invalid edits", () => {
  const current = view({
    field_labels: { region: "区域", revenue: "收入" },
    color_by_metric: { revenue: "teal" },
  });
  const applied = applyChartEditIntent(
    current,
    intent({
      patch: {
        title: "各地区收入",
        field_labels: { region: "地区" },
        color_by_metric: { revenue: "purple" },
      },
    }),
    query(),
  );
  assert.equal(applied.status, "apply");
  assert.equal(applied.view.title, "各地区收入");
  assert.deepEqual(applied.view.field_labels, { region: "地区", revenue: "收入" });
  assert.deepEqual(applied.view.color_by_metric, { revenue: "purple" });

  const invalid = applyChartEditIntent(
    current,
    intent({ patch: { dimension_field: "missing" } }),
    query(),
  );
  assert.equal(invalid.status, "clarify");
  assert.equal(invalid.code, "field_not_in_result");
  assert.deepEqual(current.field_labels, { region: "区域", revenue: "收入" });

  const newMetricQuery = query({
    columns: ["region", "revenue", "cost"],
    columnTypes: ["string", "double", "double"],
    rows: [{ region: "east", revenue: 5, cost: 3 }],
  });
  const addedMetric = applyChartEditIntent(
    current,
    intent({ patch: { metric_fields: ["revenue", "cost"] } }),
    newMetricQuery,
  );
  assert.equal(addedMetric.status, "apply");
  assert.deepEqual(current.format_by_field, { revenue: { mode: "raw", decimal_places: "auto" } });
  assert.deepEqual(addedMetric.view.format_by_field.cost, { mode: "raw", decimal_places: "auto" });
});

test("resolves pie category colors all-or-nothing and clarifies conflicts or ambiguous labels", () => {
  const pieView = view({ chart_type: "pie", sort: { mode: "original" } });
  const duplicateLabels = query({
    rows: [
      { region: "1", revenue: 5 },
      { region: 1, revenue: 2 },
    ],
  });
  const ambiguous = applyChartEditIntent(
    pieView,
    intent({
      category_color_operations: [{ category_label: "1", color: "blue" }],
    }),
    duplicateLabels,
  );
  assert.deepEqual(ambiguous, { status: "clarify", code: "category_ambiguous" });

  const conflicting = applyChartEditIntent(
    pieView,
    intent({
      category_color_operations: [
        { category_label: "east", color: "blue" },
        { category_label: "east", color: "red" },
      ],
    }),
    query(),
  );
  assert.deepEqual(conflicting, { status: "clarify", code: "conflicting_category_color" });

  const missing = applyChartEditIntent(
    pieView,
    intent({ category_color_operations: [{ category_label: "north", color: "green" }] }),
    query(),
  );
  assert.deepEqual(missing, { status: "clarify", code: "category_not_in_result" });
});

test("maps Top N to matching metric sort and clarifies independent-sort conflicts", () => {
  const ranked = applyChartEditIntent(
    view(),
    intent({
      current_result_operation: {
        kind: "top_n",
        field: "revenue",
        count: 1,
        direction: "desc",
        scope: "current_result",
      },
    }),
    query(),
  );
  assert.equal(ranked.status, "apply");
  assert.deepEqual(ranked.view.current_result_top_n, {
    field: "revenue",
    count: 1,
    direction: "desc",
  });
  assert.deepEqual(ranked.view.sort, { mode: "metric", field: "revenue", direction: "desc" });

  const conflict = applyChartEditIntent(
    view(),
    intent({
      patch: { sort: { mode: "metric", field: "revenue", direction: "asc" } },
      current_result_operation: {
        kind: "top_n",
        field: "revenue",
        count: 1,
        direction: "desc",
        scope: "current_result",
      },
    }),
    query(),
  );
  assert.deepEqual(conflict, { status: "clarify", code: "conflicting_sort" });

  const switched = applyChartEditIntent(
    view({
      current_result_top_n: { field: "revenue", count: 1, direction: "desc" },
      sort: { mode: "metric", field: "revenue", direction: "desc" },
    }),
    intent({ patch: { chart_type: "line" } }),
    query(),
  );
  assert.equal(switched.status, "apply");
  assert.equal(switched.view.current_result_top_n, undefined);
  assert.deepEqual(switched.view.sort, { mode: "original" });

  const addedUnselectedMetric = applyChartEditIntent(
    view(),
    intent({
      patch: { metric_fields: ["revenue", "cost"] },
      current_result_operation: {
        kind: "top_n",
        field: "cost",
        count: 1,
        direction: "desc",
        scope: "current_result",
      },
    }),
    query({
      columns: ["region", "revenue", "cost"],
      columnTypes: ["string", "double", "double"],
      rows: [{ region: "east", revenue: 5, cost: 3 }],
    }),
  );
  assert.deepEqual(addedUnselectedMetric, { status: "clarify", code: "top_n_metric_required" });
});

test("completion dispatch waits for staged flow handlers and carries only turn/result identities", async () => {
  const waiting = [];
  const report = {
    thread_id: "thread-1",
    turn_id: "turn-1",
    history_turn_id: "history-1",
    status: "completed",
    query_result_ids: ["result-2"],
    chart_source_result_ids: ["result-2"],
  };
  const published = publishChartEditTurnCompletion(report, (event) => {
    waiting.push(event.detail.waitUntil(Promise.resolve("committed")));
    assert.equal(event.detail.report.thread_id, "thread-1");
    assert.equal(event.detail.report.query_result_ids[0], "result-2");
    assert.equal("rows" in event.detail.report, false);
  });
  await published;
  await Promise.all(waiting);
});

test("confirmed turn start binds the exact IDs and fixed chart request marker", () => {
  let captured;
  publishChartEditTurnStart({
    thread_id: "thread-1",
    turn_id: "turn-2",
    history_turn_id: "history-2",
    confirmed_chart_query: true,
  }, (event) => { captured = event.detail; });
  assert.deepEqual(captured, {
    thread_id: "thread-1",
    turn_id: "turn-2",
    history_turn_id: "history-2",
    confirmed_chart_query: true,
  });
  assert.equal("message" in captured, false);
  assert.equal("sql" in captured, false);
});

test("mixed chart edits commit only after successful matching query and chart results", async () => {
  const staged = {
    threadId: "thread-1",
    confirmedTurnId: "turn-2",
    confirmedHistoryTurnId: "history-2",
    intent: intent({ patch: { title: "新标题" } }),
  };
  const report = {
    thread_id: "thread-1",
    turn_id: "turn-2",
    history_turn_id: "history-2",
    status: "completed",
    query_result_ids: ["result-2"],
    chart_source_result_ids: ["result-2"],
  };
  const calls = [];
  const handlers = {
    resolveTarget: async (sourceResultId) => ({ source_result_id: sourceResultId }),
    apply: (_target, value) => {
      calls.push("validate");
      return { status: "apply", view: { ...view(), title: value.patch.title } };
    },
    commit: async () => {
      calls.push("commit");
      return { saved: true };
    },
  };

  const applied = await completeStagedChartEdit(report, staged, handlers);
  assert.equal(applied.status, "applied");
  assert.deepEqual(calls, ["validate", "commit"]);

  calls.length = 0;
  const mismatch = await completeStagedChartEdit(
    { ...report, chart_source_result_ids: ["other-result"] },
    staged,
    handlers,
  );
  assert.deepEqual(mismatch, { status: "discarded", reason: "result_mismatch" });
  assert.deepEqual(calls, []);
});

test("query-only turns do not commit an unchanged chart view or create undo history", async () => {
  const staged = {
    threadId: "thread-1",
    confirmedTurnId: "turn-2",
    confirmedHistoryTurnId: "history-2",
    intent: intent({
      status: "query_required",
      query_proposal: { operation: "aggregation" },
      patch: null,
    }),
  };
  const report = {
    thread_id: "thread-1",
    turn_id: "turn-2",
    history_turn_id: "history-2",
    status: "completed",
    query_result_ids: ["result-2"],
    chart_source_result_ids: ["result-2"],
  };
  let resolveCalls = 0;
  let applyCalls = 0;
  let commitCalls = 0;
  const outcome = await completeStagedChartEdit(report, staged, {
    resolveTarget: async () => { resolveCalls += 1; return { source_result_id: "result-2" }; },
    apply: () => { applyCalls += 1; return { status: "apply", view: view() }; },
    commit: async () => { commitCalls += 1; return true; },
  });
  assert.equal(outcome.status, "no_display_changes");
  assert.equal(resolveCalls, 0);
  assert.equal(applyCalls, 0);
  assert.equal(commitCalls, 0);
});

test("failed, cancelled, incompatible, missing-result and cache-failure turns never partially commit", async () => {
  const staged = {
    threadId: "thread-1",
    confirmedTurnId: "turn-2",
    confirmedHistoryTurnId: "history-2",
    intent: intent({
      category_color_operations: [{ category_label: "north", color: "blue" }],
    }),
  };
  const report = {
    thread_id: "thread-1",
    turn_id: "turn-2",
    history_turn_id: "history-2",
    status: "completed",
    query_result_ids: ["result-2"],
    chart_source_result_ids: ["result-2"],
  };
  let commits = 0;
  const baseHandlers = {
    resolveTarget: async (sourceResultId) => ({
      source_result_id: sourceResultId,
      view: view({ chart_type: "pie" }),
      query: query(),
    }),
    apply: (target, value) => applyChartEditIntent(target.view, value, target.query),
    commit: async () => { commits += 1; return true; },
  };
  for (const status of ["failed", "cancelled", "timeout"]) {
    const outcome = await completeStagedChartEdit({ ...report, status }, staged, baseHandlers);
    assert.equal(outcome.status, "discarded");
    assert.equal(outcome.reason, status);
  }
  const missing = await completeStagedChartEdit(report, staged, {
    ...baseHandlers,
    resolveTarget: async () => undefined,
  });
  assert.deepEqual(missing, { status: "discarded", reason: "result_unavailable" });
  const incompatible = await completeStagedChartEdit(report, staged, baseHandlers);
  assert.equal(incompatible.status, "discarded");
  assert.equal(incompatible.reason, "incompatible");
  const cacheFailure = await completeStagedChartEdit(report, staged, {
    ...baseHandlers,
    apply: () => ({ status: "apply", view: view() }),
    commit: async () => undefined,
  });
  assert.deepEqual(cacheFailure, { status: "discarded", reason: "cache_failure" });
  assert.equal(commits, 0);
});


test("manual and interpreted type changes share normalization and preserve compatible colors", () => {
  const before = view({ color_by_metric: { revenue: "blue" } });
  const snapshot = structuredClone(before);
  const manual = normalizeChartViewChange(before, { ...before, chart_type: "line" }, query());
  const interpreted = applyChartEditIntent(before, intent({patch: {chart_type: "line"}}), query());
  assert.equal(interpreted.status, "apply");
  assert.deepEqual(manual, interpreted.view);
  assert.deepEqual(manual.color_by_metric, {revenue: "blue"});
  assert.deepEqual(before, snapshot);
});

test("unrelated edits preserve ordering and clear stale Top N only for incompatible changes", () => {
  const before = view({
    sort: {mode: "metric", field: "revenue", direction: "desc"},
    current_result_top_n: {field: "revenue", count: 2, direction: "desc"},
  });
  const title = normalizeChartViewChange(before, {...before, title: "New"}, query());
  assert.deepEqual(title.sort, before.sort);
  assert.deepEqual(title.current_result_top_n, before.current_result_top_n);
  const line = normalizeChartViewChange(before, {...before, chart_type: "line"}, query());
  assert.equal(line.current_result_top_n, undefined);
  assert.deepEqual(line.sort, {mode: "original"});
});

test("interpreting a local edit only previews and never commits or starts a chat", async () => {
  const calls = [];
  const value = intent({patch: {title: "预览标题"}});
  const result = await interpretChartEdit(value, {
    preview: (proposal) => calls.push(["preview", proposal]),
    commit: () => assert.fail("must not commit before confirmation"),
    showQueryProposal: () => assert.fail("must not query for a display edit"),
  });
  assert.equal(result.status, "preview");
  assert.deepEqual(calls, [["preview", value]]);
});


test("a preview expires if its source, message, thread or saved configuration changes", () => {
  const expected = {sourceResultId: "r", sourceMessageId: "m", threadId: "t", view: view()};
  assert.equal(isChartEditContextCurrent(expected, structuredClone(expected), (a,b) => JSON.stringify(a)===JSON.stringify(b)), true);
  for (const change of [
    {sourceResultId: "new"}, {sourceMessageId: "new"}, {threadId: "new"},
    {view: view({title: "Changed"})},
  ]) assert.equal(isChartEditContextCurrent(expected, {...expected, ...change}, (a,b) => JSON.stringify(a)===JSON.stringify(b)), false);
});


test("switching to pie preserves label visibility unless explicitly changed", () => {
  const before = view({show_data_labels: false});
  const compiled = applyChartEditIntent(before, intent({patch: {chart_type: "pie"}}), query());
  assert.equal(compiled.status, "apply");
  assert.equal(compiled.view.show_data_labels, false);
});

test("explicit incompatible orientation or series color fails instead of becoming a no-op", () => {
  const line = view({chart_type: "line"});
  assert.deepEqual(applyChartEditIntent(line, intent({patch: {bar_orientation: "horizontal"}}), query()),
    {status: "clarify", code: "chart_type_incompatible"});
  const pie = view({chart_type: "pie"});
  assert.deepEqual(applyChartEditIntent(pie, intent({patch: {color_by_metric: {revenue: "red"}}}), query()),
    {status: "clarify", code: "chart_type_incompatible"});
});


test("metric selection removes inherited hidden fields consistently for both editors", () => {
  const data = query({columns: ["region", "revenue", "cost"], columnTypes: ["string", "double", "double"],
    rows: [{region: "east", revenue: 5, cost: 3}]});
  const before = view({metric_fields: ["revenue", "cost"], hidden_metric_fields: ["revenue"],
    format_by_field: {revenue: {mode: "raw", decimal_places: "auto"}, cost: {mode: "raw", decimal_places: "auto"}}});
  const manual = normalizeChartViewChange(before, {...before, metric_fields: ["cost"]}, data);
  const interpreted = applyChartEditIntent(before, intent({patch: {metric_fields: ["cost"]}}), data);
  assert.equal(interpreted.status, "apply");
  assert.deepEqual(manual.hidden_metric_fields, []);
  assert.deepEqual(interpreted.view.hidden_metric_fields, []);
  const invalid = applyChartEditIntent(before, intent({patch: {metric_fields: ["cost"], hidden_metric_fields: ["revenue"]}}), data);
  assert.deepEqual(invalid, {status: "clarify", code: "field_not_in_result"});
});
