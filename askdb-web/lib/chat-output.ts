export function formatQueryResults(outputs: unknown[]) {
  return outputs.map(formatQueryResult).filter(Boolean).join("\n\n---\n\n");
}

export type SuccessfulQueryArtifact = {
  resultId?: string;
  sql: string;
  columns: string[];
  columnTypes?: string[];
  rows: Record<string, unknown>[];
  rowCount?: number;
  truncated?: boolean;
};

export type EChartsChartArtifact = {
  kind: "echarts_chart";
  schema_version: 1;
  source_result_id: string;
  chart_type: "line" | "bar" | "pie";
  x_field: string;
  series_fields: string[];
  title: string;
};

export type ChartMessagePart = {
  type: "data";
  name: "chart";
  data: { artifact: EChartsChartArtifact; queryArtifact: SuccessfulQueryArtifact };
};

export function getSuccessfulQueryArtifacts(outputs: unknown[]): SuccessfulQueryArtifact[] {
  return outputs.map(readQueryArtifact).filter((item): item is SuccessfulQueryArtifact => item !== undefined);
}

export function getChartMessageParts(outputs: unknown[]): ChartMessagePart[] {
  const queries = getSuccessfulQueryArtifacts(outputs).filter(
    (query) => typeof query.resultId === "string" && Array.isArray(query.columnTypes),
  );
  return outputs.flatMap((output) => {
    const artifact = readChartArtifact(output);
    if (!artifact) return [];
    const queryArtifact = queries.find((query) => query.resultId === artifact.source_result_id);
    return queryArtifact && isChartRenderable(artifact, queryArtifact)
      ? [{ type: "data" as const, name: "chart" as const, data: { artifact, queryArtifact } }]
      : [];
  });
}

function isChartRenderable(artifact: EChartsChartArtifact, query: SuccessfulQueryArtifact) {
  if (!query.columnTypes || query.columnTypes.length !== query.columns.length) return false;
  if (artifact.title !== `${artifact.series_fields.join(", ")} by ${artifact.x_field}`) return false;
  const fieldType = (field: string) => {
    const indexes = query.columns.flatMap((name, index) => name === field ? [index] : []);
    if (indexes.length !== 1) return undefined;
    const type = query.columnTypes?.[indexes[0]]?.toLowerCase() ?? "";
    if (/^(date|time|timestamp|duration)/.test(type)) return "temporal";
    if (/^(u?int|float|double|decimal|numeric)/.test(type)) return "numeric";
    if (/(struct|list<|map<|binary|null)/.test(type)) return "unsupported";
    return "categorical";
  };
  const xType = fieldType(artifact.x_field);
  if (xType !== "categorical" && xType !== "temporal") return false;
  if (new Set(artifact.series_fields).size !== artifact.series_fields.length) return false;
  if (artifact.series_fields.some((field) => fieldType(field) !== "numeric")) return false;
  if (artifact.chart_type === "pie") {
    if (artifact.series_fields.length !== 1 || xType !== "categorical") return false;
    const categories = new Set(query.rows.map((row) => JSON.stringify(row[artifact.x_field])));
    if (categories.size > 8) return false;
  }
  return true;
}

export function getChartUnavailableMessages(outputs: unknown[]): string[] {
  return outputs.flatMap((output) => {
    if (!output || typeof output !== "object") return [];
    const unavailable = (output as { unavailable?: unknown }).unavailable;
    if (!unavailable || typeof unavailable !== "object") return [];
    const value = unavailable as { kind?: unknown; reason?: unknown };
    return value.kind === "chart_unavailable" && typeof value.reason === "string" && value.reason.trim()
      ? [`图表暂不可用：${value.reason}`]
      : [];
  });
}

export function readChartArtifact(value: unknown): EChartsChartArtifact | undefined {
  if (!value || typeof value !== "object") return undefined;
  const wrapper = value as { artifact?: unknown };
  const candidate = wrapper.artifact ?? value;
  if (!candidate || typeof candidate !== "object") return undefined;
  const artifact = candidate as Record<string, unknown>;
  if (
    artifact.kind !== "echarts_chart" || artifact.schema_version !== 1 ||
    typeof artifact.source_result_id !== "string" || !artifact.source_result_id ||
    (artifact.chart_type !== "line" && artifact.chart_type !== "bar" && artifact.chart_type !== "pie") ||
    typeof artifact.x_field !== "string" || !artifact.x_field ||
    !Array.isArray(artifact.series_fields) || artifact.series_fields.length < 1 ||
    artifact.series_fields.length > 4 || !artifact.series_fields.every((field) => typeof field === "string" && field) ||
    typeof artifact.title !== "string" || !artifact.title
  ) return undefined;
  return artifact as EChartsChartArtifact;
}

function formatQueryResult(output: unknown) {
  if (!output || typeof output !== "object") return "";
  const envelope = output as { data?: unknown; artifact?: unknown; content?: unknown };
  if ("legacy_formatted" in envelope && typeof envelope.legacy_formatted === "string") {
    return envelope.legacy_formatted;
  }
  const result = readQueryArtifact(output);
  if (!result) return "";

  const { sql, columns, rows } = result;
  const visibleRows = rows.slice(0, 20);
  const escapeCell = (value: unknown) =>
    String(value ?? "")
      .replaceAll("|", "\\|")
      .replaceAll("\n", " ");
  const table = [
    `| ${columns.map(escapeCell).join(" | ")} |`,
    `| ${columns.map(() => "---").join(" | ")} |`,
    ...visibleRows.map(
      (row) => `| ${columns.map((column) => escapeCell(row[column])).join(" | ")} |`,
    ),
  ].join("\n");
  const rowSummary = `${result.rowCount ?? rows.length} 行${result.truncated ? "（结果已截断）" : ""}`;
  return `**SQL 语句**\n\n\`\`\`sql\n${sql}\n\`\`\`\n\n**查询结果 · ${rowSummary}**\n\n${table}${
    rows.length > visibleRows.length ? "\n\n（界面仅显示前 20 行）" : ""
  }`;
}

function readQueryArtifact(output: unknown): SuccessfulQueryArtifact | undefined {
  if (!output || typeof output !== "object") return undefined;
  const envelope = output as { data?: unknown; artifact?: unknown; content?: unknown };
  let payload: unknown = envelope.artifact ?? envelope.data;
  if (!payload && typeof envelope.content === "string") {
    try {
      payload = JSON.parse(envelope.content);
    } catch {
      return undefined;
    }
  }
  if (!payload && Array.isArray(envelope.content)) {
    const text = envelope.content
      .filter(
        (part): part is { text: string } =>
          typeof part === "object" && part !== null && "text" in part,
      )
      .map((part) => part.text)
      .join("");
    try {
      payload = JSON.parse(text);
    } catch {
      return undefined;
    }
  }
  const wrapped = payload as { data?: unknown } | undefined;
  const data = wrapped && typeof wrapped === "object" && "data" in wrapped ? wrapped.data : payload;
  if (!data || typeof data !== "object") return undefined;

  const result = data as {
    result_id?: string;
    sql?: string;
    columns?: string[];
    column_types?: string[];
    rows?: Record<string, unknown>[];
    row_count?: number;
    truncated?: boolean;
  };
  const columns = result.columns;
  if (
    typeof result.sql !== "string" || !result.sql.trim() ||
    !Array.isArray(columns) || !columns.every((column) => typeof column === "string") ||
    !Array.isArray(result.rows) || !result.rows.every((row) => typeof row === "object" && row !== null && !Array.isArray(row))
  ) return undefined;
  return {
    ...(typeof result.result_id === "string" ? { resultId: result.result_id } : {}),
    sql: result.sql,
    columns,
    ...(Array.isArray(result.column_types) && result.column_types.every((type) => typeof type === "string")
      ? { columnTypes: result.column_types }
      : {}),
    rows: result.rows,
    ...(typeof result.row_count === "number" ? { rowCount: result.row_count } : {}),
    ...(typeof result.truncated === "boolean" ? { truncated: result.truncated } : {}),
  };
}
