"use client";

import { forwardRef, useEffect, useMemo, useRef, useState } from "react";
import { Maximize2Icon, Minimize2Icon, PencilLineIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  chartViewsEqual,
  getArrowFieldKind,
  getChartFieldCandidates,
  type ChartType,
  type ChartViewConfiguration,
  type ChartViewValidation,
  type EChartsChartArtifact,
  type SuccessfulQueryArtifact,
  validateChartView,
} from "@/lib/chat-output";
import { deriveSourceTurnKey } from "@/lib/agent-chat-adapter";
import { saveThreadChartViewOverride } from "@/lib/local-thread-adapter";
import { buildEChartsOption, getChartUnitWarning } from "@/lib/chart-output";

type ChartResultProps = {
  artifact: EChartsChartArtifact;
  queryArtifact: SuccessfulQueryArtifact;
  recommendedView: ChartViewConfiguration;
  view: ChartViewConfiguration;
  hasOverride: boolean;
  persistenceAvailable?: boolean;
  overrideNotice?: string;
  userId: string;
  threadId?: string;
  sourceMessageId?: string;
};

type ChartViewDraft = Omit<ChartViewConfiguration, "format_by_field"> & {
  format_by_field: Record<string, unknown>;
};

const textControlClass =
  "h-9 w-full rounded-md border border-border bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50";
const fieldLabelClass = "mb-1.5 block text-xs font-medium text-foreground";
const unitLabels = {
  yuan: "元",
  thousand_yuan: "千元",
  ten_thousand_yuan: "万元",
  hundred_million_yuan: "亿元",
} as const;
const cnyUnits = Object.keys(unitLabels) as Array<keyof typeof unitLabels>;

const EChartCanvas = forwardRef<
  HTMLDivElement,
  {
    option: Record<string, unknown> | null;
    ariaLabel: string;
    className: string;
  }
>(function EChartCanvas({ option, ariaLabel, className }, forwardedRef) {
  const localRef = useRef<HTMLDivElement>(null);
  const [renderFailed, setRenderFailed] = useState(false);

  useEffect(() => {
    const element = localRef.current;
    setRenderFailed(false);
    if (!element || !option) return;
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
  }, [option]);

  if (renderFailed) {
    return (
      <div
        className="flex items-center rounded-lg border border-border/70 px-3 py-2 text-xs text-muted-foreground"
        role="status"
      >
        图表渲染失败，查询结果表格仍可查看。
      </div>
    );
  }
  return (
    <div
      ref={(node) => {
        localRef.current = node;
        if (typeof forwardedRef === "function") forwardedRef(node);
        else if (forwardedRef) forwardedRef.current = node;
      }}
      className={className}
      role="img"
      aria-label={ariaLabel}
    />
  );
});

function RecordFieldError({ id, error }: { id: string; error?: string }) {
  if (!error) return null;
  return (
    <p id={id} role="alert" className="mt-1 text-xs leading-5 text-destructive">
      {error}
    </p>
  );
}

function getChartTypeLabel(type: ChartType) {
  return type === "line" ? "折线图" : type === "bar" ? "柱状图" : "饼图";
}

function getFormatMode(format: unknown) {
  if (typeof format !== "object" || format === null) return "raw";
  const mode = (format as { mode?: unknown }).mode;
  return mode === "suffix" || mode === "percent" || mode === "unit_scale" ? mode : "raw";
}

function getDecimalPlaces(format: unknown): "auto" | number {
  if (typeof format !== "object" || format === null) return "auto";
  const value = (format as { decimal_places?: unknown }).decimal_places;
  return value === "auto" || (typeof value === "number" && Number.isInteger(value))
    ? value
    : "auto";
}

export function ChartResult({
  artifact,
  queryArtifact,
  recommendedView,
  view,
  hasOverride,
  persistenceAvailable = true,
  overrideNotice,
  userId,
  threadId,
  sourceMessageId,
}: ChartResultProps) {
  const cardRef = useRef<HTMLElement>(null);
  const [savedView, setSavedView] = useState(view);
  const [savedHasOverride, setSavedHasOverride] = useState(hasOverride);
  const [draft, setDraft] = useState<ChartViewDraft>(view as ChartViewDraft);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [persisting, setPersisting] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [displayNotice, setDisplayNotice] = useState(overrideNotice ?? "");
  const [fullScreen, setFullScreen] = useState(false);
  const [fullScreenError, setFullScreenError] = useState("");
  const isEmpty = queryArtifact.rows.length === 0;
  const canEdit = Boolean(persistenceAvailable && userId && threadId && sourceMessageId);
  const fieldCandidates = useMemo(() => getChartFieldCandidates(queryArtifact), [queryArtifact]);
  const temporalDimension =
    getArrowFieldKind(
      queryArtifact.columnTypes?.[queryArtifact.columns.indexOf(draft.dimension_field)] ?? "",
    ) === "temporal";
  const validation: ChartViewValidation = useMemo(
    () => validateChartView(draft, queryArtifact),
    [draft, queryArtifact],
  );
  const savedOption = useMemo(
    () => buildEChartsOption(artifact, queryArtifact, savedView),
    [artifact, queryArtifact, savedView],
  );
  const previewOption = useMemo(
    () => buildEChartsOption(artifact, queryArtifact, draft),
    [artifact, queryArtifact, draft],
  );
  const unitWarning = getChartUnitWarning(savedView);
  const pieValidation = validateChartView(
    {
      ...draft,
      chart_type: "pie",
      ...(draft.sort.mode === "metric" && draft.metric_fields.length > 1
        ? { sort: { mode: "original" as const } }
        : {}),
    },
    queryArtifact,
  );
  const pieReason =
    pieValidation.errors.chart_type ??
    pieValidation.errors.metric_fields ??
    pieValidation.errors.dimension_field ??
    "";
  const selectedMetrics = draft.metric_fields;
  const visibleMetrics = selectedMetrics.filter(
    (field) => !draft.hidden_metric_fields.includes(field),
  );
  const activeFields = [...new Set([draft.dimension_field, ...selectedMetrics].filter(Boolean))];

  useEffect(() => {
    const onFullScreenChange = () => setFullScreen(document.fullscreenElement === cardRef.current);
    document.addEventListener("fullscreenchange", onFullScreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullScreenChange);
  }, []);

  const propViewIdentity = `${artifact.source_result_id}:${JSON.stringify(view)}:${hasOverride}:${overrideNotice ?? ""}`;
  const lastPropViewIdentity = useRef(propViewIdentity);
  useEffect(() => {
    if (lastPropViewIdentity.current === propViewIdentity) return;
    lastPropViewIdentity.current = propViewIdentity;
    setSavedView(view);
    setSavedHasOverride(hasOverride);
    setDraft(view as ChartViewDraft);
    setDisplayNotice(overrideNotice ?? "");
  }, [propViewIdentity, view, hasOverride]);

  function beginEditing() {
    setDraft(savedView as ChartViewDraft);
    setSaveError("");
    setDialogOpen(true);
  }

  function closeEditor(nextOpen: boolean) {
    setDialogOpen(nextOpen);
    if (!nextOpen) {
      setDraft(savedView as ChartViewDraft);
      setSaveError("");
    }
  }

  function updateDraft(update: (current: ChartViewDraft) => ChartViewDraft) {
    setDraft((current) => update(current));
    setSaveError("");
  }

  function updateFormat(field: string, format: unknown) {
    updateDraft((current) => ({
      ...current,
      format_by_field: { ...current.format_by_field, [field]: format },
    }));
  }

  function selectChartType(nextType: ChartType) {
    updateDraft((current) => ({
      ...current,
      chart_type: nextType,
      ...(nextType === "bar" ? { bar_orientation: current.bar_orientation ?? "vertical" } : {}),
      ...(nextType === "pie" ? { show_data_labels: true } : {}),
      ...(nextType === "line" && current.sort.mode === "metric"
        ? {
            sort: temporalDimension
              ? {
                  mode: "dimension" as const,
                  field: current.dimension_field,
                  direction: "asc" as const,
                }
              : { mode: "original" as const },
          }
        : {}),
      ...(nextType === "pie" && current.sort.mode === "dimension"
        ? { sort: { mode: "original" as const } }
        : {}),
    }));
  }

  function selectDimension(field: string) {
    updateDraft((current) => ({
      ...current,
      dimension_field: field,
      ...(current.sort.mode === "dimension" ? { sort: { ...current.sort, field } } : {}),
    }));
  }

  function selectMetric(field: string, selected: boolean) {
    updateDraft((current) => {
      if (selected) {
        if (current.metric_fields.includes(field) || current.metric_fields.length >= 4)
          return current;
        return {
          ...current,
          metric_fields: [...current.metric_fields, field],
          format_by_field: {
            ...current.format_by_field,
            [field]: current.format_by_field[field] ?? { mode: "raw", decimal_places: "auto" },
          },
        };
      }
      const metrics = current.metric_fields.filter((item) => item !== field);
      return {
        ...current,
        metric_fields: metrics,
        hidden_metric_fields: current.hidden_metric_fields.filter((item) => item !== field),
        ...(current.sort.mode === "metric" && current.sort.field === field
          ? { sort: { mode: "original" as const } }
          : {}),
      };
    });
  }

  async function persistView(nextView: ChartViewConfiguration) {
    if (!threadId || !sourceMessageId) {
      setSaveError("无法关联当前会话轮次，配置未保存。");
      return false;
    }
    setPersisting(true);
    setSaveError("");
    try {
      const { sourceTurnKey } = await deriveSourceTurnKey(threadId, sourceMessageId);
      const saved = saveThreadChartViewOverride(
        userId,
        threadId,
        sourceTurnKey,
        artifact.source_result_id,
        nextView,
      );
      if (!saved) {
        setSaveError("保存失败，已保留之前保存的图表配置。请检查浏览器本地存储后重试。");
        return false;
      }
      setSavedView(nextView);
      const differs = !chartViewsEqual(nextView, recommendedView);
      setSavedHasOverride(differs);
      setDisplayNotice("");
      setDraft(nextView as ChartViewDraft);
      return true;
    } catch {
      setSaveError("保存失败，已保留之前保存的图表配置。");
      return false;
    } finally {
      setPersisting(false);
    }
  }

  async function applyDraft() {
    const result = validateChartView(draft, queryArtifact);
    if (!result.view) {
      setSaveError("");
      return;
    }
    if (await persistView(result.view)) setDialogOpen(false);
  }

  async function restoreRecommendation() {
    if (await persistView(recommendedView)) setDialogOpen(false);
  }

  async function toggleFullScreen() {
    setFullScreenError("");
    try {
      if (document.fullscreenElement === cardRef.current) await document.exitFullscreen();
      else await cardRef.current?.requestFullscreen();
    } catch {
      setFullScreenError("无法进入全屏显示。");
    }
  }

  const displayedTypeLabel = getChartTypeLabel(savedView.chart_type);
  const applyDisabled = persisting || !validation.view;

  if (isEmpty) {
    return (
      <div className="my-3 rounded-lg border border-border/70 px-3 py-2 text-xs text-muted-foreground">
        查询结果为空，暂无可展示图表。
      </div>
    );
  }

  return (
    <>
      <section
        ref={cardRef}
        className={`my-3 min-w-0 overflow-hidden rounded-xl border border-border/70 bg-background px-3 py-3 shadow-sm ${
          fullScreen ? "m-0 h-dvh w-screen overflow-y-auto rounded-none border-0 p-6" : ""
        }`}
        aria-label={savedView.title}
      >
        <div className="mb-1 flex items-center justify-between gap-3">
          <h3 className="min-w-0 truncate text-sm font-medium">{savedView.title}</h3>
          <div className="flex shrink-0 items-center gap-1">
            {canEdit && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label="编辑图表"
                onClick={beginEditing}
              >
                <PencilLineIcon aria-hidden="true" />
                <span>编辑图表</span>
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={fullScreen ? "退出全屏" : "全屏查看图表"}
              aria-pressed={fullScreen}
              onClick={() => void toggleFullScreen()}
            >
              {fullScreen ? (
                <Minimize2Icon aria-hidden="true" />
              ) : (
                <Maximize2Icon aria-hidden="true" />
              )}
            </Button>
          </div>
        </div>
        <div className="mb-2 text-[11px] text-muted-foreground">{displayedTypeLabel}</div>
        <EChartCanvas
          option={savedOption}
          ariaLabel={`${savedView.title}，${displayedTypeLabel}`}
          className={`w-full min-w-0 ${fullScreen ? "h-[calc(100dvh-8rem)]" : "h-[280px]"}`}
        />
        {queryArtifact.truncated ||
        queryArtifact.rows.length > 1000 ||
        (queryArtifact.rowCount !== undefined &&
          queryArtifact.rowCount > queryArtifact.rows.length) ? (
          <p className="mt-2 text-[11px] text-muted-foreground">图表仅展示已返回的部分查询结果。</p>
        ) : null}
        {unitWarning && (
          <p role="status" className="mt-2 text-[11px] text-amber-700">
            {unitWarning}
          </p>
        )}
        {displayNotice && (
          <p role="status" className="mt-2 text-[11px] text-muted-foreground">
            {displayNotice}
          </p>
        )}
        {!persistenceAvailable && (
          <p role="status" className="mt-2 text-[11px] text-amber-700">
            本轮查询结果未完整保存在浏览器中，图表设置无法保存。
          </p>
        )}
        {fullScreenError && (
          <p role="alert" className="mt-2 text-xs text-destructive">
            {fullScreenError}
          </p>
        )}
      </section>

      {canEdit && (
        <Dialog open={dialogOpen} onOpenChange={closeEditor}>
          <DialogContent
            showCloseButton={false}
            className="flex max-h-[92dvh] max-w-[calc(100%-1rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-6xl"
          >
            <DialogHeader className="shrink-0 border-b border-border/70 px-5 py-4 pr-14">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <DialogTitle>编辑图表</DialogTitle>
                  <DialogDescription className="mt-1">
                    设置只作用于当前图表；查询表格、原始值和查询结果顺序保持不变。
                  </DialogDescription>
                </div>
                <DialogClose
                  render={
                    <Button type="button" variant="ghost" size="icon-sm" aria-label="关闭编辑器" />
                  }
                >
                  <span aria-hidden="true">×</span>
                </DialogClose>
              </div>
            </DialogHeader>

            <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[minmax(19rem,0.9fr)_minmax(0,1.1fr)] lg:overflow-hidden">
              <div className="min-h-0 space-y-5 overflow-y-auto px-5 py-5 lg:border-r lg:border-border/70">
                <fieldset>
                  <legend className={fieldLabelClass}>图表类型</legend>
                  <div className="grid grid-cols-3 gap-2">
                    {(["line", "bar", "pie"] as ChartType[]).map((type) => {
                      const disabled = type === "pie" && Boolean(pieReason);
                      return (
                        <label
                          key={type}
                          className={`flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-md border px-2 text-xs ${
                            draft.chart_type === type
                              ? "border-primary bg-primary/5"
                              : "border-border"
                          } ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
                        >
                          <input
                            type="radio"
                            name={`chart-type-${artifact.source_result_id}`}
                            value={type}
                            checked={draft.chart_type === type}
                            disabled={disabled}
                            onChange={() => selectChartType(type)}
                            aria-label={getChartTypeLabel(type)}
                            aria-describedby={
                              type === "pie" && pieReason ? "chart-pie-reason" : undefined
                            }
                          />
                          {getChartTypeLabel(type)}
                        </label>
                      );
                    })}
                  </div>
                  {pieReason && (
                    <p
                      id="chart-pie-reason"
                      className="mt-1 text-xs leading-5 text-muted-foreground"
                    >
                      饼图暂不可选：{pieReason}
                    </p>
                  )}
                  <RecordFieldError id="chart-type-error" error={validation.errors.chart_type} />
                </fieldset>

                <section aria-labelledby="chart-fields-title">
                  <h4 id="chart-fields-title" className={fieldLabelClass}>
                    字段
                  </h4>
                  <label className="mb-3 block">
                    <span className={fieldLabelClass}>维度</span>
                    <select
                      className={textControlClass}
                      value={draft.dimension_field}
                      aria-label="图表维度"
                      aria-invalid={Boolean(validation.errors.dimension_field)}
                      aria-describedby={
                        validation.errors.dimension_field ? "chart-dimension-error" : undefined
                      }
                      onChange={(event) => selectDimension(event.currentTarget.value)}
                    >
                      {fieldCandidates
                        .filter(({ kind }) => kind === "categorical" || kind === "temporal")
                        .map(({ name, kind }) => (
                          <option key={name} value={name}>
                            {name}
                            {kind === "temporal" ? " · 日期/时间" : " · 分类"}
                          </option>
                        ))}
                    </select>
                    <RecordFieldError
                      id="chart-dimension-error"
                      error={validation.errors.dimension_field}
                    />
                  </label>

                  <fieldset>
                    <legend className={fieldLabelClass}>指标（最多 4 个）</legend>
                    <div className="max-h-36 space-y-1 overflow-y-auto rounded-md border border-border/70 p-2">
                      {fieldCandidates
                        .filter(({ kind }) => kind === "numeric")
                        .map(({ name }) => {
                          const checked = selectedMetrics.includes(name);
                          return (
                            <label key={name} className="flex min-h-8 items-center gap-2 text-xs">
                              <input
                                type="checkbox"
                                checked={checked}
                                disabled={!checked && selectedMetrics.length >= 4}
                                onChange={(event) =>
                                  selectMetric(name, event.currentTarget.checked)
                                }
                                aria-label={`选择指标 ${name}`}
                              />
                              <span>{name}</span>
                            </label>
                          );
                        })}
                      {fieldCandidates.every(({ kind }) => kind !== "numeric") && (
                        <p className="px-1 py-2 text-xs text-muted-foreground">
                          查询结果没有受支持的数值字段。
                        </p>
                      )}
                    </div>
                    <RecordFieldError
                      id="chart-metrics-error"
                      error={validation.errors.metric_fields}
                    />
                  </fieldset>

                  {selectedMetrics.length > 0 && (
                    <fieldset className="mt-3">
                      <legend className={fieldLabelClass}>系列显隐</legend>
                      <div className="flex flex-wrap gap-x-4 gap-y-2">
                        {selectedMetrics.map((field) => {
                          const visible = !draft.hidden_metric_fields.includes(field);
                          return (
                            <label key={field} className="inline-flex items-center gap-2 text-xs">
                              <input
                                type="checkbox"
                                checked={visible}
                                disabled={visible && visibleMetrics.length <= 1}
                                onChange={(event) => {
                                  const checked = event.currentTarget.checked;
                                  updateDraft((current) => ({
                                    ...current,
                                    hidden_metric_fields: checked
                                      ? current.hidden_metric_fields.filter(
                                          (item) => item !== field,
                                        )
                                      : [...current.hidden_metric_fields, field],
                                    ...(current.sort.mode === "metric" &&
                                    current.sort.field === field &&
                                    !checked
                                      ? { sort: { mode: "original" as const } }
                                      : {}),
                                  }));
                                }}
                                aria-label={`${visible ? "显示" : "隐藏"}指标 ${field}`}
                              />
                              显示 {field}
                            </label>
                          );
                        })}
                      </div>
                    </fieldset>
                  )}

                  {draft.chart_type === "bar" && (
                    <label className="mt-3 block">
                      <span className={fieldLabelClass}>柱状图方向</span>
                      <select
                        className={textControlClass}
                        value={draft.bar_orientation ?? "vertical"}
                        aria-label="柱状图方向"
                        onChange={(event) => {
                          const value = event.currentTarget.value as "vertical" | "horizontal";
                          updateDraft((current) => ({ ...current, bar_orientation: value }));
                        }}
                      >
                        <option value="vertical">纵向</option>
                        <option value="horizontal">横向</option>
                      </select>
                    </label>
                  )}
                </section>

                <section aria-labelledby="chart-sort-title">
                  <h4 id="chart-sort-title" className={fieldLabelClass}>
                    排序
                  </h4>
                  <div className="grid grid-cols-2 gap-2">
                    <label>
                      <span className="sr-only">排序方式</span>
                      <select
                        className={textControlClass}
                        aria-label="图表排序方式"
                        value={draft.sort.mode}
                        onChange={(event) => {
                          const mode = event.currentTarget.value;
                          updateDraft((current) => {
                            const nextSort =
                              mode === "dimension"
                                ? {
                                    mode: "dimension" as const,
                                    field: current.dimension_field,
                                    direction: "asc" as const,
                                  }
                                : mode === "metric"
                                  ? {
                                      mode: "metric" as const,
                                      field: visibleMetrics[0] ?? "",
                                      direction: "desc" as const,
                                    }
                                  : { mode: "original" as const };
                            return { ...current, sort: nextSort };
                          });
                        }}
                      >
                        <option value="original">查询原序</option>
                        <option value="dimension" disabled={draft.chart_type === "pie"}>
                          按维度
                        </option>
                        {draft.chart_type !== "line" && <option value="metric">按指标</option>}
                      </select>
                    </label>
                    {draft.sort.mode !== "original" && (
                      <label>
                        <span className="sr-only">排序方向</span>
                        <select
                          className={textControlClass}
                          aria-label="图表排序方向"
                          value={draft.sort.direction}
                          onChange={(event) => {
                            const direction = event.currentTarget.value as "asc" | "desc";
                            updateDraft((current) => ({
                              ...current,
                              sort: {
                                ...current.sort,
                                direction,
                              },
                            }));
                          }}
                        >
                          <option value="asc">升序</option>
                          <option value="desc">降序</option>
                        </select>
                      </label>
                    )}
                  </div>
                  {draft.sort.mode === "metric" && (
                    <label className="mt-2 block">
                      <span className={fieldLabelClass}>排序指标</span>
                      <select
                        className={textControlClass}
                        aria-label="排序指标"
                        value={draft.sort.field}
                        onChange={(event) => {
                          const field = event.currentTarget.value;
                          updateDraft((current) => ({
                            ...current,
                            sort: { ...current.sort, field },
                          }));
                        }}
                      >
                        {visibleMetrics.map((field) => (
                          <option key={field} value={field}>
                            {field}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <RecordFieldError id="chart-sort-error" error={validation.errors.sort} />
                  {temporalDimension && draft.chart_type === "line" && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      时间维度按时间值排序；折线图不按指标重排。
                    </p>
                  )}
                </section>

                <section aria-labelledby="chart-title-format-title">
                  <h4 id="chart-title-format-title" className={fieldLabelClass}>
                    标题与数值格式
                  </h4>
                  <label className="mb-3 block">
                    <span className={fieldLabelClass}>图表标题</span>
                    <input
                      className={textControlClass}
                      value={draft.title}
                      maxLength={120}
                      aria-label="图表标题"
                      aria-invalid={Boolean(validation.errors.title)}
                      aria-describedby={validation.errors.title ? "chart-title-error" : undefined}
                      onChange={(event) => {
                        const title = event.currentTarget.value;
                        updateDraft((current) => ({ ...current, title }));
                      }}
                    />
                    <RecordFieldError id="chart-title-error" error={validation.errors.title} />
                  </label>

                  <div className="space-y-4">
                    {selectedMetrics.map((field) => {
                      const rawFormat = draft.format_by_field[field];
                      const format =
                        typeof rawFormat === "object" && rawFormat !== null
                          ? (rawFormat as Record<string, unknown>)
                          : {};
                      const mode = getFormatMode(rawFormat);
                      const places = getDecimalPlaces(rawFormat);
                      const precision = places === "auto" ? "auto" : String(places);
                      const setMode = (nextMode: string) => {
                        const decimal_places = places;
                        if (nextMode === "raw")
                          updateFormat(field, { mode: "raw", decimal_places });
                        else if (nextMode === "suffix")
                          updateFormat(field, { mode: "suffix", suffix: "", decimal_places });
                        else if (nextMode === "percent")
                          updateFormat(field, {
                            mode: "percent",
                            encoding: "",
                            decimal_places,
                          });
                        else
                          updateFormat(field, {
                            mode: "unit_scale",
                            unit_family: "CNY",
                            source_unit: "",
                            display_unit: "",
                            decimal_places,
                          });
                      };
                      const formatError = validation.errors[`format_by_field.${field}`];
                      return (
                        <fieldset key={field} className="rounded-md border border-border/70 p-3">
                          <legend className="px-1 text-xs font-medium">{field}</legend>
                          <label className="mb-2 block">
                            <span className={fieldLabelClass}>显示格式</span>
                            <select
                              className={textControlClass}
                              value={mode}
                              aria-label={`${field} 显示格式`}
                              aria-invalid={Boolean(formatError)}
                              aria-describedby={
                                formatError ? `chart-format-${field}-error` : undefined
                              }
                              onChange={(event) => setMode(event.currentTarget.value)}
                            >
                              <option value="raw">原值（不换算）</option>
                              <option value="unit_scale">人民币单位换算</option>
                              <option value="suffix">只添加显示后缀</option>
                              <option value="percent">百分比（选择原值编码）</option>
                            </select>
                          </label>

                          {mode === "percent" && (
                            <label className="mb-2 block">
                              <span className={fieldLabelClass}>原值百分比编码</span>
                              <select
                                className={textControlClass}
                                value={typeof format.encoding === "string" ? format.encoding : ""}
                                aria-label={`${field} 百分比原值编码`}
                                onChange={(event) =>
                                  updateFormat(field, {
                                    mode: "percent",
                                    encoding: event.currentTarget.value,
                                    decimal_places: places,
                                  })
                                }
                              >
                                <option value="">请选择原值编码</option>
                                <option value="ratio_0_1">比率 0–1（0.12 显示为 12%）</option>
                                <option value="percent_0_100">百分数 0–100（12 显示为 12%）</option>
                              </select>
                            </label>
                          )}

                          {mode === "unit_scale" && (
                            <div className="grid grid-cols-2 gap-2">
                              {(["source_unit", "display_unit"] as const).map((unitKey) => (
                                <label key={unitKey}>
                                  <span className={fieldLabelClass}>
                                    {unitKey === "source_unit" ? "原值单位" : "显示单位"}
                                  </span>
                                  <select
                                    className={textControlClass}
                                    value={
                                      typeof format[unitKey] === "string"
                                        ? (format[unitKey] as string)
                                        : ""
                                    }
                                    aria-label={`${field} ${unitKey === "source_unit" ? "原值单位" : "显示单位"}`}
                                    onChange={(event) =>
                                      updateFormat(field, {
                                        ...format,
                                        unit_family: "CNY",
                                        [unitKey]: event.currentTarget.value,
                                        decimal_places: places,
                                      })
                                    }
                                  >
                                    <option value="">请选择单位</option>
                                    {cnyUnits.map((unit) => (
                                      <option key={unit} value={unit}>
                                        {unitLabels[unit]}
                                      </option>
                                    ))}
                                  </select>
                                </label>
                              ))}
                              <p className="col-span-2 text-[11px] text-muted-foreground">
                                仅按明确选择的人民币尺度换算，不做汇率转换。
                              </p>
                            </div>
                          )}

                          {mode === "suffix" && (
                            <label className="mb-2 block">
                              <span className={fieldLabelClass}>自定义后缀（只显示，不换算）</span>
                              <input
                                className={textControlClass}
                                value={typeof format.suffix === "string" ? format.suffix : ""}
                                maxLength={24}
                                aria-label={`${field} 自定义显示后缀`}
                                onChange={(event) =>
                                  updateFormat(field, {
                                    mode: "suffix",
                                    suffix: event.currentTarget.value,
                                    decimal_places: places,
                                  })
                                }
                              />
                            </label>
                          )}

                          <label className="mt-2 block max-w-40">
                            <span className={fieldLabelClass}>小数位</span>
                            <select
                              className={textControlClass}
                              value={precision}
                              aria-label={`${field} 小数位数`}
                              onChange={(event) => {
                                const nextPlaces =
                                  event.currentTarget.value === "auto"
                                    ? "auto"
                                    : Number(event.currentTarget.value);
                                updateFormat(field, { ...format, decimal_places: nextPlaces });
                              }}
                            >
                              <option value="auto">自动</option>
                              {[0, 1, 2, 3, 4, 5, 6].map((number) => (
                                <option key={number} value={number}>
                                  {number} 位
                                </option>
                              ))}
                            </select>
                          </label>
                          <RecordFieldError
                            id={`chart-format-${field}-error`}
                            error={formatError}
                          />
                        </fieldset>
                      );
                    })}
                  </div>
                  {validation.errors.format_by_field && (
                    <RecordFieldError
                      id="chart-format-error"
                      error={validation.errors.format_by_field}
                    />
                  )}
                </section>

                <section aria-labelledby="chart-labels-title">
                  <h4 id="chart-labels-title" className={fieldLabelClass}>
                    字段显示名
                  </h4>
                  <div className="space-y-2">
                    {activeFields.map((field) => (
                      <label key={field} className="block">
                        <span className={fieldLabelClass}>{field}</span>
                        <input
                          className={textControlClass}
                          value={draft.field_labels[field] ?? ""}
                          placeholder={field}
                          maxLength={80}
                          aria-label={`${field} 图表显示名`}
                          aria-invalid={Boolean(validation.errors[`field_labels.${field}`])}
                          aria-describedby={
                            validation.errors[`field_labels.${field}`]
                              ? `chart-label-${field}-error`
                              : undefined
                          }
                          onChange={(event) => {
                            const value = event.currentTarget.value;
                            updateDraft((current) => ({
                              ...current,
                              field_labels: {
                                ...current.field_labels,
                                [field]: value,
                              },
                            }));
                          }}
                        />
                        <RecordFieldError
                          id={`chart-label-${field}-error`}
                          error={validation.errors[`field_labels.${field}`]}
                        />
                      </label>
                    ))}
                  </div>
                </section>

                <section aria-labelledby="chart-display-title">
                  <h4 id="chart-display-title" className={fieldLabelClass}>
                    标签和图例
                  </h4>
                  <div className="space-y-2">
                    <label className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={draft.show_data_labels}
                        onChange={(event) => {
                          const checked = event.currentTarget.checked;
                          updateDraft((current) => ({ ...current, show_data_labels: checked }));
                        }}
                      />
                      显示数据标签
                    </label>
                    <label className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={draft.show_legend}
                        onChange={(event) => {
                          const checked = event.currentTarget.checked;
                          updateDraft((current) => ({ ...current, show_legend: checked }));
                        }}
                      />
                      显示图例
                    </label>
                  </div>
                  {unitWarning && <p className="mt-2 text-xs text-amber-700">{unitWarning}</p>}
                </section>
              </div>

              <section
                className="min-h-64 border-t border-border/70 bg-muted/20 px-5 py-5 lg:min-h-0 lg:overflow-y-auto lg:border-t-0"
                aria-label="图表草稿预览"
              >
                <div className="mb-3 flex items-center justify-between gap-3">
                  <h4 className="text-sm font-medium">即时预览</h4>
                  <span className="text-xs text-muted-foreground">
                    {getChartTypeLabel(draft.chart_type)}
                  </span>
                </div>
                {previewOption ? (
                  <EChartCanvas
                    option={previewOption}
                    ariaLabel={`${draft.title}，草稿预览`}
                    className="h-64 w-full min-w-0 rounded-lg border border-border/70 bg-background sm:h-80"
                  />
                ) : (
                  <div
                    className="flex min-h-40 items-center rounded-lg border border-dashed border-border px-4 py-3 text-sm text-muted-foreground"
                    role="status"
                  >
                    {Object.values(validation.errors)[0] ?? "当前草稿暂不可绘制，请调整对应设置。"}
                  </div>
                )}
                {queryArtifact.rows.length > 1000 && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    预览仅使用已返回的前 1,000 行。
                  </p>
                )}
                {getChartUnitWarning(draft as ChartViewConfiguration) && (
                  <p className="mt-2 text-xs text-amber-700">
                    {getChartUnitWarning(draft as ChartViewConfiguration)}
                  </p>
                )}
              </section>
            </div>

            <DialogFooter className="sticky bottom-0 z-10 shrink-0 items-center border-t border-border/70 bg-background px-5 py-3 sm:flex-row sm:justify-between">
              <div className="flex flex-wrap items-center gap-2">
                {savedHasOverride && (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={persisting}
                    onClick={() => void restoreRecommendation()}
                  >
                    恢复 AI 推荐
                  </Button>
                )}
                {saveError && (
                  <p role="alert" className="text-xs text-destructive">
                    {saveError}
                  </p>
                )}
              </div>
              <div className="flex justify-end gap-2">
                <DialogClose
                  render={<Button type="button" variant="outline" disabled={persisting} />}
                >
                  取消
                </DialogClose>
                <Button type="button" disabled={applyDisabled} onClick={() => void applyDraft()}>
                  {persisting ? "保存中…" : "应用"}
                </Button>
              </div>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}
