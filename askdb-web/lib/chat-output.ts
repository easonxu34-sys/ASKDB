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

export type ChartType = EChartsChartArtifact["chart_type"];
export type ChartFieldKind = "numeric" | "temporal" | "categorical";
export type ChartSort =
  | { mode: "original" }
  | { mode: "dimension"; field: string; direction: "asc" | "desc" }
  | { mode: "metric"; field: string; direction: "asc" | "desc" };
export type ChartValueFormat =
  | { mode: "raw"; decimal_places: "auto" | number }
  | { mode: "suffix"; suffix: string; decimal_places: "auto" | number }
  | {
      mode: "unit_scale";
      unit_family: "CNY";
      source_unit: "yuan" | "thousand_yuan" | "ten_thousand_yuan" | "hundred_million_yuan";
      display_unit: "yuan" | "thousand_yuan" | "ten_thousand_yuan" | "hundred_million_yuan";
      decimal_places: "auto" | number;
    }
  | {
      mode: "percent";
      encoding: "ratio_0_1" | "percent_0_100";
      decimal_places: "auto" | number;
    };
export type ChartViewConfiguration = {
  chart_type: ChartType;
  dimension_field: string;
  metric_fields: string[];
  hidden_metric_fields: string[];
  bar_orientation?: "vertical" | "horizontal";
  title: string;
  field_labels: Record<string, string>;
  sort: ChartSort;
  format_by_field: Record<string, ChartValueFormat>;
  show_data_labels: boolean;
  show_legend: boolean;
};
export type ChartViewOverride = {
  kind: "chart_view_override";
  schema_version: 1;
  source_result_id: string;
  view: ChartViewConfiguration;
};
export type ChartViewValidation = {
  view?: ChartViewConfiguration;
  errors: Record<string, string>;
};

export type ChartMessagePart = {
  type: "data";
  name: "chart";
  data: {
    artifact: EChartsChartArtifact;
    queryArtifact: SuccessfulQueryArtifact;
    recommendedView: ChartViewConfiguration;
    view: ChartViewConfiguration;
    hasOverride: boolean;
    overrideNotice?: string;
  };
};

const CNY_UNITS = ["yuan", "thousand_yuan", "ten_thousand_yuan", "hundred_million_yuan"] as const;
type CnyUnit = (typeof CNY_UNITS)[number];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function getArrowFieldKind(typeName: string): ChartFieldKind | undefined {
  const type = typeName.trim().toLowerCase();
  if (
    /^(?:u?int(?:8|16|32|64)?|float(?:16|32|64)?|double|decimal(?:32|64|128|256)?|numeric)(?:$|[\s[(])/.test(
      type,
    )
  ) {
    return "numeric";
  }
  if (/^(?:date|time|timestamp)(?:32|64)?(?:$|[\s[(])/.test(type)) return "temporal";
  if (/^dictionary(?:$|[<[(])/.test(type)) return "categorical";
  if (/^(?:string|large_string|bool|boolean)(?:$|[\s[(])/.test(type)) return "categorical";
  return undefined;
}

export function getChartFieldCandidates(query: SuccessfulQueryArtifact) {
  if (!query.columnTypes || query.columnTypes.length !== query.columns.length) return [];
  return query.columns.flatMap((name, index) => {
    if (query.columns.lastIndexOf(name) !== index) return [];
    const kind = getArrowFieldKind(query.columnTypes![index]);
    return kind ? [{ name, kind }] : [];
  });
}

function fieldKind(query: SuccessfulQueryArtifact, name: string) {
  const indexes = query.columns.flatMap((column, index) => (column === name ? [index] : []));
  if (
    indexes.length !== 1 ||
    !query.columnTypes ||
    query.columnTypes.length !== query.columns.length
  )
    return undefined;
  return getArrowFieldKind(query.columnTypes[indexes[0]]);
}

function readFormat(value: unknown): ChartValueFormat | undefined {
  if (!isRecord(value)) return undefined;
  const places = value.decimal_places;
  if (
    !(
      places === "auto" ||
      (typeof places === "number" && Number.isInteger(places) && places >= 0 && places <= 6)
    )
  ) {
    return undefined;
  }
  const decimalPlaces = places as "auto" | number;
  if (value.mode === "raw") return { mode: "raw", decimal_places: decimalPlaces };
  if (value.mode === "suffix" && typeof value.suffix === "string" && value.suffix.length <= 24) {
    return { mode: "suffix", suffix: value.suffix, decimal_places: decimalPlaces };
  }
  if (
    value.mode === "unit_scale" &&
    value.unit_family === "CNY" &&
    CNY_UNITS.includes(value.source_unit as (typeof CNY_UNITS)[number]) &&
    CNY_UNITS.includes(value.display_unit as (typeof CNY_UNITS)[number])
  ) {
    return {
      mode: "unit_scale",
      unit_family: "CNY",
      source_unit: value.source_unit as CnyUnit,
      display_unit: value.display_unit as CnyUnit,
      decimal_places: decimalPlaces,
    };
  }
  if (
    value.mode === "percent" &&
    (value.encoding === "ratio_0_1" || value.encoding === "percent_0_100")
  ) {
    return { mode: "percent", encoding: value.encoding, decimal_places: decimalPlaces };
  }
  return undefined;
}

function validPieRows(query: SuccessfulQueryArtifact, dimension: string, metric: string) {
  if (
    query.truncated ||
    query.rows.length > 1000 ||
    (query.rowCount !== undefined && query.rowCount > query.rows.length)
  ) {
    return false;
  }
  const seen = new Set<string>();
  let total = 0;
  for (const row of query.rows) {
    const category = row[dimension];
    if (
      category === null ||
      category === undefined ||
      !(
        typeof category === "string" ||
        typeof category === "number" ||
        typeof category === "boolean"
      ) ||
      (typeof category === "string" && !category.trim())
    )
      return false;
    const key = JSON.stringify([typeof category, category]);
    if (seen.has(key)) return false;
    seen.add(key);
    if (seen.size > 8) return false;
    const value = row[metric];
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return false;
    total += value;
    if (!Number.isFinite(total)) return false;
  }
  return total > 0;
}

export function validateChartView(
  input: unknown,
  query: SuccessfulQueryArtifact,
): ChartViewValidation {
  const errors: Record<string, string> = {};
  const issue = (field: string, reason: string) => {
    errors[field] ??= reason;
  };
  if (!isRecord(input)) return { errors: { chart_type: "图表配置格式无效。" } };

  const chartType = input.chart_type;
  if (chartType !== "line" && chartType !== "bar" && chartType !== "pie") {
    issue("chart_type", "请选择折线图、柱状图或饼图。");
  }
  const dimension = typeof input.dimension_field === "string" ? input.dimension_field : "";
  if (!dimension || !["categorical", "temporal"].includes(fieldKind(query, dimension) ?? "")) {
    issue("dimension_field", "请选择支持的日期、时间或分类字段。");
  }
  const metricFields =
    Array.isArray(input.metric_fields) &&
    input.metric_fields.every((field) => typeof field === "string")
      ? ([...input.metric_fields] as string[])
      : [];
  if (
    metricFields.length < 1 ||
    metricFields.length > 4 ||
    new Set(metricFields).size !== metricFields.length
  ) {
    issue("metric_fields", "请选择一至四个不同的数值指标。");
  }
  if (metricFields.some((field) => fieldKind(query, field) !== "numeric")) {
    issue("metric_fields", "指标只能使用 Arrow 数值字段。");
  }
  const hiddenFields =
    Array.isArray(input.hidden_metric_fields) &&
    input.hidden_metric_fields.every((field) => typeof field === "string")
      ? ([...input.hidden_metric_fields] as string[])
      : [];
  if (!Array.isArray(input.hidden_metric_fields)) {
    issue("metric_fields", "指标显隐配置无效。");
  }
  if (
    new Set(hiddenFields).size !== hiddenFields.length ||
    hiddenFields.some((field) => !metricFields.includes(field))
  )
    issue("metric_fields", "隐藏指标必须属于当前已选指标。");
  if (metricFields.filter((field) => !hiddenFields.includes(field)).length < 1) {
    issue("metric_fields", "至少保留一个可见指标。");
  }

  const title = typeof input.title === "string" ? input.title.trim() : "";
  if (!title || title.length > 120) issue("title", "标题需为 1–120 个字符。");
  const fieldLabels: Record<string, string> = {};
  if (!isRecord(input.field_labels)) {
    issue("field_labels", "字段显示名配置无效。");
  } else {
    for (const [field, value] of Object.entries(input.field_labels)) {
      if (fieldKind(query, field) === undefined || typeof value !== "string" || value.length > 80) {
        issue(`field_labels.${field}`, "字段显示名只能对应查询字段，且不超过 80 个字符。");
        break;
      }
      if (value.trim()) fieldLabels[field] = value.trim();
    }
  }

  let barOrientation: "vertical" | "horizontal" = "vertical";
  if (input.bar_orientation !== undefined) {
    if (input.bar_orientation === "vertical" || input.bar_orientation === "horizontal") {
      barOrientation = input.bar_orientation;
    } else {
      issue("chart_type", "请选择纵向或横向柱状图。");
    }
  }

  let sort: ChartSort = { mode: "original" };
  if (isRecord(input.sort) && input.sort.mode === "original") {
    sort = { mode: "original" };
  } else if (
    isRecord(input.sort) &&
    input.sort.mode === "dimension" &&
    input.sort.field === dimension &&
    (input.sort.direction === "asc" || input.sort.direction === "desc")
  ) {
    sort = { mode: "dimension", field: dimension, direction: input.sort.direction };
  } else if (
    isRecord(input.sort) &&
    input.sort.mode === "metric" &&
    typeof input.sort.field === "string" &&
    metricFields.includes(input.sort.field) &&
    !hiddenFields.includes(input.sort.field) &&
    (input.sort.direction === "asc" || input.sort.direction === "desc")
  ) {
    sort = { mode: "metric", field: input.sort.field, direction: input.sort.direction };
  } else {
    issue("sort", "排序字段或方向与当前图表不兼容。");
  }
  if (chartType === "line" && sort.mode === "metric") {
    issue("sort", "折线图不能按指标重排时间序列。");
  }
  if (chartType === "pie" && sort.mode === "metric" && metricFields.length !== 1) {
    issue("sort", "饼图只能按唯一指标排序。");
  }
  if (chartType === "pie" && sort.mode === "dimension") {
    issue("sort", "饼图只支持查询原序或按唯一指标排序。");
  }

  const formatByField: Record<string, ChartValueFormat> = {};
  const suppliedFormats = isRecord(input.format_by_field) ? input.format_by_field : {};
  for (const field of metricFields) {
    const format = readFormat(suppliedFormats[field]);
    if (!format) issue(`format_by_field.${field}`, `请为“${field}”选择完整、有效的数值格式。`);
    else formatByField[field] = format;
  }
  if (!isRecord(input.format_by_field)) issue("format_by_field", "数值格式配置无效。");

  if (chartType === "pie") {
    if (metricFields.length !== 1 || hiddenFields.length > 0) {
      issue("metric_fields", "饼图只支持一个可见数值指标。");
    }
    if (fieldKind(query, dimension) !== "categorical") {
      issue("dimension_field", "饼图需要分类字段作为维度。");
    }
    if (metricFields.length === 1 && !validPieRows(query, dimension, metricFields[0])) {
      issue("chart_type", "饼图要求未截断结果、最多八个唯一非空分类、有限非负指标值且合计大于零。");
    }
  }

  if (input.show_data_labels !== true && input.show_data_labels !== false) {
    issue("show_data_labels", "请选择是否显示数据标签。");
  }
  if (input.show_legend !== true && input.show_legend !== false) {
    issue("show_legend", "请选择是否显示图例。");
  }
  if (Object.keys(errors).length > 0) return { errors };

  const view: ChartViewConfiguration = {
    chart_type: chartType as ChartType,
    dimension_field: dimension,
    metric_fields: metricFields,
    hidden_metric_fields: hiddenFields,
    ...(chartType === "bar" ? { bar_orientation: barOrientation } : {}),
    title,
    field_labels: fieldLabels,
    sort,
    format_by_field: formatByField,
    show_data_labels: input.show_data_labels as boolean,
    show_legend: input.show_legend as boolean,
  };
  return { view, errors };
}

export function createRecommendedChartView(
  artifact: EChartsChartArtifact,
  query: SuccessfulQueryArtifact,
): ChartViewConfiguration | undefined {
  if (
    !query.resultId ||
    artifact.source_result_id !== query.resultId ||
    !query.columnTypes ||
    query.columnTypes.length !== query.columns.length ||
    artifact.title !== `${artifact.series_fields.join(", ")} by ${artifact.x_field}`
  )
    return undefined;
  const timeDimension = fieldKind(query, artifact.x_field) === "temporal";
  const candidate: ChartViewConfiguration = {
    chart_type: artifact.chart_type,
    dimension_field: artifact.x_field,
    metric_fields: [...artifact.series_fields],
    hidden_metric_fields: [],
    ...(artifact.chart_type === "bar" ? { bar_orientation: "vertical" } : {}),
    title: artifact.title,
    field_labels: {},
    sort:
      artifact.chart_type === "line" && timeDimension
        ? { mode: "dimension", field: artifact.x_field, direction: "asc" }
        : { mode: "original" },
    format_by_field: Object.fromEntries(
      artifact.series_fields.map((field) => [field, { mode: "raw", decimal_places: "auto" }]),
    ),
    show_data_labels: artifact.chart_type === "pie",
    show_legend: artifact.series_fields.length > 1,
  };
  return validateChartView(candidate, query).view;
}

export function chartViewsEqual(left: ChartViewConfiguration, right: ChartViewConfiguration) {
  const stable = (view: ChartViewConfiguration) =>
    JSON.stringify({
      chart_type: view.chart_type,
      dimension_field: view.dimension_field,
      metric_fields: view.metric_fields,
      hidden_metric_fields: [...view.hidden_metric_fields].sort(),
      bar_orientation: view.chart_type === "bar" ? (view.bar_orientation ?? "vertical") : undefined,
      title: view.title,
      field_labels: Object.fromEntries(
        Object.entries(view.field_labels).sort(([a], [b]) => a.localeCompare(b)),
      ),
      sort: view.sort,
      format_by_field: Object.fromEntries(
        view.metric_fields.map((field) => [field, view.format_by_field[field]]),
      ),
      show_data_labels: view.show_data_labels,
      show_legend: view.show_legend,
    });
  return stable(left) === stable(right);
}

export function readChartViewOverride(value: unknown): ChartViewOverride | undefined {
  if (!isRecord(value)) return undefined;
  const candidate = isRecord(value.artifact) ? value.artifact : value;
  if (
    candidate.kind !== "chart_view_override" ||
    candidate.schema_version !== 1 ||
    typeof candidate.source_result_id !== "string" ||
    !candidate.source_result_id ||
    !isRecord(candidate.view)
  )
    return undefined;
  return candidate as unknown as ChartViewOverride;
}

export function isChartViewOverrideCandidate(value: unknown) {
  return chartArtifactCandidate(value)?.kind === "chart_view_override";
}

function chartArtifactCandidate(value: unknown) {
  if (!isRecord(value)) return undefined;
  return isRecord(value.artifact) ? value.artifact : value;
}

export function getSuccessfulQueryArtifacts(outputs: unknown[]): SuccessfulQueryArtifact[] {
  return outputs
    .map(readQueryArtifact)
    .filter((item): item is SuccessfulQueryArtifact => item !== undefined);
}

export function getChartMessageParts(outputs: unknown[]): ChartMessagePart[] {
  const queries = getSuccessfulQueryArtifacts(outputs).filter(
    (query) => typeof query.resultId === "string" && Array.isArray(query.columnTypes),
  );
  return outputs.flatMap((output) => {
    const artifact = readChartArtifact(output);
    if (!artifact) return [];
    const queryArtifact = queries.find((query) => query.resultId === artifact.source_result_id);
    if (!queryArtifact) return [];
    const recommendedView = createRecommendedChartView(artifact, queryArtifact);
    if (!recommendedView) return [];
    const overrideEntry = outputs
      .filter((candidate) => {
        const value = chartArtifactCandidate(candidate);
        return (
          value?.kind === "chart_view_override" &&
          value.source_result_id === artifact.source_result_id
        );
      })
      .at(-1);
    const parsedOverride = overrideEntry ? readChartViewOverride(overrideEntry) : undefined;
    const validation = parsedOverride
      ? validateChartView(parsedOverride.view, queryArtifact)
      : undefined;
    const validOverride =
      parsedOverride?.source_result_id === artifact.source_result_id ? validation?.view : undefined;
    const view = validOverride ?? recommendedView;
    const hasOverride =
      validOverride !== undefined && !chartViewsEqual(validOverride, recommendedView);
    const overrideNotice =
      overrideEntry && !validOverride
        ? "用户图表配置无效或与当前查询不匹配，已回退到 AI 推荐配置。"
        : undefined;
    return [
      {
        type: "data" as const,
        name: "chart" as const,
        data: {
          artifact,
          queryArtifact,
          recommendedView,
          view,
          hasOverride,
          ...(overrideNotice ? { overrideNotice } : {}),
        },
      },
    ];
  });
}

export function getChartUnavailableMessages(outputs: unknown[]): string[] {
  const queryArtifacts = getSuccessfulQueryArtifacts(outputs);
  return outputs.flatMap((output) => {
    const chart = readChartArtifact(output);
    if (chart) {
      const query = queryArtifacts.find((item) => item.resultId === chart.source_result_id);
      if (!query) return ["图表暂不可用：查询结果缓存已失效，助手文本仍可查看。"];
      if (!createRecommendedChartView(chart, query)) {
        return ["图表暂不可用：推荐图表字段或查询结果不满足绘制条件。"];
      }
    } else {
      const candidate = chartArtifactCandidate(output);
      if (candidate?.kind === "echarts_chart") {
        return ["图表暂不可用：推荐图表格式无效，查询结果表格仍可查看。"];
      }
    }
    if (!output || typeof output !== "object") return [];
    const unavailable = (output as { unavailable?: unknown }).unavailable;
    if (!unavailable || typeof unavailable !== "object") return [];
    const value = unavailable as { kind?: unknown; reason?: unknown };
    return value.kind === "chart_unavailable" &&
      typeof value.reason === "string" &&
      value.reason.trim()
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
    artifact.kind !== "echarts_chart" ||
    artifact.schema_version !== 1 ||
    typeof artifact.source_result_id !== "string" ||
    !artifact.source_result_id ||
    (artifact.chart_type !== "line" &&
      artifact.chart_type !== "bar" &&
      artifact.chart_type !== "pie") ||
    typeof artifact.x_field !== "string" ||
    !artifact.x_field ||
    !Array.isArray(artifact.series_fields) ||
    artifact.series_fields.length < 1 ||
    artifact.series_fields.length > 4 ||
    !artifact.series_fields.every((field) => typeof field === "string" && field) ||
    typeof artifact.title !== "string" ||
    !artifact.title
  )
    return undefined;
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
    typeof result.sql !== "string" ||
    !result.sql.trim() ||
    !Array.isArray(columns) ||
    !columns.every((column) => typeof column === "string") ||
    !Array.isArray(result.rows) ||
    !result.rows.every((row) => typeof row === "object" && row !== null && !Array.isArray(row))
  )
    return undefined;
  return {
    ...(typeof result.result_id === "string" ? { resultId: result.result_id } : {}),
    sql: result.sql,
    columns,
    ...(Array.isArray(result.column_types) &&
    result.column_types.every((type) => typeof type === "string")
      ? { columnTypes: result.column_types }
      : {}),
    rows: result.rows,
    ...(typeof result.row_count === "number" ? { rowCount: result.row_count } : {}),
    ...(typeof result.truncated === "boolean" ? { truncated: result.truncated } : {}),
  };
}
