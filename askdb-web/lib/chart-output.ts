import {
  chartCategoryKey,
  chartHeatmapAxisKey,
  createRecommendedChartView,
  validateChartView,
  CHART_COLOR_PALETTES,
  type ChartValueFormat,
  type ChartColorSpec,
  type ChartPaletteId,
  type ChartViewConfiguration,
  type EChartsChartArtifact,
  type SuccessfulQueryArtifact,
} from "./chat-output.ts";
import {
  compareDecimalValues,
  averageDecimalValues,
  decimalParts,
  decimalText,
  exactPercentShare,
  exactPlotNumber,
  formatDecimal,
  sumDecimalValues,
} from "./chart-decimal.ts";
import {
  calculateChartStatistics,
  getChartViewportRows,
  type ChartStatisticsScope,
  type ChartViewport,
} from "./chart-statistics.ts";

export { chartCategoryKey, resolvePieCategoryLabel } from "./chat-output.ts";

type QueryShape = SuccessfulQueryArtifact & {
  resultId: string;
  columnTypes: string[];
};

export type ChartDisplayRow = {
  row: Record<string, unknown>;
  sourceRowIndex: number;
  sourceRowIndexes?: number[];
};

const CNY_UNIT_POWERS = {
  yuan: 0,
  thousand_yuan: 3,
  ten_thousand_yuan: 4,
  hundred_million_yuan: 8,
} as const;
const CNY_LABELS = {
  yuan: "元",
  thousand_yuan: "千元",
  ten_thousand_yuan: "万元",
  hundred_million_yuan: "亿元",
} as const;
function echartColor(color: ChartColorSpec) {
  if (color.mode === "solid") return color.hex;
  const directions = {
    horizontal: [0, 0, 1, 0],
    vertical: [0, 0, 0, 1],
    diagonal_down: [0, 0, 1, 1],
    diagonal_up: [0, 1, 1, 0],
  } as const;
  const [x, y, x2, y2] = directions[color.direction];
  return {
    type: "linear",
    x,
    y,
    x2,
    y2,
    colorStops: [
      { offset: 0, color: color.start_hex },
      { offset: 1, color: color.end_hex },
    ],
    global: false,
  };
}

function chartStyle(color: ChartColorSpec) {
  return { color: echartColor(color), opacity: color.opacity / 100 };
}

function chartCoordinateNumber(value: unknown) {
  const parts = decimalParts(value);
  if (!parts) return undefined;
  const number = Number(decimalText(parts));
  return Number.isFinite(number) ? number : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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
    new Set(value.series_fields).size === value.series_fields.length &&
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
    rowCount: value.rowCount ?? value.row_count,
  };
  return isQueryShape(normalized) ? normalized : null;
}

function safeLabel(value: unknown) {
  if (typeof value === "string" && value.length > 0) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return String(value);
  return "（空值）";
}

function displayName(view: ChartViewConfiguration, field: string) {
  return view.field_labels[field]?.trim() || field;
}

function safeAnnotationLabel(label: string) {
  return (
    label
      .replace(/[\u0000-\u001f\u007f]/gu, " ")
      .replace(/\s+/gu, " ")
      .trim()
      .slice(0, 80) || "目标值"
  );
}

export function formatChartValue(value: unknown, format: ChartValueFormat): string {
  let shift = 0;
  if (format.mode === "unit_scale") {
    shift = CNY_UNIT_POWERS[format.source_unit] - CNY_UNIT_POWERS[format.display_unit];
  } else if (format.mode === "percent" && format.encoding === "ratio_0_1") {
    shift = 2;
  }
  const text = formatDecimal(value, shift, format.decimal_places);
  if (text === "—") return text;
  if (format.mode === "percent") return `${text}%`;
  if (format.mode === "suffix") return `${text}${format.suffix}`;
  if (format.mode === "unit_scale") return `${text} ${CNY_LABELS[format.display_unit]}`;
  return text;
}

function unitSignature(format: ChartValueFormat) {
  if (format.mode === "unit_scale") {
    return `${format.unit_family}:${format.source_unit}->${format.display_unit}`;
  }
  if (format.mode === "suffix") return `suffix:${format.suffix.trim()}`;
  if (format.mode === "percent") return `percent:${format.encoding}`;
  return "raw";
}

function sharedAxisFormat(view: ChartViewConfiguration, metrics: string[]): ChartValueFormat {
  const formats = metrics.map((field) => view.format_by_field[field]);
  const signatures = new Set(formats.map(unitSignature));
  return signatures.size <= 1
    ? { ...formats[0], decimal_places: "auto" }
    : { mode: "raw", decimal_places: "auto" };
}

export function getChartUnitWarning(view: ChartViewConfiguration) {
  const visibleMetrics = view.metric_fields.filter(
    (field) => !view.hidden_metric_fields.includes(field),
  );
  const axisMetrics = {
    left: visibleMetrics.filter((field) => view.y_axis_by_metric?.[field] !== "right"),
    right: visibleMetrics.filter((field) => view.y_axis_by_metric?.[field] === "right"),
  };
  const mixedAxisUnits = Object.values(axisMetrics).some((fields) => {
    const units = new Set(fields.map((field) => unitSignature(view.format_by_field[field])));
    return units.size > 1;
  });
  if (mixedAxisUnits) return "同一 Y 轴上的指标单位不同，数值不宜直接比较。";
  if (axisMetrics.right.length > 0) return "图表使用左右独立 Y 轴刻度，请按各轴刻度读取。";
  const units = new Set(visibleMetrics.map((field) => unitSignature(view.format_by_field[field])));
  return units.size > 1 ? "指标单位不同，数值不宜直接比较。" : "";
}

function compareNullable(left: unknown, right: unknown, temporal: boolean) {
  const empty = (value: unknown) => value === null || value === undefined || value === "";
  if (empty(left) !== empty(right)) return empty(left) ? 1 : -1;
  if (empty(left)) return 0;
  if (temporal) {
    const leftTime = Date.parse(String(left));
    const leftTimeOnly = timeOfDay(String(left));
    const rightTime = Date.parse(String(right));
    const rightTimeOnly = timeOfDay(String(right));
    const leftValue = Number.isFinite(leftTimeOnly) ? leftTimeOnly : leftTime;
    const rightValue = Number.isFinite(rightTimeOnly) ? rightTimeOnly : rightTime;
    const leftValid = Number.isFinite(leftValue);
    const rightValid = Number.isFinite(rightValue);
    if (leftValid !== rightValid) return leftValid ? -1 : 1;
    if (!leftValid) return 0;
    return leftValue - rightValue;
  }
  if (typeof left === "number" && typeof right === "number") return left - right;
  if (typeof left === "boolean" && typeof right === "boolean") return Number(left) - Number(right);
  const leftText = safeLabel(left);
  const rightText = safeLabel(right);
  return leftText < rightText ? -1 : leftText > rightText ? 1 : 0;
}

function sortRows(query: QueryShape, view: ChartViewConfiguration): ChartDisplayRow[] {
  const rows = query.rows.slice(0, 1000).map((row, sourceRowIndex) => ({ row, sourceRowIndex }));
  if (view.sort.mode === "original") return rows;
  const direction = view.sort.direction === "asc" ? 1 : -1;
  const dimensionSort = view.sort.mode === "dimension";
  const dimensionType = dimensionSort
    ? query.columnTypes[query.columns.indexOf(view.dimension_field)].toLowerCase()
    : "";
  const temporal = dimensionSort && /^(date|time|timestamp)/.test(dimensionType);
  const sortField = view.sort.mode === "dimension" ? view.dimension_field : view.sort.field;
  return rows.sort((left, right) => {
    const a = left.row[sortField];
    const b = right.row[sortField];
    let result: number;
    if (view.sort.mode === "metric") {
      const aValid = compareDecimalValues(a, a) !== undefined;
      const bValid = compareDecimalValues(b, b) !== undefined;
      if (aValid !== bValid) return aValid ? -1 : 1;
      result = aValid && bValid ? (compareDecimalValues(a, b) ?? 0) : 0;
    } else {
      result = compareNullable(a, b, temporal);
      if (
        (a === null || a === undefined || a === "") !== (b === null || b === undefined || b === "")
      ) {
        return result;
      }
    }
    return result * direction || left.sourceRowIndex - right.sourceRowIndex;
  });
}

function projectRows(query: QueryShape, view: ChartViewConfiguration): ChartDisplayRow[] {
  const sortedRows = sortRows(query, view);
  return view.current_result_top_n
    ? sortedRows.slice(0, view.current_result_top_n.count)
    : sortedRows;
}

function heatmapAggregateValue(
  values: unknown[],
  aggregation: NonNullable<ChartViewConfiguration["heatmap_fields"]>["aggregation"],
) {
  const validValues = values.filter((value) => decimalParts(value) !== undefined);
  if (!validValues.length) return null;
  if (aggregation === "none") return validValues[0];
  if (aggregation === "sum") {
    const total = sumDecimalValues(validValues);
    return total ? decimalText(total) : null;
  }
  if (aggregation === "avg") return averageDecimalValues(validValues) ?? null;
  return validValues.reduce((best, value) => {
    const comparison = compareDecimalValues(value, best) ?? 0;
    return aggregation === "min" ? (comparison < 0 ? value : best) : comparison > 0 ? value : best;
  });
}

function aggregateHeatmapRows(
  rows: ChartDisplayRow[],
  fields: NonNullable<ChartViewConfiguration["heatmap_fields"]>,
): ChartDisplayRow[] {
  const buckets = new Map<
    string,
    { first: ChartDisplayRow; sourceRowIndexes: number[]; values: unknown[] }
  >();
  for (const entry of rows) {
    const xKey = chartHeatmapAxisKey(entry.row[fields.x_field]);
    const yKey = chartHeatmapAxisKey(entry.row[fields.y_field]);
    if (xKey === undefined || yKey === undefined) continue;
    const key = JSON.stringify([xKey, yKey]);
    const bucket = buckets.get(key);
    if (bucket) {
      bucket.sourceRowIndexes.push(...(entry.sourceRowIndexes ?? [entry.sourceRowIndex]));
      bucket.values.push(entry.row[fields.value_field]);
    } else {
      buckets.set(key, {
        first: entry,
        sourceRowIndexes: [...(entry.sourceRowIndexes ?? [entry.sourceRowIndex])],
        values: [entry.row[fields.value_field]],
      });
    }
  }
  return [...buckets.values()].map(({ first, sourceRowIndexes, values }) => ({
    row: {
      ...first.row,
      [fields.value_field]: heatmapAggregateValue(values, fields.aggregation),
    },
    sourceRowIndex: sourceRowIndexes[0],
    sourceRowIndexes,
  }));
}

export function deriveChartRows(queryArtifact: unknown, viewInput: unknown): ChartDisplayRow[] {
  const query = normalizeQuery(queryArtifact);
  if (!query) return [];
  const validation = validateChartView(viewInput, query);
  if (!validation.view) return [];
  const rows = projectRows(query, validation.view);
  return validation.view.chart_type === "heatmap" && validation.view.heatmap_fields
    ? aggregateHeatmapRows(rows, validation.view.heatmap_fields)
    : rows;
}

function timeOfDay(value: string) {
  const match = /^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$/.exec(value);
  if (!match) return Number.NaN;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  const second = Number(match[3] ?? "0");
  if (hour > 23 || minute > 59 || second > 59) return Number.NaN;
  return ((hour * 60 + minute) * 60 + second) * 1000 + Number(`0.${match[4] ?? "0"}`) * 1000;
}

function escapeTooltipText(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function tooltipHtml(
  rows: ChartDisplayRow[],
  view: ChartViewConfiguration,
  metrics: string[],
  params: unknown,
) {
  const first = Array.isArray(params) ? params[0] : params;
  if (!isRecord(first) || typeof first.dataIndex !== "number") return "";
  const row = rows[first.dataIndex]?.row;
  if (!row) return "";
  const dimension = `<div>${escapeTooltipText(displayName(view, view.dimension_field))}: ${escapeTooltipText(safeLabel(row[view.dimension_field]))}</div>`;
  const values = metrics
    .map((field) => {
      const text = formatChartValue(row[field], view.format_by_field[field]);
      return `<div>${escapeTooltipText(displayName(view, field))}: ${escapeTooltipText(text)}</div>`;
    })
    .join("");
  return `${dimension}${values}`;
}

export function isChartQueryTruncated(query: SuccessfulQueryArtifact) {
  return getChartCompletenessState(query) === "possibly_incomplete";
}

export type ChartCompletenessState = "possibly_incomplete" | "not_marked_truncated" | "unknown";

export function getChartCompletenessState(queryArtifact: unknown): ChartCompletenessState {
  if (!isRecord(queryArtifact) || !Array.isArray(queryArtifact.rows)) return "unknown";
  const rowCount = queryArtifact.rowCount ?? queryArtifact.row_count;
  if (
    queryArtifact.truncated === true ||
    queryArtifact.rows.length > 1000 ||
    (typeof rowCount === "number" &&
      Number.isFinite(rowCount) &&
      rowCount !== queryArtifact.rows.length)
  ) {
    return "possibly_incomplete";
  }
  if (
    queryArtifact.truncated === false &&
    typeof rowCount === "number" &&
    Number.isFinite(rowCount) &&
    rowCount === queryArtifact.rows.length
  ) {
    return "not_marked_truncated";
  }
  return "unknown";
}

export function buildEChartsOption(
  chartArtifact: unknown,
  queryArtifact: unknown,
  viewInput?: unknown,
  viewport: ChartViewport = { start: 0, end: 100 },
): Record<string, unknown> | null {
  if (!isChartArtifact(chartArtifact)) return null;
  const query = normalizeQuery(queryArtifact);
  if (!query || chartArtifact.source_result_id !== query.resultId) return null;
  if (
    chartArtifact.title !== `${chartArtifact.series_fields.join(", ")} by ${chartArtifact.x_field}`
  )
    return null;
  const recommended = createRecommendedChartView(chartArtifact, query);
  if (!recommended) return null;
  const validation = validateChartView(viewInput ?? recommended, query);
  const view = validation.view;
  if (!view) return null;

  const projectedRows = projectRows(query, view);
  const rows =
    view.chart_type === "heatmap" && view.heatmap_fields
      ? aggregateHeatmapRows(projectedRows, view.heatmap_fields)
      : projectedRows;
  const labels = rows.map(({ row }) => safeLabel(row[view.dimension_field]));
  const categoryPositions = rows.map((_, index) => index);
  const visibleMetrics = view.metric_fields.filter(
    (field) => !view.hidden_metric_fields.includes(field),
  );
  const axisMetrics = {
    left: visibleMetrics.filter((field) => view.y_axis_by_metric?.[field] !== "right"),
    right: visibleMetrics.filter((field) => view.y_axis_by_metric?.[field] === "right"),
  };
  const hasRightYAxis = axisMetrics.right.length > 0;
  const axisNameOptions = (
    side: "left" | "right",
  ): {
    name?: string;
    nameLocation?: "middle";
    nameGap?: number;
    nameMoveOverlap?: boolean;
    nameTextStyle?: { fontSize: number };
  } => {
    const config = view.y_axis_names?.[side];
    const customName = config?.text?.trim();
    const visible = config?.visible ?? (hasRightYAxis || Boolean(customName));
    const generatedName = axisMetrics[side].map((field) => displayName(view, field)).join(" / ");
    const name =
      customName || (generatedName.length > 36 ? `${generatedName.slice(0, 33)}…` : generatedName);
    if (!visible || !name) return {};
    return {
      name,
      nameLocation: "middle" as const,
      nameGap: 42,
      nameMoveOverlap: true,
      nameTextStyle: { fontSize: 10 },
    };
  };
  const leftAxisNameOptions = axisNameOptions("left");
  const rawAxisFormat = sharedAxisFormat(view, visibleMetrics);
  const axisFormat =
    view.chart_type === "bar" && view.bar_stack_mode === "percent"
      ? { mode: "percent" as const, encoding: "percent_0_100" as const, decimal_places: 1 as const }
      : rawAxisFormat;
  const paletteId = (view.color_palette_id ?? "system_default") as ChartPaletteId;
  const paletteColors = CHART_COLOR_PALETTES[paletteId] ?? CHART_COLOR_PALETTES.classic;

  if (view.chart_type === "scatter" && view.scatter_fields) {
    const fields = view.scatter_fields;
    const visualValues = fields.visual_field
      ? rows
          .map(({ row }) => chartCoordinateNumber(row[fields.visual_field!]))
          .filter((value): value is number => value !== undefined)
      : [];
    const visualMin = visualValues.length ? Math.min(...visualValues) : undefined;
    const visualMax = visualValues.length ? Math.max(...visualValues) : undefined;
    const visualRange =
      visualMin !== undefined && visualMax !== undefined && visualMin === visualMax
        ? [visualMin - 1, visualMax + 1]
        : [visualMin ?? 0, visualMax ?? 1];
    const visualMap =
      fields.visual_field && fields.visual_encoding
        ? {
            type: "continuous",
            dimension: 2,
            min: visualRange[0],
            max: visualRange[1],
            right: 4,
            top: 20,
            text: fields.visual_encoding === "size" ? ["较大", "较小"] : ["高", "低"],
            inRange:
              fields.visual_encoding === "size"
                ? { symbolSize: [6, 32] }
                : { color: [paletteColors[0], paletteColors.at(-1)] },
            calculable: false,
          }
        : undefined;
    const scatterData = rows.map(({ row }) => {
      const x = exactPlotNumber(row[fields.x_field]);
      const y = exactPlotNumber(row[fields.y_field]);
      const visual = fields.visual_field ? exactPlotNumber(row[fields.visual_field]) : undefined;
      return {
        value: [x ?? null, y ?? null, ...(fields.visual_field ? [visual ?? null] : [])],
      };
    });
    return {
      tooltip: {
        trigger: "item",
        formatter: (params: unknown) => {
          const first = Array.isArray(params) ? params[0] : params;
          if (!isRecord(first) || typeof first.dataIndex !== "number") return "";
          const row = rows[first.dataIndex]?.row;
          if (!row) return "";
          const lines = [
            fields.x_field,
            fields.y_field,
            ...(fields.visual_field ? [fields.visual_field] : []),
          ].map((field) => {
            const format = view.format_by_field[field] ?? {
              mode: "raw" as const,
              decimal_places: "auto" as const,
            };
            return `<div>${escapeTooltipText(displayName(view, field))}: ${escapeTooltipText(formatChartValue(row[field], format))}</div>`;
          });
          return lines.join("");
        },
      },
      legend: { show: view.show_legend, top: 0 },
      ...(visualMap ? { visualMap } : {}),
      grid: { left: 58, right: visualMap ? 76 : 22, top: 22, bottom: 44 },
      xAxis: {
        type: "value",
        name: displayName(view, fields.x_field),
        axisLabel: {
          formatter: (value: unknown) =>
            formatChartValue(
              value,
              view.format_by_field[fields.x_field] ?? { mode: "raw", decimal_places: "auto" },
            ),
        },
      },
      yAxis: {
        type: "value",
        name: displayName(view, fields.y_field),
        scale: true,
        axisLabel: {
          formatter: (value: unknown) =>
            formatChartValue(value, view.format_by_field[fields.y_field]),
        },
      },
      series: [
        {
          name: displayName(view, fields.y_field),
          type: "scatter",
          symbolSize: fields.visual_encoding === "size" ? 12 : 10,
          itemStyle: { color: paletteColors[0], opacity: 0.72 },
          label: {
            show: view.show_data_labels,
            position: "top",
            formatter: (params: unknown) => {
              if (!isRecord(params) || typeof params.dataIndex !== "number") return "";
              const row = rows[params.dataIndex]?.row;
              if (!row) return "";
              const xFormat = view.format_by_field[fields.x_field] ?? {
                mode: "raw",
                decimal_places: "auto",
              };
              return `(${formatChartValue(row[fields.x_field], xFormat)}, ${formatChartValue(row[fields.y_field], view.format_by_field[fields.y_field])})`;
            },
          },
          data: scatterData,
        },
      ],
    };
  }

  if (view.chart_type === "heatmap" && view.heatmap_fields) {
    const fields = view.heatmap_fields;
    const xKeys: string[] = [];
    const yKeys: string[] = [];
    const xLabels: string[] = [];
    const yLabels: string[] = [];
    const xValues: unknown[] = [];
    const yValues: unknown[] = [];
    const xIndex = new Map<string, number>();
    const yIndex = new Map<string, number>();
    for (const { row } of rows) {
      const xKey = chartHeatmapAxisKey(row[fields.x_field]);
      const yKey = chartHeatmapAxisKey(row[fields.y_field]);
      if (xKey !== undefined && !xIndex.has(xKey)) {
        xIndex.set(xKey, xKeys.length);
        xKeys.push(xKey);
        xLabels.push(safeLabel(row[fields.x_field]));
        xValues.push(row[fields.x_field]);
      }
      if (yKey !== undefined && !yIndex.has(yKey)) {
        yIndex.set(yKey, yKeys.length);
        yKeys.push(yKey);
        yLabels.push(safeLabel(row[fields.y_field]));
        yValues.push(row[fields.y_field]);
      }
    }
    const disambiguateAxisLabels = (axisLabels: string[], values: unknown[]) => {
      const keyCounts = new Map<string, number>();
      for (const label of axisLabels) keyCounts.set(label, (keyCounts.get(label) ?? 0) + 1);
      return axisLabels.map((label, index) => {
        if ((keyCounts.get(label) ?? 0) < 2) return label;
        const value = values[index];
        const kind =
          value === null || value === undefined
            ? "空值"
            : typeof value === "number"
              ? "数值"
              : typeof value === "boolean"
                ? "布尔值"
                : "文本";
        return `${label} · ${kind}`;
      });
    };
    const displayXLabels = disambiguateAxisLabels(xLabels, xValues);
    const displayYLabels = disambiguateAxisLabels(yLabels, yValues);
    const numericValues = rows
      .map(({ row }) => chartCoordinateNumber(row[fields.value_field]))
      .filter((value): value is number => value !== undefined);
    if (!numericValues.length) return null;
    const valueMin = Math.min(...numericValues);
    const valueMax = Math.max(...numericValues);
    const visualRange = valueMin === valueMax ? [valueMin - 1, valueMax + 1] : [valueMin, valueMax];
    const aggregateLabels = {
      none: "单行值",
      sum: "求和",
      avg: "平均值",
      min: "最小值",
      max: "最大值",
    } as const;
    return {
      tooltip: {
        trigger: "item",
        formatter: (params: unknown) => {
          const first = Array.isArray(params) ? params[0] : params;
          if (!isRecord(first) || typeof first.dataIndex !== "number") return "";
          const entry = rows[first.dataIndex];
          if (!entry) return "";
          const value = formatChartValue(
            entry.row[fields.value_field],
            view.format_by_field[fields.value_field],
          );
          return [
            `<div>${escapeTooltipText(displayName(view, fields.x_field))}: ${escapeTooltipText(safeLabel(entry.row[fields.x_field]))}</div>`,
            `<div>${escapeTooltipText(displayName(view, fields.y_field))}: ${escapeTooltipText(safeLabel(entry.row[fields.y_field]))}</div>`,
            `<div>${escapeTooltipText(displayName(view, fields.value_field))}（${aggregateLabels[fields.aggregation]}）: ${escapeTooltipText(value)}</div>`,
            entry.sourceRowIndexes && entry.sourceRowIndexes.length > 1
              ? `<div>合并 ${entry.sourceRowIndexes.length} 行已返回结果</div>`
              : "",
          ].join("");
        },
      },
      legend: { show: view.show_legend, top: 0 },
      visualMap: {
        type: "continuous",
        dimension: 2,
        min: visualRange[0],
        max: visualRange[1],
        right: 4,
        top: 20,
        text: ["高", "低"],
        inRange: { color: [paletteColors[0], paletteColors.at(-1)] },
        calculable: false,
      },
      grid: { left: 54, right: 76, top: 22, bottom: 50 },
      xAxis: {
        type: "category",
        name: displayName(view, fields.x_field),
        data: xKeys.map((_, index) => index),
        axisLabel: {
          hideOverlap: true,
          formatter: (_value: unknown, index: number) => displayXLabels[index] ?? "",
        },
      },
      yAxis: {
        type: "category",
        name: displayName(view, fields.y_field),
        data: yKeys.map((_, index) => index),
        axisLabel: {
          hideOverlap: true,
          formatter: (_value: unknown, index: number) => displayYLabels[index] ?? "",
        },
      },
      series: [
        {
          name: displayName(view, fields.value_field),
          type: "heatmap",
          data: rows.map(({ row }) => [
            xIndex.get(chartHeatmapAxisKey(row[fields.x_field]) ?? "") ?? -1,
            yIndex.get(chartHeatmapAxisKey(row[fields.y_field]) ?? "") ?? -1,
            exactPlotNumber(row[fields.value_field]) ?? null,
          ]),
          label: {
            show: view.show_data_labels,
            formatter: (params: unknown) => {
              if (!isRecord(params) || typeof params.dataIndex !== "number") return "";
              const row = rows[params.dataIndex]?.row;
              return row
                ? formatChartValue(
                    row[fields.value_field],
                    view.format_by_field[fields.value_field],
                  )
                : "";
            },
          },
          emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(15, 23, 42, 0.35)" } },
        },
      ],
    };
  }

  const annotations = view.annotations ?? { reference_lines: [], reference_areas: [] };
  const horizontalBar = view.chart_type === "bar" && view.bar_orientation === "horizontal";
  const dimensionAxis = horizontalBar ? "yAxis" : "xAxis";
  const valueAxis = horizontalBar ? "xAxis" : "yAxis";
  const dimensionAxisIndex = horizontalBar ? "yAxisIndex" : "xAxisIndex";
  const metricColor = (field: string) => {
    const customColor = view.color_by_metric?.[field];
    if (customColor) {
      return customColor.mode === "solid" ? customColor.hex : customColor.start_hex;
    }
    const index = visibleMetrics.indexOf(field);
    return paletteColors[index % paletteColors.length];
  };
  const targetAnnotations = annotations.reference_lines.filter(
    (annotation) => annotation.kind === "value" && visibleMetrics.includes(annotation.metric_field),
  );
  const targetSummaryTexts = targetAnnotations.map((annotation) => {
    const label = safeAnnotationLabel(annotation.label);
    const metric = safeAnnotationLabel(displayName(view, annotation.metric_field)).slice(0, 40);
    const value = formatChartValue(annotation.value, view.format_by_field[annotation.metric_field]);
    return `${label} · ${metric} ${value}`;
  });
  const targetSummaryGraphics = targetAnnotations.map((annotation, index) => ({
    type: "text",
    left: 12,
    top: 4 + index * 16,
    silent: true,
    style: {
      text: targetSummaryTexts[index],
      fill: metricColor(annotation.metric_field),
      fontSize: 11,
      fontWeight: 500,
    },
  }));
  const needsStatistics = annotations.reference_lines.some(
    (annotation) => annotation.kind !== "value",
  );
  const statisticsByScope: Record<
    ChartStatisticsScope,
    ReturnType<typeof calculateChartStatistics>
  > = {
    returned_rows: needsStatistics
      ? calculateChartStatistics(query, view, "returned_rows", viewport, rows)
      : [],
    viewport: needsStatistics
      ? calculateChartStatistics(query, view, "viewport", viewport, rows)
      : [],
  };
  const statisticFor = (field: string, scope: ChartStatisticsScope) =>
    statisticsByScope[scope].find((statistic) => statistic.metric_field === field);
  const sourcePositionByIndex = new Map(
    rows.map(({ sourceRowIndex }, index) => [sourceRowIndex, index]),
  );
  const viewportRows = getChartViewportRows(rows, viewport);
  const createMarkLine = (field: string) => {
    const tooltipDetails: string[] = [];
    const data = annotations.reference_lines
      .filter((annotation) => annotation.metric_field === field && annotation.kind !== "peak")
      .flatMap((annotation) => {
        const statistic = statisticFor(field, annotation.scope);
        const value = annotation.kind === "value" ? annotation.value : statistic?.mean;
        const plottedValue = chartCoordinateNumber(value);
        if (value === undefined || plottedValue === undefined) return [];
        const formatted = formatChartValue(value, view.format_by_field[field]);
        const label = safeAnnotationLabel(annotation.label);
        tooltipDetails.push(
          `<div>${escapeTooltipText(label)}</div><div>${escapeTooltipText(displayName(view, field))}：${escapeTooltipText(formatted)}</div>`,
        );
        return [
          {
            name: label,
            [valueAxis]: plottedValue,
            lineStyle: {
              color: annotation.kind === "value" ? "#d97706" : "#0891b2",
              type: annotation.kind === "value" ? "dashed" : "dotted",
              width: 1.5,
            },
            label: {
              show: annotation.kind !== "value",
              position: "insideEndTop",
              formatter: () => `${label} · ${displayName(view, field)} ${formatted}`,
            },
          },
        ];
      });
    return data.length
      ? {
          silent: false,
          symbol: "none",
          tooltip: {
            trigger: "item",
            formatter: (params: unknown) => {
              const first = Array.isArray(params) ? params[0] : params;
              if (!isRecord(first) || typeof first.dataIndex !== "number") return "";
              return tooltipDetails[first.dataIndex] ?? "";
            },
          },
          data,
        }
      : undefined;
  };
  const createMarkPoint = (field: string) => {
    const data = annotations.reference_lines
      .filter((annotation) => annotation.metric_field === field && annotation.kind === "peak")
      .flatMap((annotation) => {
        const statistic = statisticFor(field, annotation.scope);
        if (statistic?.peak === undefined) return [];
        const candidateRows =
          annotation.scope === "returned_rows"
            ? query.rows.map((row, sourceRowIndex) => ({ row, sourceRowIndex }))
            : viewportRows;
        const plottedValue = chartCoordinateNumber(statistic.peak);
        if (plottedValue === undefined) return [];
        const formatted = formatChartValue(statistic.peak, view.format_by_field[field]);
        const points = candidateRows.flatMap(({ row, sourceRowIndex }) => {
          const displayIndex = sourcePositionByIndex.get(sourceRowIndex);
          if (
            displayIndex === undefined ||
            exactPlotNumber(row[field]) === undefined ||
            compareDecimalValues(row[field], statistic.peak) !== 0
          ) {
            return [];
          }
          return [
            {
              name: annotation.label,
              coord: horizontalBar ? [plottedValue, displayIndex] : [displayIndex, plottedValue],
              value: plottedValue,
              label: {
                show: true,
                position: "top",
                formatter: () => `${annotation.label} · ${displayName(view, field)} ${formatted}`,
              },
            },
          ];
        });
        return points.map((point, index) => ({
          ...point,
          label: { ...point.label, show: index === 0 },
        }));
      });
    return data.length
      ? { symbol: "pin", symbolSize: 24, itemStyle: { color: "#dc2626" }, data }
      : undefined;
  };
  const markAreaData =
    view.chart_type === "pie"
      ? []
      : annotations.reference_areas.flatMap((area) => {
          const positions = area.source_row_indices
            .map((sourceRowIndex) => sourcePositionByIndex.get(sourceRowIndex))
            .filter((position): position is number => position !== undefined)
            .sort((left, right) => left - right);
          if (!positions.length) return [];
          const ranges: Array<[number, number]> = [];
          let start = positions[0];
          let end = positions[0];
          for (const position of positions.slice(1)) {
            if (position <= end + 1) {
              end = position;
            } else {
              ranges.push([start, end]);
              start = position;
              end = position;
            }
          }
          ranges.push([start, end]);
          return ranges.map(([first, last]) => [
            { [dimensionAxis]: first, name: area.label },
            { [dimensionAxis]: last },
          ]);
        });
  const series = visibleMetrics.map((field, index) => {
    const metricYAxisSide = view.y_axis_by_metric?.[field] ?? "left";
    const customColor = view.color_by_metric?.[field];
    const paletteColor =
      paletteId === "system_default" ? undefined : paletteColors[index % paletteColors.length];
    const style = customColor
      ? chartStyle(customColor)
      : paletteColor
        ? { color: paletteColor, opacity: 1 }
        : undefined;
    const markLine = view.chart_type === "pie" ? undefined : createMarkLine(field);
    const markPoint = view.chart_type === "pie" ? undefined : createMarkPoint(field);
    const percentStack = view.chart_type === "bar" && view.bar_stack_mode === "percent";
    return {
      name: displayName(view, field),
      type: new Set<string>(["line", "area", "step_line"]).has(view.chart_type) ? "line" : "bar",
      ...(!horizontalBar && hasRightYAxis
        ? { yAxisIndex: metricYAxisSide === "right" ? 1 : 0 }
        : {}),
      data: rows.map(({ row }) => {
        if (percentStack) {
          const total = sumDecimalValues(visibleMetrics.map((metric) => row[metric]));
          const share = total ? exactPercentShare(row[field], total) : undefined;
          const plotted = share === undefined ? undefined : Number(share);
          return plotted !== undefined && Number.isFinite(plotted) ? plotted : null;
        }
        return exactPlotNumber(row[field]) ?? null;
      }),
      ...(style
        ? {
            itemStyle: style,
            ...(new Set<string>(["line", "area", "step_line"]).has(view.chart_type)
              ? { lineStyle: style }
              : {}),
          }
        : {}),
      label: {
        show: view.show_data_labels,
        position:
          view.chart_type === "bar" && view.bar_orientation === "horizontal" ? "right" : "top",
        formatter: (params: unknown) => {
          const value = isRecord(params) ? params.value : params;
          const sourceValue =
            isRecord(params) && typeof params.dataIndex === "number"
              ? rows[params.dataIndex]?.row[field]
              : value;
          if (percentStack && isRecord(params) && typeof params.dataIndex === "number") {
            const row = rows[params.dataIndex]?.row;
            const total = row
              ? sumDecimalValues(visibleMetrics.map((metric) => row[metric]))
              : undefined;
            const share = total ? exactPercentShare(row?.[field], total) : undefined;
            return share === undefined ? "" : `${share}%`;
          }
          return formatChartValue(sourceValue, view.format_by_field[field]);
        },
      },
      ...(new Set<string>(["line", "area", "step_line"]).has(view.chart_type)
        ? {
            showSymbol: rows.length <= 60,
            connectNulls: false,
            ...(view.chart_type === "area" ? { areaStyle: { opacity: 0.2 } } : {}),
            ...(view.chart_type === "step_line" ? { step: view.step_position ?? "end" } : {}),
          }
        : {}),
      ...(view.chart_type === "bar" && view.bar_stack_mode !== "grouped"
        ? { stack: "chart-stack" }
        : {}),
      ...(markLine ? { markLine } : {}),
      ...(markPoint ? { markPoint } : {}),
      ...(markAreaData.length > 0 && index === 0
        ? {
            markArea: {
              silent: true,
              itemStyle: {
                color: "rgba(59, 130, 246, 0.12)",
                borderColor: "rgba(59, 130, 246, 0.45)",
              },
              label: { show: true, color: "#1d4ed8", fontSize: 10 },
              data: markAreaData,
            },
          }
        : {}),
    };
  });
  const tooltip = {
    trigger: view.chart_type === "pie" || view.chart_type === "scatter" ? "item" : "axis",
    formatter: (params: unknown) => {
      if (view.chart_type !== "bar" || view.bar_stack_mode !== "percent") {
        return tooltipHtml(rows, view, visibleMetrics, params);
      }
      const entries = Array.isArray(params) ? params : [params];
      const first = entries[0];
      if (!isRecord(first) || typeof first.dataIndex !== "number") return "";
      const row = rows[first.dataIndex]?.row;
      if (!row) return "";
      const total = sumDecimalValues(visibleMetrics.map((field) => row[field]));
      if (!total) return "";
      const dimensions = `<div>${escapeTooltipText(displayName(view, view.dimension_field))}: ${escapeTooltipText(safeLabel(row[view.dimension_field]))}</div>`;
      const values = visibleMetrics.map((field) => {
        const raw = formatChartValue(row[field], view.format_by_field[field]);
        const share = exactPercentShare(row[field], total);
        return `<div>${escapeTooltipText(displayName(view, field))}: ${escapeTooltipText(raw)}（${escapeTooltipText(share)}%）</div>`;
      });
      const denominator = formatChartValue(decimalText(total), rawAxisFormat);
      return [
        dimensions,
        ...values,
        `<div>行内分母（当前可见指标之和）: ${escapeTooltipText(denominator)}</div>`,
      ].join("");
    },
    ...(view.chart_type !== "pie"
      ? {
          axisPointer: {
            type: new Set<string>(["line", "area", "step_line"]).has(view.chart_type)
              ? "cross"
              : "shadow",
            axis: view.chart_type === "bar" && view.bar_orientation === "horizontal" ? "y" : "x",
            snap: true,
            lineStyle: { color: "#64748b", width: 1, type: "dashed" },
            shadowStyle: { color: "rgba(100, 116, 139, 0.14)" },
            crossStyle: { color: "#64748b", width: 1, type: "dashed" },
            label: { show: true },
          },
        }
      : {}),
  };

  if (view.chart_type === "pie") {
    const field = visibleMetrics[0];
    const total = sumDecimalValues(rows.map(({ row }) => row[field]));
    if (!total || total.coefficient <= BigInt("0")) return null;
    const piePaletteColors =
      paletteId === "system_default" ? CHART_COLOR_PALETTES.classic : paletteColors;
    const defaultColorByKey = new Map<string, string>();
    query.rows.slice(0, 1000).forEach(({ [view.dimension_field]: value }) => {
      const key = chartCategoryKey(value);
      if (key && !defaultColorByKey.has(key)) {
        defaultColorByKey.set(
          key,
          piePaletteColors[defaultColorByKey.size % piePaletteColors.length],
        );
      }
    });
    const data = rows.map(({ row }) => {
      const categoryKey = chartCategoryKey(row[view.dimension_field]);
      const colorSpec = categoryKey
        ? view.pie_category_colors?.by_category_key[categoryKey]
        : undefined;
      const defaultColor = categoryKey ? defaultColorByKey.get(categoryKey) : undefined;
      const color = colorSpec
        ? chartStyle(colorSpec)
        : defaultColor
          ? { color: defaultColor, opacity: 1 }
          : undefined;
      return {
        name: safeLabel(row[view.dimension_field]),
        value: exactPlotNumber(row[field]) ?? null,
        ...(color ? { itemStyle: color } : {}),
      };
    });
    return {
      tooltip: {
        ...tooltip,
        formatter: (params: unknown) => {
          const first = Array.isArray(params) ? params[0] : params;
          if (!isRecord(first) || typeof first.dataIndex !== "number") return "";
          const row = rows[first.dataIndex]?.row;
          if (!row) return "";
          const slice = exactPercentShare(row[field], total);
          return `${tooltipHtml(rows, view, [field], first)}<div>分类占比: ${slice}%</div>`;
        },
      },
      legend: { show: view.show_legend, type: "scroll", bottom: 0 },
      series: [
        {
          name: displayName(view, field),
          type: "pie",
          radius: ["0%", "68%"],
          center: ["50%", "49%"],
          label: {
            show: view.show_data_labels,
            formatter: (params: unknown) => {
              if (!isRecord(params)) return "";
              const name = typeof params.name === "string" ? params.name : "";
              const sourceValue =
                typeof params.dataIndex === "number"
                  ? rows[params.dataIndex]?.row[field]
                  : params.value;
              return `${name}: ${formatChartValue(sourceValue, view.format_by_field[field])}`;
            },
          },
          data,
        },
      ],
    };
  }

  const commonAxisLabel = {
    hideOverlap: true,
    formatter: (_value: unknown, index: number) => labels[index] ?? "",
  };
  const axis = horizontalBar
    ? {
        xAxis: {
          type: "value",
          scale: false,
          ...(view.chart_type === "bar" && view.bar_stack_mode === "percent"
            ? { min: 0, max: 100 }
            : {}),
          axisLabel: {
            formatter: (value: unknown) => formatChartValue(value, axisFormat),
          },
        },
        yAxis: {
          type: "category",
          data: categoryPositions,
          inverse: true,
          axisLabel: commonAxisLabel,
        },
      }
    : {
        xAxis: {
          type: "category",
          data: categoryPositions,
          axisLabel: commonAxisLabel,
        },
        yAxis: hasRightYAxis
          ? (["left", "right"] as const).map((side) => {
              const metrics = axisMetrics[side];
              return {
                type: "value",
                position: side,
                scale: new Set<string>(["line", "area", "step_line"]).has(view.chart_type),
                ...(view.chart_type === "bar" && view.bar_stack_mode === "percent"
                  ? { min: 0, max: 100 }
                  : {}),
                ...axisNameOptions(side),
                axisLabel: {
                  formatter: (value: unknown) =>
                    formatChartValue(value, sharedAxisFormat(view, metrics)),
                },
              };
            })
          : {
              type: "value",
              scale: new Set<string>(["line", "area", "step_line"]).has(view.chart_type),
              ...(view.chart_type === "bar" && view.bar_stack_mode === "percent"
                ? { min: 0, max: 100 }
                : {}),
              ...leftAxisNameOptions,
              axisLabel: {
                formatter: (value: unknown) => formatChartValue(value, axisFormat),
              },
            },
      };
  const zoomAxis = horizontalBar ? "yAxisIndex" : "xAxisIndex";
  const zoomAxisIndices = 0;
  const hasSlider = labels.length > 30;
  const verticalSliderBottom = view.show_legend ? 44 : 16;
  const dataZoom = [
    {
      type: "inside",
      [zoomAxis]: zoomAxisIndices,
      start: Math.max(0, Math.min(100, viewport.start)),
      end: Math.max(0, Math.min(100, viewport.end)),
      filterMode: "none",
      zoomOnMouseWheel: true,
      moveOnMouseMove: true,
      moveOnMouseWheel: false,
    },
    ...(hasSlider
      ? [
          {
            type: "slider",
            [zoomAxis]: zoomAxisIndices,
            start: Math.max(0, Math.min(100, viewport.start)),
            end: Math.max(0, Math.min(100, viewport.end)),
            filterMode: "none",
            showDetail: false,
            ...(horizontalBar
              ? { orient: "vertical", right: 6, top: 28, bottom: 28, width: 12 }
              : { left: 42, right: 16, bottom: verticalSliderBottom, height: 14 }),
          },
        ]
      : []),
  ];
  const gridBottom =
    !horizontalBar && hasSlider ? (view.show_legend ? 76 : 54) : view.show_legend ? 50 : 28;
  const targetSummaryDescription = targetSummaryTexts.length
    ? `目标值：${targetSummaryTexts.join("；")}`
    : "";
  const ariaDescription = [
    isChartQueryTruncated(query) ? "图表仅展示部分查询结果" : "",
    targetSummaryDescription,
  ]
    .filter(Boolean)
    .join("。 ");
  return {
    tooltip,
    legend: {
      show: view.show_legend,
      type: "scroll",
      bottom: 0,
      ...(view.chart_type === "bar" && view.bar_stack_mode === "percent"
        ? { selectedMode: false }
        : {}),
    },
    toolbox: { show: false },
    grid: {
      left: hasRightYAxis || leftAxisNameOptions.name ? 64 : 12,
      right: hasRightYAxis ? 64 : horizontalBar && hasSlider ? 48 : 16,
      top: 28 + targetSummaryTexts.length * 16,
      bottom: gridBottom,
      containLabel: true,
    },
    ...axis,
    dataZoom,
    ...(targetSummaryGraphics.length ? { graphic: targetSummaryGraphics } : {}),
    brush: {
      toolbox: ["lineX", "lineY"],
      brushLink: "all",
      brushMode: "single",
      [dimensionAxisIndex]: zoomAxisIndices,
      throttleType: "debounce",
      throttleDelay: 100,
    },
    series,
    ...(ariaDescription ? { aria: { enabled: true, description: ariaDescription } } : {}),
  };
}
