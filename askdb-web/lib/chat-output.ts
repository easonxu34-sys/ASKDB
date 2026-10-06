import { decimalParts, exactPlotNumber, sumDecimalValues } from "./chart-decimal.ts";

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
export const CHART_PALETTE_TOKENS = [
  "blue",
  "teal",
  "green",
  "amber",
  "orange",
  "red",
  "purple",
  "slate",
] as const;
export type ChartPaletteToken = (typeof CHART_PALETTE_TOKENS)[number];
export const CHART_PALETTE_IDS = [
  "system_default",
  "classic",
  "ocean",
  "warm",
  "earth",
  "pastel",
  "high_contrast",
  "color_vision_friendly",
] as const;
export type ChartPaletteId = (typeof CHART_PALETTE_IDS)[number];
export const CHART_COLOR_PALETTES: Record<ChartPaletteId, readonly string[]> = {
  system_default: ["#5470C6", "#91CC75", "#FAC858", "#EE6666", "#73C0DE", "#3BA272", "#FC8452", "#9A60B4"],
  classic: ["#3B82F6", "#14B8A6", "#22C55E", "#F59E0B", "#F97316", "#EF4444", "#A855F7", "#64748B"],
  ocean: ["#155E75", "#0E7490", "#0891B2", "#06B6D4", "#2563EB", "#4F46E5", "#6366F1", "#8B5CF6"],
  warm: ["#7F1D1D", "#B91C1C", "#DC2626", "#EA580C", "#F97316", "#D97706", "#CA8A04", "#92400E"],
  earth: ["#3F6212", "#4D7C0F", "#65A30D", "#15803D", "#0F766E", "#A16207", "#854D0E", "#78716C"],
  pastel: ["#8FBCE6", "#8FD3C1", "#E9A9CF", "#F2CD8F", "#BBA5EA", "#E89A9A", "#87C9CF", "#B6D99A"],
  high_contrast: ["#005FCC", "#D00000", "#008A00", "#AA00AA", "#B36B00", "#008C95", "#5B3A29", "#333333"],
  color_vision_friendly: ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948", "#B07AA1", "#9C755F"],
};
export type ChartColorDirection = "horizontal" | "vertical" | "diagonal_down" | "diagonal_up";
export type ChartColorSpec =
  | { mode: "solid"; hex: string; opacity: number }
  | {
      mode: "linear_gradient";
      start_hex: string;
      end_hex: string;
      direction: ChartColorDirection;
      opacity: number;
    };
export type ChartSolidColorSpec = Extract<ChartColorSpec, { mode: "solid" }>;
const LEGACY_CHART_TOKEN_HEX: Record<ChartPaletteToken, string> = {
  blue: "#3B82F6",
  teal: "#14B8A6",
  green: "#22C55E",
  amber: "#F59E0B",
  orange: "#F97316",
  red: "#EF4444",
  purple: "#A855F7",
  slate: "#64748B",
};
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
  color_palette_id?: ChartPaletteId;
  color_by_metric?: Record<string, ChartColorSpec>;
  pie_category_colors?: {
    dimension_field: string;
    by_category_key: Record<string, ChartSolidColorSpec>;
  };
  current_result_top_n?: { field: string; count: number; direction: "asc" | "desc" };
};
export type ChartViewEditOrigin = "manual" | "natural_language" | "restore_recommendation";
export type ChartViewUndoRecord = {
  view: ChartViewConfiguration;
  summary: string;
  origin: ChartViewEditOrigin;
};
export type ChartViewOverride = {
  kind: "chart_view_override";
  schema_version: 1 | 2 | 3;
  source_result_id: string;
  view: ChartViewConfiguration;
  undo_history?: ChartViewUndoRecord[];
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
    undoHistory: ChartViewUndoRecord[];
    persistenceAvailable: boolean;
    overrideNotice?: string;
  };
};

const CNY_UNITS = ["yuan", "thousand_yuan", "ten_thousand_yuan", "hundred_million_yuan"] as const;
type CnyUnit = (typeof CNY_UNITS)[number];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readChartColorSpec(value: unknown): ChartColorSpec | undefined {
  if (typeof value === "string" && CHART_PALETTE_TOKENS.includes(value as ChartPaletteToken)) {
    return {
      mode: "solid",
      hex: LEGACY_CHART_TOKEN_HEX[value as ChartPaletteToken],
      opacity: 100,
    };
  }
  if (!isRecord(value) || !Number.isInteger(value.opacity) || (value.opacity as number) < 0 || (value.opacity as number) > 100) {
    return undefined;
  }
  if (
    value.mode === "solid" &&
    Object.keys(value).length === 3 &&
    Object.hasOwn(value, "hex") &&
    typeof value.hex === "string" &&
    /^#[0-9a-fA-F]{6}$/.test(value.hex)
  ) {
    return { mode: "solid", hex: value.hex.toUpperCase(), opacity: value.opacity as number };
  }
  if (
    value.mode === "linear_gradient" &&
    Object.keys(value).length === 5 &&
    typeof value.start_hex === "string" &&
    /^#[0-9a-fA-F]{6}$/.test(value.start_hex) &&
    typeof value.end_hex === "string" &&
    /^#[0-9a-fA-F]{6}$/.test(value.end_hex) &&
    (value.direction === "horizontal" || value.direction === "vertical" ||
      value.direction === "diagonal_down" || value.direction === "diagonal_up")
  ) {
    return {
      mode: "linear_gradient",
      start_hex: value.start_hex.toUpperCase(),
      end_hex: value.end_hex.toUpperCase(),
      direction: value.direction,
      opacity: value.opacity as number,
    };
  }
  return undefined;
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
  const values: unknown[] = [];
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
    const key = chartCategoryKey(category);
    if (!key) return false;
    if (seen.has(key)) return false;
    seen.add(key);
    if (seen.size > 8) return false;
    const value = row[metric];
    const parsed = decimalParts(value);
    if (!parsed || parsed.coefficient < BigInt("0") || exactPlotNumber(value) === undefined) {
      return false;
    }
    values.push(value);
  }
  const total = sumDecimalValues(values);
  return total !== undefined && total.coefficient > BigInt("0");
}

export function chartCategoryKey(value: unknown): string | undefined {
  if (
    (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean") ||
    (typeof value === "number" && !Number.isFinite(value))
  ) {
    return undefined;
  }
  return JSON.stringify([typeof value, value]);
}

export function resolvePieCategoryLabel(
  query: SuccessfulQueryArtifact,
  dimension: string,
  label: string,
):
  | { status: "matched"; category_key: string }
  | { status: "not_found" }
  | { status: "ambiguous" } {
  const keys = new Set<string>();
  for (const row of query.rows) {
    const value = row[dimension];
    if (value === null || value === undefined) continue;
    const key = chartCategoryKey(value);
    const rendered =
      typeof value === "string" || typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : "（空值）";
    if (rendered === label && key) keys.add(key);
  }
  if (keys.size === 0) return { status: "not_found" };
  if (keys.size > 1) return { status: "ambiguous" };
  return { status: "matched", category_key: [...keys][0] };
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
  let colorPaletteId: ChartPaletteId = "system_default";
  if (input.color_palette_id !== undefined) {
    if (CHART_PALETTE_IDS.includes(input.color_palette_id as ChartPaletteId)) {
      colorPaletteId = input.color_palette_id as ChartPaletteId;
    } else {
      issue("color_palette_id", "请选择有效的图表色板。");
    }
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

  let colorByMetric: Record<string, ChartColorSpec> | undefined;
  if (input.color_by_metric !== undefined) {
    if (!isRecord(input.color_by_metric)) {
      issue("color_by_metric", "指标配色配置无效。");
    } else {
      colorByMetric = {};
      for (const [field, value] of Object.entries(input.color_by_metric)) {
        if (chartType === "pie") {
          issue("chart_type", "饼图按类别设置颜色，不能按指标设置颜色。");
          break;
        }
        const color = readChartColorSpec(value);
        if (Object.keys(input.color_by_metric).length > 4 || !metricFields.includes(field) ||
            hiddenFields.includes(field) || !color || (color.mode === "linear_gradient" && chartType === "pie")) {
          issue("color_by_metric", "指标配色只能用于当前可见指标，且颜色格式无效。");
          break;
        }
        colorByMetric[field] = color;
      }
    }
  }

  let pieCategoryColors: ChartViewConfiguration["pie_category_colors"];
  if (input.pie_category_colors !== undefined) {
    const candidate = input.pie_category_colors;
    if (
      chartType !== "pie" ||
      !isRecord(candidate) ||
      candidate.dimension_field !== dimension ||
      !isRecord(candidate.by_category_key) ||
      Object.keys(candidate.by_category_key).length > 8
    ) {
      issue("pie_category_colors", "饼图类别配色必须对应当前维度，且最多八项。");
    } else {
      const allowedKeys = new Set(
        query.rows
          .map((row) => chartCategoryKey(row[dimension]))
          .filter((key): key is string => key !== undefined),
      );
      const byCategoryKey: Record<string, ChartSolidColorSpec> = {};
      for (const [key, value] of Object.entries(candidate.by_category_key)) {
        const color = readChartColorSpec(value);
        if (!allowedKeys.has(key) || !color || color.mode !== "solid") {
          issue("pie_category_colors", "类别必须唯一存在于当前饼图结果中，并使用有效的纯色。");
          break;
        }
        byCategoryKey[key] = color;
      }
      if (!errors.pie_category_colors) {
        pieCategoryColors = { dimension_field: dimension, by_category_key: byCategoryKey };
      }
    }
  }

  let currentResultTopN: ChartViewConfiguration["current_result_top_n"];
  if (input.current_result_top_n !== undefined) {
    const candidate = input.current_result_top_n;
    if (
      !isRecord(candidate) ||
      typeof candidate.field !== "string" ||
      !Number.isInteger(candidate.count) ||
      (candidate.count as number) < 1 ||
      (candidate.count as number) > 100 ||
      (candidate.direction !== "asc" && candidate.direction !== "desc") ||
      !metricFields.includes(candidate.field) ||
      hiddenFields.includes(candidate.field) ||
      fieldKind(query, candidate.field) !== "numeric" ||
      chartType === "line" ||
      sort.mode !== "metric" ||
      sort.field !== candidate.field ||
      sort.direction !== candidate.direction ||
      (chartType === "pie" &&
        (metricFields.length !== 1 || !validPieRows(query, dimension, candidate.field)))
    ) {
      issue("current_result_top_n", "当前结果排名字段、范围、图表类型或排序不兼容。");
    } else {
      currentResultTopN = {
        field: candidate.field,
        count: candidate.count as number,
        direction: candidate.direction,
      };
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
    color_palette_id: colorPaletteId,
    ...(colorByMetric && Object.keys(colorByMetric).length > 0
      ? { color_by_metric: colorByMetric }
      : {}),
    ...(pieCategoryColors && Object.keys(pieCategoryColors.by_category_key).length > 0
      ? { pie_category_colors: pieCategoryColors }
      : {}),
    ...(currentResultTopN ? { current_result_top_n: currentResultTopN } : {}),
  };
  return { view, errors };
}

export type ChartEditClarificationCode =
  | "top_n_scope_required"
  | "top_n_metric_required"
  | "source_unit_required"
  | "field_not_in_result"
  | "category_not_in_result"
  | "category_ambiguous"
  | "conflicting_category_color"
  | "chart_type_incompatible"
  | "conflicting_sort"
  | "operation_unsupported";

export type ChartEditApplication =
  | { status: "apply"; view: ChartViewConfiguration }
  | { status: "clarify"; code: ChartEditClarificationCode };

const fieldErrorText = (errors: Record<string, string>) => Object.values(errors).join(" ");

type ChartViewChangeShape = Omit<ChartViewConfiguration, "format_by_field"> & {
  format_by_field: Record<string, unknown>;
};

export function normalizeChartViewChange<T extends ChartViewChangeShape>(
  before: T,
  proposed: T,
  query: SuccessfulQueryArtifact,
  { explicitSort = JSON.stringify(before.sort) !== JSON.stringify(proposed.sort), preserveTopN = false } = {},
): T {
  const next = {
    ...proposed,
    metric_fields: [...proposed.metric_fields],
    hidden_metric_fields: [...proposed.hidden_metric_fields],
    sort: { ...proposed.sort },
    field_labels: { ...proposed.field_labels },
    format_by_field: { ...proposed.format_by_field },
    ...(proposed.color_by_metric ? { color_by_metric: { ...proposed.color_by_metric } } : {}),
  };
  const dimensionChanged = next.dimension_field !== before.dimension_field;
  if (JSON.stringify(next.metric_fields) !== JSON.stringify(before.metric_fields)) {
    next.hidden_metric_fields = next.hidden_metric_fields.filter((field) => next.metric_fields.includes(field));
  }
  if (dimensionChanged || next.chart_type !== "pie") delete next.pie_category_colors;
  if (next.chart_type === "pie") delete next.color_by_metric;
  if (next.chart_type !== "bar") delete next.bar_orientation;
  else next.bar_orientation ??= "vertical";
  for (const field of Object.keys(next.color_by_metric ?? {})) {
    if (!next.metric_fields.includes(field) || next.hidden_metric_fields.includes(field)) {
      delete next.color_by_metric![field];
    }
  }
  if (next.color_by_metric && Object.keys(next.color_by_metric).length === 0) delete next.color_by_metric;
  for (const field of next.metric_fields) {
    next.format_by_field[field] ??= { mode: "raw", decimal_places: "auto" };
  }
  if (dimensionChanged && !explicitSort && next.sort.mode === "dimension") {
    next.sort = { ...next.sort, field: next.dimension_field };
  }
  if (!preserveTopN) {
    const normalized = clearTopNForManualChange(before, next);
    next.current_result_top_n = normalized.current_result_top_n;
    if (!next.current_result_top_n) delete next.current_result_top_n;
  }
  if (!explicitSort) {
    const metricSortUnavailable = next.sort.mode === "metric" &&
      (!next.metric_fields.includes(next.sort.field) || next.hidden_metric_fields.includes(next.sort.field));
    const lineSortIncompatible = next.chart_type === "line" && next.sort.mode === "metric";
    const pieSortIncompatible = next.chart_type === "pie" &&
      (next.sort.mode === "dimension" || (next.sort.mode === "metric" && next.metric_fields.length !== 1));
    if (metricSortUnavailable || lineSortIncompatible || pieSortIncompatible) {
      next.sort = next.chart_type === "line" && fieldKind(query, next.dimension_field) === "temporal"
        ? { mode: "dimension", field: next.dimension_field, direction: "asc" }
        : { mode: "original" };
    }
  }
  return next;
}

export function applyChartEditIntent(
  current: ChartViewConfiguration,
  intent: {
    status: "apply" | "query_required" | "clarify";
    patch?: Record<string, unknown> | null;
    current_result_operation?: {
      kind: "top_n";
      field: string;
      count: number;
      direction: "asc" | "desc";
      scope: "current_result";
    } | null;
    category_color_operations?: Array<{ category_label: string; color: ChartPaletteToken }>;
  },
  query: SuccessfulQueryArtifact,
): ChartEditApplication {
  if (intent.status === "clarify") return { status: "clarify", code: "operation_unsupported" };
  const patch = intent.patch ?? {};
  let next = {
    ...current,
    metric_fields: [...current.metric_fields],
    hidden_metric_fields: [...current.hidden_metric_fields],
    field_labels: { ...current.field_labels },
    sort: { ...current.sort },
    format_by_field: { ...current.format_by_field },
    ...(current.color_by_metric ? { color_by_metric: { ...current.color_by_metric } } : {}),
    ...(current.pie_category_colors
      ? {
          pie_category_colors: {
            dimension_field: current.pie_category_colors.dimension_field,
            by_category_key: { ...current.pie_category_colors.by_category_key },
          },
        }
      : {}),
    ...(current.current_result_top_n
      ? { current_result_top_n: { ...current.current_result_top_n } }
      : {}),
  } as ChartViewConfiguration;
  for (const key of [
    "chart_type",
    "dimension_field",
    "metric_fields",
    "hidden_metric_fields",
    "bar_orientation",
    "title",
    "show_data_labels",
    "show_legend",
  ] as const) {
    const value = patch[key];
    if (value !== undefined && value !== null) {
      (next as unknown as Record<string, unknown>)[key] = Array.isArray(value) ? [...value] : value;
    }
  }
  if (isRecord(patch.field_labels)) next.field_labels = { ...current.field_labels, ...patch.field_labels } as Record<string, string>;
  if (isRecord(patch.format_by_field)) next.format_by_field = { ...current.format_by_field, ...patch.format_by_field } as Record<string, ChartValueFormat>;
  if (isRecord(patch.color_by_metric)) next.color_by_metric = { ...current.color_by_metric, ...patch.color_by_metric } as Record<string, ChartColorSpec>;
  if (isRecord(patch.sort)) {
    next.sort = patch.sort.mode === "original"
      ? { mode: "original" }
      : patch.sort as unknown as ChartSort;
  }

  if ((patch.bar_orientation != null && next.chart_type !== "bar") ||
      (patch.color_by_metric != null && next.chart_type === "pie")) {
    return { status: "clarify", code: "chart_type_incompatible" };
  }
  if (Array.isArray(patch.hidden_metric_fields) &&
      patch.hidden_metric_fields.some((field) => !next.metric_fields.includes(field))) {
    return { status: "clarify", code: "field_not_in_result" };
  }

  const dimensionChanged = next.dimension_field !== current.dimension_field;
  const topN = intent.current_result_operation;
  if (topN) {
    if (patch.sort !== undefined && patch.sort !== null) {
      const suppliedSort = patch.sort;
      if (
        !isRecord(suppliedSort) ||
        suppliedSort.mode !== "metric" ||
        suppliedSort.field !== topN.field ||
        suppliedSort.direction !== topN.direction
      ) return { status: "clarify", code: "conflicting_sort" };
    }
    if (!query.columns.includes(topN.field)) {
      return { status: "clarify", code: "field_not_in_result" };
    }
    if (
      !current.metric_fields.includes(topN.field) ||
      current.hidden_metric_fields.includes(topN.field) ||
      !next.metric_fields.includes(topN.field) ||
      next.hidden_metric_fields.includes(topN.field) ||
      fieldKind(query, topN.field) !== "numeric"
    ) return { status: "clarify", code: "top_n_metric_required" };
    if (next.chart_type === "line") return { status: "clarify", code: "chart_type_incompatible" };
    next.current_result_top_n = { field: topN.field, count: topN.count, direction: topN.direction };
    next.sort = { mode: "metric", field: topN.field, direction: topN.direction };
  }
  next = normalizeChartViewChange(current, next, query, {
    explicitSort: patch.sort !== undefined && patch.sort !== null,
    preserveTopN: Boolean(topN),
  });

  if (
    !query.columns.includes(next.dimension_field) ||
    next.metric_fields.some((field) => !query.columns.includes(field)) ||
    Object.keys(next.field_labels).some((field) => !query.columns.includes(field)) ||
    Object.keys(next.color_by_metric ?? {}).some((field) => !query.columns.includes(field)) ||
    (next.sort.mode !== "original" && !query.columns.includes(next.sort.field))
  ) return { status: "clarify", code: "field_not_in_result" };

  const operations = intent.category_color_operations ?? [];
  if (operations.length > 0) {
    if (next.chart_type !== "pie") return { status: "clarify", code: "chart_type_incompatible" };
    const byCategoryKey = dimensionChanged
      ? {}
      : { ...(next.pie_category_colors?.by_category_key ?? {}) };
    const requested = new Map<string, ChartPaletteToken>();
    for (const operation of operations) {
      const resolution = resolvePieCategoryLabel(query, next.dimension_field, operation.category_label);
      if (resolution.status === "not_found") return { status: "clarify", code: "category_not_in_result" };
      if (resolution.status === "ambiguous") return { status: "clarify", code: "category_ambiguous" };
      const color = readChartColorSpec(operation.color);
      if (!color || color.mode !== "solid") return { status: "clarify", code: "operation_unsupported" };
      const prior = requested.get(resolution.category_key);
      if (prior && prior !== operation.color) return { status: "clarify", code: "conflicting_category_color" };
      requested.set(resolution.category_key, operation.color);
      byCategoryKey[resolution.category_key] = color;
    }
    next.pie_category_colors = { dimension_field: next.dimension_field, by_category_key: byCategoryKey };
  }

  const validation = validateChartView(next, query);
  if (!validation.view) {
    const text = fieldErrorText(validation.errors);
    const hasMissingField = Object.keys(validation.errors).some((key) =>
      ["chart_type", "dimension_field", "metric_fields", "sort", "field_labels", "format_by_field", "color_by_metric"].some((prefix) => key.startsWith(prefix)),
    );
    if (/字段显示名只能对应查询字段|指标只能使用 Arrow 数值字段|请选择支持的日期、时间或分类字段/.test(text)) {
      return { status: "clarify", code: "field_not_in_result" };
    }
    if (validation.errors.current_result_top_n) {
      return { status: "clarify", code: "top_n_metric_required" };
    }
    if (hasMissingField && /与当前图表不兼容|必须|请选择/.test(text)) {
      return { status: "clarify", code: "chart_type_incompatible" };
    }
    return { status: "clarify", code: "operation_unsupported" };
  }
  return { status: "apply", view: validation.view };
}

export function summarizeChartViewChange(
  before: ChartViewConfiguration,
  after: ChartViewConfiguration,
) {
  const changes: string[] = [];
  const sortLabel = (sort: ChartSort) =>
    sort.mode === "original"
      ? "查询原序"
      : `${sort.mode === "dimension" ? "维度" : "指标"}“${sort.field}”${sort.direction === "asc" ? "升序" : "降序"}`;
  const changedMap = <T,>(left: Record<string, T>, right: Record<string, T>) =>
    [...new Set([...Object.keys(left), ...Object.keys(right)])]
      .filter((key) => JSON.stringify(left[key]) !== JSON.stringify(right[key]))
      .map((key) => [key, right[key]] as const);
  if (before.title !== after.title) changes.push(`标题改为“${after.title}”`);
  if (before.chart_type !== after.chart_type) changes.push(`图表类型改为${after.chart_type === "line" ? "折线图" : after.chart_type === "bar" ? "柱状图" : "饼图"}`);
  if (before.dimension_field !== after.dimension_field) changes.push(`维度改为“${after.dimension_field}”`);
  if (before.bar_orientation !== after.bar_orientation && after.chart_type === "bar") changes.push(`柱形方向改为${after.bar_orientation === "horizontal" ? "横向" : "纵向"}`);
  if (JSON.stringify(before.metric_fields) !== JSON.stringify(after.metric_fields)) changes.push(`指标改为${after.metric_fields.map((field) => `“${field}”`).join("、")}`);
  if (JSON.stringify(before.hidden_metric_fields) !== JSON.stringify(after.hidden_metric_fields)) changes.push(`可见指标改为${after.metric_fields.filter((field) => !after.hidden_metric_fields.includes(field)).map((field) => `“${field}”`).join("、")}`);
  if (JSON.stringify(before.current_result_top_n) !== JSON.stringify(after.current_result_top_n)) {
    changes.push(after.current_result_top_n
      ? `${after.current_result_top_n.direction === "desc" ? "Top" : "Bottom"} ${after.current_result_top_n.count}（${after.current_result_top_n.field}，当前结果）`
      : "移除当前结果排名");
  }
  const paletteNames: Record<ChartPaletteId, string> = {
    system_default: "系统默认",
    classic: "经典",
    ocean: "海洋",
    warm: "暖色",
    earth: "自然",
    pastel: "柔和",
    high_contrast: "高对比",
    color_vision_friendly: "色觉友好",
  };
  if ((before.color_palette_id ?? "system_default") !== (after.color_palette_id ?? "system_default")) {
    changes.push(`色板改为${paletteNames[after.color_palette_id ?? "system_default"]}`);
  }
  const colorDescription = (color: ChartColorSpec | undefined) => {
    if (!color) return "恢复色板配色";
    return color.mode === "solid"
      ? `纯色 ${color.hex}${color.opacity < 100 ? `（${color.opacity}% 不透明度）` : ""}`
      : `渐变 ${color.start_hex} 至 ${color.end_hex}${color.opacity < 100 ? `（${color.opacity}% 不透明度）` : ""}`;
  };
  const metricColors = changedMap(before.color_by_metric ?? {}, after.color_by_metric ?? {});
  for (const [field, color] of metricColors) changes.push(`“${field}”${colorDescription(color)}`);
  const beforeCategoryColors = before.pie_category_colors?.by_category_key ?? {};
  const afterCategoryColors = after.pie_category_colors?.by_category_key ?? {};
  for (const [key, color] of changedMap(beforeCategoryColors, afterCategoryColors)) {
    let label = key;
    try {
      const parsed = JSON.parse(key) as [string, unknown];
      label = `${String(parsed[1])}${parsed[0] === "number" ? "（数值）" : parsed[0] === "boolean" ? "（布尔值）" : ""}`;
    } catch { /* Keep the already validated stable key if decoding unexpectedly fails. */ }
    changes.push(`类别“${label}”${colorDescription(color)}`);
  }
  if (JSON.stringify(before.sort) !== JSON.stringify(after.sort) && !after.current_result_top_n) changes.push(`排序改为${sortLabel(after.sort)}`);
  for (const [field, label] of changedMap(before.field_labels, after.field_labels)) changes.push(label ? `“${field}”显示为“${label}”` : `“${field}”恢复原字段名`);
  for (const [field, format] of changedMap(before.format_by_field, after.format_by_field)) {
    changes.push(format ? `“${field}”数值格式已更新` : `“${field}”数值格式已移除`);
  }
  if (before.show_data_labels !== after.show_data_labels) changes.push(after.show_data_labels ? "显示数据标签" : "隐藏数据标签");
  if (before.show_legend !== after.show_legend) changes.push(after.show_legend ? "显示图例" : "隐藏图例");
  return changes.length ? changes.join("；") : "图表配置已更新";
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
    color_palette_id: "system_default",
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
      color_palette_id: view.color_palette_id ?? "system_default",
      color_by_metric: view.color_by_metric
        ? Object.fromEntries(Object.entries(view.color_by_metric).sort(([a], [b]) => a.localeCompare(b)))
        : undefined,
      pie_category_colors: view.pie_category_colors
        ? {
            dimension_field: view.pie_category_colors.dimension_field,
            by_category_key: Object.fromEntries(
              Object.entries(view.pie_category_colors.by_category_key).sort(([a], [b]) =>
                a.localeCompare(b),
              ),
            ),
          }
        : undefined,
      current_result_top_n: view.current_result_top_n,
    });
  return stable(left) === stable(right);
}

type ManualChartViewShape = Pick<
  ChartViewConfiguration,
  "chart_type" | "sort" | "metric_fields" | "hidden_metric_fields" | "current_result_top_n"
>;

export function clearTopNForManualChange<T extends ManualChartViewShape>(before: T, next: T): T {
  const rankedField = before.current_result_top_n?.field;
  if (!rankedField) return next;
  const sortChanged = JSON.stringify(before.sort) !== JSON.stringify(next.sort);
  const rankFieldUnavailable =
    !next.metric_fields.includes(rankedField) || next.hidden_metric_fields.includes(rankedField);
  if (before.chart_type === next.chart_type && !sortChanged && !rankFieldUnavailable) return next;
  const normalized = { ...next };
  delete normalized.current_result_top_n;
  return normalized;
}

export function readChartViewOverride(value: unknown): ChartViewOverride | undefined {
  if (!isRecord(value)) return undefined;
  const candidate = isRecord(value.artifact) ? value.artifact : value;
  if (
    candidate.kind !== "chart_view_override" ||
    (candidate.schema_version !== 1 && candidate.schema_version !== 2 && candidate.schema_version !== 3) ||
    typeof candidate.source_result_id !== "string" ||
    !candidate.source_result_id ||
    !isRecord(candidate.view)
  )
    return undefined;
  return candidate as unknown as ChartViewOverride;
}

function readChartViewUndoHistory(
  override: ChartViewOverride | undefined,
  query: SuccessfulQueryArtifact,
): ChartViewUndoRecord[] {
  if (!override || override.schema_version === 1 || override.undo_history === undefined) return [];
  if (!Array.isArray(override.undo_history) || override.undo_history.length > 20) return [];
  const history: ChartViewUndoRecord[] = [];
  for (const entry of override.undo_history) {
    if (
      !isRecord(entry) ||
      Object.keys(entry).some((key) => !["view", "summary", "origin"].includes(key)) ||
      typeof entry.summary !== "string" ||
      !entry.summary.trim() ||
      entry.summary.length > 512 ||
      (entry.origin !== "manual" &&
        entry.origin !== "natural_language" &&
        entry.origin !== "restore_recommendation")
    ) {
      return [];
    }
    const validation = validateChartView(entry.view, query);
    if (!validation.view) return [];
    history.push({ view: validation.view, summary: entry.summary, origin: entry.origin });
  }
  return history;
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

export function getChartMessageParts(
  outputs: unknown[],
  options: { persistenceAvailable?: boolean } = {},
): ChartMessagePart[] {
  const persistenceAvailable = options.persistenceAvailable ?? true;
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
    const undoHistory = validOverride
      ? readChartViewUndoHistory(parsedOverride, queryArtifact)
      : [];
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
          undoHistory,
          persistenceAvailable,
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
    return envelope.legacy_formatted.replace(/^\*\*SQL 语句\*\*\r?\n\r?\n/gm, "");
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
  return `\`\`\`sql\n${sql}\n\`\`\`\n\n**查询结果 · ${rowSummary}**\n\n${table}${
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
