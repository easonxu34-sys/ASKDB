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

export type AgentChartType = EChartsChartArtifact["chart_type"];
export type ChartType = AgentChartType | "area" | "step_line" | "scatter" | "heatmap";
export type ChartYAxisSide = "left" | "right";
export type ChartYAxisNameConfig = {
  visible?: boolean;
  text?: string;
};
export type ScatterFields = {
  x_field: string;
  y_field: string;
  visual_field?: string;
  visual_encoding?: "size" | "color";
};
export type HeatmapFields = {
  x_field: string;
  y_field: string;
  value_field: string;
  aggregation: "none" | "sum" | "avg" | "min" | "max";
};
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
  system_default: [
    "#5470C6",
    "#91CC75",
    "#FAC858",
    "#EE6666",
    "#73C0DE",
    "#3BA272",
    "#FC8452",
    "#9A60B4",
  ],
  classic: ["#3B82F6", "#14B8A6", "#22C55E", "#F59E0B", "#F97316", "#EF4444", "#A855F7", "#64748B"],
  ocean: ["#155E75", "#0E7490", "#0891B2", "#06B6D4", "#2563EB", "#4F46E5", "#6366F1", "#8B5CF6"],
  warm: ["#7F1D1D", "#B91C1C", "#DC2626", "#EA580C", "#F97316", "#D97706", "#CA8A04", "#92400E"],
  earth: ["#3F6212", "#4D7C0F", "#65A30D", "#15803D", "#0F766E", "#A16207", "#854D0E", "#78716C"],
  pastel: ["#8FBCE6", "#8FD3C1", "#E9A9CF", "#F2CD8F", "#BBA5EA", "#E89A9A", "#87C9CF", "#B6D99A"],
  high_contrast: [
    "#005FCC",
    "#D00000",
    "#008A00",
    "#AA00AA",
    "#B36B00",
    "#008C95",
    "#5B3A29",
    "#333333",
  ],
  color_vision_friendly: [
    "#4E79A7",
    "#F28E2B",
    "#E15759",
    "#76B7B2",
    "#59A14F",
    "#EDC948",
    "#B07AA1",
    "#9C755F",
  ],
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
  bar_stack_mode?: "grouped" | "stacked" | "percent";
  y_axis_by_metric?: Record<string, ChartYAxisSide>;
  y_axis_names?: Partial<Record<ChartYAxisSide, ChartYAxisNameConfig>>;
  step_position?: "start" | "middle" | "end";
  scatter_fields?: ScatterFields;
  heatmap_fields?: HeatmapFields;
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
  annotations?: ChartAnnotations;
};
export type ChartAnnotations = {
  reference_lines: Array<{
    id: string;
    metric_field: string;
    kind: "value" | "mean" | "peak";
    value?: string;
    scope: "returned_rows" | "viewport";
    label: string;
  }>;
  reference_areas: Array<{
    id: string;
    source_row_indices: number[];
    label: string;
  }>;
};
export type ChartViewEditOrigin = "manual" | "natural_language" | "restore_recommendation";
export type ChartViewUndoRecord = {
  view: ChartViewConfiguration;
  summary: string;
  origin: ChartViewEditOrigin;
};
export type ChartViewOverride = {
  kind: "chart_view_override";
  schema_version: 1 | 2 | 3 | 4;
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
  if (
    !isRecord(value) ||
    !Number.isInteger(value.opacity) ||
    (value.opacity as number) < 0 ||
    (value.opacity as number) > 100
  ) {
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
    (value.direction === "horizontal" ||
      value.direction === "vertical" ||
      value.direction === "diagonal_down" ||
      value.direction === "diagonal_up")
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

export function chartHeatmapAxisKey(value: unknown): string | undefined {
  if (value === null || value === undefined) return "null";
  if (
    (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean") ||
    (typeof value === "number" && !Number.isFinite(value))
  ) {
    return undefined;
  }
  return JSON.stringify([typeof value, value]);
}

function validPercentStackRows(query: SuccessfulQueryArtifact, metrics: string[]) {
  if (metrics.length < 2) return false;
  for (const row of query.rows.slice(0, 1000)) {
    const values = metrics.map((field) => row[field]);
    const parts = values.map(decimalParts);
    if (parts.some((value) => !value || value.coefficient < BigInt("0"))) return false;
    if (values.some((value) => exactPlotNumber(value) === undefined)) return false;
    const total = sumDecimalValues(values);
    if (!total || total.coefficient <= BigInt("0")) return false;
  }
  return query.rows.length > 0;
}

export function resolvePieCategoryLabel(
  query: SuccessfulQueryArtifact,
  dimension: string,
  label: string,
): { status: "matched"; category_key: string } | { status: "not_found" } | { status: "ambiguous" } {
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

const EMPTY_CHART_ANNOTATIONS: ChartAnnotations = {
  reference_lines: [],
  reference_areas: [],
};
const ANNOTATION_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/u;

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]) {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function readChartAnnotations(
  value: unknown,
  query: SuccessfulQueryArtifact,
  visibleMetrics: string[],
): ChartAnnotations | undefined {
  if (value === undefined) {
    return {
      reference_lines: [],
      reference_areas: [],
    };
  }
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, ["reference_lines", "reference_areas"]) ||
    !Array.isArray(value.reference_lines) ||
    value.reference_lines.length > 8 ||
    !Array.isArray(value.reference_areas) ||
    value.reference_areas.length > 32
  ) {
    return undefined;
  }

  const referenceLines: ChartAnnotations["reference_lines"] = [];
  for (const entry of value.reference_lines) {
    if (
      !isRecord(entry) ||
      !hasOnlyKeys(entry, ["id", "metric_field", "kind", "value", "scope", "label"]) ||
      typeof entry.id !== "string" ||
      !ANNOTATION_ID_PATTERN.test(entry.id) ||
      typeof entry.metric_field !== "string" ||
      !visibleMetrics.includes(entry.metric_field) ||
      fieldKind(query, entry.metric_field) !== "numeric" ||
      (entry.kind !== "value" && entry.kind !== "mean" && entry.kind !== "peak") ||
      (entry.scope !== "returned_rows" && entry.scope !== "viewport") ||
      typeof entry.label !== "string" ||
      !entry.label.trim() ||
      entry.label.length > 80
    ) {
      return undefined;
    }
    if (entry.kind === "value") {
      if (
        typeof entry.value !== "string" ||
        entry.value.length > 256 ||
        decimalParts(entry.value) === undefined
      ) {
        return undefined;
      }
      referenceLines.push({
        id: entry.id,
        metric_field: entry.metric_field,
        kind: "value",
        value: entry.value,
        scope: entry.scope,
        label: entry.label.trim(),
      });
    } else {
      if (entry.value !== undefined) return undefined;
      referenceLines.push({
        id: entry.id,
        metric_field: entry.metric_field,
        kind: entry.kind,
        scope: entry.scope,
        label: entry.label.trim(),
      });
    }
  }

  const referenceAreas: ChartAnnotations["reference_areas"] = [];
  const maxSourceRowIndex = Math.min(query.rows.length, 1000);
  for (const entry of value.reference_areas) {
    if (
      !isRecord(entry) ||
      !hasOnlyKeys(entry, ["id", "source_row_indices", "label"]) ||
      typeof entry.id !== "string" ||
      !ANNOTATION_ID_PATTERN.test(entry.id) ||
      !Array.isArray(entry.source_row_indices) ||
      entry.source_row_indices.length > 1000 ||
      typeof entry.label !== "string" ||
      !entry.label.trim() ||
      entry.label.length > 80
    ) {
      return undefined;
    }
    const sourceRowIndices: number[] = [];
    const seen = new Set<number>();
    for (const index of entry.source_row_indices) {
      if (!Number.isSafeInteger(index) || index < 0 || index >= maxSourceRowIndex) return undefined;
      if (!seen.has(index)) {
        seen.add(index);
        sourceRowIndices.push(index);
      }
    }
    if (sourceRowIndices.length === 0) return undefined;
    referenceAreas.push({
      id: entry.id,
      source_row_indices: sourceRowIndices,
      label: entry.label.trim(),
    });
  }
  return { reference_lines: referenceLines, reference_areas: referenceAreas };
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
  if (
    chartType !== "line" &&
    chartType !== "area" &&
    chartType !== "step_line" &&
    chartType !== "bar" &&
    chartType !== "pie" &&
    chartType !== "scatter" &&
    chartType !== "heatmap"
  ) {
    issue("chart_type", "请选择有效的图表类型。");
  }
  const scatterCandidate = isRecord(input.scatter_fields) ? input.scatter_fields : undefined;
  const heatmapCandidate = isRecord(input.heatmap_fields) ? input.heatmap_fields : undefined;
  let colorPaletteId: ChartPaletteId = "system_default";
  if (input.color_palette_id !== undefined) {
    if (CHART_PALETTE_IDS.includes(input.color_palette_id as ChartPaletteId)) {
      colorPaletteId = input.color_palette_id as ChartPaletteId;
    } else {
      issue("color_palette_id", "请选择有效的图表色板。");
    }
  }
  const dimension = typeof input.dimension_field === "string" ? input.dimension_field : "";
  const dimensionKind = fieldKind(query, dimension);
  const dimensionValid =
    chartType === "scatter"
      ? dimensionKind === "numeric" && scatterCandidate?.x_field === dimension
      : ["categorical", "temporal"].includes(dimensionKind ?? "");
  if (!dimension || !dimensionValid) {
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
  const visibleMetricFields = metricFields.filter((field) => !hiddenFields.includes(field));
  let scatterFields: ScatterFields | undefined;
  if (chartType === "scatter") {
    if (
      !scatterCandidate ||
      !hasOnlyKeys(scatterCandidate, ["x_field", "y_field", "visual_field", "visual_encoding"])
    ) {
      issue("scatter_fields", "散点图字段配置无效。");
    } else {
      const xField = scatterCandidate.x_field;
      const yField = scatterCandidate.y_field;
      const visualField = scatterCandidate.visual_field;
      const visualEncoding = scatterCandidate.visual_encoding;
      if (
        typeof xField !== "string" ||
        xField !== dimension ||
        fieldKind(query, xField) !== "numeric" ||
        typeof yField !== "string" ||
        fieldKind(query, yField) !== "numeric" ||
        xField === yField
      ) {
        issue("scatter_fields", "散点图 X、Y 必须是两个不同的 Arrow 数值字段。");
      } else if (
        (visualField === undefined && visualEncoding !== undefined) ||
        (visualField !== undefined &&
          (typeof visualField !== "string" ||
            fieldKind(query, visualField) !== "numeric" ||
            (visualEncoding !== "size" && visualEncoding !== "color") ||
            visualField === xField ||
            visualField === yField))
      ) {
        issue("scatter_fields", "气泡大小或颜色映射必须使用第三个不同的数值字段。");
      } else {
        const expectedMetrics = [yField, ...(typeof visualField === "string" ? [visualField] : [])];
        if (
          JSON.stringify(metricFields) !== JSON.stringify(expectedMetrics) ||
          hiddenFields.length > 0
        ) {
          issue("metric_fields", "散点图指标必须对应 Y 字段和可选的第三映射字段。");
        }
        if (
          typeof visualField === "string" &&
          query.rows.slice(0, 1000).some((row) => {
            const value = row[visualField];
            const parts = decimalParts(value);
            return (
              !parts ||
              exactPlotNumber(value) === undefined ||
              (visualEncoding === "size" && parts.coefficient < BigInt("0"))
            );
          })
        ) {
          issue(
            "scatter_fields",
            visualEncoding === "size"
              ? "气泡大小字段必须全部是有限的非负数值。"
              : "颜色映射字段必须全部是有限数值。",
          );
        }
        if (
          !query.rows
            .slice(0, 1000)
            .some(
              (row) =>
                exactPlotNumber(row[xField]) !== undefined &&
                exactPlotNumber(row[yField]) !== undefined,
            )
        ) {
          issue("scatter_fields", "当前已返回结果中没有可绘制的有限 X、Y 数值点。");
        }
        scatterFields = {
          x_field: xField,
          y_field: yField,
          ...(typeof visualField === "string"
            ? { visual_field: visualField, visual_encoding: visualEncoding as "size" | "color" }
            : {}),
        };
      }
    }
  } else if (input.scatter_fields !== undefined) {
    issue("scatter_fields", "散点图字段配置不能用于其他图表类型。");
  }

  let heatmapFields: HeatmapFields | undefined;
  if (chartType === "heatmap") {
    const aggregations = ["none", "sum", "avg", "min", "max"];
    if (
      !heatmapCandidate ||
      !hasOnlyKeys(heatmapCandidate, ["x_field", "y_field", "value_field", "aggregation"])
    ) {
      issue("heatmap_fields", "热力图字段配置无效。");
    } else {
      const xField = heatmapCandidate.x_field;
      const yField = heatmapCandidate.y_field;
      const valueField = heatmapCandidate.value_field;
      const aggregation = heatmapCandidate.aggregation;
      if (
        typeof xField !== "string" ||
        xField !== dimension ||
        !["categorical", "temporal"].includes(fieldKind(query, xField) ?? "") ||
        typeof yField !== "string" ||
        !["categorical", "temporal"].includes(fieldKind(query, yField) ?? "") ||
        typeof valueField !== "string" ||
        fieldKind(query, valueField) !== "numeric" ||
        new Set([xField, yField, valueField]).size !== 3 ||
        !aggregations.includes(String(aggregation))
      ) {
        issue("heatmap_fields", "热力图需要两个不同的分类/时间字段和一个数值指标。");
      } else {
        const xKeys = new Set<string>();
        const yKeys = new Set<string>();
        const cellKeys = new Set<string>();
        let duplicateCell = false;
        let invalidAxis = false;
        let invalidValue = false;
        let hasNumericValue = false;
        for (const row of query.rows.slice(0, 1000)) {
          const xKey = chartHeatmapAxisKey(row[xField]);
          const yKey = chartHeatmapAxisKey(row[yField]);
          if (xKey === undefined || yKey === undefined) {
            invalidAxis = true;
            continue;
          }
          xKeys.add(xKey);
          yKeys.add(yKey);
          const cellKey = JSON.stringify([xKey, yKey]);
          if (cellKeys.has(cellKey)) duplicateCell = true;
          cellKeys.add(cellKey);
          const rawValue = row[valueField];
          if (rawValue !== null && rawValue !== undefined && rawValue !== "") {
            if (!decimalParts(rawValue) || exactPlotNumber(rawValue) === undefined) {
              invalidValue = true;
            } else {
              hasNumericValue = true;
            }
          }
        }
        if (xKeys.size > 60 || yKeys.size > 60 || xKeys.size * yKeys.size > 3600) {
          issue("heatmap_fields", "热力图每个坐标轴最多 60 个值，网格最多 3,600 个单元格。");
        }
        if (invalidAxis) issue("heatmap_fields", "热力图维度值必须是可显示的分类、时间或空值。");
        if (invalidValue) issue("heatmap_fields", "热力图指标只能包含有限数值或空值。");
        if (!hasNumericValue) issue("heatmap_fields", "当前已返回结果中没有可绘制的数值单元格。");
        if (aggregation === "none" && duplicateCell) {
          issue("heatmap_fields", "维度组合存在重复行；请选择明确的聚合方式后再绘制。");
        }
        if (JSON.stringify(metricFields) !== JSON.stringify([valueField]) || hiddenFields.length) {
          issue("metric_fields", "热力图只能使用一个可见数值指标。");
        }
        heatmapFields = {
          x_field: xField,
          y_field: yField,
          value_field: valueField,
          aggregation: aggregation as HeatmapFields["aggregation"],
        };
      }
    }
  } else if (input.heatmap_fields !== undefined) {
    issue("heatmap_fields", "热力图字段配置不能用于其他图表类型。");
  }

  const annotations = readChartAnnotations(input.annotations, query, visibleMetricFields);
  if (!annotations)
    issue("annotations", "参考标注必须使用当前可见指标和本次查询结果中的有效索引。");
  if (
    !["line", "area", "step_line", "bar"].includes(String(chartType)) &&
    annotations &&
    (annotations.reference_lines.length > 0 || annotations.reference_areas.length > 0)
  ) {
    issue("annotations", "参考标注只支持折线、面积、阶梯线和柱状图。");
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

  let barStackMode: "grouped" | "stacked" | "percent" = "grouped";
  if (input.bar_stack_mode !== undefined) {
    if (
      chartType !== "bar" ||
      (input.bar_stack_mode !== "grouped" &&
        input.bar_stack_mode !== "stacked" &&
        input.bar_stack_mode !== "percent")
    ) {
      issue("bar_stack_mode", "堆叠模式只能用于柱状图。");
    } else {
      barStackMode = input.bar_stack_mode;
    }
  }

  let yAxisByMetric: Record<string, ChartYAxisSide> | undefined;
  if (input.y_axis_by_metric !== undefined) {
    if (!isRecord(input.y_axis_by_metric)) {
      issue("y_axis_by_metric", "Y 轴分配配置无效。");
    } else {
      yAxisByMetric = {};
      for (const [field, side] of Object.entries(input.y_axis_by_metric)) {
        if (!metricFields.includes(field)) {
          issue("y_axis_by_metric", "Y 轴分配只能对应当前已选指标。");
          continue;
        }
        if (side !== "left" && side !== "right") {
          issue("y_axis_by_metric", "每个指标只能分配到左 Y 轴或右 Y 轴。");
          continue;
        }
        yAxisByMetric[field] = side;
      }
      for (const field of metricFields) yAxisByMetric[field] ??= "left";
      const supportsMultipleYAxis =
        chartType === "line" ||
        chartType === "area" ||
        chartType === "step_line" ||
        (chartType === "bar" && barOrientation === "vertical" && barStackMode === "grouped");
      const hasRightMetric = metricFields.some(
        (field) => !hiddenFields.includes(field) && yAxisByMetric?.[field] === "right",
      );
      const hasLeftMetric = visibleMetricFields.some((field) => yAxisByMetric?.[field] !== "right");
      if (Object.values(yAxisByMetric).includes("right") && !supportsMultipleYAxis) {
        issue("y_axis_by_metric", "多 Y 轴只支持折线、面积、阶梯线和纵向分组柱状图。");
      }
      if (hasRightMetric && !hasLeftMetric) {
        issue("y_axis_by_metric", "使用右 Y 轴时，至少还需一个可见指标保留在左 Y 轴。");
      }
      if (Object.values(yAxisByMetric).every((side) => side === "left")) {
        yAxisByMetric = undefined;
      }
    }
  }

  let yAxisNames: ChartViewConfiguration["y_axis_names"];
  if (input.y_axis_names !== undefined) {
    if (!isRecord(input.y_axis_names)) {
      issue("y_axis_names", "Y 轴名称配置无效。");
    } else {
      const parsedNames: NonNullable<ChartViewConfiguration["y_axis_names"]> = {};
      const supportsYAxisNames =
        chartType === "line" ||
        chartType === "area" ||
        chartType === "step_line" ||
        (chartType === "bar" && barOrientation === "vertical");
      const supportsRightYAxis =
        chartType === "line" ||
        chartType === "area" ||
        chartType === "step_line" ||
        (chartType === "bar" && barOrientation === "vertical" && barStackMode === "grouped");
      const hasRightAssignment = Object.values(yAxisByMetric ?? {}).includes("right");

      if (!supportsYAxisNames) {
        issue("y_axis_names", "Y 轴名称只适用于纵向数值轴图表。");
      }
      for (const [side, value] of Object.entries(input.y_axis_names)) {
        if (side !== "left" && side !== "right") {
          issue("y_axis_names", "Y 轴名称只能配置左轴或右轴。");
          continue;
        }
        if (!isRecord(value)) {
          issue(`y_axis_names.${side}`, "Y 轴名称配置无效。");
          continue;
        }
        if (Object.keys(value).some((key) => key !== "visible" && key !== "text")) {
          issue(`y_axis_names.${side}`, "Y 轴名称配置包含不支持的字段。");
        }
        if (side === "right" && (!supportsRightYAxis || !hasRightAssignment)) {
          issue("y_axis_names.right", "右轴名称需要先启用兼容的右 Y 轴系列。");
        }

        const name: ChartYAxisNameConfig = {};
        if (value.visible !== undefined) {
          if (typeof value.visible === "boolean") name.visible = value.visible;
          else issue(`y_axis_names.${side}.visible`, "请选择是否显示 Y 轴名称。");
        }
        if (value.text !== undefined) {
          if (typeof value.text !== "string" || value.text.length > 36) {
            issue(`y_axis_names.${side}.text`, "Y 轴名称不能超过 36 个字符。");
          } else if (value.text.trim()) {
            name.text = value.text.trim();
          }
        }
        if (Object.keys(name).length > 0) parsedNames[side] = name;
      }
      if (Object.keys(parsedNames).length > 0) yAxisNames = parsedNames;
    }
  }

  let stepPosition: "start" | "middle" | "end" = "end";
  if (input.step_position !== undefined) {
    if (
      chartType !== "step_line" ||
      (input.step_position !== "start" &&
        input.step_position !== "middle" &&
        input.step_position !== "end")
    ) {
      issue("step_position", "阶梯位置只能用于阶梯线，并需选择开始、中间或结束。");
    } else {
      stepPosition = input.step_position;
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
  if (
    ["line", "area", "step_line", "scatter", "heatmap"].includes(String(chartType)) &&
    sort.mode === "metric"
  ) {
    issue("sort", "当前图表不能按指标重排数据点。");
  }
  if (chartType === "pie" && sort.mode === "metric" && metricFields.length !== 1) {
    issue("sort", "饼图只能按唯一指标排序。");
  }
  if (chartType === "pie" && sort.mode === "dimension") {
    issue("sort", "饼图只支持查询原序或按唯一指标排序。");
  }
  if ((chartType === "scatter" || chartType === "heatmap") && sort.mode !== "original") {
    issue("sort", "散点图和热力图保留查询原序以稳定坐标映射。");
  }
  if (
    chartType === "step_line" &&
    fieldKind(query, dimension) === "categorical" &&
    sort.mode !== "original"
  ) {
    issue("sort", "分类字段的阶梯线必须使用查询返回顺序；需要排序时请先在查询中明确顺序。");
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
  if (chartType === "bar" && barStackMode === "percent") {
    if (!validPercentStackRows(query, visibleMetricFields)) {
      issue(
        "bar_stack_mode",
        "百分比堆叠需要至少两个可见指标，且每行均为有限非负数值并有正的行内合计。",
      );
    }
    if (
      annotations &&
      (annotations.reference_lines.length > 0 || annotations.reference_areas.length > 0)
    ) {
      issue("annotations", "百分比堆叠图不支持原始值参考标注。");
    }
  }

  let colorByMetric: Record<string, ChartColorSpec> | undefined;
  if (input.color_by_metric !== undefined) {
    if (!isRecord(input.color_by_metric)) {
      issue("color_by_metric", "指标配色配置无效。");
    } else {
      colorByMetric = {};
      for (const [field, value] of Object.entries(input.color_by_metric)) {
        if (chartType === "pie" || chartType === "scatter" || chartType === "heatmap") {
          issue("chart_type", "饼图、散点图和热力图使用类别或连续视觉映射配色。");
          break;
        }
        const color = readChartColorSpec(value);
        if (
          Object.keys(input.color_by_metric).length > 4 ||
          !metricFields.includes(field) ||
          hiddenFields.includes(field) ||
          !color ||
          (color.mode === "linear_gradient" && chartType === "pie")
        ) {
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
      ["line", "area", "step_line", "scatter", "heatmap"].includes(String(chartType)) ||
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
    ...(chartType === "bar" ? { bar_stack_mode: barStackMode } : {}),
    ...(yAxisByMetric ? { y_axis_by_metric: yAxisByMetric } : {}),
    ...(yAxisNames ? { y_axis_names: yAxisNames } : {}),
    ...(chartType === "step_line" ? { step_position: stepPosition } : {}),
    ...(scatterFields ? { scatter_fields: scatterFields } : {}),
    ...(heatmapFields ? { heatmap_fields: heatmapFields } : {}),
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
    annotations: annotations ?? { reference_lines: [], reference_areas: [] },
  };
  return { view, errors };
}

type ChartViewChangeShape = Omit<ChartViewConfiguration, "format_by_field"> & {
  format_by_field: Record<string, unknown>;
};

export function normalizeChartViewChange<T extends ChartViewChangeShape>(
  before: T,
  proposed: T,
  query: SuccessfulQueryArtifact,
  {
    explicitSort = JSON.stringify(before.sort) !== JSON.stringify(proposed.sort),
    preserveTopN = false,
  } = {},
): T {
  const next = {
    ...proposed,
    metric_fields: [...proposed.metric_fields],
    hidden_metric_fields: [...proposed.hidden_metric_fields],
    sort: { ...proposed.sort },
    field_labels: { ...proposed.field_labels },
    format_by_field: { ...proposed.format_by_field },
    annotations: {
      reference_lines: (proposed.annotations ?? EMPTY_CHART_ANNOTATIONS).reference_lines.map(
        (line) => ({
          ...line,
        }),
      ),
      reference_areas: (proposed.annotations ?? EMPTY_CHART_ANNOTATIONS).reference_areas.map(
        (area) => ({
          ...area,
          source_row_indices: [...area.source_row_indices],
        }),
      ),
    },
    ...(proposed.color_by_metric ? { color_by_metric: { ...proposed.color_by_metric } } : {}),
    ...(proposed.y_axis_by_metric ? { y_axis_by_metric: { ...proposed.y_axis_by_metric } } : {}),
    ...(proposed.y_axis_names
      ? {
          y_axis_names: {
            ...(proposed.y_axis_names.left ? { left: { ...proposed.y_axis_names.left } } : {}),
            ...(proposed.y_axis_names.right ? { right: { ...proposed.y_axis_names.right } } : {}),
          },
        }
      : {}),
    ...(proposed.scatter_fields ? { scatter_fields: { ...proposed.scatter_fields } } : {}),
    ...(proposed.heatmap_fields ? { heatmap_fields: { ...proposed.heatmap_fields } } : {}),
  };
  const visibleMetrics = new Set(
    next.metric_fields.filter((field) => !next.hidden_metric_fields.includes(field)),
  );
  next.annotations.reference_lines = next.annotations.reference_lines.filter((line) =>
    visibleMetrics.has(line.metric_field),
  );
  const dimensionChanged = next.dimension_field !== before.dimension_field;
  if (JSON.stringify(next.metric_fields) !== JSON.stringify(before.metric_fields)) {
    next.hidden_metric_fields = next.hidden_metric_fields.filter((field) =>
      next.metric_fields.includes(field),
    );
  }
  if (dimensionChanged || next.chart_type !== "pie") delete next.pie_category_colors;
  if (new Set<string>(["pie", "scatter", "heatmap"]).has(next.chart_type))
    delete next.color_by_metric;
  if (next.chart_type !== "bar") {
    delete next.bar_orientation;
    delete next.bar_stack_mode;
  } else {
    next.bar_orientation ??= "vertical";
    next.bar_stack_mode ??= "grouped";
  }
  const supportsMultipleYAxis =
    next.chart_type === "line" ||
    next.chart_type === "area" ||
    next.chart_type === "step_line" ||
    (next.chart_type === "bar" &&
      next.bar_orientation === "vertical" &&
      next.bar_stack_mode === "grouped");
  if (!supportsMultipleYAxis) {
    delete next.y_axis_by_metric;
  } else if (next.y_axis_by_metric) {
    const yAxisByMetric: Record<string, ChartYAxisSide> = {};
    for (const field of next.metric_fields) {
      yAxisByMetric[field] = next.y_axis_by_metric[field] === "right" ? "right" : "left";
    }
    const visible = next.metric_fields.filter(
      (field) => !next.hidden_metric_fields.includes(field),
    );
    const hasRight = visible.some((field) => yAxisByMetric[field] === "right");
    const hasLeft = visible.some((field) => yAxisByMetric[field] === "left");
    if (hasRight && !hasLeft) {
      for (const field of visible) yAxisByMetric[field] = "left";
    }
    if (Object.values(yAxisByMetric).every((side) => side === "left")) {
      delete next.y_axis_by_metric;
    } else {
      next.y_axis_by_metric = yAxisByMetric;
    }
  }
  const supportsYAxisNames =
    next.chart_type === "line" ||
    next.chart_type === "area" ||
    next.chart_type === "step_line" ||
    (next.chart_type === "bar" && next.bar_orientation === "vertical");
  if (!supportsYAxisNames) {
    delete next.y_axis_names;
  } else if (next.y_axis_names) {
    const names = { ...next.y_axis_names };
    const supportsRightYAxis =
      next.chart_type === "line" ||
      next.chart_type === "area" ||
      next.chart_type === "step_line" ||
      (next.chart_type === "bar" && next.bar_stack_mode === "grouped");
    const hasRightAssignment = Object.values(next.y_axis_by_metric ?? {}).includes("right");
    if (!supportsRightYAxis || !hasRightAssignment) delete names.right;
    for (const side of ["left", "right"] as const) {
      const name = names[side];
      if (!name) continue;
      const text = name.text?.trim();
      if (text) name.text = text;
      else delete name.text;
      if (name.visible === undefined && !name.text) delete names[side];
    }
    if (Object.keys(names).length > 0) next.y_axis_names = names;
    else delete next.y_axis_names;
  }
  if (next.chart_type !== "step_line") delete next.step_position;
  else next.step_position ??= "end";
  if (next.chart_type !== "scatter") delete next.scatter_fields;
  if (next.chart_type !== "heatmap") delete next.heatmap_fields;
  if (!new Set<string>(["line", "area", "step_line", "bar"]).has(next.chart_type)) {
    next.annotations = { reference_lines: [], reference_areas: [] };
  }
  if (next.chart_type === "bar" && next.bar_stack_mode === "percent") {
    next.annotations = { reference_lines: [], reference_areas: [] };
  }
  for (const field of Object.keys(next.color_by_metric ?? {})) {
    if (!next.metric_fields.includes(field) || next.hidden_metric_fields.includes(field)) {
      delete next.color_by_metric![field];
    }
  }
  if (next.color_by_metric && Object.keys(next.color_by_metric).length === 0)
    delete next.color_by_metric;
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
    const metricSortUnavailable =
      next.sort.mode === "metric" &&
      (!next.metric_fields.includes(next.sort.field) ||
        next.hidden_metric_fields.includes(next.sort.field));
    const lineSortIncompatible =
      new Set<string>(["line", "area", "step_line", "scatter", "heatmap"]).has(next.chart_type) &&
      next.sort.mode === "metric";
    const pieSortIncompatible =
      next.chart_type === "pie" &&
      (next.sort.mode === "dimension" ||
        (next.sort.mode === "metric" && next.metric_fields.length !== 1));
    const coordinateSortIncompatible =
      (next.chart_type === "scatter" || next.chart_type === "heatmap") &&
      next.sort.mode !== "original";
    const categoryStepSortIncompatible =
      next.chart_type === "step_line" &&
      fieldKind(query, next.dimension_field) === "categorical" &&
      next.sort.mode !== "original";
    if (
      metricSortUnavailable ||
      lineSortIncompatible ||
      pieSortIncompatible ||
      coordinateSortIncompatible ||
      categoryStepSortIncompatible
    ) {
      next.sort =
        new Set<string>(["line", "area", "step_line"]).has(next.chart_type) &&
        fieldKind(query, next.dimension_field) === "temporal"
          ? { mode: "dimension", field: next.dimension_field, direction: "asc" }
          : { mode: "original" };
    }
  }
  return next;
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
  const changedMap = <T>(left: Record<string, T>, right: Record<string, T>) =>
    [...new Set([...Object.keys(left), ...Object.keys(right)])]
      .filter((key) => JSON.stringify(left[key]) !== JSON.stringify(right[key]))
      .map((key) => [key, right[key]] as const);
  if (before.title !== after.title) changes.push(`标题改为“${after.title}”`);
  if (before.chart_type !== after.chart_type)
    changes.push(`图表类型改为${chartTypeLabel(after.chart_type)}`);
  if (before.dimension_field !== after.dimension_field)
    changes.push(`维度改为“${after.dimension_field}”`);
  if (before.bar_orientation !== after.bar_orientation && after.chart_type === "bar")
    changes.push(`柱形方向改为${after.bar_orientation === "horizontal" ? "横向" : "纵向"}`);
  if (before.bar_stack_mode !== after.bar_stack_mode && after.chart_type === "bar")
    changes.push(
      `柱状图模式改为${after.bar_stack_mode === "percent" ? "百分比堆叠" : after.bar_stack_mode === "stacked" ? "堆叠" : "分组"}`,
    );
  if (before.step_position !== after.step_position && after.chart_type === "step_line")
    changes.push(
      `阶梯位置改为${after.step_position === "start" ? "开始" : after.step_position === "middle" ? "中间" : "结束"}`,
    );
  if (JSON.stringify(before.scatter_fields) !== JSON.stringify(after.scatter_fields))
    changes.push("散点图字段映射已更新");
  if (JSON.stringify(before.heatmap_fields) !== JSON.stringify(after.heatmap_fields))
    changes.push("热力图字段或聚合方式已更新");
  if (JSON.stringify(before.y_axis_names) !== JSON.stringify(after.y_axis_names))
    changes.push("Y 轴名称设置已更新");
  if (
    after.metric_fields.some((field) => {
      const beforeSide = before.y_axis_by_metric?.[field] ?? "left";
      const afterSide = after.y_axis_by_metric?.[field] ?? "left";
      return before.metric_fields.includes(field)
        ? beforeSide !== afterSide
        : afterSide === "right";
    })
  ) {
    changes.push("Y 轴系列分配已更新");
  }
  if (JSON.stringify(before.metric_fields) !== JSON.stringify(after.metric_fields))
    changes.push(`指标改为${after.metric_fields.map((field) => `“${field}”`).join("、")}`);
  if (JSON.stringify(before.hidden_metric_fields) !== JSON.stringify(after.hidden_metric_fields))
    changes.push(
      `可见指标改为${after.metric_fields
        .filter((field) => !after.hidden_metric_fields.includes(field))
        .map((field) => `“${field}”`)
        .join("、")}`,
    );
  if (JSON.stringify(before.current_result_top_n) !== JSON.stringify(after.current_result_top_n)) {
    changes.push(
      after.current_result_top_n
        ? `${after.current_result_top_n.direction === "desc" ? "Top" : "Bottom"} ${after.current_result_top_n.count}（${after.current_result_top_n.field}，当前结果）`
        : "移除当前结果排名",
    );
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
  if (
    (before.color_palette_id ?? "system_default") !== (after.color_palette_id ?? "system_default")
  ) {
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
    } catch {
      /* Keep the already validated stable key if decoding unexpectedly fails. */
    }
    changes.push(`类别“${label}”${colorDescription(color)}`);
  }
  if (JSON.stringify(before.sort) !== JSON.stringify(after.sort) && !after.current_result_top_n)
    changes.push(`排序改为${sortLabel(after.sort)}`);
  for (const [field, label] of changedMap(before.field_labels, after.field_labels))
    changes.push(label ? `“${field}”显示为“${label}”` : `“${field}”恢复原字段名`);
  for (const [field, format] of changedMap(before.format_by_field, after.format_by_field)) {
    changes.push(format ? `“${field}”数值格式已更新` : `“${field}”数值格式已移除`);
  }
  if (before.show_data_labels !== after.show_data_labels)
    changes.push(after.show_data_labels ? "显示数据标签" : "隐藏数据标签");
  if (before.show_legend !== after.show_legend)
    changes.push(after.show_legend ? "显示图例" : "隐藏图例");
  if (
    JSON.stringify(before.annotations ?? EMPTY_CHART_ANNOTATIONS) !==
    JSON.stringify(after.annotations ?? EMPTY_CHART_ANNOTATIONS)
  ) {
    const count =
      (after.annotations?.reference_lines.length ?? 0) +
      (after.annotations?.reference_areas.length ?? 0);
    changes.push(`参考标注更新（${count} 项）`);
  }
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

export function chartTypeLabel(type: ChartType) {
  return {
    line: "折线图",
    area: "面积图",
    step_line: "阶梯线",
    bar: "柱状图",
    pie: "饼图",
    scatter: "散点图",
    heatmap: "热力图",
  }[type];
}

export function chartViewsEqual(left: ChartViewConfiguration, right: ChartViewConfiguration) {
  const stable = (view: ChartViewConfiguration) =>
    JSON.stringify({
      chart_type: view.chart_type,
      dimension_field: view.dimension_field,
      metric_fields: view.metric_fields,
      hidden_metric_fields: [...view.hidden_metric_fields].sort(),
      bar_orientation: view.chart_type === "bar" ? (view.bar_orientation ?? "vertical") : undefined,
      bar_stack_mode: view.chart_type === "bar" ? (view.bar_stack_mode ?? "grouped") : undefined,
      y_axis_by_metric: Object.fromEntries(
        view.metric_fields
          .map((field) => [field, view.y_axis_by_metric?.[field] ?? "left"] as const)
          .sort(([left], [right]) => left.localeCompare(right)),
      ),
      y_axis_names: (() => {
        const hasRightYAxis = view.metric_fields.some(
          (field) =>
            !view.hidden_metric_fields.includes(field) &&
            view.y_axis_by_metric?.[field] === "right",
        );
        return Object.fromEntries(
          (["left", "right"] as const).map((side) => {
            const config = view.y_axis_names?.[side];
            const text = config?.text?.trim() || undefined;
            const visible = config?.visible ?? (hasRightYAxis || Boolean(text));
            return [side, { visible, text }] as const;
          }),
        );
      })(),
      step_position: view.chart_type === "step_line" ? (view.step_position ?? "end") : undefined,
      scatter_fields: view.scatter_fields,
      heatmap_fields: view.heatmap_fields,
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
        ? Object.fromEntries(
            Object.entries(view.color_by_metric).sort(([a], [b]) => a.localeCompare(b)),
          )
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
      annotations: view.annotations ?? EMPTY_CHART_ANNOTATIONS,
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
    (candidate.schema_version !== 1 &&
      candidate.schema_version !== 2 &&
      candidate.schema_version !== 3 &&
      candidate.schema_version !== 4) ||
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
