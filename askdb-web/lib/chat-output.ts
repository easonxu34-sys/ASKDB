export function formatQueryResults(outputs: unknown[]) {
  return outputs.map(formatQueryResult).filter(Boolean).join("\n\n---\n\n");
}

export type SuccessfulQueryArtifact = {
  sql: string;
  columns: string[];
  rows: Record<string, unknown>[];
  rowCount?: number;
  truncated?: boolean;
};

export function getSuccessfulQueryArtifacts(outputs: unknown[]): SuccessfulQueryArtifact[] {
  return outputs.map(readQueryArtifact).filter((item): item is SuccessfulQueryArtifact => item !== undefined);
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
    sql?: string;
    columns?: string[];
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
    sql: result.sql,
    columns,
    rows: result.rows,
    ...(typeof result.row_count === "number" ? { rowCount: result.row_count } : {}),
    ...(typeof result.truncated === "boolean" ? { truncated: result.truncated } : {}),
  };
}
