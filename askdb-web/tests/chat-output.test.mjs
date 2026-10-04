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

  assert.equal(chatOutput.getChartMessageParts([query, chart]).length, 1);
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
