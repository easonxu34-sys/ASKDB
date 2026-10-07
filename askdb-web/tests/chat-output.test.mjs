import assert from "node:assert/strict";
import test from "node:test";

function chartView(overrides = {}) {
  return {
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
    ...overrides,
  };
}

function chartQuery(overrides = {}) {
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

test("formats every executed query result in order", async () => {
  const chatOutput = await import("../lib/chat-output.ts").catch(() => null);
  assert.ok(chatOutput, "chat output formatter should be available");

  const formatted = chatOutput.formatQueryResults([
    { data: { sql: "SELECT 1", columns: ["value"], rows: [{ value: 1 }] } },
    { data: { sql: "SELECT 2", columns: ["value"], rows: [{ value: 2 }] } },
  ]);

  assert.ok(formatted.includes("SELECT 1"));
  assert.ok(formatted.includes("SELECT 2"));
  assert.ok(formatted.indexOf("SELECT 1") < formatted.indexOf("SELECT 2"));
});

test("accepts only supported chart artifacts with a matching query result", async () => {
  const chatOutput = await import("../lib/chat-output.ts");
  const query = {
    ok: true,
    data: {
      result_id: "result-1",
      sql: "SELECT month, revenue",
      columns: ["month", "revenue"],
      column_types: ["date32[day]", "double"],
      rows: [{ month: "2026-01-01", revenue: 5 }],
    },
  };
  const chart = {
    artifact: {
      kind: "echarts_chart",
      schema_version: 1,
      source_result_id: "result-1",
      chart_type: "line",
      x_field: "month",
      series_fields: ["revenue"],
      title: "revenue by month",
    },
  };

  const [persistedPart] = chatOutput.getChartMessageParts([query, chart]);
  assert.equal(persistedPart.data.persistenceAvailable, true);
  const [volatilePart] = chatOutput.getChartMessageParts([query, chart], {
    persistenceAvailable: false,
  });
  assert.equal(volatilePart.data.persistenceAvailable, false);
  assert.equal(
    chatOutput.getChartMessageParts([query, { artifact: { ...chart.artifact, schema_version: 2 } }])
      .length,
    0,
  );
  assert.equal(
    chatOutput.getChartMessageParts([
      query,
      { artifact: { ...chart.artifact, x_field: "unknown" } },
    ]).length,
    0,
  );
  assert.equal(chatOutput.getChartMessageParts([chart]).length, 0);
});

test("uses an independent chart view override while keeping the recommendation immutable", async () => {
  const chatOutput = await import("../lib/chat-output.ts");
  const query = {
    result_id: "result-1",
    sql: "SELECT month, revenue, orders",
    columns: ["month", "revenue", "orders"],
    column_types: ["date32[day]", "double", "int64"],
    rows: [{ month: "2026-01-01", revenue: 12, orders: 2 }],
  };
  const recommendation = {
    kind: "echarts_chart",
    schema_version: 1,
    source_result_id: "result-1",
    chart_type: "line",
    x_field: "month",
    series_fields: ["revenue"],
    title: "revenue by month",
  };
  const recommendedView = chatOutput.createRecommendedChartView(recommendation, {
    resultId: query.result_id,
    columns: query.columns,
    columnTypes: query.column_types,
    rows: query.rows,
  });
  assert.equal(recommendedView.chart_type, "line");
  assert.equal(recommendedView.format_by_field.revenue.mode, "raw");
  assert.equal(recommendedView.show_data_labels, false);

  const override = {
    kind: "chart_view_override",
    schema_version: 1,
    source_result_id: "result-1",
    view: {
      ...recommendedView,
      chart_type: "bar",
      bar_orientation: "horizontal",
      title: "Monthly revenue",
      field_labels: { month: "月份", revenue: "收入" },
    },
  };
  const [part] = chatOutput.getChartMessageParts([
    { data: query },
    { artifact: recommendation },
    override,
  ]);

  assert.equal(part.data.artifact.chart_type, "line");
  assert.equal(part.data.view.chart_type, "bar");
  assert.equal(part.data.view.title, "Monthly revenue");
  assert.equal(part.data.view.field_labels.revenue, "收入");
  assert.equal(part.data.hasOverride, true);
  assert.deepEqual(part.data.undoHistory, []);
});

test("normalizes annotations, validates their data-bound targets, and reads schema four", async () => {
  const chatOutput = await import("../lib/chat-output.ts");
  const query = chartQuery();
  const artifact = {
    kind: "echarts_chart",
    schema_version: 1,
    source_result_id: "result-1",
    chart_type: "bar",
    x_field: "region",
    series_fields: ["revenue"],
    title: "revenue by region",
  };
  const recommended = chatOutput.createRecommendedChartView(artifact, query);
  assert.deepEqual(recommended.annotations, { reference_lines: [], reference_areas: [] });

  const candidate = {
    ...recommended,
    annotations: {
      reference_lines: [
        {
          id: "target-1",
          metric_field: "revenue",
          kind: "value",
          value: "1.2500",
          scope: "returned_rows",
          label: "目标",
        },
        {
          id: "mean-1",
          metric_field: "revenue",
          kind: "mean",
          scope: "viewport",
          label: "窗口均值",
        },
        {
          id: "peak-1",
          metric_field: "revenue",
          kind: "peak",
          scope: "returned_rows",
          label: "峰值",
        },
      ],
      reference_areas: [{ id: "area-1", source_row_indices: [1, 0, 1], label: "重点区间" }],
    },
  };
  const validation = chatOutput.validateChartView(candidate, query);
  assert.deepEqual(validation.errors, {});
  assert.deepEqual(
    validation.view.annotations.reference_lines,
    candidate.annotations.reference_lines,
  );
  assert.deepEqual(validation.view.annotations.reference_areas, [
    { id: "area-1", source_row_indices: [1, 0], label: "重点区间" },
  ]);
  assert.equal(chatOutput.chartViewsEqual(recommended, validation.view), false);
  assert.equal(
    chatOutput.readChartViewOverride({
      kind: "chart_view_override",
      schema_version: 4,
      source_result_id: "result-1",
      view: candidate,
    }).schema_version,
    4,
  );
});

test("rejects annotation fields, values, scopes, and source row indexes outside the chart contract", async () => {
  const chatOutput = await import("../lib/chat-output.ts");
  const query = chartQuery();
  const artifact = {
    kind: "echarts_chart",
    schema_version: 1,
    source_result_id: "result-1",
    chart_type: "bar",
    x_field: "region",
    series_fields: ["revenue"],
    title: "revenue by region",
  };
  const recommended = chatOutput.createRecommendedChartView(artifact, query);
  const invalid = chatOutput.validateChartView(
    {
      ...recommended,
      annotations: {
        reference_lines: [
          {
            id: "bad-line",
            metric_field: "region",
            kind: "value",
            value: "Infinity",
            scope: "all_rows",
            label: "x".repeat(81),
          },
        ],
        reference_areas: [{ id: "bad-area", source_row_indices: [2], label: "超范围" }],
      },
    },
    query,
  );
  assert.ok(invalid.errors.annotations);

  const tooMany = chatOutput.validateChartView(
    {
      ...recommended,
      annotations: {
        reference_lines: Array.from({ length: 9 }, (_, index) => ({
          id: `line-${index}`,
          metric_field: "revenue",
          kind: "mean",
          scope: "returned_rows",
          label: `均值 ${index}`,
        })),
        reference_areas: [],
      },
    },
    query,
  );
  assert.ok(tooMany.errors.annotations);
});

test("ignores unknown versions and invalid or mismatched overrides", async () => {
  const chatOutput = await import("../lib/chat-output.ts");
  const query = {
    result_id: "result-1",
    sql: "SELECT region, revenue",
    columns: ["region", "revenue"],
    column_types: ["string", "double"],
    rows: [{ region: "east", revenue: 12 }],
  };
  const chart = {
    kind: "echarts_chart",
    schema_version: 1,
    source_result_id: "result-1",
    chart_type: "bar",
    x_field: "region",
    series_fields: ["revenue"],
    title: "revenue by region",
  };
  const recommended = chatOutput.createRecommendedChartView(chart, {
    resultId: query.result_id,
    columns: query.columns,
    columnTypes: query.column_types,
    rows: query.rows,
  });
  for (const override of [
    { kind: "chart_view_override", schema_version: 3, source_result_id: "result-1", view: {} },
    {
      kind: "chart_view_override",
      schema_version: 1,
      source_result_id: "result-1",
      view: { ...recommended, dimension_field: "missing" },
    },
  ]) {
    const [part] = chatOutput.getChartMessageParts([
      { data: query },
      { artifact: chart },
      override,
    ]);
    assert.deepEqual(part.data.view, recommended);
    assert.equal(part.data.hasOverride, false);
    assert.ok(part.data.overrideNotice);
  }
  const [mismatchedPart] = chatOutput.getChartMessageParts([
    { data: query },
    { artifact: chart },
    {
      kind: "chart_view_override",
      schema_version: 1,
      source_result_id: "other-result",
      view: { ...recommended, title: "Another chart" },
    },
  ]);
  assert.deepEqual(mismatchedPart.data.view, recommended);
  assert.equal(mismatchedPart.data.hasOverride, false);

  const [v2Part] = chatOutput.getChartMessageParts([
    { data: query },
    { artifact: chart },
    {
      kind: "chart_view_override",
      schema_version: 2,
      source_result_id: "result-1",
      view: { ...recommended, color_by_metric: { revenue: "teal" } },
      undo_history: [{ view: recommended, summary: "修改指标颜色", origin: "manual" }],
    },
  ]);
  assert.equal(v2Part.data.view.color_by_metric.revenue, "teal");
  assert.deepEqual(v2Part.data.undoHistory, [
    { view: recommended, summary: "修改指标颜色", origin: "manual" },
  ]);
});

test("classifies only the documented Arrow field types", async () => {
  const chatOutput = await import("../lib/chat-output.ts");
  assert.equal(chatOutput.getArrowFieldKind("decimal128(12, 2)"), "numeric");
  assert.equal(chatOutput.getArrowFieldKind("timestamp[us]"), "temporal");
  assert.equal(chatOutput.getArrowFieldKind("dictionary<values=string>"), "categorical");
  assert.equal(chatOutput.getArrowFieldKind("struct<amount: double>"), undefined);
  assert.equal(chatOutput.getArrowFieldKind("custom_business_number"), undefined);
  assert.equal(chatOutput.getArrowFieldKind("string_custom"), undefined);
});

test("reports unusable recommendations without hiding the query result", async () => {
  const chatOutput = await import("../lib/chat-output.ts");
  const query = {
    result_id: "result-1",
    sql: "SELECT region, revenue",
    columns: ["region", "revenue"],
    column_types: ["string", "double"],
    rows: [
      { region: "east", revenue: 4 },
      { region: "east", revenue: 3 },
    ],
  };
  const invalidChart = {
    kind: "echarts_chart",
    schema_version: 1,
    source_result_id: "result-1",
    chart_type: "pie",
    x_field: "region",
    series_fields: ["revenue"],
    title: "revenue by region",
  };
  assert.equal(chatOutput.getSuccessfulQueryArtifacts([{ data: query }]).length, 1);
  assert.deepEqual(
    chatOutput.getChartUnavailableMessages([{ data: query }, { artifact: invalidChart }]),
    ["图表暂不可用：推荐图表字段或查询结果不满足绘制条件。"],
  );
  assert.match(
    chatOutput.getChartUnavailableMessages([{ artifact: invalidChart }])[0],
    /查询结果缓存已失效/u,
  );
});

test("validates fixed metric palettes and rejects CSS colors or pie metric colors", async () => {
  const { validateChartView } = await import("../lib/chat-output.ts");
  const query = chartQuery();
  const accepted = validateChartView(chartView({ color_by_metric: { revenue: "teal" } }), query);
  assert.equal(accepted.view.color_by_metric.revenue, "teal");

  assert.ok(
    validateChartView(chartView({ color_by_metric: { revenue: "#ff0000" } }), query).errors
      .color_by_metric,
  );
  assert.ok(
    validateChartView(chartView({ color_by_metric: { unknown: "blue" } }), query).errors
      .color_by_metric,
  );
  assert.ok(
    validateChartView(chartView({ chart_type: "pie", color_by_metric: { revenue: "blue" } }), query)
      .errors.chart_type,
  );
});

test("validates typed pie category keys and detects missing or ambiguous labels", async () => {
  const { chartCategoryKey, resolvePieCategoryLabel } = await import("../lib/chart-output.ts");
  const { validateChartView } = await import("../lib/chat-output.ts");
  const query = chartQuery({
    columns: ["region", "revenue"],
    columnTypes: ["string", "double"],
    rows: [
      { region: "1", revenue: 4 },
      { region: 1, revenue: 6 },
    ],
  });
  const stringKey = chartCategoryKey("1");
  const numericKey = chartCategoryKey(1);
  assert.notEqual(stringKey, numericKey);
  assert.deepEqual(resolvePieCategoryLabel(query, "region", "1"), { status: "ambiguous" });
  assert.deepEqual(resolvePieCategoryLabel(query, "region", "missing"), { status: "not_found" });

  const valid = validateChartView(
    chartView({
      chart_type: "pie",
      sort: { mode: "original" },
      pie_category_colors: {
        dimension_field: "region",
        by_category_key: { [stringKey]: "blue", [numericKey]: "red" },
      },
    }),
    query,
  );
  assert.deepEqual(valid.errors, {});
  assert.deepEqual(valid.view.pie_category_colors.by_category_key, {
    [stringKey]: "blue",
    [numericKey]: "red",
  });
  assert.deepEqual(resolvePieCategoryLabel(chartQuery(), "region", "east"), {
    status: "matched",
    category_key: chartCategoryKey("east"),
  });
  assert.ok(
    validateChartView(
      chartView({
        chart_type: "pie",
        pie_category_colors: {
          dimension_field: "region",
          by_category_key: { [chartCategoryKey("unknown")]: "blue" },
        },
      }),
      chartQuery(),
    ).errors.pie_category_colors,
  );
  assert.ok(
    validateChartView(
      chartView({
        chart_type: "pie",
        pie_category_colors: {
          dimension_field: "other",
          by_category_key: { [chartCategoryKey("east")]: "blue" },
        },
      }),
      chartQuery(),
    ).errors.pie_category_colors,
  );
});

test("validates current-result Top N field, chart type, count and matching sort", async () => {
  const { validateChartView } = await import("../lib/chat-output.ts");
  const query = chartQuery({
    columns: ["region", "revenue", "orders", "label"],
    columnTypes: ["string", "double", "int64", "string"],
    rows: [{ region: "east", revenue: 5, orders: 1, label: "x" }],
  });
  const topNView = chartView({
    sort: { mode: "metric", field: "revenue", direction: "desc" },
    current_result_top_n: { field: "revenue", count: 10, direction: "desc" },
  });
  assert.equal(validateChartView(topNView, query).view.current_result_top_n.count, 10);

  const invalid = [
    { ...topNView, current_result_top_n: { field: "unknown", count: 3, direction: "desc" } },
    { ...topNView, hidden_metric_fields: ["revenue"] },
    {
      ...topNView,
      metric_fields: ["orders"],
      format_by_field: { orders: { mode: "raw", decimal_places: "auto" } },
    },
    { ...topNView, current_result_top_n: { field: "label", count: 3, direction: "desc" } },
    { ...topNView, current_result_top_n: { field: "revenue", count: 0, direction: "desc" } },
    { ...topNView, current_result_top_n: { field: "revenue", count: 101, direction: "desc" } },
    { ...topNView, sort: { mode: "metric", field: "revenue", direction: "asc" } },
    {
      ...topNView,
      chart_type: "line",
      sort: { mode: "dimension", field: "region", direction: "asc" },
    },
  ];
  for (const view of invalid) {
    assert.equal(validateChartView(view, query).view, undefined);
  }
});

test("manual sort, chart type and ranking field changes clear Top N while display edits keep it", async () => {
  const { clearTopNForManualChange } = await import("../lib/chat-output.ts");
  const ranked = chartView({
    sort: { mode: "metric", field: "revenue", direction: "desc" },
    current_result_top_n: { field: "revenue", count: 5, direction: "desc" },
  });
  assert.equal(
    clearTopNForManualChange(ranked, { ...ranked, title: "New title" }).current_result_top_n.count,
    5,
  );
  assert.equal(
    clearTopNForManualChange(ranked, { ...ranked, sort: { mode: "original" } })
      .current_result_top_n,
    undefined,
  );
  assert.equal(
    clearTopNForManualChange(ranked, { ...ranked, chart_type: "line" }).current_result_top_n,
    undefined,
  );
  assert.equal(
    clearTopNForManualChange(ranked, { ...ranked, hidden_metric_fields: ["revenue"] })
      .current_result_top_n,
    undefined,
  );
  assert.equal(
    clearTopNForManualChange(ranked, { ...ranked, metric_fields: ["orders"] }).current_result_top_n,
    undefined,
  );
});

test("accepts pie Top N only when existing pie constraints are met", async () => {
  const { validateChartView } = await import("../lib/chat-output.ts");
  const pie = chartView({
    chart_type: "pie",
    sort: { mode: "metric", field: "revenue", direction: "desc" },
    current_result_top_n: { field: "revenue", count: 1, direction: "desc" },
  });
  assert.equal(validateChartView(pie, chartQuery()).view.current_result_top_n.count, 1);
  assert.equal(
    validateChartView(
      pie,
      chartQuery({
        rows: [
          { region: "east", revenue: 5 },
          { region: "east", revenue: 2 },
        ],
      }),
    ).view,
    undefined,
  );
});

test("ignores invalid undo history without rejecting a valid current override", async () => {
  const chatOutput = await import("../lib/chat-output.ts");
  const query = {
    result_id: "result-1",
    sql: "SELECT region, revenue",
    columns: ["region", "revenue"],
    column_types: ["string", "double"],
    rows: [{ region: "east", revenue: 12 }],
  };
  const chart = {
    kind: "echarts_chart",
    schema_version: 1,
    source_result_id: "result-1",
    chart_type: "bar",
    x_field: "region",
    series_fields: ["revenue"],
    title: "revenue by region",
  };
  const recommended = chatOutput.createRecommendedChartView(chart, {
    resultId: query.result_id,
    columns: query.columns,
    columnTypes: query.column_types,
    rows: query.rows,
  });
  const [part] = chatOutput.getChartMessageParts([
    { data: query },
    { artifact: chart },
    {
      kind: "chart_view_override",
      schema_version: 2,
      source_result_id: "result-1",
      view: { ...recommended, title: "Saved view" },
      undo_history: [
        {
          view: { ...recommended, dimension_field: "missing" },
          summary: "invalid",
          origin: "manual",
        },
      ],
    },
  ]);
  assert.equal(part.data.view.title, "Saved view");
  assert.deepEqual(part.data.undoHistory, []);
});
