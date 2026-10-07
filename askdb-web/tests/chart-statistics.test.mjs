import assert from "node:assert/strict";
import test from "node:test";

import { calculateChartStatistics } from "../lib/chart-statistics.ts";
import { deriveChartRows } from "../lib/chart-output.ts";

const query = {
  resultId: "result-1",
  sql: "",
  columns: ["category", "income", "cost", "empty"],
  columnTypes: ["string", "decimal128(20, 2)", "decimal128(20, 2)", "double"],
  rows: [
    { category: "A", income: "0.10", cost: "-0.50", empty: null },
    { category: "B", income: "0.20", cost: "-0.25", empty: "NaN" },
    { category: "C", income: "-0.20", cost: null, empty: "invalid" },
    { category: "D", income: null, cost: "", empty: undefined },
  ],
  rowCount: 4,
  truncated: false,
};

const view = {
  chart_type: "bar",
  dimension_field: "category",
  metric_fields: ["income", "cost", "empty"],
  hidden_metric_fields: [],
  bar_orientation: "vertical",
  title: "Statistics",
  field_labels: {},
  sort: { mode: "original" },
  format_by_field: {
    income: { mode: "raw", decimal_places: "auto" },
    cost: { mode: "raw", decimal_places: "auto" },
    empty: { mode: "raw", decimal_places: "auto" },
  },
  show_data_labels: false,
  show_legend: true,
};

test("calculates decimal means and peaks independently and excludes invalid values", () => {
  assert.deepEqual(calculateChartStatistics(query, view, "returned_rows"), [
    {
      metric_field: "income",
      mean: "0.033333333333333333333333",
      peak: "0.2",
      validCount: 3,
      scope: "returned_rows",
    },
    {
      metric_field: "cost",
      mean: "-0.375",
      peak: "-0.25",
      validCount: 2,
      scope: "returned_rows",
    },
    {
      metric_field: "empty",
      validCount: 0,
      scope: "returned_rows",
    },
  ]);
});

test("uses the sorted Top N viewport intersection only for viewport-scoped statistics", () => {
  const result = {
    ...query,
    columns: ["category", "income"],
    columnTypes: ["string", "double"],
    rows: [
      { category: "A", income: "2" },
      { category: "B", income: "4" },
      { category: "C", income: "8" },
      { category: "D", income: "10" },
    ],
    rowCount: 4,
  };
  const rankingView = {
    ...view,
    metric_fields: ["income"],
    format_by_field: { income: { mode: "raw", decimal_places: "auto" } },
    sort: { mode: "metric", field: "income", direction: "desc" },
    current_result_top_n: { field: "income", count: 3, direction: "desc" },
  };

  assert.deepEqual(calculateChartStatistics(result, rankingView, "returned_rows"), [
    { metric_field: "income", mean: "6", peak: "10", validCount: 4, scope: "returned_rows" },
  ]);
  assert.deepEqual(
    calculateChartStatistics(
      result,
      rankingView,
      "viewport",
      { start: 50, end: 100 },
      deriveChartRows(result, rankingView),
    ),
    [{ metric_field: "income", mean: "6", peak: "8", validCount: 2, scope: "viewport" }],
  );
});
