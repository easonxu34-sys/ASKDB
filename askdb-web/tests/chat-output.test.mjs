import assert from "node:assert/strict";
import test from "node:test";

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
    { kind: "chart_view_override", schema_version: 2, source_result_id: "result-1", view: {} },
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
