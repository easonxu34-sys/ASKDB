import type { EChartsChartArtifact, SuccessfulQueryArtifact } from "@/lib/chat-output";

type QueryShape = SuccessfulQueryArtifact & {
  resultId: string;
  columnTypes: string[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function typeGroup(typeName: string) {
  const normalized = typeName.toLowerCase();
  if (/^(date|time|timestamp|duration)/.test(normalized)) return "temporal";
  if (/^(u?int|float|double|decimal|numeric)/.test(normalized)) return "numeric";
  if (/(struct|list<|map<|binary|null)/.test(normalized)) return "unsupported";
  return "categorical";
}

function isChartArtifact(value: unknown): value is EChartsChartArtifact {
  if (!isRecord(value)) return false;
  return (
    value.kind === "echarts_chart" &&
    value.schema_version === 1 &&
    typeof value.source_result_id === "string" &&
    value.source_result_id.length > 0 &&
    (value.chart_type === "line" || value.chart_type === "bar" || value.chart_type === "pie") &&
    typeof value.x_field === "string" &&
    value.x_field.length > 0 &&
    Array.isArray(value.series_fields) &&
    value.series_fields.length > 0 &&
    value.series_fields.length <= 4 &&
    value.series_fields.every((field) => typeof field === "string" && field.length > 0) &&
    typeof value.title === "string" &&
    value.title.length > 0
  );
}

function isQueryShape(value: unknown): value is QueryShape {
  if (!isRecord(value)) return false;
  return (
    typeof value.resultId === "string" &&
    value.resultId.length > 0 &&
    Array.isArray(value.columns) &&
    value.columns.every((field) => typeof field === "string") &&
    Array.isArray(value.columnTypes) &&
    value.columnTypes.length === value.columns.length &&
    value.columnTypes.every((type) => typeof type === "string") &&
    Array.isArray(value.rows) &&
    value.rows.every(isRecord)
  );
}

function normalizeQuery(value: unknown): QueryShape | null {
  if (!isRecord(value)) return null;
  const normalized = {
    ...value,
    resultId: value.resultId ?? value.result_id,
    columnTypes: value.columnTypes ?? value.column_types,
  };
  return isQueryShape(normalized) ? normalized : null;
}

function safeTitle(artifact: EChartsChartArtifact) {
  return `${artifact.series_fields.join(", ")} by ${artifact.x_field}`;
}

function validRows(query: QueryShape) {
  return query.rows.slice(0, 1000);
}

function safeLabel(value: unknown) {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function temporalOrder(rows: Record<string, unknown>[], field: string) {
  return rows
    .map((row, index) => ({ row, index, time: Date.parse(String(row[field] ?? "")) }))
    .sort((left, right) => {
      const leftValid = Number.isFinite(left.time);
      const rightValid = Number.isFinite(right.time);
      if (leftValid && rightValid) return left.time - right.time || left.index - right.index;
      if (leftValid !== rightValid) return leftValid ? -1 : 1;
      return left.index - right.index;
    })
    .map(({ row }) => row);
}

export function buildEChartsOption(
  chartArtifact: unknown,
  queryArtifact: unknown,
): Record<string, unknown> | null {
  if (!isChartArtifact(chartArtifact)) return null;
  const query = normalizeQuery(queryArtifact);
  if (!query) return null;
  if (chartArtifact.source_result_id !== query.resultId) return null;
  if (chartArtifact.title !== safeTitle(chartArtifact)) return null;

  const fieldIndex = (field: string) => query.columns.indexOf(field);
  const xIndex = fieldIndex(chartArtifact.x_field);
  if (xIndex < 0 || query.columns.lastIndexOf(chartArtifact.x_field) !== xIndex) return null;
  const xGroup = typeGroup(query.columnTypes[xIndex]);
  if (xGroup !== "categorical" && xGroup !== "temporal") return null;

  const seriesIndexes = chartArtifact.series_fields.map(fieldIndex);
  if (
    seriesIndexes.some(
      (index) => index < 0 || query.columns.lastIndexOf(query.columns[index]) !== index,
    ) ||
    seriesIndexes.some((index) => typeGroup(query.columnTypes[index]) !== "numeric") ||
    new Set(chartArtifact.series_fields).size !== chartArtifact.series_fields.length
  )
    return null;
  if (chartArtifact.chart_type === "pie" && chartArtifact.series_fields.length !== 1) return null;

  let rows = validRows(query);
  if (chartArtifact.chart_type === "line" && xGroup === "temporal") {
    rows = temporalOrder(rows, chartArtifact.x_field);
  }
  const labels = rows.map((row) => safeLabel(row[chartArtifact.x_field]));
  const seriesValues = (field: string) =>
    rows.map((row) => {
      const value = row[field];
      return typeof value === "number" && Number.isFinite(value) ? value : null;
    });

  if (chartArtifact.chart_type === "pie") {
    if (new Set(rows.map((row) => JSON.stringify(row[chartArtifact.x_field]))).size > 8) return null;
    const data = rows.map((row) => ({
      name: safeLabel(row[chartArtifact.x_field]),
      value:
        typeof row[chartArtifact.series_fields[0]] === "number" &&
        Number.isFinite(row[chartArtifact.series_fields[0]] as number)
          ? (row[chartArtifact.series_fields[0]] as number)
          : null,
    }));
    return {
      title: {
        text: chartArtifact.title,
        left: "center",
        textStyle: { fontSize: 14, fontWeight: 500 },
      },
      tooltip: { trigger: "item" },
      legend: { show: data.length <= 8, type: "scroll", bottom: 0 },
      series: [
        { type: "pie", radius: ["0%", "68%"], center: ["50%", "54%"], label: { show: true }, data },
      ],
    };
  }

  return {
    title: {
      text: chartArtifact.title,
      left: "left",
      textStyle: { fontSize: 14, fontWeight: 500 },
    },
    tooltip: { trigger: "axis" },
    legend: { show: chartArtifact.series_fields.length > 1, type: "scroll", bottom: 0 },
    grid: {
      left: 12,
      right: 16,
      top: 42,
      bottom: chartArtifact.series_fields.length > 1 ? 50 : 28,
      containLabel: true,
    },
    xAxis: { type: "category", data: labels, axisLabel: { hideOverlap: true } },
    yAxis: { type: "value", scale: true },
    series: chartArtifact.series_fields.map((name) => ({
      name,
      type: chartArtifact.chart_type,
      data: seriesValues(name),
      ...(chartArtifact.chart_type === "line"
        ? { showSymbol: rows.length <= 60, connectNulls: false }
        : {}),
    })),
  };
}
