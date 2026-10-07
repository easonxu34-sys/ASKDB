import assert from "node:assert/strict";
import test from "node:test";

import { buildChartCsv, getChartCsvFilename } from "../lib/chart-export.ts";

test("exports returned artifact columns and values with RFC-style CSV escaping", () => {
  const result = buildChartCsv({
    columns: ["地区", "说明", "金额"],
    columnTypes: ["string", "string", "decimal128"],
    rows: [
      { 地区: "东区", 说明: '两行,\n含引号 "好"', 金额: -2.5 },
      { 地区: "西区", 说明: null, 金额: 0 },
    ],
  });

  assert.equal(result.ok, true);
  assert.equal(result.rowCount, 2);
  assert.equal(result.columnCount, 3);
  assert.equal(
    result.content,
    '\uFEFF"地区","说明","金额"\r\n"东区","两行,\n含引号 ""好""","-2.5"\r\n"西区","","0"',
  );
});

test("neutralizes spreadsheet formulas while preserving typed negative decimal text", () => {
  const result = buildChartCsv({
    columns: ["label", "numeric", "text"],
    columnTypes: ["string", "decimal128", "string"],
    rows: [{ label: "=2+3", numeric: "-2.50", text: "\t@SUM(A1)" }],
  });

  assert.equal(result.ok, true);
  assert.match(result.content, /"'=2\+3","-2\.50","'\t@SUM\(A1\)"/u);
});

test("rejects duplicate columns and oversized or malformed exports", () => {
  assert.deepEqual(buildChartCsv({ columns: ["x", "x"], rows: [] }), {
    ok: false,
    reason: "duplicate_columns",
  });
  assert.deepEqual(buildChartCsv({ columns: ["x"], rows: [null] }), {
    ok: false,
    reason: "invalid_artifact",
  });
  assert.deepEqual(buildChartCsv({ columns: ["x"], rows: Array(10_001).fill({ x: 1 }) }), {
    ok: false,
    reason: "export_limit",
  });
  assert.deepEqual(buildChartCsv(null), { ok: false, reason: "invalid_artifact" });
  assert.equal(
    getChartCsvFilename(12, "possibly_incomplete"),
    "askdb-chart-results-12-possibly_incomplete.csv",
  );
});
