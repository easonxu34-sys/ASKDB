export function formatQueryResults(outputs: unknown[]) {
  return outputs.map(formatQueryResult).filter(Boolean).join("\n\n---\n\n");
}

function formatQueryResult(output: unknown) {
  if (!output || typeof output !== "object") return "";
  const envelope = output as { data?: unknown; artifact?: unknown; content?: unknown };
  if ("legacy_formatted" in envelope && typeof envelope.legacy_formatted === "string") {
    return envelope.legacy_formatted;
  }
  let payload: unknown = envelope.artifact ?? envelope.data;
  if (!payload && typeof envelope.content === "string") {
    try {
      payload = JSON.parse(envelope.content);
    } catch {
      return "";
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
      return "";
    }
  }
  const wrapped = payload as { data?: unknown } | undefined;
  const data = wrapped && typeof wrapped === "object" && "data" in wrapped ? wrapped.data : payload;
  if (!data || typeof data !== "object") return "";

  const result = data as {
    sql?: string;
    columns?: string[];
    rows?: Record<string, unknown>[];
    row_count?: number;
    truncated?: boolean;
  };
  const columns = result.columns;
  if (!result.sql || !Array.isArray(columns) || !Array.isArray(result.rows)) return "";

  const visibleRows = result.rows.slice(0, 20);
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
  const rowSummary = `${result.row_count ?? result.rows.length} 行${result.truncated ? "（结果已截断）" : ""}`;
  return `**SQL 语句**\n\n\`\`\`sql\n${result.sql}\n\`\`\`\n\n**查询结果 · ${rowSummary}**\n\n${table}${
    result.rows.length > visibleRows.length ? "\n\n（界面仅显示前 20 行）" : ""
  }`;
}
