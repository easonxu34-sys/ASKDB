"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { ECharts, EChartsOption } from "echarts";
import {
  chartIndexesForCoordinateRanges,
  normalizeChartDataIndexes,
} from "@/lib/chart-interactions";
import type { ChartDisplayRow } from "@/lib/chart-output";

export type ChartCanvasHandle = {
  resetZoom(): boolean;
  downloadImage(filename: string): boolean;
  setBrushMode(enabled: boolean, axis: "x" | "y"): boolean;
  clearBrushSelection(): boolean;
};

type ChartCanvasProps = {
  option: Record<string, unknown> | null;
  ariaLabel: string;
  className: string;
  sourceKey: string;
  sourceRows: readonly ChartDisplayRow[];
  interactive?: boolean;
  onPointClick?: (sourceRowIndices: number[], seriesIndex: number) => void;
  onBrushSelection?: (sourceRowIndices: number[]) => void;
  onDataZoom?: (window: { start: number; end: number }) => void;
};

function safeFilename(filename: string) {
  const normalized = filename.replace(/[^A-Za-z0-9._-]+/gu, "-").replace(/^-+|-+$/gu, "");
  return normalized.slice(0, 100) || "chart.png";
}

function readDataIndexes(event: unknown): unknown[] {
  if (typeof event !== "object" || event === null) return [];
  const payload = event as { batch?: unknown };
  const batch = Array.isArray(payload.batch) ? payload.batch : [payload];
  const dataIndexes: unknown[] = [];
  for (const item of batch) {
    if (typeof item !== "object" || item === null) continue;
    const selected = (item as { selected?: unknown }).selected;
    if (!Array.isArray(selected)) continue;
    for (const series of selected) {
      if (typeof series !== "object" || series === null) continue;
      const indexes = (series as { dataIndex?: unknown }).dataIndex;
      if (Array.isArray(indexes)) dataIndexes.push(...indexes);
    }
  }
  return dataIndexes;
}

function readBrushIndexes(event: unknown, rowCount: number): unknown[] {
  const explicitIndexes = readDataIndexes(event);
  if (explicitIndexes.length) return explicitIndexes;
  if (typeof event !== "object" || event === null) return [];
  const payload = event as { batch?: unknown };
  const batch = Array.isArray(payload.batch) ? payload.batch : [payload];
  const ranges: Array<[number, number]> = [];
  for (const item of batch) {
    if (typeof item !== "object" || item === null) continue;
    const areas = (item as { areas?: unknown }).areas;
    if (!Array.isArray(areas)) continue;
    for (const area of areas) {
      if (typeof area !== "object" || area === null) continue;
      const brushArea = area as {
        brushType?: unknown;
        coordRange?: unknown;
        coordRanges?: unknown;
      };
      if (brushArea.brushType !== "lineX" && brushArea.brushType !== "lineY") continue;
      const coordinateRange = Array.isArray(brushArea.coordRange)
        ? brushArea.coordRange
        : Array.isArray(brushArea.coordRanges) && Array.isArray(brushArea.coordRanges[0])
          ? brushArea.coordRanges[0]
          : undefined;
      if (
        coordinateRange?.length === 2 &&
        typeof coordinateRange[0] === "number" &&
        typeof coordinateRange[1] === "number"
      ) {
        ranges.push([coordinateRange[0], coordinateRange[1]]);
      }
    }
  }
  return chartIndexesForCoordinateRanges(rowCount, ranges);
}

function readZoomStates(chart: ECharts) {
  const option = chart.getOption() as {
    dataZoom?: Array<{ start?: number; end?: number }>;
  };
  return (option.dataZoom ?? []).map(({ start, end }) => ({ start, end }));
}

const ChartCanvas = forwardRef<ChartCanvasHandle, ChartCanvasProps>(function ChartCanvas(
  {
    option,
    ariaLabel,
    className,
    sourceKey,
    sourceRows,
    interactive = false,
    onPointClick,
    onBrushSelection,
    onDataZoom,
  },
  forwardedRef,
) {
  const elementRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ECharts | null>(null);
  const optionRef = useRef(option);
  const rowsRef = useRef(sourceRows);
  const interactiveRef = useRef(interactive);
  const onPointClickRef = useRef(onPointClick);
  const onBrushSelectionRef = useRef(onBrushSelection);
  const onDataZoomRef = useRef(onDataZoom);
  const [renderFailed, setRenderFailed] = useState(false);
  optionRef.current = option;
  rowsRef.current = sourceRows;
  interactiveRef.current = interactive;
  onPointClickRef.current = onPointClick;
  onBrushSelectionRef.current = onBrushSelection;
  onDataZoomRef.current = onDataZoom;

  useImperativeHandle(
    forwardedRef,
    () => ({
      resetZoom() {
        const chart = chartRef.current;
        if (!chart) return false;
        chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
        return true;
      },
      downloadImage(filename: string) {
        const chart = chartRef.current;
        if (!chart) return false;
        try {
          const link = document.createElement("a");
          link.href = chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#fff" });
          link.download = safeFilename(filename);
          link.click();
          return true;
        } catch {
          return false;
        }
      },
      setBrushMode(enabled: boolean, axis: "x" | "y") {
        const chart = chartRef.current;
        if (!chart) return false;
        chart.dispatchAction({
          type: "takeGlobalCursor",
          key: "brush",
          brushOption: enabled
            ? { brushType: axis === "y" ? "lineY" : "lineX", brushMode: "single" }
            : { brushType: false },
        });
        if (!enabled) chart.dispatchAction({ type: "brush", areas: [] });
        return true;
      },
      clearBrushSelection() {
        const chart = chartRef.current;
        if (!chart) return false;
        chart.dispatchAction({ type: "brush", areas: [] });
        return true;
      },
    }),
    [],
  );

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;
    setRenderFailed(false);
    let disposed = false;
    let chart: ECharts | undefined;
    let observer: ResizeObserver | undefined;
    const resize = () => chart?.resize();
    const onClick = (event: unknown) => {
      if (!interactiveRef.current || typeof event !== "object" || event === null) return;
      const payload = event as { dataIndex?: unknown; seriesIndex?: unknown };
      if (!Number.isInteger(payload.seriesIndex)) return;
      const sourceRowIndexes = normalizeChartDataIndexes(rowsRef.current, [payload.dataIndex]);
      if (sourceRowIndexes.length) {
        onPointClickRef.current?.(sourceRowIndexes, payload.seriesIndex as number);
      }
    };
    const onBrush = (event: unknown) => {
      if (!interactiveRef.current) return;
      const sourceRowIndexes = normalizeChartDataIndexes(
        rowsRef.current,
        readBrushIndexes(event, rowsRef.current.length),
      );
      if (sourceRowIndexes.length) onBrushSelectionRef.current?.(sourceRowIndexes);
    };
    const onZoom = (event: unknown) => {
      if (!interactiveRef.current || typeof event !== "object" || event === null) return;
      const payload = event as { start?: unknown; end?: unknown; batch?: unknown };
      const firstBatch = Array.isArray(payload.batch) ? payload.batch[0] : undefined;
      const batch =
        typeof firstBatch === "object" && firstBatch !== null
          ? (firstBatch as { start?: unknown; end?: unknown })
          : undefined;
      const start = typeof payload.start === "number" ? payload.start : batch?.start;
      const end = typeof payload.end === "number" ? payload.end : batch?.end;
      if (typeof start === "number" && typeof end === "number") {
        onDataZoomRef.current?.({ start, end });
      }
    };

    void import("echarts")
      .then((echarts) => {
        if (disposed) return;
        try {
          chart = echarts.init(element, undefined, { renderer: "canvas" });
          chartRef.current = chart;
          if (optionRef.current) {
            chart.setOption(optionRef.current as EChartsOption, {
              notMerge: true,
              lazyUpdate: false,
            });
          }
          chart.on("click", onClick);
          chart.on("brushSelected", onBrush);
          chart.on("datazoom", onZoom);
          if (typeof ResizeObserver !== "undefined") {
            observer = new ResizeObserver(resize);
            observer.observe(element);
          } else {
            window.addEventListener("resize", resize);
          }
        } catch {
          chart?.dispose();
          chart = undefined;
          chartRef.current = null;
          if (!disposed) setRenderFailed(true);
        }
      })
      .catch(() => {
        if (!disposed) setRenderFailed(true);
      });

    return () => {
      disposed = true;
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      if (chart) {
        chart.off("click", onClick);
        chart.off("brushSelected", onBrush);
        chart.off("datazoom", onZoom);
        chart.dispose();
      }
      if (chartRef.current === chart) chartRef.current = null;
    };
  }, [sourceKey]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !option) return;
    try {
      setRenderFailed(false);
      const previousZoom = readZoomStates(chart);
      chart.setOption(option as EChartsOption, { notMerge: true, lazyUpdate: false });
      const nextZoom = readZoomStates(chart);
      previousZoom.forEach(({ start, end }, dataZoomIndex) => {
        if (
          dataZoomIndex < nextZoom.length &&
          typeof start === "number" &&
          typeof end === "number"
        ) {
          chart.dispatchAction({ type: "dataZoom", dataZoomIndex, start, end });
        }
      });
    } catch {
      setRenderFailed(true);
    }
  }, [option]);

  return (
    <div className={`${className} relative`}>
      <div
        ref={elementRef}
        className="h-full w-full"
        role="img"
        aria-label={ariaLabel}
        aria-hidden={renderFailed}
      />
      {renderFailed && (
        <div
          className="absolute inset-0 flex items-center rounded-lg border border-border/70 bg-background px-3 py-2 text-xs text-muted-foreground"
          role="status"
        >
          图表渲染失败，查询结果表格仍可查看。
        </div>
      )}
    </div>
  );
});

export default ChartCanvas;
