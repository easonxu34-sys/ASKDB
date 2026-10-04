import assert from "node:assert/strict";
import test from "node:test";

import { buildEChartsOption } from "../lib/chart-output.ts";

const query = {
  result_id: "result-1",
  columns: ["month", "revenue", "orders"],
  column_types: ["date32[day]", "double", "int64"],
  rows: [
    { month: "2026-02-01", revenue: 8, orders: 2 },
    { month: "2026-01-01", revenue: 3, orders: 1 },
  ],
};

const line = {
  kind: "echarts_chart",
  schema_version: 1,
  source_result_id: "result-1",
  chart_type: "line",
  x_field: "month",
  series_fields: ["revenue"],
  title: "revenue by month",
};

test("builds deterministic line options and sorts valid temporal values", () => {
  const option = buildEChartsOption(line, query);

  assert.deepEqual(option.xAxis.data, ["2026-01-01", "2026-02-01"]);
  assert.deepEqual(option.series[0].data, [3, 8]);
  assert.equal(option.series[0].type, "line");
  assert.equal(option.tooltip.trigger, "axis");
});

test("builds bar options with multiple bounded numeric series", () => {
  const option = buildEChartsOption(
    {
      ...line,
      chart_type: "bar",
      x_field: "month",
      series_fields: ["revenue", "orders"],
      title: "revenue, orders by month",
    },
    query,
  );

  assert.equal(option.series.length, 2);
  assert.deepEqual(
    option.series.map((series) => series.type),
    ["bar", "bar"],
  );
  assert.equal(option.legend.show, true);
});

test("builds pie options from the exact categorical and numeric rows", () => {
  const artifact = {
    ...line,
    chart_type: "pie",
    x_field: "region",
    series_fields: ["revenue"],
    title: "revenue by region",
  };
  const result = {
    result_id: "result-1",
    columns: ["region", "revenue"],
    column_types: ["string", "double"],
    rows: [
      { region: "east", revenue: 8 },
      { region: "west", revenue: 3 },
    ],
  };

  const option = buildEChartsOption(artifact, result);

  assert.deepEqual(option.series[0].data, [
    { name: "east", value: 8 },
    { name: "west", value: 3 },
  ]);
});

test("rejects mismatched source IDs, unknown fields, and malformed values safely", () => {
  assert.equal(buildEChartsOption({ ...line, source_result_id: "other" }, query), null);
  assert.equal(buildEChartsOption({ ...line, x_field: "unknown" }, query), null);
  const malformed = { ...query, rows: [{ month: "2026-01-01", revenue: "8", orders: 2 }] };
  assert.deepEqual(buildEChartsOption(line, malformed)?.series[0].data, [null]);
});

test("rejects unsupported chart types and unbounded series", () => {
  assert.equal(buildEChartsOption({ ...line, chart_type: "scatter" }, query), null);
  assert.equal(
    buildEChartsOption({ ...line, series_fields: Array(5).fill("revenue") }, query),
    null,
  );
});
