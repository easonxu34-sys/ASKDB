"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsChartArtifact, SuccessfulQueryArtifact } from "@/lib/chat-output";
import { buildEChartsOption } from "@/lib/chart-output";

type ChartResultProps = {
  artifact: EChartsChartArtifact;
  queryArtifact: SuccessfulQueryArtifact;
};

export function ChartResult({ artifact, queryArtifact }: ChartResultProps) {
  const elementRef = useRef<HTMLDivElement>(null);
  const [renderFailed, setRenderFailed] = useState(false);
  const option = useMemo(
    () => buildEChartsOption(artifact, queryArtifact),
    [artifact, queryArtifact],
  );
  const isEmpty = queryArtifact.rows.length === 0;

  useEffect(() => {
    const element = elementRef.current;
    if (!element || !option || isEmpty) return;
    let disposed = false;
    let chart: import("echarts").ECharts | undefined;
    let observer: ResizeObserver | undefined;
    const resize = () => chart?.resize();

    void import("echarts")
      .then((echarts) => {
        if (disposed) return;
        try {
          chart = echarts.init(element, undefined, { renderer: "canvas" });
          chart.setOption(option as import("echarts").EChartsOption, {
            notMerge: true,
            lazyUpdate: false,
          });
          if (typeof ResizeObserver !== "undefined") {
            observer = new ResizeObserver(resize);
            observer.observe(element);
          } else {
            window.addEventListener("resize", resize);
          }
        } catch {
          chart?.dispose();
          chart = undefined;
          setRenderFailed(true);
        }
      })
      .catch(() => setRenderFailed(true));

    return () => {
      disposed = true;
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      chart?.dispose();
    };
  }, [isEmpty, option]);

  if (!option || renderFailed) {
    return (
      <div className="my-3 rounded-lg border border-border/70 px-3 py-2 text-xs text-muted-foreground">
        图表暂不可用，查询结果表格仍可查看。
      </div>
    );
  }
  if (isEmpty) {
    return (
      <div className="my-3 rounded-lg border border-border/70 px-3 py-2 text-xs text-muted-foreground">
        查询结果为空，暂无可展示图表。
      </div>
    );
  }

  return (
    <section
      className="my-3 min-w-0 overflow-hidden rounded-xl border border-border/70 bg-background px-3 py-3 shadow-sm"
      aria-label={artifact.title}
    >
      <div className="mb-1 flex items-center justify-between gap-3">
        <h3 className="truncate text-sm font-medium">{artifact.title}</h3>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          {artifact.chart_type === "line"
            ? "折线图"
            : artifact.chart_type === "bar"
              ? "柱状图"
              : "饼图"}
        </span>
      </div>
      <div
        ref={elementRef}
        className="h-[280px] w-full min-w-0"
        role="img"
        aria-label={`${artifact.title}，${artifact.chart_type}图`}
      />
    </section>
  );
}
