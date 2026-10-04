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

  assert.deepEqual(option.xAxis.data, [0, 1]);
  assert.equal(option.xAxis.axisLabel.formatter(0, 0), "2026-01-01");
  assert.equal(option.xAxis.axisLabel.formatter(1, 1), "2026-02-01");
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
  const malformed = { ...query, rows: [{ month: "2026-01-01", revenue: "not-a-number", orders: 2 }] };
  assert.deepEqual(buildEChartsOption(line, malformed)?.series[0].data, [null]);
});

test("rejects unsupported chart types and unbounded series", () => {
  assert.equal(buildEChartsOption({ ...line, chart_type: "scatter" }, query), null);
  assert.equal(
    buildEChartsOption({ ...line, series_fields: Array(5).fill("revenue") }, query),
    null,
  );
});

test("sorts metric nulls last and preserves source order for ties", async () => {
  const { buildEChartsOption } = await import("../lib/chart-output.ts");
  const result = {
    result_id: "result-1",
    columns: ["region", "revenue"],
    column_types: ["string", "double"],
    rows: [
      { region: "first tie", revenue: 5 },
      { region: "missing", revenue: null },
      { region: "second tie", revenue: 5 },
      { region: "small", revenue: 1 },
    ],
  };
  const option = buildEChartsOption(
    { ...line, chart_type: "bar", x_field: "region", title: "revenue by region" },
    result,
    {
      chart_type: "bar",
      dimension_field: "region",
      metric_fields: ["revenue"],
      hidden_metric_fields: [],
      bar_orientation: "horizontal",
      title: "Revenue",
      field_labels: {},
      sort: { mode: "metric", field: "revenue", direction: "desc" },
      format_by_field: { revenue: { mode: "raw", decimal_places: "auto" } },
      show_data_labels: false,
      show_legend: false,
    },
  );

  assert.deepEqual(option.yAxis.data, [0, 1, 2, 3]);
  assert.deepEqual(
    option.yAxis.data.map((value, index) => option.yAxis.axisLabel.formatter(value, index)),
    ["first tie", "second tie", "small", "missing"],
  );
  assert.deepEqual(option.series[0].data, [5, 5, 1, null]);
  assert.equal(option.xAxis.type, "value");
  assert.equal(option.yAxis.inverse, true);
});

test("does not aggregate duplicate categories and disables invalid pie inputs", async () => {
  const { buildEChartsOption } = await import("../lib/chart-output.ts");
  const pie = { ...line, chart_type: "pie", x_field: "region", title: "revenue by region" };
  const duplicateQuery = {
    result_id: "result-1",
    columns: ["region", "revenue"],
    column_types: ["string", "double"],
    rows: [
      { region: "east", revenue: 2 },
      { region: "east", revenue: 3 },
    ],
  };
  assert.equal(buildEChartsOption(pie, duplicateQuery), null);
  assert.equal(
    buildEChartsOption(pie, { ...duplicateQuery, rows: [{ region: "", revenue: 1 }] }),
    null,
  );
  assert.equal(
    buildEChartsOption(pie, { ...duplicateQuery, rows: [{ region: "east", revenue: -1 }] }),
    null,
  );
  assert.equal(
    buildEChartsOption(pie, {
      ...duplicateQuery,
      truncated: true,
      rows: [{ region: "east", revenue: 1 }],
    }),
    null,
  );
  const lineWithDuplicates = buildEChartsOption(
    { ...line, chart_type: "line", x_field: "region", title: "revenue by region" },
    duplicateQuery,
  );
  assert.deepEqual(lineWithDuplicates.xAxis.data, [0, 1]);
  assert.equal(lineWithDuplicates.xAxis.axisLabel.formatter(0, 0), "east");
  assert.equal(lineWithDuplicates.xAxis.axisLabel.formatter(1, 1), "east");
  assert.deepEqual(lineWithDuplicates.series[0].data, [2, 3]);
});

test("sorts Arrow time fields by their time values", async () => {
  const { buildEChartsOption } = await import("../lib/chart-output.ts");
  const result = {
    result_id: "result-1",
    columns: ["event_time", "revenue"],
    column_types: ["time64[us]", "double"],
    rows: [
      { event_time: "13:00:00", revenue: 13 },
      { event_time: "08:30:00", revenue: 8 },
    ],
  };
  const option = buildEChartsOption(
    { ...line, x_field: "event_time", title: "revenue by event_time" },
    result,
  );
  assert.deepEqual(option.xAxis.data, [0, 1]);
  assert.equal(option.xAxis.axisLabel.formatter(0, 0), "08:30:00");
  assert.equal(option.xAxis.axisLabel.formatter(1, 1), "13:00:00");
  assert.deepEqual(option.series[0].data, [8, 13]);
});

test("formats percentages and explicit CNY units only from user-selected encodings", async () => {
  const { formatChartValue } = await import("../lib/chart-output.ts");
  assert.equal(
    formatChartValue(0.12, { mode: "percent", encoding: "ratio_0_1", decimal_places: 1 }),
    "12.0%",
  );
  assert.equal(
    formatChartValue(12, { mode: "percent", encoding: "percent_0_100", decimal_places: 0 }),
    "12%",
  );
  assert.equal(
    formatChartValue(12345, {
      mode: "unit_scale",
      unit_family: "CNY",
      source_unit: "yuan",
      display_unit: "ten_thousand_yuan",
      decimal_places: 2,
    }),
    "1.23 万元",
  );
  assert.equal(formatChartValue("1.235", { mode: "raw", decimal_places: 2 }), "1.24");
  assert.equal(formatChartValue("-1.235", { mode: "raw", decimal_places: 2 }), "-1.24");
  assert.equal(
    formatChartValue(12345, { mode: "suffix", suffix: " 元", decimal_places: 0 }),
    "12,345 元",
  );
  assert.equal(formatChartValue(0.12, { mode: "raw", decimal_places: "auto" }), "0.12");
});

test("formats decimal strings without binary floating point loss", async () => {
  const { formatChartValue } = await import("../lib/chart-output.ts");
  assert.equal(
    formatChartValue("0.0000001", { mode: "raw", decimal_places: "auto" }),
    "0.0000001",
  );
  assert.equal(
    formatChartValue("123456789012345678.12345678", {
      mode: "unit_scale",
      unit_family: "CNY",
      source_unit: "yuan",
      display_unit: "ten_thousand_yuan",
      decimal_places: 2,
    }),
    "12,345,678,901,234.57 万元",
  );
});

test("sorts decimal metric strings exactly and plots only exact finite numbers", () => {
  const result = {
    result_id: "result-1",
    columns: ["region", "revenue"],
    column_types: ["string", "decimal128(30, 8)"],
    rows: [
      { region: "smaller", revenue: "9007199254740993.02" },
      { region: "larger", revenue: "9007199254740993.10" },
      { region: "unrepresentable", revenue: "9007199254740993.11" },
    ],
  };
  const option = buildEChartsOption(
    { ...line, chart_type: "bar", x_field: "region", title: "revenue by region" },
    result,
    {
      chart_type: "bar",
      dimension_field: "region",
      metric_fields: ["revenue"],
      hidden_metric_fields: [],
      bar_orientation: "horizontal",
      title: "Revenue",
      field_labels: {},
      sort: { mode: "metric", field: "revenue", direction: "desc" },
      format_by_field: { revenue: { mode: "raw", decimal_places: "auto" } },
      show_data_labels: true,
      show_legend: false,
    },
  );

  assert.deepEqual(
    option.yAxis.data.map((value, index) => option.yAxis.axisLabel.formatter(value, index)),
    ["unrepresentable", "larger", "smaller"],
  );
  assert.deepEqual(option.series[0].data, [null, null, null]);
  assert.equal(option.series[0].label.formatter({ value: null, dataIndex: 1 }), "9,007,199,254,740,993.1");
});

test("validates and displays exact decimal pie values and shares", () => {
  const result = {
    result_id: "result-1",
    columns: ["region", "revenue"],
    column_types: ["string", "decimal128(12, 4)"],
    rows: [
      { region: "east", revenue: "0.1" },
      { region: "west", revenue: "0.2" },
    ],
  };
  const option = buildEChartsOption(
    { ...line, chart_type: "pie", x_field: "region", title: "revenue by region" },
    result,
  );

  assert.deepEqual(option.series[0].data, [
    { name: "east", value: 0.1 },
    { name: "west", value: 0.2 },
  ]);
  assert.match(option.tooltip.formatter({ dataIndex: 0 }), /分类占比: 33\.33%/u);
});

test("warns when source scales differ and uses one automatic shared axis precision", async () => {
  const { getChartUnitWarning } = await import("../lib/chart-output.ts");
  const view = {
    chart_type: "bar",
    dimension_field: "region",
    metric_fields: ["amount", "ratio"],
    hidden_metric_fields: [],
    bar_orientation: "vertical",
    title: "Revenue",
    field_labels: {},
    sort: { mode: "original" },
    format_by_field: {
      amount: {
        mode: "unit_scale",
        unit_family: "CNY",
        source_unit: "yuan",
        display_unit: "yuan",
        decimal_places: 0,
      },
      ratio: { mode: "percent", encoding: "percent_0_100", decimal_places: 2 },
    },
    show_data_labels: false,
    show_legend: true,
  };
  assert.match(getChartUnitWarning(view), /单位不同/u);
  assert.match(
    getChartUnitWarning({
      ...view,
      metric_fields: ["amount", "revenue"],
      format_by_field: {
        amount: {
          mode: "unit_scale",
          unit_family: "CNY",
          source_unit: "yuan",
          display_unit: "ten_thousand_yuan",
          decimal_places: 2,
        },
        revenue: {
          mode: "unit_scale",
          unit_family: "CNY",
          source_unit: "ten_thousand_yuan",
          display_unit: "ten_thousand_yuan",
          decimal_places: 2,
        },
      },
    }),
    /单位不同/u,
  );

  const option = buildEChartsOption(
    {
      ...line,
      chart_type: "bar",
      x_field: "region",
      series_fields: ["amount", "ratio"],
      title: "amount, ratio by region",
    },
    {
      result_id: "result-1",
      columns: ["region", "amount", "ratio"],
      column_types: ["string", "double", "double"],
      rows: [{ region: "east", amount: 1.234, ratio: 50 }],
    },
    {
      ...view,
      format_by_field: {
        amount: { mode: "raw", decimal_places: 0 },
        ratio: { mode: "raw", decimal_places: 2 },
      },
    },
  );
  assert.equal(option.yAxis.axisLabel.formatter(1.234), "1.234");
});
