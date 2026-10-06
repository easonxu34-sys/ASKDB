"use client";

import { forwardRef, useEffect, useId, useMemo, useRef, useState } from "react";
import { useAui } from "@assistant-ui/react";
import { Maximize2Icon, Minimize2Icon, PencilLineIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ComposerSelect, type ComposerSelectOption } from "@/components/ui/composer-select";
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
  applyChartEditIntent,
  CHART_COLOR_PALETTES,
  CHART_PALETTE_IDS,
  normalizeChartViewChange,
  type ChartMessagePart,
  type ChartColorDirection,
  type ChartColorSpec,
  type ChartPaletteId,
  type ChartSolidColorSpec,
  getArrowFieldKind,
  getChartFieldCandidates,
  getChartMessageParts,
  summarizeChartViewChange,
  type ChartPaletteToken,
  type ChartType,
  type ChartViewConfiguration,
  type ChartViewEditOrigin,
  type ChartViewUndoRecord,
  type ChartViewValidation,
  type EChartsChartArtifact,
  type SuccessfulQueryArtifact,
  validateChartView,
} from "@/lib/chat-output";
import { deriveSourceTurnKey } from "@/lib/agent-chat-adapter";
import {
  commitThreadChartViewChange,
  getThreadModelProfileId,
  getThreadResultArtifacts,
  undoThreadChartViewChange,
} from "@/lib/local-thread-adapter";
import { requestCsrfToken } from "@/lib/auth-api";
import {
  CHART_EDIT_TURN_COMPLETION_EVENT,
  CHART_EDIT_TURN_START_EVENT,
  buildChartEditRequest,
  confirmChartEditQuery,
  completeStagedChartEdit,
  interpretChartEdit,
  isChartEditContextCurrent,
} from "@/lib/chart-edit-flow.mjs";
import { readChartEditError, readChartEditIntent } from "@/lib/chart-edit-request.mjs";
import { shouldRefreshModelSelection } from "@/lib/model-selection";
import { fetchChatModelOptions, type ChatModelCatalog } from "@/lib/model-profiles";
import {
  buildEChartsOption,
  chartCategoryKey,
  getChartUnitWarning,
  isChartQueryTruncated,
  resolvePieCategoryLabel,
} from "@/lib/chart-output";
import { SqlDisclosure } from "./sql-disclosure";

type ChartResultProps = {
  artifact: EChartsChartArtifact;
  queryArtifact: SuccessfulQueryArtifact;
  recommendedView: ChartViewConfiguration;
  view: ChartViewConfiguration;
  hasOverride: boolean;
  undoHistory?: ChartViewUndoRecord[];
  persistenceAvailable?: boolean;
  overrideNotice?: string;
  userId: string;
  threadId?: string;
  sourceMessageId?: string;
};

type ChartViewDraft = Omit<ChartViewConfiguration, "format_by_field"> & {
  format_by_field: Record<string, unknown>;
};

type ChartEditIntent = {
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
  query_proposal?: { operation: string } | null;
  clarification?: { code: string } | null;
};

type ChartEditProposal = {
  originalInstruction: string;
  intent: ChartEditIntent;
  exactMessage: string;
  operationLabel: string;
  sourceResultId: string;
  sourceTurnKey: string;
  sourceMessageId: string;
  threadId: string;
  baseView: ChartViewConfiguration;
};

type StagedChartEdit = {
  threadId: string;
  intent: ChartEditProposal["intent"];
  sourceResultId: string;
  sourceTurnKey: string;
  sourceMessageId: string;
  baseView: ChartViewConfiguration;
  confirmedTurnId?: string;
  confirmedHistoryTurnId?: string;
};

type ChartEditCompletionReport = {
  thread_id: string;
  turn_id: string;
  history_turn_id: string;
  status: "completed" | "failed" | "cancelled";
  query_result_ids: string[];
  chart_source_result_ids: string[];
};

type ChartEditCompletionEvent = CustomEvent<{
  report: ChartEditCompletionReport;
  waitUntil: (promise: Promise<unknown>) => void;
}>;

type ChartEditTurnStartEvent = CustomEvent<{
  thread_id: string;
  turn_id: string;
  history_turn_id: string;
  confirmed_chart_query: boolean;
}>;

const chartEditClarifications: Record<string, string> = {
  top_n_scope_required: "请说明排名范围。当前图表编辑只支持对已返回结果进行排名。",
  top_n_metric_required: "请先选择一个可见的数值指标，再说明按它进行 Top N 或 Bottom N。",
  source_unit_required: "请说明该数值当前使用的单位。",
  field_not_in_result: "请求的字段不在当前查询结果中，请改用图表中的字段。",
  category_not_in_result: "请求的类别不在当前结果中，请改用图表中显示的类别。",
  category_ambiguous: "该类别标签对应多个不同值，请提供能区分它们的完整标签。",
  conflicting_category_color: "同一类别被指定了不同颜色，请为它保留一个颜色。",
  chart_type_incompatible: "这项编辑与当前图表类型不兼容，请先调整图表类型或编辑内容。",
  conflicting_sort: "Top N 与单独指定的排序冲突，请明确要保留哪一种排序。",
  operation_unsupported: "无法安全应用这项图表编辑，请换一种说法。",
};

function getQueryOperationLabel(intent: ChartEditProposal["intent"]) {
  const operation = intent.query_proposal?.operation;
  return operation === "database_filter"
    ? "需要重新查询并筛选数据"
    : operation === "full_data_top_n"
      ? "需要查询全量数据后排名"
      : operation === "aggregation"
        ? "需要重新聚合数据"
        : operation === "period_comparison"
          ? "需要查询并比较不同期间"
          : "需要重新查询数据";
}

const textControlClass =
  "h-9 w-full rounded-md border border-border bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50";
const selectTriggerClass =
  "h-9 w-full max-w-none rounded-md border-border bg-background px-2.5 text-sm text-foreground";
const fieldLabelClass = "mb-1.5 block text-xs font-medium text-foreground";
const unitLabels = {
  yuan: "元",
  thousand_yuan: "千元",
  ten_thousand_yuan: "万元",
  hundred_million_yuan: "亿元",
} as const;
const cnyUnits = Object.keys(unitLabels) as Array<keyof typeof unitLabels>;
const paletteLabels: Record<ChartPaletteId, string> = {
  system_default: "系统默认",
  classic: "经典",
  ocean: "海洋",
  warm: "暖色",
  earth: "自然",
  pastel: "柔和",
  high_contrast: "高对比",
  color_vision_friendly: "色觉友好",
};
const colorDirections: Array<{ value: ChartColorDirection; label: string }> = [
  { value: "horizontal", label: "横向" },
  { value: "vertical", label: "纵向" },
  { value: "diagonal_down", label: "左上到右下" },
  { value: "diagonal_up", label: "左下到右上" },
];
const colorModeButtonClass =
  "rounded-md px-2.5 py-1.5 text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50";

function toSolidColor(color: ChartColorSpec): ChartColorSpec {
  return {
    mode: "solid",
    hex: color.mode === "solid" ? color.hex : color.start_hex,
    opacity: color.opacity,
  };
}

function ColorHexInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (hex: string) => void;
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const update = (next: string) => {
    setText(next);
    if (/^#[0-9a-fA-F]{6}$/.test(next)) onChange(next.toUpperCase());
  };

  return (
    <div className="flex min-w-0 items-center gap-2">
      <input
        aria-label={`${label}颜色选择器`}
        type="color"
        value={value}
        className="h-8 w-12 shrink-0 cursor-pointer rounded-md border border-border bg-background p-1"
        onChange={(event) => update(event.currentTarget.value)}
      />
      <input
        aria-label={`${label} HEX 色值`}
        type="text"
        autoComplete="off"
        maxLength={7}
        spellCheck={false}
        value={text}
        className="h-8 min-w-0 w-24 rounded-md border border-border bg-background px-2 font-mono text-[11px] uppercase outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        onChange={(event) => update(event.currentTarget.value)}
        onBlur={() => setText(value)}
      />
    </div>
  );
}

function ColorSpecEditor({
  id,
  color,
  allowGradient,
  onChange,
}: {
  id: string;
  color: ChartColorSpec;
  allowGradient: boolean;
  onChange: (color: ChartColorSpec) => void;
}) {
  const setOpacity = (opacity: number) => onChange({ ...color, opacity });
  const gradientDirection =
    color.mode === "linear_gradient"
      ? {
          horizontal: "to right",
          vertical: "to bottom",
          diagonal_down: "to bottom right",
          diagonal_up: "to top right",
        }[color.direction]
      : "to right";

  return (
    <div className="mt-2 space-y-2">
      {color.mode === "solid" ? (
        <div className="flex items-center justify-between gap-3 text-xs">
          <span>颜色</span>
          <ColorHexInput
            label="自定义"
            value={color.hex}
            onChange={(hex) => onChange({ ...color, hex })}
          />
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2">
            <div className="flex min-w-0 flex-col gap-1.5 text-xs">
              <span>起始色</span>
              <ColorHexInput
                label="渐变起始"
                value={color.start_hex}
                onChange={(start_hex) => onChange({ ...color, start_hex })}
              />
            </div>
            <div className="flex min-w-0 flex-col gap-1.5 text-xs">
              <span>结束色</span>
              <ColorHexInput
                label="渐变结束"
                value={color.end_hex}
                onChange={(end_hex) => onChange({ ...color, end_hex })}
              />
            </div>
          </div>
          {allowGradient && (
            <ComposerSelect
              id={`${id}-direction`}
              ariaLabel="渐变方向"
              value={color.direction}
              options={colorDirections}
              placeholder="渐变方向"
              triggerClassName="h-8 w-full max-w-none"
              onValueChange={(value) => onChange({ ...color, direction: value as ChartColorDirection })}
            />
          )}
          <div
            aria-hidden="true"
            className="h-2.5 rounded-full border border-border/70"
            style={{ backgroundImage: `linear-gradient(${gradientDirection}, ${color.start_hex}, ${color.end_hex})`, opacity: color.opacity / 100 }}
          />
        </>
      )}

      <label className="grid grid-cols-[auto_minmax(0,1fr)_3rem] items-center gap-2 text-xs">
        <span>不透明度</span>
        <input
          aria-label="颜色不透明度"
          type="range"
          min={0}
          max={100}
          step={1}
          value={color.opacity}
          className="w-full accent-primary"
          onChange={(event) => setOpacity(Number(event.currentTarget.value))}
        />
        <output className="text-right tabular-nums text-muted-foreground">{color.opacity}%</output>
      </label>
    </div>
  );
}

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
  undoHistory = [],
  persistenceAvailable = true,
  overrideNotice,
  userId,
  threadId,
  sourceMessageId,
}: ChartResultProps) {
  const aui = useAui();
  const controlIdPrefix = `chart-${useId().replace(/:/g, "")}`;
  const cardRef = useRef<HTMLElement>(null);
  const [savedView, setSavedView] = useState(view);
  const [savedHasOverride, setSavedHasOverride] = useState(hasOverride);
  const [savedUndoHistory, setSavedUndoHistory] = useState(undoHistory);
  const [draft, setDraft] = useState<ChartViewDraft>(view as ChartViewDraft);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [draftOrigin, setDraftOrigin] = useState<ChartViewEditOrigin>("manual");
  const naturalPreviewRef = useRef<{
    sourceResultId: string;
    sourceMessageId?: string;
    threadId?: string;
    view: ChartViewConfiguration;
  } | null>(null);
  const [persisting, setPersisting] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [displayNotice, setDisplayNotice] = useState(overrideNotice ?? "");
  const [naturalInstruction, setNaturalInstruction] = useState("");
  const [chartEditModelCatalog, setChartEditModelCatalog] = useState<ChatModelCatalog | null>(null);
  const [chartEditModelCatalogError, setChartEditModelCatalogError] = useState("");
  const [chartEditModelLoading, setChartEditModelLoading] = useState(false);
  const [chartEditModelReloadKey, setChartEditModelReloadKey] = useState(0);
  const [chartEditModelProfileId, setChartEditModelProfileId] = useState("");
  const [interpreting, setInterpreting] = useState(false);
  const [sendingProposal, setSendingProposal] = useState(false);
  const [chartEditProposal, setChartEditProposal] = useState<ChartEditProposal | null>(null);
  const [stagedEditLabel, setStagedEditLabel] = useState("");
  const stagedEditRef = useRef<StagedChartEdit | null>(null);
  const interpretingRef = useRef(false);
  const chartEditModelManuallySelectedRef = useRef(false);
  const chartEditModelThreadIdRef = useRef(threadId);
  const stagedTimeoutRef = useRef<number | undefined>(undefined);
  const editContextRef = useRef({
    sourceResultId: artifact.source_result_id,
    sourceMessageId,
    threadId,
    view: savedView,
  });
  const [fullScreen, setFullScreen] = useState(false);
  const [fullScreenError, setFullScreenError] = useState("");
  const isEmpty = queryArtifact.rows.length === 0;
  const canEdit = Boolean(persistenceAvailable && userId && threadId && sourceMessageId);
  const chartEditModelMatchesThread = chartEditModelThreadIdRef.current === threadId;
  const availableChartEditModels =
    chartEditModelCatalog?.profiles.filter((profile) => profile.available) ?? [];
  const selectedChartEditModelIsAvailable = availableChartEditModels.some(
    (profile) => profile.id === chartEditModelProfileId,
  );
  const chartEditModelOptions: ComposerSelectOption[] = [
    ...(!selectedChartEditModelIsAvailable
      ? [
          {
            value: "",
            label: chartEditModelCatalogError
              ? "模型列表暂不可用"
              : chartEditModelLoading || !chartEditModelCatalog
                ? "正在读取可用模型…"
                : "没有可用模型",
          },
        ]
      : []),
    ...availableChartEditModels.map((profile) => ({
      value: profile.id,
      label: `${profile.name} · ${profile.model}${profile.id === chartEditModelCatalog?.default_profile_id ? "（默认）" : ""}`,
    })),
  ];
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
  const pieColorChoices = useMemo(() => {
    const seen = new Set<string>();
    return queryArtifact.rows.flatMap((row) => {
      const value = row[draft.dimension_field];
      const key = chartCategoryKey(value);
      if (!key || seen.has(key)) return [];
      seen.add(key);
      const label =
        typeof value === "string" || typeof value === "number" || typeof value === "boolean"
          ? String(value)
          : "（空值）";
      const resolution = resolvePieCategoryLabel(queryArtifact, draft.dimension_field, label);
      return [
        {
          key,
          label:
            resolution.status === "ambiguous"
              ? `${label} · ${typeof value}`
              : label,
        },
      ];
    });
  }, [queryArtifact, draft.dimension_field]);
  const activeFields = [...new Set([draft.dimension_field, ...selectedMetrics].filter(Boolean))];

  useEffect(() => {
    const onFullScreenChange = () => setFullScreen(document.fullscreenElement === cardRef.current);
    document.addEventListener("fullscreenchange", onFullScreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullScreenChange);
  }, []);

  useEffect(
    () => () => {
      if (stagedTimeoutRef.current !== undefined) window.clearTimeout(stagedTimeoutRef.current);
      stagedEditRef.current = null;
    },
    [],
  );

  useEffect(() => {
    editContextRef.current = {
      sourceResultId: artifact.source_result_id,
      sourceMessageId,
      threadId,
      view: savedView,
    };
    setChartEditProposal((current) =>
      current &&
      (current.sourceResultId !== artifact.source_result_id ||
        current.sourceMessageId !== sourceMessageId ||
        current.threadId !== threadId ||
        !chartViewsEqual(current.baseView, savedView))
        ? null
        : current,
    );
  }, [artifact.source_result_id, sourceMessageId, threadId, savedView]);

  useEffect(() => {
    const onTurnStart = (event: Event) => {
      const detail = (event as ChartEditTurnStartEvent).detail;
      const staged = stagedEditRef.current;
      if (!detail?.confirmed_chart_query || !staged || detail.thread_id !== staged.threadId) return;
      staged.confirmedTurnId = detail.turn_id;
      staged.confirmedHistoryTurnId = detail.history_turn_id;
      if (stagedTimeoutRef.current !== undefined) window.clearTimeout(stagedTimeoutRef.current);
      setStagedEditLabel("正在等待新查询完成；编辑暂存中，原图保持不变。");
    };
    const onTurnCompletion = (event: Event) => {
      const detail = (event as ChartEditCompletionEvent).detail;
      if (!detail || detail.report.thread_id !== threadId) return;
      detail.waitUntil(handleChartEditCompletion(detail.report));
    };
    window.addEventListener(CHART_EDIT_TURN_START_EVENT, onTurnStart);
    window.addEventListener(CHART_EDIT_TURN_COMPLETION_EVENT, onTurnCompletion);
    return () => {
      window.removeEventListener(CHART_EDIT_TURN_START_EVENT, onTurnStart);
      window.removeEventListener(CHART_EDIT_TURN_COMPLETION_EVENT, onTurnCompletion);
    };
  }, [threadId, artifact.source_result_id, sourceMessageId, savedView, userId]);

  useEffect(() => {
    if (chartEditModelThreadIdRef.current === threadId) return;
    chartEditModelThreadIdRef.current = threadId;
    chartEditModelManuallySelectedRef.current = false;
    setChartEditModelProfileId("");
  }, [threadId]);

  useEffect(() => {
    if (!canEdit || isEmpty) return;
    let active = true;
    const loadModelCatalog = async () => {
      setChartEditModelLoading(true);
      setChartEditModelCatalogError("");
      try {
        const catalog = await fetchChatModelOptions();
        if (!active) return;
        setChartEditModelCatalog(catalog);
        const availableProfiles = catalog.profiles.filter((profile) => profile.available);
        const availableIds = new Set(availableProfiles.map((profile) => profile.id));
        const threadProfileId = threadId ? getThreadModelProfileId(userId, threadId) : undefined;
        const effectiveThreadProfileId =
          threadProfileId && availableIds.has(threadProfileId) ? threadProfileId : undefined;
        const defaultProfileId =
          catalog.default_profile_id && availableIds.has(catalog.default_profile_id)
            ? catalog.default_profile_id
            : undefined;
        const fallbackProfileId =
          effectiveThreadProfileId ?? defaultProfileId ?? availableProfiles[0]?.id ?? "";
        setChartEditModelProfileId((current) =>
          chartEditModelManuallySelectedRef.current && availableIds.has(current)
            ? current
            : fallbackProfileId,
        );
      } catch (error) {
        if (!active) return;
        setChartEditModelCatalog(null);
        setChartEditModelCatalogError(
          error instanceof Error ? error.message : "可用模型列表读取失败，请重试。",
        );
        setChartEditModelProfileId("");
      } finally {
        if (active) setChartEditModelLoading(false);
      }
    };
    void loadModelCatalog();
    window.addEventListener("askdb:model-catalog-updated", loadModelCatalog);
    return () => {
      active = false;
      window.removeEventListener("askdb:model-catalog-updated", loadModelCatalog);
    };
  }, [canEdit, chartEditModelReloadKey, isEmpty, threadId, userId]);

  const propViewIdentity = `${artifact.source_result_id}:${JSON.stringify(view)}:${hasOverride}:${JSON.stringify(undoHistory)}:${overrideNotice ?? ""}`;
  const lastPropViewIdentity = useRef(propViewIdentity);
  useEffect(() => {
    if (lastPropViewIdentity.current === propViewIdentity) return;
    lastPropViewIdentity.current = propViewIdentity;
    setSavedView(view);
    setSavedHasOverride(hasOverride);
    setSavedUndoHistory(undoHistory);
    setDraft(view as ChartViewDraft);
    setDisplayNotice(overrideNotice ?? "");
  }, [propViewIdentity, view, hasOverride, undoHistory]);

  function beginEditing() {
    naturalPreviewRef.current = null;
    setDraftOrigin("manual");
    setDraft(savedView as ChartViewDraft);
    setSaveError("");
    setDialogOpen(true);
  }

  function closeEditor(nextOpen: boolean) {
    setDialogOpen(nextOpen);
    if (!nextOpen) {
      naturalPreviewRef.current = null;
      setDraftOrigin("manual");
      setDraft(savedView as ChartViewDraft);
      setSaveError("");
    }
  }

  function updateDraft(update: (current: ChartViewDraft) => ChartViewDraft) {
    setDraft((current) => normalizeChartViewChange(current, update(current), queryArtifact));
    setSaveError("");
  }

  function updateFormat(field: string, format: unknown) {
    updateDraft((current) => ({
      ...current,
      format_by_field: { ...current.format_by_field, [field]: format },
    }));
  }

  function selectChartType(nextType: ChartType) {
    updateDraft((current) => ({ ...current, chart_type: nextType }));
  }

  function selectDimension(field: string) {
    updateDraft((current) => ({
      ...current,
      dimension_field: field,
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
        };
      }
      const metrics = current.metric_fields.filter((item) => item !== field);
      const next = {
        ...current,
        metric_fields: metrics,
      };
      return next;
    });
  }

  function updateMetricColor(field: string, color?: ChartColorSpec) {
    updateDraft((current) => {
      const colors = { ...(current.color_by_metric ?? {}) };
      if (color) colors[field] = color;
      else delete colors[field];
      return {
        ...current,
        ...(Object.keys(colors).length ? { color_by_metric: colors } : { color_by_metric: undefined }),
      };
    });
  }

  function updateMetricColorMode(field: string, mode: ChartColorSpec["mode"]) {
    const metricIndex = Math.max(0, visibleMetrics.indexOf(field));
    const palette = CHART_COLOR_PALETTES[draft.color_palette_id ?? "system_default"];
    const currentColor = draft.color_by_metric?.[field] ?? {
      mode: "solid" as const,
      hex: palette[metricIndex % palette.length],
      opacity: 100,
    };
    const nextColor = mode === "solid"
      ? toSolidColor(currentColor)
      : currentColor.mode === "linear_gradient"
        ? currentColor
        : {
            mode: "linear_gradient" as const,
            start_hex: currentColor.hex,
            end_hex: palette[(metricIndex + 1) % palette.length],
            direction: "horizontal" as const,
            opacity: currentColor.opacity,
          };
    updateMetricColor(field, nextColor);
  }

  function updatePieCategoryColor(categoryKey: string, color?: ChartSolidColorSpec) {
    updateDraft((current) => {
      const colors = { ...(current.pie_category_colors?.by_category_key ?? {}) };
      if (color) colors[categoryKey] = color;
      else delete colors[categoryKey];
      return {
        ...current,
        ...(Object.keys(colors).length
          ? {
              pie_category_colors: {
                dimension_field: current.dimension_field,
                by_category_key: colors,
              },
            }
          : { pie_category_colors: undefined }),
      };
    });
  }

  function updatePalette(paletteId: ChartPaletteId) {
    updateDraft((current) => ({ ...current, color_palette_id: paletteId }));
  }

  function restoreAutomaticColors() {
    updateDraft((current) => ({
      ...current,
      color_by_metric: undefined,
      pie_category_colors: undefined,
    }));
  }

  async function persistView(
    nextView: ChartViewConfiguration,
    origin: ChartViewEditOrigin = "manual",
    summary = summarizeChartViewChange(savedView, nextView),
  ) {
    if (!threadId || !sourceMessageId) {
      setSaveError("无法关联当前会话轮次，配置未保存。");
      return false;
    }
    setPersisting(true);
    setSaveError("");
    try {
      const { sourceTurnKey } = await deriveSourceTurnKey(threadId, sourceMessageId);
      const saved = commitThreadChartViewChange(
        userId,
        threadId,
        sourceTurnKey,
        artifact.source_result_id,
        savedView,
        nextView,
        recommendedView,
        summary,
        origin,
      );
      if (!saved) {
        setSaveError("保存失败，已保留之前保存的图表配置。请检查浏览器本地存储后重试。");
        return false;
      }
      setSavedView(saved.view);
      editContextRef.current = {
        sourceResultId: artifact.source_result_id,
        sourceMessageId,
        threadId,
        view: saved.view,
      };
      setSavedUndoHistory(saved.undoHistory);
      const differs = !chartViewsEqual(saved.view, recommendedView);
      setSavedHasOverride(differs);
      setDisplayNotice("");
      setDraft(saved.view as ChartViewDraft);
      return true;
    } catch {
      setSaveError("保存失败，已保留之前保存的图表配置。");
      return false;
    } finally {
      setPersisting(false);
    }
  }

  async function handleChartEditCompletion(report: ChartEditCompletionReport) {
    const staged = stagedEditRef.current;
    if (
      !staged ||
      staged.threadId !== report.thread_id ||
      !staged.confirmedTurnId ||
      staged.confirmedTurnId !== report.turn_id ||
      staged.confirmedHistoryTurnId !== report.history_turn_id
    ) return;
    stagedEditRef.current = null;
    if (stagedTimeoutRef.current !== undefined) window.clearTimeout(stagedTimeoutRef.current);
    stagedTimeoutRef.current = undefined;
    setStagedEditLabel("");
    const sourceContext = editContextRef.current;
    if (
      sourceContext.sourceResultId !== staged.sourceResultId ||
      sourceContext.sourceMessageId !== staged.sourceMessageId ||
      sourceContext.threadId !== staged.threadId ||
      !chartViewsEqual(sourceContext.view, staged.baseView)
    ) {
      setDisplayNotice("原图结果或配置已更新，暂存编辑已丢弃，原图保持不变。");
      return;
    }
    const outcome = await completeStagedChartEdit(report, staged, {
      resolveTarget: (resultId: string, completion: ChartEditCompletionReport) => {
        const artifacts = getThreadResultArtifacts(
          userId,
          completion.thread_id,
          completion.history_turn_id,
        );
        const matchingParts = getChartMessageParts(artifacts).filter(
          (item) => item.data.artifact.source_result_id === resultId,
        );
        const part = matchingParts.length === 1 ? matchingParts[0] : undefined;
        return part
          ? { source_result_id: part.data.artifact.source_result_id, data: part.data }
          : undefined;
      },
      apply: (
        target: { source_result_id: string; data: ChartMessagePart["data"] },
        intent: ChartEditIntent,
      ) => applyChartEditIntent(target.data.recommendedView, intent, target.data.queryArtifact),
      commit: (
        target: { source_result_id: string; data: ChartMessagePart["data"] },
        nextView: ChartViewConfiguration,
      ) => {
        const summary = summarizeChartViewChange(target.data.recommendedView, nextView);
        const saved = commitThreadChartViewChange(
          userId,
          report.thread_id,
          report.history_turn_id,
          target.source_result_id,
          target.data.recommendedView,
          nextView,
          target.data.recommendedView,
          summary,
          "natural_language",
        );
        return saved ? { summary } : undefined;
      },
    });
    if (outcome.status === "applied") {
      const summary = (outcome as { target: { source_result_id: string; data: ChartMessagePart["data"] }; view: ChartViewConfiguration }).target;
      setDisplayNotice(`查询完成，已应用：${summarizeChartViewChange(summary.data.recommendedView, (outcome as { view: ChartViewConfiguration }).view)}。`);
      return;
    }
    if (outcome.status === "no_display_changes") {
      setDisplayNotice("查询已完成，新结果已生成；没有额外的图表配置修改。");
      return;
    }
    if (outcome.status === "discarded") {
      const reason = outcome.reason;
      if (reason === "cancelled") setDisplayNotice("查询已取消，暂存的图表编辑已丢弃，原图保持不变。");
      else if (reason === "failed" || reason === "timeout") setDisplayNotice("查询未成功，暂存的图表编辑已丢弃，原图保持不变。");
      else if (reason === "result_mismatch") setDisplayNotice("新查询没有唯一匹配的图表结果，暂存编辑已丢弃，原图保持不变。");
      else if (reason === "result_unavailable") setDisplayNotice("新查询结果或图表已过期，暂存编辑已丢弃，原图保持不变。");
      else if (reason === "cache_failure") setDisplayNotice("图表编辑未能保存，暂存编辑已丢弃，原图保持不变。");
      else setDisplayNotice(`${chartEditClarifications[String((outcome as { code?: unknown }).code)] ?? chartEditClarifications.operation_unsupported}暂存编辑已丢弃，原图保持不变。`);
    }
  }

  async function requestNaturalLanguageEdit() {
    if (
      !threadId || !sourceMessageId || !naturalInstruction.trim() ||
      !chartEditModelMatchesThread ||
      !selectedChartEditModelIsAvailable || chartEditModelLoading ||
      chartEditModelCatalogError || interpretingRef.current
    ) return;
    interpretingRef.current = true;
    setInterpreting(true);
    setSaveError("");
    setChartEditProposal(null);
    const requestContext = {
      sourceResultId: artifact.source_result_id,
      sourceMessageId,
      threadId,
      view: savedView,
    };
    try {
      const { sourceTurnKey } = await deriveSourceTurnKey(threadId, sourceMessageId);
      const csrfToken = await requestCsrfToken();
      const body = buildChartEditRequest({
        threadId,
        modelProfileId: chartEditModelProfileId,
        instruction: naturalInstruction,
        chartArtifact: artifact,
        queryArtifact,
        view: savedView,
      });
      const response = await fetch("/api/chart-edits/interpret", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
        body: JSON.stringify(body),
      });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const chartEditError = readChartEditError(payload);
        console.error(
          "[AskDB chart edit diagnostic]",
          JSON.stringify(
            {
              event: "chart_edit_failure",
              http_status: response.status,
              code: chartEditError.code,
              diagnostic: chartEditError.diagnostic ?? null,
            },
            null,
            2,
          ),
        );
        if (shouldRefreshModelSelection(chartEditError.code)) {
          window.dispatchEvent(new Event("askdb:model-catalog-updated"));
        }
        throw new Error(chartEditError.message);
      }
      const intent = readChartEditIntent(payload);
      const currentContext = editContextRef.current;
      if (!isChartEditContextCurrent(requestContext, currentContext, chartViewsEqual)) {
        throw new Error("图表结果或配置已更新，请基于当前图表重新提交编辑。");
      }
      await interpretChartEdit(intent, {
        preview: (value: ChartEditIntent) => {
          const result = applyChartEditIntent(savedView, value, queryArtifact);
          if (result.status !== "apply") {
            setSaveError(chartEditClarifications[result.code] ?? chartEditClarifications.operation_unsupported);
            return;
          }
          naturalPreviewRef.current = requestContext;
          setDraftOrigin("natural_language");
          setDraft(result.view);
          setDialogOpen(true);
        },
        clarify: (code: string) => {
          setSaveError(chartEditClarifications[String(code)] ?? chartEditClarifications.operation_unsupported);
        },
        showQueryProposal: async (value: ChartEditIntent) => {
          const exactMessage = await confirmChartEditQuery(value.query_proposal, naturalInstruction, (message: string) => message);
          setChartEditProposal({
            originalInstruction: naturalInstruction,
            intent: value,
            exactMessage,
            operationLabel: getQueryOperationLabel(value),
            sourceResultId: requestContext.sourceResultId,
            sourceTurnKey,
            sourceMessageId: requestContext.sourceMessageId,
            threadId: requestContext.threadId,
            baseView: requestContext.view,
          });
        },
      });
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "无法解释此次图表编辑，请稍后重试。");
    } finally {
      interpretingRef.current = false;
      setInterpreting(false);
    }
  }

  async function confirmQueryProposal() {
    if (!chartEditProposal || !threadId || sendingProposal) return;
    const proposal = chartEditProposal;
    const currentContext = editContextRef.current;
    const originalSourceStillCached = getChartMessageParts(
      getThreadResultArtifacts(userId, proposal.threadId, proposal.sourceTurnKey),
    ).some((part) => part.data.artifact.source_result_id === proposal.sourceResultId);
    if (
      proposal.threadId !== threadId ||
      proposal.sourceResultId !== currentContext.sourceResultId ||
      proposal.sourceMessageId !== currentContext.sourceMessageId ||
      !chartViewsEqual(proposal.baseView, currentContext.view) ||
      !originalSourceStillCached
    ) {
      setChartEditProposal(null);
      setSaveError("原图结果已过期或图表配置已更新，请基于当前图表重新提交编辑。");
      return;
    }
    const staged: StagedChartEdit = {
      threadId: proposal.threadId,
      intent: proposal.intent,
      sourceResultId: proposal.sourceResultId,
      sourceTurnKey: proposal.sourceTurnKey,
      sourceMessageId: proposal.sourceMessageId,
      baseView: proposal.baseView,
    };
    stagedEditRef.current = staged;
    setStagedEditLabel("正在等待新查询完成；编辑暂存中，原图保持不变。");
    stagedTimeoutRef.current = window.setTimeout(() => {
      if (stagedEditRef.current !== staged) return;
      stagedEditRef.current = null;
      stagedTimeoutRef.current = undefined;
      setStagedEditLabel("");
      setDisplayNotice("等待查询结果超时，暂存编辑已丢弃，原图保持不变。");
    }, 120_000);
    setSendingProposal(true);
    setSaveError("");
    try {
      await confirmChartEditQuery(proposal.intent.query_proposal, proposal.originalInstruction, (message: string) => {
        if (message !== proposal.exactMessage) throw new Error("图表查询提议发生变化，请重新确认。");
        aui.composer.setText(message);
        aui.composer.send();
      });
      setChartEditProposal(null);
      setNaturalInstruction("");
    } catch (error) {
      stagedEditRef.current = null;
      if (stagedTimeoutRef.current !== undefined) window.clearTimeout(stagedTimeoutRef.current);
      stagedTimeoutRef.current = undefined;
      setStagedEditLabel("");
      setSaveError(error instanceof Error ? error.message : "无法发送已确认的查询请求。");
    } finally {
      setSendingProposal(false);
    }
  }

  function cancelQueryProposal() {
    setChartEditProposal(null);
    setSaveError("");
  }

  async function applyDraft() {
    if (naturalPreviewRef.current &&
        !isChartEditContextCurrent(naturalPreviewRef.current, editContextRef.current, chartViewsEqual)) {
      setSaveError("图表结果或配置已更新，请取消预览并重新提交编辑。");
      return;
    }
    const result = validateChartView(draft, queryArtifact);
    if (!result.view) {
      setSaveError("");
      return;
    }
    const summary = summarizeChartViewChange(savedView, result.view);
    if (await persistView(result.view, draftOrigin, summary)) {
      if (draftOrigin === "natural_language") {
        setNaturalInstruction("");
        setDisplayNotice(`已应用：${summary}。`);
      }
      naturalPreviewRef.current = null;
      setDraftOrigin("manual");
      setDialogOpen(false);
    }
  }

  async function restoreRecommendation() {
    if (
      await persistView(
        recommendedView,
        "restore_recommendation",
        "恢复 AI 推荐配置",
      )
    ) {
      setDialogOpen(false);
    }
  }

  async function undoLastEdit() {
    if (!threadId || !sourceMessageId || savedUndoHistory.length === 0) return;
    setPersisting(true);
    setSaveError("");
    try {
      const { sourceTurnKey } = await deriveSourceTurnKey(threadId, sourceMessageId);
      const undone = undoThreadChartViewChange(
        userId,
        threadId,
        sourceTurnKey,
        artifact.source_result_id,
        savedView,
        recommendedView,
      );
      if (!undone) {
        setDisplayNotice("撤销失败，当前图表和撤销记录均已保留。");
        return;
      }
      setSavedView(undone.view);
      setSavedUndoHistory(undone.undoHistory);
      setSavedHasOverride(!chartViewsEqual(undone.view, recommendedView));
      setDisplayNotice("");
      setDraft(undone.view as ChartViewDraft);
    } catch {
      setDisplayNotice("撤销失败，当前图表和撤销记录均已保留。");
    } finally {
      setPersisting(false);
    }
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
        className={`my-4 min-w-0 overflow-hidden rounded-xl border border-border/70 bg-background px-3 py-3 shadow-sm ${
          fullScreen ? "m-0 h-dvh w-screen overflow-y-auto rounded-none border-0 p-6" : ""
        }`}
        aria-label={savedView.title}
      >
        <div className="mb-1 flex items-center justify-between gap-3">
          <h3 className="min-w-0 truncate text-sm font-medium">{savedView.title}</h3>
          <div className="flex shrink-0 items-center gap-1">
            {canEdit && savedUndoHistory.length > 0 && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={persisting}
                aria-label="撤销上次图表编辑"
                onClick={() => void undoLastEdit()}
              >
                撤销
              </Button>
            )}
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
        {canEdit && (
          <section className="mt-3 rounded-lg border border-border/70 bg-muted/20 p-3" aria-label="自然语言编辑图表">
            <label className="block">
              <span className={fieldLabelClass}>用自然语言编辑图表</span>
              <input
                className={textControlClass}
                value={naturalInstruction}
                maxLength={2048}
                aria-label="自然语言图表编辑指令"
                placeholder="例如：标题改成各地区收入，按收入取 Top 5"
                disabled={interpreting || sendingProposal || Boolean(stagedEditRef.current)}
                onChange={(event) => setNaturalInstruction(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void requestNaturalLanguageEdit();
                  }
                }}
              />
            </label>
            <div className="mt-2 flex flex-wrap items-end gap-3">
              <label className="block w-full sm:max-w-xs">
                <span className={fieldLabelClass}>本次图表编辑模型</span>
                <ComposerSelect
                  id={`${controlIdPrefix}-model`}
                  ariaLabel="本次图表编辑使用的模型"
                  value={selectedChartEditModelIsAvailable ? chartEditModelProfileId : ""}
                  options={chartEditModelOptions}
                  placeholder="选择模型"
                  triggerClassName={selectTriggerClass}
                  disabled={
                    !chartEditModelMatchesThread ||
                    !chartEditModelCatalog ||
                    chartEditModelLoading ||
                    Boolean(chartEditModelCatalogError) ||
                    availableChartEditModels.length === 0 || interpreting || sendingProposal ||
                    Boolean(stagedEditRef.current)
                  }
                  onValueChange={(value) => {
                    chartEditModelManuallySelectedRef.current = true;
                    setChartEditModelProfileId(value);
                    setSaveError("");
                  }}
                />
              </label>
              <p className="max-w-xl pb-2 text-[11px] leading-5 text-muted-foreground">
                只用于理解这次图表指令。若模型不支持结构化输出，可切换后重试；确认重新查询时仍使用当前会话模型。
              </p>
            </div>
            {chartEditModelCatalogError && (
              <div className="mt-2 flex flex-wrap items-center gap-2" role="status">
                <span className="text-xs text-destructive">{chartEditModelCatalogError}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={chartEditModelLoading}
                  onClick={() => setChartEditModelReloadKey((value) => value + 1)}
                >
                  {chartEditModelLoading ? "正在重试…" : "重试"}
                </Button>
              </div>
            )}
            {!chartEditModelCatalogError && !chartEditModelLoading &&
              chartEditModelCatalog && availableChartEditModels.length === 0 && (
                <p className="mt-2 text-xs text-muted-foreground" role="status">
                  当前没有可用模型，请检查模型设置。
                </p>
              )}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Button
                type="button"
                size="sm"
                disabled={
                  interpreting || sendingProposal || Boolean(stagedEditRef.current) ||
                  !naturalInstruction.trim() || chartEditModelLoading ||
                  Boolean(chartEditModelCatalogError) || !selectedChartEditModelIsAvailable ||
                  !chartEditModelMatchesThread
                }
                onClick={() => void requestNaturalLanguageEdit()}
              >
                {interpreting ? "正在理解…" : "解释并预览"}
              </Button>
              {stagedEditLabel && <span role="status" className="text-xs text-muted-foreground">{stagedEditLabel}</span>}
            </div>
            {chartEditProposal && (
              <div className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/5 p-3" aria-live="polite">
                <p className="text-sm font-medium">{chartEditProposal.operationLabel}</p>
                <p className="mt-1 text-xs text-muted-foreground">确认后将通过当前会话查询。以下是将发送的完整消息：</p>
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-background p-2 text-xs leading-5">{chartEditProposal.exactMessage}</pre>
                <div className="mt-2 flex gap-2">
                  <Button type="button" size="sm" disabled={sendingProposal} onClick={() => void confirmQueryProposal()}>
                    {sendingProposal ? "正在发送…" : "确认并发送"}
                  </Button>
                  <Button type="button" size="sm" variant="outline" disabled={sendingProposal} onClick={cancelQueryProposal}>
                    取消
                  </Button>
                </div>
              </div>
            )}
            {saveError && <p role="alert" className="mt-2 text-xs leading-5 text-destructive">{saveError}</p>}
          </section>
        )}
        {savedView.current_result_top_n && (
          <p role="status" className="mt-2 text-[11px] text-muted-foreground">
            当前结果排名：按已返回的前 1,000 行最多显示 {savedView.current_result_top_n.count} 项。
          </p>
        )}
        {isChartQueryTruncated(queryArtifact) ? (
          <p className="mt-2 text-[11px] text-muted-foreground">
            {savedView.current_result_top_n
              ? "查询结果存在截断信号，此排名不代表全量数据排名。"
              : "图表仅展示已返回的部分查询结果。"}
          </p>
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
            <DialogHeader className="relative shrink-0 border-b border-border/70 px-5 py-4 pr-14">
              <div>
                <DialogTitle>编辑图表</DialogTitle>
                <DialogDescription className="mt-1">
                  设置只作用于当前图表；查询表格、原始值和查询结果顺序保持不变。
                </DialogDescription>
                {draftOrigin === "natural_language" && (
                  <p role="status" className="mt-2 text-xs text-muted-foreground">
                    待应用：{validation.view
                      ? summarizeChartViewChange(savedView, validation.view)
                      : "请先修正图表设置"}。确认应用后保存，取消将保留原图。
                  </p>
                )}
              </div>
              <DialogClose
                render={
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="absolute top-4 right-4"
                    aria-label="关闭编辑器"
                  />
                }
              >
                <span aria-hidden="true">×</span>
              </DialogClose>
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
                    <ComposerSelect
                      id={`${controlIdPrefix}-dimension`}
                      ariaLabel="图表维度"
                      value={draft.dimension_field}
                      options={fieldCandidates
                        .filter(({ kind }) => kind === "categorical" || kind === "temporal")
                        .map(({ name, kind }) => ({
                          value: name,
                          label: `${name}${kind === "temporal" ? " · 日期/时间" : " · 分类"}`,
                        }))}
                      placeholder="选择图表维度"
                      triggerClassName={selectTriggerClass}
                      ariaInvalid={Boolean(validation.errors.dimension_field)}
                      ariaDescribedBy={
                        validation.errors.dimension_field
                          ? `${controlIdPrefix}-dimension-error`
                          : undefined
                      }
                      onValueChange={selectDimension}
                    />
                    <RecordFieldError
                      id={`${controlIdPrefix}-dimension-error`}
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
                                  updateDraft((current) => {
                                    const next = {
                                      ...current,
                                      hidden_metric_fields: checked
                                        ? current.hidden_metric_fields.filter(
                                            (item) => item !== field,
                                          )
                                        : [...current.hidden_metric_fields, field],
                                    };
                                    return next;
                                  });
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

                  <section className="mt-3 rounded-lg border border-border/70 p-3" aria-label="配色方案">
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <h4 className={fieldLabelClass}>配色方案</h4>
                      {(Object.keys(draft.color_by_metric ?? {}).length > 0 ||
                        Object.keys(draft.pie_category_colors?.by_category_key ?? {}).length > 0) && (
                        <button
                          type="button"
                          className="text-[11px] text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                          onClick={restoreAutomaticColors}
                        >
                          恢复全部自动配色
                        </button>
                      )}
                    </div>
                    <p className="mb-2 text-[11px] leading-4 text-muted-foreground">
                      自动配色跟随色板；单独设置的指标或类别颜色会保留为自定义颜色。
                    </p>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                      {CHART_PALETTE_IDS.map((paletteId) => (
                        <button
                          key={paletteId}
                          type="button"
                          aria-pressed={(draft.color_palette_id ?? "system_default") === paletteId}
                          className={`min-w-0 rounded-lg border px-2 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${(draft.color_palette_id ?? "system_default") === paletteId ? "border-primary bg-primary/5" : "border-border/70 hover:bg-muted/50"}`}
                          onClick={() => updatePalette(paletteId)}
                        >
                          <span className="block truncate text-[11px] font-medium">{paletteLabels[paletteId]}</span>
                          <span className="mt-1.5 flex gap-1" aria-hidden="true">
                            {(paletteId === "system_default" && draft.chart_type === "pie"
                              ? CHART_COLOR_PALETTES.classic
                              : CHART_COLOR_PALETTES[paletteId]).map((color, index) => (
                              <span
                                key={`${paletteId}-${index}`}
                                className="h-2 min-w-0 flex-1 rounded-full"
                                style={{ backgroundColor: color }}
                              />
                            ))}
                          </span>
                        </button>
                      ))}
                    </div>
                  </section>

                  {draft.chart_type !== "pie" && visibleMetrics.length > 0 && (
                    <fieldset className="mt-3 space-y-2">
                      <legend className={fieldLabelClass}>指标颜色</legend>
                      {visibleMetrics.map((field) => {
                        const color = draft.color_by_metric?.[field];
                        const metricIndex = Math.max(0, visibleMetrics.indexOf(field));
                        const selectedPalette = draft.color_palette_id ?? "system_default";
                        const palette = selectedPalette === "system_default"
                          ? CHART_COLOR_PALETTES.classic
                          : CHART_COLOR_PALETTES[selectedPalette];
                        const fallbackColor = palette[metricIndex % palette.length];
                        return (
                          <div key={field} className="rounded-lg border border-border/70 p-3">
                            <div className="mb-2 flex min-w-0 items-center justify-between gap-2">
                              <span className="truncate text-xs font-medium">{field}</span>
                              <span
                                className="size-4 shrink-0 rounded-full border border-border/70"
                                style={{
                                  background: color?.mode === "linear_gradient"
                                    ? `linear-gradient(to right, ${color.start_hex}, ${color.end_hex})`
                                    : color?.hex ?? fallbackColor,
                                  opacity: color?.opacity === undefined ? 1 : color.opacity / 100,
                                }}
                                aria-hidden="true"
                              />
                            </div>
                            <div className="grid grid-cols-3 gap-1 rounded-lg bg-muted/60 p-1" role="group" aria-label={`指标 ${field} 的配色模式`}>
                              {([
                                ["auto", "跟随色板"],
                                ["solid", "纯色"],
                                ["linear_gradient", "渐变"],
                              ] as const).map(([mode, label]) => (
                                <button
                                  key={mode}
                                  type="button"
                                  aria-pressed={mode === "auto" ? !color : color?.mode === mode}
                                  className={`${colorModeButtonClass} ${(mode === "auto" ? !color : color?.mode === mode) ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                                  onClick={() => {
                                    if (mode === "auto") updateMetricColor(field);
                                    else updateMetricColorMode(field, mode);
                                  }}
                                >
                                  {label}
                                </button>
                              ))}
                            </div>
                            {color && (
                              <ColorSpecEditor
                                id={`${controlIdPrefix}-metric-color-${encodeURIComponent(field)}`}
                                color={color}
                                allowGradient
                                onChange={(nextColor) => updateMetricColor(field, nextColor)}
                              />
                            )}
                          </div>
                        );
                      })}
                    </fieldset>
                  )}

                  {draft.chart_type === "pie" && pieColorChoices.length > 0 && (
                    <fieldset className="mt-3 space-y-2">
                      <legend className={fieldLabelClass}>类别颜色</legend>
                      {pieColorChoices.map(({ key, label }, index) => {
                        const color = draft.pie_category_colors?.by_category_key[key];
                        const palette = CHART_COLOR_PALETTES[draft.color_palette_id ?? "system_default"];
                        return (
                          <div key={key} className="rounded-lg border border-border/70 p-3">
                            <div className="mb-2 flex min-w-0 items-center justify-between gap-2">
                              <span className="truncate text-xs font-medium">{label}</span>
                              <span
                                className="size-4 shrink-0 rounded-full border border-border/70"
                                style={{ backgroundColor: color?.hex ?? palette[index % palette.length], opacity: color ? color.opacity / 100 : 1 }}
                                aria-hidden="true"
                              />
                            </div>
                            <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted/60 p-1" role="group" aria-label={`类别 ${label} 的配色模式`}>
                              <button
                                type="button"
                                aria-pressed={!color}
                                className={`${colorModeButtonClass} ${!color ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                                onClick={() => updatePieCategoryColor(key)}
                              >
                                跟随色板
                              </button>
                              <button
                                type="button"
                                aria-pressed={Boolean(color)}
                                className={`${colorModeButtonClass} ${color ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                                onClick={() => updatePieCategoryColor(key, color ?? {
                                  mode: "solid",
                                  hex: palette[index % palette.length],
                                  opacity: 100,
                                })}
                              >
                                自定义纯色
                              </button>
                            </div>
                            {color && (
                              <ColorSpecEditor
                                id={`${controlIdPrefix}-category-color-${encodeURIComponent(key)}`}
                                color={color}
                                allowGradient={false}
                                onChange={(nextColor) => {
                                  if (nextColor.mode === "solid") updatePieCategoryColor(key, nextColor);
                                }}
                              />
                            )}
                          </div>
                        );
                      })}
                    </fieldset>
                  )}

                  {draft.chart_type === "bar" && (
                    <label className="mt-3 block">
                      <span className={fieldLabelClass}>柱状图方向</span>
                      <ComposerSelect
                        id={`${controlIdPrefix}-bar-orientation`}
                        ariaLabel="柱状图方向"
                        value={draft.bar_orientation ?? "vertical"}
                        options={[
                          { value: "vertical", label: "纵向" },
                          { value: "horizontal", label: "横向" },
                        ]}
                        placeholder="选择柱状图方向"
                        triggerClassName={selectTriggerClass}
                        onValueChange={(value) => {
                          const orientation = value as "vertical" | "horizontal";
                          updateDraft((current) => ({ ...current, bar_orientation: orientation }));
                        }}
                      />
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
                      <ComposerSelect
                        id={`${controlIdPrefix}-sort-mode`}
                        ariaLabel="图表排序方式"
                        value={draft.sort.mode}
                        options={[
                          { value: "original", label: "查询原序" },
                          {
                            value: "dimension",
                            label: "按维度",
                            disabled: draft.chart_type === "pie",
                          },
                          ...(draft.chart_type !== "line"
                            ? [{ value: "metric", label: "按指标" }]
                            : []),
                        ]}
                        placeholder="选择排序方式"
                        triggerClassName={selectTriggerClass}
                        onValueChange={(mode) => {
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
                      />
                    </label>
                    {draft.sort.mode !== "original" && (
                      <label>
                        <span className="sr-only">排序方向</span>
                        <ComposerSelect
                          id={`${controlIdPrefix}-sort-direction`}
                          ariaLabel="图表排序方向"
                          value={draft.sort.direction}
                          options={[
                            { value: "asc", label: "升序" },
                            { value: "desc", label: "降序" },
                          ]}
                          placeholder="选择排序方向"
                          triggerClassName={selectTriggerClass}
                          onValueChange={(value) => {
                            const direction = value as "asc" | "desc";
                            updateDraft((current) => ({
                              ...current,
                              sort: { ...current.sort, direction } as ChartViewConfiguration["sort"],
                            }));
                          }}
                        />
                      </label>
                    )}
                  </div>
                  {draft.sort.mode === "metric" && (
                    <label className="mt-2 block">
                      <span className={fieldLabelClass}>排序指标</span>
                      <ComposerSelect
                        id={`${controlIdPrefix}-sort-field`}
                        ariaLabel="排序指标"
                        value={draft.sort.field}
                        options={visibleMetrics.map((field) => ({ value: field, label: field }))}
                        placeholder="选择排序指标"
                        triggerClassName={selectTriggerClass}
                        onValueChange={(field) => {
                          updateDraft((current) => ({
                            ...current,
                            sort: { ...current.sort, field } as ChartViewConfiguration["sort"],
                          }));
                        }}
                      />
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
                      const formatErrorId =
                        `${controlIdPrefix}-format-${encodeURIComponent(field)}-error`;
                      return (
                        <fieldset key={field} className="rounded-md border border-border/70 p-3">
                          <legend className="px-1 text-xs font-medium">{field}</legend>
                          <label className="mb-2 block">
                            <span className={fieldLabelClass}>显示格式</span>
                            <ComposerSelect
                              id={`${controlIdPrefix}-format-${encodeURIComponent(field)}`}
                              ariaLabel={`${field} 显示格式`}
                              value={mode}
                              options={[
                                { value: "raw", label: "原值（不换算）" },
                                { value: "unit_scale", label: "人民币单位换算" },
                                { value: "suffix", label: "只添加显示后缀" },
                                { value: "percent", label: "百分比（选择原值编码）" },
                              ]}
                              placeholder="选择显示格式"
                              triggerClassName={selectTriggerClass}
                              ariaInvalid={Boolean(formatError)}
                              ariaDescribedBy={formatError ? formatErrorId : undefined}
                              onValueChange={setMode}
                            />
                          </label>

                          {mode === "percent" && (
                            <label className="mb-2 block">
                              <span className={fieldLabelClass}>原值百分比编码</span>
                              <ComposerSelect
                                id={`${controlIdPrefix}-format-encoding-${encodeURIComponent(field)}`}
                                ariaLabel={`${field} 百分比原值编码`}
                                value={typeof format.encoding === "string" ? format.encoding : ""}
                                options={[
                                  { value: "", label: "请选择原值编码" },
                                  {
                                    value: "ratio_0_1",
                                    label: "比率 0–1（0.12 显示为 12%）",
                                  },
                                  {
                                    value: "percent_0_100",
                                    label: "百分数 0–100（12 显示为 12%）",
                                  },
                                ]}
                                placeholder="请选择原值编码"
                                triggerClassName={selectTriggerClass}
                                onValueChange={(value) =>
                                  updateFormat(field, {
                                    mode: "percent",
                                    encoding: value,
                                    decimal_places: places,
                                  })
                                }
                              />
                            </label>
                          )}

                          {mode === "unit_scale" && (
                            <div className="grid grid-cols-2 gap-2">
                              {(["source_unit", "display_unit"] as const).map((unitKey) => (
                                <label key={unitKey}>
                                  <span className={fieldLabelClass}>
                                    {unitKey === "source_unit" ? "原值单位" : "显示单位"}
                                  </span>
                                  <ComposerSelect
                                    id={`${controlIdPrefix}-format-${unitKey}-${encodeURIComponent(field)}`}
                                    ariaLabel={`${field} ${unitKey === "source_unit" ? "原值单位" : "显示单位"}`}
                                    value={
                                      typeof format[unitKey] === "string"
                                        ? (format[unitKey] as string)
                                        : ""
                                    }
                                    options={[
                                      { value: "", label: "请选择单位" },
                                      ...cnyUnits.map((unit) => ({
                                        value: unit,
                                        label: unitLabels[unit],
                                      })),
                                    ]}
                                    placeholder="请选择单位"
                                    triggerClassName={selectTriggerClass}
                                    onValueChange={(value) =>
                                      updateFormat(field, {
                                        ...format,
                                        unit_family: "CNY",
                                        [unitKey]: value,
                                        decimal_places: places,
                                      })
                                    }
                                  />
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
                            <ComposerSelect
                              id={`${controlIdPrefix}-format-precision-${encodeURIComponent(field)}`}
                              ariaLabel={`${field} 小数位数`}
                              value={String(precision)}
                              options={[
                                { value: "auto", label: "自动" },
                                ...[0, 1, 2, 3, 4, 5, 6].map((number) => ({
                                  value: String(number),
                                  label: `${number} 位`,
                                })),
                              ]}
                              placeholder="选择小数位"
                              triggerClassName={selectTriggerClass}
                              onValueChange={(value) => {
                                const nextPlaces =
                                  value === "auto" ? "auto" : Number(value);
                                updateFormat(field, { ...format, decimal_places: nextPlaces });
                              }}
                            />
                          </label>
                          <RecordFieldError
                            id={formatErrorId}
                            error={formatError}
                          />
                        </fieldset>
                      );
                    })}
                  </div>
                  {validation.errors.format_by_field && (
                    <RecordFieldError
                      id={`${controlIdPrefix}-format-error`}
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
                <SqlDisclosure code={queryArtifact.sql} />
                <h5 className="mb-3 break-words text-sm font-semibold leading-5">{draft.title}</h5>
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
