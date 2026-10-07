import {
  averageDecimalValues,
  compareDecimalValues,
  decimalParts,
  decimalText,
} from "./chart-decimal.ts";
import type { ChartDisplayRow } from "./chart-output.ts";
import type { ChartViewConfiguration, SuccessfulQueryArtifact } from "./chat-output.ts";

export type ChartStatisticsScope = "returned_rows" | "viewport";
export type ChartViewport = { start: number; end: number };
export type ChartMetricStatistics = {
  metric_field: string;
  mean?: string;
  peak?: string;
  validCount: number;
  scope: ChartStatisticsScope;
};

export function getChartViewportRows(rows: readonly ChartDisplayRow[], viewport: ChartViewport) {
  if (!rows.length) return rows;
  const start = Math.max(0, Math.min(100, viewport.start));
  const end = Math.max(0, Math.min(100, viewport.end));
  if (!Number.isFinite(start) || !Number.isFinite(end) || start > end) return [];
  const lastIndex = rows.length - 1;
  const first = Math.floor((start / 100) * lastIndex);
  const last = Math.ceil((end / 100) * lastIndex);
  return rows.slice(first, last + 1);
}

export function calculateChartStatistics(
  query: SuccessfulQueryArtifact,
  view: ChartViewConfiguration,
  scope: ChartStatisticsScope,
  viewport: ChartViewport = { start: 0, end: 100 },
  projectedRows: readonly ChartDisplayRow[] = [],
): ChartMetricStatistics[] {
  const rows =
    scope === "returned_rows"
      ? query.rows.map((row, sourceRowIndex) => ({ row, sourceRowIndex }))
      : getChartViewportRows(projectedRows, viewport);
  const visibleMetrics = view.metric_fields.filter(
    (field) => !view.hidden_metric_fields.includes(field),
  );
  return visibleMetrics.map((metricField) => {
    const values = rows
      .map(({ row }) => row[metricField])
      .filter((value) => decimalParts(value) !== undefined);
    const mean = averageDecimalValues(values);
    let peak: string | undefined;
    for (const value of values) {
      const parts = decimalParts(value);
      if (!parts) continue;
      const canonicalValue = decimalText(parts);
      if (peak === undefined || (compareDecimalValues(canonicalValue, peak) ?? -1) > 0) {
        peak = canonicalValue;
      }
    }
    return {
      metric_field: metricField,
      ...(mean !== undefined ? { mean } : {}),
      ...(peak !== undefined ? { peak } : {}),
      validCount: values.length,
      scope,
    };
  });
}
