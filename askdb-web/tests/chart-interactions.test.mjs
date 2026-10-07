import assert from "node:assert/strict";
import test from "node:test";

import {
  buildChartSelectionContext,
  chartIndexesForCoordinateRanges,
  fillComposerWithChartSelection,
  normalizeChartDataIndexes,
} from "../lib/chart-interactions.ts";

test("maps only unique in-range chart data indexes to projected source rows", () => {
  const rows = [
    { row: { region: "repeat", revenue: 9 }, sourceRowIndex: 1 },
    { row: { region: "west", revenue: 4 }, sourceRowIndex: 2 },
    { row: { region: "repeat", revenue: 2 }, sourceRowIndex: 0 },
  ];

  assert.deepEqual(normalizeChartDataIndexes(rows, [1, 0, 1, -1, 3, NaN, "2"]), [2, 1]);
  assert.deepEqual(normalizeChartDataIndexes(rows, [2, 0, 1], 2), [0, 1]);
});

test("maps linked lineX and lineY category ranges to exact plotted category indexes", () => {
  assert.deepEqual(
    chartIndexesForCoordinateRanges(6, [
      [1, 3],
      [3, 4],
      [-2, 0],
      [2.2, 2.8],
      [4, 3],
    ]),
    [1, 2, 3, 4, 0],
  );
  assert.deepEqual(chartIndexesForCoordinateRanges(1001, [[0, 1000]]).slice(-1), [999]);
  assert.deepEqual(chartIndexesForCoordinateRanges(0, [[0, 1]]), []);
});

test("builds bounded follow-up context from visible fields in the current result only", () => {
  const query = {
    resultId: "result-current",
    columns: ["region", "revenue", "secret"],
    rows: [
      { region: "东区", revenue: "1.20", secret: "never include" },
      { region: "西区", revenue: 5, secret: "hidden" },
    ],
  };
  const view = {
    dimension_field: "region",
    metric_fields: ["revenue", "secret"],
    hidden_metric_fields: ["secret"],
    field_labels: { region: "地区", revenue: "收入", secret: "内部字段" },
  };

  const context = buildChartSelectionContext(
    query,
    "result-current",
    view,
    [1, 0, 1],
    "查询结果可能不完整",
  );

  assert.equal(context.ok, true);
  assert.deepEqual(context.sourceRowIndices, [1, 0]);
  assert.match(context.text, /地区 \| 收入/u);
  assert.match(context.text, /"西区" \| "5"/u);
  assert.match(context.text, /查询结果可能不完整/u);
  assert.doesNotMatch(context.text, /secret|never include|内部字段/u);
});

test("rejects stale, oversized, invalid, and overlong selection context without truncation", () => {
  const query = {
    resultId: "result-current",
    columns: ["region", "revenue"],
    rows: [{ region: "东区", revenue: "1".repeat(257) }],
  };
  const view = {
    dimension_field: "region",
    metric_fields: ["revenue"],
    hidden_metric_fields: [],
    field_labels: {},
  };
  assert.equal(
    buildChartSelectionContext(query, "result-old", view, [0], "完整性未明确").reason,
    "stale_result",
  );
  assert.equal(
    buildChartSelectionContext(
      query,
      "result-current",
      view,
      [0, ...Array(50).fill(0)],
      "完整性未明确",
    ).reason,
    "cell_too_long",
  );
  assert.equal(
    buildChartSelectionContext(
      { ...query, rows: [{ region: "东区", revenue: 1 }] },
      "result-current",
      view,
      Array.from({ length: 51 }, (_, index) => index),
      "完整性未明确",
    ).reason,
    "invalid_selection",
  );
  assert.equal(
    buildChartSelectionContext(
      { ...query, rows: Array.from({ length: 51 }, () => ({ region: "东区", revenue: 1 })) },
      "result-current",
      view,
      Array.from({ length: 51 }, (_, index) => index),
      "完整性未明确",
    ).reason,
    "too_many_rows",
  );
});

test("fills an editable composer draft without sending the chat turn", () => {
  let value = "用户已有草稿";
  let sendCount = 0;
  const composer = {
    setText(text) {
      value = text;
    },
    send() {
      sendCount += 1;
    },
  };

  const result = fillComposerWithChartSelection(composer, value, "选中的图表数据");

  assert.equal(result.ok, true);
  assert.equal(value, "用户已有草稿\n\n选中的图表数据");
  assert.equal(sendCount, 0);
});
