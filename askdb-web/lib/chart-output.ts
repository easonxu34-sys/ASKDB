import {
  createRecommendedChartView,
  validateChartView,
  type ChartValueFormat,
  type ChartViewConfiguration,
  type EChartsChartArtifact,
  type SuccessfulQueryArtifact,
} from "./chat-output.ts";
import {
  compareDecimalValues,
  exactPercentShare,
  exactPlotNumber,
  formatDecimal,
  sumDecimalValues,
} from "./chart-decimal.ts";

type QueryShape = SuccessfulQueryArtifact & {
  resultId: string;
  columnTypes: string[];
};

type IndexedRow = { row: Record<string, unknown>; index: number };

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

function sortRows(query: QueryShape, view: ChartViewConfiguration): IndexedRow[] {
  const rows = query.rows.slice(0, 1000).map((row, index) => ({ row, index }));
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
    return result * direction || left.index - right.index;
  });
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
  rows: IndexedRow[],
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

function isTruncated(query: QueryShape) {
  return (
    query.truncated === true ||
    query.rows.length > 1000 ||
    (typeof query.rowCount === "number" && query.rowCount > query.rows.length)
  );
}

export function buildEChartsOption(
  chartArtifact: unknown,
  queryArtifact: unknown,
  viewInput?: unknown,
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

  const rows = sortRows(query, view);
  const labels = rows.map(({ row }) => safeLabel(row[view.dimension_field]));
  const categoryPositions = rows.map((_, index) => index);
  const visibleMetrics = view.metric_fields.filter(
    (field) => !view.hidden_metric_fields.includes(field),
  );
  const axisFormat = sharedAxisFormat(view, visibleMetrics);
  const series = visibleMetrics.map((field) => ({
    name: displayName(view, field),
    type: view.chart_type === "line" ? "line" : "bar",
    data: rows.map(({ row }) => {
      return exactPlotNumber(row[field]) ?? null;
    }),
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
        return formatChartValue(sourceValue, view.format_by_field[field]);
      },
    },
    ...(view.chart_type === "line" ? { showSymbol: rows.length <= 60, connectNulls: false } : {}),
  }));
  const tooltip = {
    trigger: view.chart_type === "pie" ? "item" : "axis",
    formatter: (params: unknown) => tooltipHtml(rows, view, visibleMetrics, params),
  };

  if (view.chart_type === "pie") {
    const field = visibleMetrics[0];
    const total = sumDecimalValues(rows.map(({ row }) => row[field]));
    if (!total || total.coefficient <= BigInt("0")) return null;
    const data = rows.map(({ row }) => ({
      name: safeLabel(row[view.dimension_field]),
      value: exactPlotNumber(row[field]) ?? null,
    }));
    return {
      title: { text: view.title, left: "center", textStyle: { fontSize: 14, fontWeight: 500 } },
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
          center: ["50%", "54%"],
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

  const dimensionName = displayName(view, view.dimension_field);
  const metricName = visibleMetrics.map((field) => displayName(view, field)).join(" / ");
  const commonAxisLabel = {
    hideOverlap: true,
    formatter: (_value: unknown, index: number) => labels[index] ?? "",
  };
  const axis =
    view.chart_type === "bar" && view.bar_orientation === "horizontal"
      ? {
          xAxis: {
            type: "value",
            name: metricName,
            scale: true,
            axisLabel: {
              formatter: (value: unknown) => formatChartValue(value, axisFormat),
            },
          },
          yAxis: {
            type: "category",
            name: dimensionName,
            data: categoryPositions,
            inverse: true,
            axisLabel: commonAxisLabel,
          },
        }
      : {
          xAxis: {
            type: "category",
            name: dimensionName,
            data: categoryPositions,
            axisLabel: commonAxisLabel,
          },
          yAxis: {
            type: "value",
            name: metricName,
            scale: true,
            axisLabel: {
              formatter: (value: unknown) => formatChartValue(value, axisFormat),
            },
          },
        };
  return {
    title: { text: view.title, left: "left", textStyle: { fontSize: 14, fontWeight: 500 } },
    tooltip,
    legend: { show: view.show_legend, type: "scroll", bottom: 0 },
    grid: {
      left: 12,
      right: 16,
      top: 42,
      bottom: view.show_legend ? 50 : 28,
      containLabel: true,
    },
    ...axis,
    series,
    ...(isTruncated(query)
      ? { aria: { enabled: true, description: "图表仅展示部分查询结果" } }
      : {}),
  };
}
