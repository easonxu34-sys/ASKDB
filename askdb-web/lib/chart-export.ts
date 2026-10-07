export type ChartCsvBuildResult =
  | { ok: true; content: string; rowCount: number; columnCount: number }
  | { ok: false; reason: "invalid_artifact" | "duplicate_columns" | "export_limit" };

const MAX_EXPORT_ROWS = 10_000;
const MAX_EXPORT_COLUMNS = 256;
const MAX_CELL_LENGTH = 64_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function cellText(value: unknown): string | undefined {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : undefined;
  if (typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value) ?? "";
  } catch {
    return undefined;
  }
}

function isNumericColumn(type: unknown) {
  return (
    typeof type === "string" &&
    /^(?:u?int(?:8|16|32|64)?|float(?:16|32|64)?|double|decimal(?:32|64|128|256)?|numeric)(?:$|[\s[(])/iu.test(
      type,
    )
  );
}

function safeSpreadsheetText(value: unknown, numericColumn = false): string | undefined {
  const text = cellText(value);
  if (text === undefined || text.length > MAX_CELL_LENGTH) return undefined;
  if (typeof value === "string" && /^[\u0000-\u0020]*[=+\-@]/u.test(text)) {
    const numericText = /^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?$/u.test(text.trim());
    if (!numericColumn || !numericText) return `'${text}`;
  }
  return text;
}

function quoteCsvCell(value: string) {
  return `"${value.replaceAll('"', '""')}"`;
}

export function buildChartCsv(queryArtifact: unknown): ChartCsvBuildResult {
  if (
    !isRecord(queryArtifact) ||
    !Array.isArray(queryArtifact.columns) ||
    !Array.isArray(queryArtifact.rows)
  ) {
    return { ok: false, reason: "invalid_artifact" };
  }
  const columns = queryArtifact.columns;
  const rows = queryArtifact.rows;
  const rawColumnTypes = queryArtifact.columnTypes ?? queryArtifact.column_types;
  if (
    rawColumnTypes !== undefined &&
    (!Array.isArray(rawColumnTypes) ||
      rawColumnTypes.length !== columns.length ||
      !rawColumnTypes.every((type) => typeof type === "string"))
  ) {
    return { ok: false, reason: "invalid_artifact" };
  }
  const columnTypes = (rawColumnTypes ?? []) as string[];
  if (!columns.every((column) => typeof column === "string") || !rows.every(isRecord)) {
    return { ok: false, reason: "invalid_artifact" };
  }
  if (new Set(columns).size !== columns.length) {
    return { ok: false, reason: "duplicate_columns" };
  }
  if (rows.length > MAX_EXPORT_ROWS || columns.length > MAX_EXPORT_COLUMNS) {
    return { ok: false, reason: "export_limit" };
  }
  const encoded: string[] = [];
  const header = columns.map((column) => safeSpreadsheetText(column));
  if (header.some((cell) => cell === undefined)) return { ok: false, reason: "export_limit" };
  encoded.push(header.map((cell) => quoteCsvCell(cell!)).join(","));
  for (const row of rows) {
    const cells = columns.map((column, index) =>
      safeSpreadsheetText(row[column], isNumericColumn(columnTypes[index])),
    );
    if (cells.some((cell) => cell === undefined)) return { ok: false, reason: "export_limit" };
    encoded.push(cells.map((cell) => quoteCsvCell(cell!)).join(","));
  }
  return {
    ok: true,
    content: `\uFEFF${encoded.join("\r\n")}`,
    rowCount: rows.length,
    columnCount: columns.length,
  };
}

export function getChartCsvFilename(
  rowCount: number,
  completeness: "possibly_incomplete" | "not_marked_truncated" | "unknown",
) {
  const boundedCount = Number.isSafeInteger(rowCount) && rowCount >= 0 ? rowCount : 0;
  return `askdb-chart-results-${boundedCount}-${completeness}.csv`;
}
