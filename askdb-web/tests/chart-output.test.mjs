import assert from "node:assert/strict";
import test from "node:test";

import {
  buildEChartsOption,
  deriveChartRows,
  getChartCompletenessState,
} from "../lib/chart-output.ts";

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
  const tooltip = option.tooltip.formatter([
    { dataIndex: 0, seriesIndex: 0 },
    { dataIndex: 0, seriesIndex: 1 },
  ]);
  assert.match(tooltip, /revenue/iu);
  assert.match(tooltip, /orders/iu);
});

test("adds aligned axis zoom and category pointers only for line and bar charts", () => {
  const manyRows = Array.from({ length: 31 }, (_, index) => ({
    month: `2026-${String(index + 1).padStart(2, "0")}-01`,
    region: `region-${index + 1}`,
    revenue: index + 1,
  }));
  const longLine = buildEChartsOption(line, {
    result_id: "result-1",
    columns: ["month", "revenue"],
    column_types: ["date32[day]", "double"],
    rows: manyRows,
  });
  assert.deepEqual(
    longLine.dataZoom.map(({ type }) => type),
    ["inside", "slider"],
  );
  const zoomed = buildEChartsOption(line, { ...query, rows: manyRows }, undefined, {
    start: 20,
    end: 60,
  });
  assert.equal(zoomed.dataZoom[0].start, 20);
  assert.equal(zoomed.dataZoom[1].end, 60);
  assert.equal(longLine.dataZoom[0].xAxisIndex, 0);
  assert.equal(longLine.dataZoom[1].xAxisIndex, 0);
  const shortLine = buildEChartsOption(line, query);
  assert.deepEqual(
    shortLine.dataZoom.map(({ type }) => type),
    ["inside"],
  );
  assert.equal(longLine.tooltip.axisPointer.type, "cross");

  const horizontal = buildEChartsOption(
    {
      ...line,
      chart_type: "bar",
      x_field: "region",
      title: "revenue by region",
    },
    {
      result_id: "result-1",
      columns: ["region", "revenue"],
      column_types: ["string", "double"],
      rows: manyRows,
    },
    {
      chart_type: "bar",
      dimension_field: "region",
      metric_fields: ["revenue"],
      hidden_metric_fields: [],
      bar_orientation: "horizontal",
      title: "Revenue",
      field_labels: {},
      sort: { mode: "original" },
      format_by_field: { revenue: { mode: "raw", decimal_places: "auto" } },
      show_data_labels: false,
      show_legend: false,
    },
  );
  assert.deepEqual(
    horizontal.dataZoom.map(({ type }) => type),
    ["inside", "slider"],
  );
  assert.equal(horizontal.dataZoom[0].yAxisIndex, 0);
  assert.equal(horizontal.dataZoom[1].yAxisIndex, 0);
  assert.equal(horizontal.tooltip.axisPointer.type, "shadow");
  assert.equal(horizontal.tooltip.axisPointer.axis, "y");
  const vertical = buildEChartsOption(
    {
      ...line,
      chart_type: "bar",
      x_field: "region",
      title: "revenue by region",
    },
    {
      result_id: "result-1",
      columns: ["region", "revenue"],
      column_types: ["string", "double"],
      rows: manyRows,
    },
    {
      chart_type: "bar",
      dimension_field: "region",
      metric_fields: ["revenue"],
      hidden_metric_fields: [],
      bar_orientation: "vertical",
      title: "Revenue",
      field_labels: {},
      sort: { mode: "original" },
      format_by_field: { revenue: { mode: "raw", decimal_places: "auto" } },
      show_data_labels: false,
      show_legend: false,
    },
  );
  assert.equal(vertical.dataZoom[0].xAxisIndex, 0);
  assert.equal(vertical.tooltip.axisPointer.axis, "x");

  const pie = buildEChartsOption(
    { ...line, chart_type: "pie", x_field: "region", title: "revenue by region" },
    {
      result_id: "result-1",
      columns: ["region", "revenue"],
      column_types: ["string", "double"],
      rows: [
        { region: "east", revenue: 2 },
        { region: "west", revenue: 1 },
      ],
    },
  );
  assert.equal(pie.dataZoom, undefined);
  assert.equal(pie.tooltip.axisPointer, undefined);
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
    { name: "east", value: 8, itemStyle: { color: "#3b82f6" } },
    { name: "west", value: 3, itemStyle: { color: "#14b8a6" } },
  ]);
});

test("rejects mismatched source IDs, unknown fields, and malformed values safely", () => {
  assert.equal(buildEChartsOption({ ...line, source_result_id: "other" }, query), null);
  assert.equal(buildEChartsOption({ ...line, x_field: "unknown" }, query), null);
  const malformed = {
    ...query,
    rows: [{ month: "2026-01-01", revenue: "not-a-number", orders: 2 }],
  };
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

test("keeps original row indexes through sorting and current-result Top N without merging duplicate categories", () => {
  const result = {
    result_id: "result-1",
    columns: ["region", "revenue"],
    column_types: ["string", "double"],
    rows: [
      { region: "repeat", revenue: 2 },
      { region: "repeat", revenue: 9 },
      { region: "west", revenue: 4 },
    ],
  };
  const view = {
    chart_type: "bar",
    dimension_field: "region",
    metric_fields: ["revenue"],
    hidden_metric_fields: [],
    bar_orientation: "vertical",
    title: "Revenue",
    field_labels: {},
    sort: { mode: "original" },
    format_by_field: { revenue: { mode: "raw", decimal_places: "auto" } },
    show_data_labels: false,
    show_legend: false,
  };

  assert.deepEqual(
    deriveChartRows(result, view).map(({ row, sourceRowIndex }) => [row.region, sourceRowIndex]),
    [
      ["repeat", 0],
      ["repeat", 1],
      ["west", 2],
    ],
  );
  assert.deepEqual(
    deriveChartRows(result, {
      ...view,
      sort: { mode: "metric", field: "revenue", direction: "desc" },
      current_result_top_n: { field: "revenue", count: 2, direction: "desc" },
    }).map(({ row, sourceRowIndex }) => [row.region, sourceRowIndex]),
    [
      ["repeat", 1],
      ["west", 2],
    ],
  );
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
  assert.equal(formatChartValue("0.0000001", { mode: "raw", decimal_places: "auto" }), "0.0000001");
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
  assert.equal(
    option.series[0].label.formatter({ value: null, dataIndex: 1 }),
    "9,007,199,254,740,993.1",
  );
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
    { name: "east", value: 0.1, itemStyle: { color: "#3b82f6" } },
    { name: "west", value: 0.2, itemStyle: { color: "#14b8a6" } },
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

test("applies current-result Top N to the first 1,000 rows with stable ties", () => {
  const result = {
    result_id: "result-1",
    columns: ["region", "revenue"],
    column_types: ["string", "double"],
    rows: [
      { region: "first tie", revenue: 5 },
      { region: "small", revenue: 1 },
      { region: "second tie", revenue: 5 },
      { region: "largest", revenue: 10 },
    ],
  };
  const artifact = {
    ...line,
    chart_type: "bar",
    x_field: "region",
    title: "revenue by region",
  };
  const view = {
    chart_type: "bar",
    dimension_field: "region",
    metric_fields: ["revenue"],
    hidden_metric_fields: [],
    bar_orientation: "vertical",
    title: "Revenue",
    field_labels: {},
    sort: { mode: "metric", field: "revenue", direction: "desc" },
    format_by_field: { revenue: { mode: "raw", decimal_places: "auto" } },
    show_data_labels: false,
    show_legend: false,
    current_result_top_n: { field: "revenue", count: 2, direction: "desc" },
  };
  const option = buildEChartsOption(artifact, result, view);
  assert.deepEqual(
    option.xAxis.data.map((value, index) => option.xAxis.axisLabel.formatter(value, index)),
    ["largest", "first tie"],
  );
  assert.deepEqual(option.series[0].data, [10, 5]);

  const bottom = buildEChartsOption(artifact, result, {
    ...view,
    sort: { mode: "metric", field: "revenue", direction: "asc" },
    current_result_top_n: { field: "revenue", count: 2, direction: "asc" },
  });
  assert.deepEqual(
    bottom.xAxis.data.map((value, index) => bottom.xAxis.axisLabel.formatter(value, index)),
    ["small", "first tie"],
  );
});

test("Top N ignores rows after the first 1,000 and reports every truncation signal", async () => {
  const { isChartQueryTruncated } = await import("../lib/chart-output.ts");
  const rows = Array.from({ length: 1000 }, (_, index) => ({ region: `r${index}`, revenue: 1 }));
  rows.push({ region: "outside returned prefix", revenue: 1_000_000 });
  const result = {
    result_id: "result-1",
    columns: ["region", "revenue"],
    column_types: ["string", "double"],
    rows,
  };
  const artifact = { ...line, chart_type: "bar", x_field: "region", title: "revenue by region" };
  const option = buildEChartsOption(artifact, result, {
    chart_type: "bar",
    dimension_field: "region",
    metric_fields: ["revenue"],
    hidden_metric_fields: [],
    bar_orientation: "vertical",
    title: "Revenue",
    field_labels: {},
    sort: { mode: "metric", field: "revenue", direction: "desc" },
    format_by_field: { revenue: { mode: "raw", decimal_places: "auto" } },
    show_data_labels: false,
    show_legend: false,
    current_result_top_n: { field: "revenue", count: 1, direction: "desc" },
  });
  assert.equal(option.series[0].data.length, 1);
  assert.notEqual(option.xAxis.axisLabel.formatter(0, 0), "outside returned prefix");
  assert.equal(option.aria.description, "图表仅展示部分查询结果");
  assert.equal(isChartQueryTruncated({ ...query, truncated: true }), true);
  assert.equal(isChartQueryTruncated({ ...query, rowCount: 99 }), true);
  assert.equal(isChartQueryTruncated({ ...query, rows: Array(1001).fill(query.rows[0]) }), true);
});

test("classifies result completeness only from explicit truncation evidence and matching counts", () => {
  const completeRows = [{ value: 1 }, { value: 2 }];
  assert.equal(
    getChartCompletenessState({ rows: completeRows, rowCount: 2, truncated: false }),
    "not_marked_truncated",
  );
  assert.equal(
    getChartCompletenessState({ rows: completeRows, rowCount: 3, truncated: false }),
    "possibly_incomplete",
  );
  assert.equal(
    getChartCompletenessState({ rows: completeRows, rowCount: 1, truncated: false }),
    "possibly_incomplete",
  );
  assert.equal(
    getChartCompletenessState({ rows: completeRows, rowCount: 2, truncated: true }),
    "possibly_incomplete",
  );
  assert.equal(getChartCompletenessState({ rows: completeRows, truncated: false }), "unknown");
  assert.equal(getChartCompletenessState({ rows: completeRows, rowCount: 2 }), "unknown");
  assert.equal(
    getChartCompletenessState({ rows: Array.from({ length: 1001 }, () => ({ value: 1 })) }),
    "possibly_incomplete",
  );
});

test("renders target and mean reference lines, tied peak points, and merged source-row areas", () => {
  const result = {
    result_id: "result-1",
    columns: ["region", "revenue"],
    column_types: ["string", "double"],
    rows: [
      { region: "east", revenue: 1 },
      { region: "west", revenue: 5 },
      { region: "north", revenue: 5 },
      { region: "south", revenue: 2 },
    ],
    row_count: 4,
    truncated: false,
  };
  const artifact = {
    ...line,
    chart_type: "bar",
    x_field: "region",
    title: "revenue by region",
  };
  const option = buildEChartsOption(artifact, result, {
    chart_type: "bar",
    dimension_field: "region",
    metric_fields: ["revenue"],
    hidden_metric_fields: [],
    bar_orientation: "vertical",
    title: "Revenue",
    field_labels: {},
    sort: { mode: "original" },
    format_by_field: { revenue: { mode: "raw", decimal_places: 2 } },
    show_data_labels: false,
    show_legend: false,
    annotations: {
      reference_lines: [
        {
          id: "target",
          metric_field: "revenue",
          kind: "value",
          value: "3.50",
          scope: "returned_rows",
          label: "目标",
        },
        {
          id: "mean",
          metric_field: "revenue",
          kind: "mean",
          scope: "returned_rows",
          label: "已返回均值",
        },
        {
          id: "peak",
          metric_field: "revenue",
          kind: "peak",
          scope: "returned_rows",
          label: "峰值",
        },
      ],
      reference_areas: [{ id: "focus", source_row_indices: [1, 2], label: "关注区间" }],
    },
  });

  assert.deepEqual(
    option.series[0].markLine.data.map(({ yAxis }) => yAxis),
    [3.5, 3.25],
  );
  assert.deepEqual(
    option.series[0].markLine.data.map(({ label }) => label.position),
    ["insideEndTop", "insideEndTop"],
  );
  assert.deepEqual(
    option.series[0].markPoint.data.map(({ coord }) => coord),
    [
      [1, 5],
      [2, 5],
    ],
  );
  assert.equal(option.series[0].markPoint.symbolSize, 24);
  assert.deepEqual(
    option.series[0].markPoint.data.map(({ label }) => label.show),
    [true, false],
  );
  assert.deepEqual(option.series[0].markArea.data, [
    [{ xAxis: 1, name: "关注区间" }, { xAxis: 2 }],
  ]);
});

test("maps fixed metric and pie category palettes to stable typed categories", async () => {
  const { chartCategoryKey } = await import("../lib/chart-output.ts");
  const rows = [
    { region: "east", revenue: 3 },
    { region: "west", revenue: 9 },
    { region: "north", revenue: 5 },
  ];
  const result = {
    result_id: "result-1",
    columns: ["region", "revenue"],
    column_types: ["string", "double"],
    rows,
  };
  const pie = {
    ...line,
    chart_type: "pie",
    x_field: "region",
    title: "revenue by region",
  };
  const view = {
    chart_type: "pie",
    dimension_field: "region",
    metric_fields: ["revenue"],
    hidden_metric_fields: [],
    title: "Revenue",
    field_labels: {},
    sort: { mode: "metric", field: "revenue", direction: "desc" },
    format_by_field: { revenue: { mode: "raw", decimal_places: "auto" } },
    show_data_labels: false,
    show_legend: false,
    pie_category_colors: {
      dimension_field: "region",
      by_category_key: { [chartCategoryKey("east")]: "purple" },
    },
  };
  const option = buildEChartsOption(pie, result, view);
  const colorsByCategory = Object.fromEntries(
    option.series[0].data.map((item) => [item.name, item.itemStyle.color]),
  );
  assert.equal(colorsByCategory.east, "#a855f7");
  assert.equal(colorsByCategory.west, "#14b8a6");
  assert.equal(colorsByCategory.north, "#22c55e");

  const bar = buildEChartsOption(
    { ...line, chart_type: "bar", x_field: "region", title: "revenue by region" },
    result,
    {
      chart_type: "bar",
      dimension_field: "region",
      metric_fields: ["revenue"],
      hidden_metric_fields: [],
      bar_orientation: "vertical",
      title: "Revenue",
      field_labels: {},
      sort: { mode: "original" },
      format_by_field: { revenue: { mode: "raw", decimal_places: "auto" } },
      show_data_labels: false,
      show_legend: false,
      color_by_metric: { revenue: "teal" },
    },
  );
  assert.equal(bar.series[0].itemStyle.color, "#14b8a6");
  assert.equal(bar.series[0].lineStyle, undefined);
});
