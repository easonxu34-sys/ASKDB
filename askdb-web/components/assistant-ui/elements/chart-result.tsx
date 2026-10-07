"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useAui, useAuiState } from "@assistant-ui/react";
import { Maximize2Icon, Minimize2Icon, PencilLineIcon, RotateCcwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ComposerSelect } from "@/components/ui/composer-select";
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
  chartTypeLabel,
  CHART_COLOR_PALETTES,
  CHART_PALETTE_IDS,
  normalizeChartViewChange,
  type ChartColorDirection,
  type ChartColorSpec,
  type ChartPaletteId,
  type ChartSolidColorSpec,
  type ChartYAxisSide,
  getArrowFieldKind,
  getChartFieldCandidates,
  getChartMessageParts,
  summarizeChartViewChange,
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
import { commitThreadChartViewChange, undoThreadChartViewChange } from "@/lib/local-thread-adapter";
import { requestCsrfToken } from "@/lib/auth-api";
import {
  buildEChartsOption,
  chartCategoryKey,
  deriveChartRows,
  formatChartValue,
  getChartCompletenessState,
  getChartUnitWarning,
  isChartQueryTruncated,
  resolvePieCategoryLabel,
} from "@/lib/chart-output";
import ChartCanvas from "./chart-canvas";
import type { ChartCanvasHandle } from "./chart-canvas";
import { SqlDisclosure } from "./sql-disclosure";
import { calculateChartStatistics, type ChartViewport } from "@/lib/chart-statistics";
import { buildChartCsv, getChartCsvFilename } from "@/lib/chart-export";
import {
  buildChartSelectionContext,
  fillComposerWithChartSelection,
} from "@/lib/chart-interactions";

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
              onValueChange={(value) =>
                onChange({ ...color, direction: value as ChartColorDirection })
              }
            />
          )}
          <div
            aria-hidden="true"
            className="h-2.5 rounded-full border border-border/70"
            style={{
              backgroundImage: `linear-gradient(${gradientDirection}, ${color.start_hex}, ${color.end_hex})`,
              opacity: color.opacity / 100,
            }}
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

function RecordFieldError({ id, error }: { id: string; error?: string }) {
  if (!error) return null;
  return (
    <p id={id} role="alert" className="mt-1 text-xs leading-5 text-destructive">
      {error}
    </p>
  );
}

function getChartTypeLabel(type: ChartType) {
  return chartTypeLabel(type);
}

function getChartCanvasSourceKey(
  sourceResultId: string,
  threadId: string | undefined,
  view: Pick<
    ChartViewConfiguration,
    | "chart_type"
    | "dimension_field"
    | "sort"
    | "current_result_top_n"
    | "bar_stack_mode"
    | "step_position"
    | "scatter_fields"
    | "heatmap_fields"
  >,
) {
  const sort =
    view.sort.mode === "original"
      ? ["original"]
      : [view.sort.mode, view.sort.field, view.sort.direction];
  return JSON.stringify([
    threadId ?? "",
    sourceResultId,
    view.chart_type,
    view.dimension_field,
    sort,
    view.current_result_top_n ?? null,
    view.bar_stack_mode ?? null,
    view.step_position ?? null,
    view.scatter_fields ?? null,
    view.heatmap_fields ?? null,
  ]);
}

function createChartAnnotationId(prefix: string) {
  return `${prefix}_${globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)}`;
}

function chartCompletenessLabel(query: SuccessfulQueryArtifact) {
  const state = getChartCompletenessState(query);
  return state === "possibly_incomplete"
    ? "查询结果可能不完整"
    : state === "not_marked_truncated"
      ? "本次结果未标记截断"
      : "完整性未明确";
}

function chartCategoryDisplayLabel(value: unknown) {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "（空值）";
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
  const composerText = useAuiState((state) => state.thread.composer.text);
  const controlIdPrefix = `chart-${useId().replace(/:/g, "")}`;
  const cardRef = useRef<HTMLElement>(null);
  const [savedView, setSavedView] = useState(view);
  const [savedHasOverride, setSavedHasOverride] = useState(hasOverride);
  const [savedUndoHistory, setSavedUndoHistory] = useState(undoHistory);
  const [draft, setDraft] = useState<ChartViewDraft>(view as ChartViewDraft);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [persisting, setPersisting] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [displayNotice, setDisplayNotice] = useState(overrideNotice ?? "");
  const [fullScreen, setFullScreen] = useState(false);
  const [fullScreenError, setFullScreenError] = useState("");
  const [targetMetricField, setTargetMetricField] = useState("");
  const [targetValue, setTargetValue] = useState("");
  const [targetLabel, setTargetLabel] = useState("目标值");
  const [selectedSourceRowIndices, setSelectedSourceRowIndices] = useState<number[]>([]);
  const [selectionSourceKey, setSelectionSourceKey] = useState("");
  const [brushMode, setBrushMode] = useState(false);
  const [selectionDialogOpen, setSelectionDialogOpen] = useState(false);
  const [keyboardRowIndex, setKeyboardRowIndex] = useState("");
  const [savedViewportState, setSavedViewportState] = useState<
    ChartViewport & { sourceKey: string }
  >({ sourceKey: "", start: 0, end: 100 });
  const chartCanvasRef = useRef<ChartCanvasHandle>(null);
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
  const savedCanvasSourceKey = getChartCanvasSourceKey(
    artifact.source_result_id,
    threadId,
    savedView,
  );
  const savedViewport =
    savedViewportState.sourceKey === savedCanvasSourceKey
      ? { start: savedViewportState.start, end: savedViewportState.end }
      : { start: 0, end: 100 };
  const savedOption = useMemo(
    () => buildEChartsOption(artifact, queryArtifact, savedView, savedViewport),
    [artifact, queryArtifact, savedView, savedViewport.start, savedViewport.end],
  );
  const savedRows = useMemo(
    () => deriveChartRows(queryArtifact, savedView),
    [queryArtifact, savedView],
  );
  const previewOption = useMemo(
    () => buildEChartsOption(artifact, queryArtifact, draft),
    [artifact, queryArtifact, draft],
  );
  const previewRows = useMemo(() => deriveChartRows(queryArtifact, draft), [queryArtifact, draft]);
  const previewCanvasSourceKey = getChartCanvasSourceKey(
    artifact.source_result_id,
    threadId,
    draft,
  );
  const visibleSelection =
    selectionSourceKey === savedCanvasSourceKey ? selectedSourceRowIndices : [];
  const returnedStatistics = useMemo(
    () => calculateChartStatistics(queryArtifact, savedView, "returned_rows"),
    [queryArtifact, savedView],
  );
  const viewportStatistics = useMemo(
    () => calculateChartStatistics(queryArtifact, savedView, "viewport", savedViewport, savedRows),
    [queryArtifact, savedView, savedViewport.start, savedViewport.end, savedRows],
  );
  const unitWarning = getChartUnitWarning(savedView);
  const pieValidation = validateChartView(
    {
      ...draft,
      chart_type: "pie",
      dimension_field:
        getArrowFieldKind(
          queryArtifact.columnTypes?.[queryArtifact.columns.indexOf(draft.dimension_field)] ?? "",
        ) === "categorical"
          ? draft.dimension_field
          : (fieldCandidates.find(({ kind }) => kind === "categorical")?.name ?? ""),
      metric_fields: draft.metric_fields
        .filter((field) =>
          fieldCandidates.some(
            (candidate) => candidate.name === field && candidate.kind === "numeric",
          ),
        )
        .slice(0, 1),
      hidden_metric_fields: [],
      sort: { mode: "original" },
      color_by_metric: undefined,
      pie_category_colors: undefined,
      current_result_top_n: undefined,
      bar_orientation: undefined,
      bar_stack_mode: undefined,
      y_axis_by_metric: undefined,
      y_axis_names: undefined,
      step_position: undefined,
      scatter_fields: undefined,
      heatmap_fields: undefined,
      annotations: { reference_lines: [], reference_areas: [] },
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
  const supportsMultipleYAxis =
    draft.chart_type === "line" ||
    draft.chart_type === "area" ||
    draft.chart_type === "step_line" ||
    (draft.chart_type === "bar" &&
      draft.bar_orientation !== "horizontal" &&
      (draft.bar_stack_mode ?? "grouped") === "grouped");
  const supportsYAxisNames =
    draft.chart_type === "line" ||
    draft.chart_type === "area" ||
    draft.chart_type === "step_line" ||
    (draft.chart_type === "bar" && draft.bar_orientation !== "horizontal");
  const hasVisibleRightYAxis = visibleMetrics.some(
    (field) => draft.y_axis_by_metric?.[field] === "right",
  );
  const editableYAxisSides: ChartYAxisSide[] = hasVisibleRightYAxis ? ["left", "right"] : ["left"];
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
          label: resolution.status === "ambiguous" ? `${label} · ${typeof value}` : label,
        },
      ];
    });
  }, [queryArtifact, draft.dimension_field]);
  const activeFields = [
    ...new Set(
      [
        draft.dimension_field,
        ...(draft.chart_type === "heatmap" && draft.heatmap_fields
          ? [draft.heatmap_fields.y_field]
          : []),
        ...selectedMetrics,
      ].filter(Boolean),
    ),
  ];

  useEffect(() => {
    const onFullScreenChange = () => setFullScreen(document.fullscreenElement === cardRef.current);
    document.addEventListener("fullscreenchange", onFullScreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullScreenChange);
  }, []);

  useEffect(() => {
    setSelectedSourceRowIndices([]);
    setSelectionSourceKey(savedCanvasSourceKey);
    setBrushMode(false);
    setSelectionDialogOpen(false);
    setKeyboardRowIndex("");
  }, [savedCanvasSourceKey]);

  useEffect(() => {
    if (!brushMode) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      const axis =
        savedView.chart_type === "bar" && savedView.bar_orientation === "horizontal" ? "y" : "x";
      chartCanvasRef.current?.setBrushMode(false, axis);
      setBrushMode(false);
      setDisplayNotice("已退出框选模式。");
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [brushMode, savedView.chart_type, savedView.bar_orientation]);

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
    setDraft((current) => normalizeChartViewChange(current, update(current), queryArtifact));
    setSaveError("");
  }

  function updateStatisticAnnotation(
    metricField: string,
    kind: "mean" | "peak",
    patch: { enabled?: boolean; scope?: "returned_rows" | "viewport" },
  ) {
    updateDraft((current) => {
      const annotations = current.annotations ?? { reference_lines: [], reference_areas: [] };
      const existing = annotations.reference_lines.find(
        (line) => line.metric_field === metricField && line.kind === kind,
      );
      let referenceLines = annotations.reference_lines.filter(
        (line) => !(line.metric_field === metricField && line.kind === kind),
      );
      if (patch.enabled === false)
        return { ...current, annotations: { ...annotations, reference_lines: referenceLines } };
      if (existing || patch.enabled) {
        referenceLines = [
          ...referenceLines,
          {
            id: existing?.id ?? createChartAnnotationId(kind),
            metric_field: metricField,
            kind,
            scope: patch.scope ?? existing?.scope ?? "returned_rows",
            label: existing?.label ?? (kind === "mean" ? "均值" : "峰值"),
          },
        ];
      }
      return { ...current, annotations: { ...annotations, reference_lines: referenceLines } };
    });
  }

  function saveTargetAnnotation() {
    const metricField = visibleMetrics.includes(targetMetricField)
      ? targetMetricField
      : visibleMetrics[0];
    if (
      !metricField ||
      !targetValue.trim() ||
      !/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/u.test(targetValue.trim())
    )
      return;
    updateDraft((current) => {
      const annotations = current.annotations ?? { reference_lines: [], reference_areas: [] };
      const existing = annotations.reference_lines.find(
        (line) => line.metric_field === metricField && line.kind === "value",
      );
      const line = {
        id: existing?.id ?? createChartAnnotationId("target"),
        metric_field: metricField,
        kind: "value" as const,
        value: targetValue.trim(),
        scope: existing?.scope ?? ("returned_rows" as const),
        label: targetLabel.trim().slice(0, 80) || "目标值",
      };
      return {
        ...current,
        annotations: {
          ...annotations,
          reference_lines: [
            ...annotations.reference_lines.filter(
              (item) => !(item.metric_field === metricField && item.kind === "value"),
            ),
            line,
          ],
        },
      };
    });
  }

  function updateFormat(field: string, format: unknown) {
    updateDraft((current) => ({
      ...current,
      format_by_field: { ...current.format_by_field, [field]: format },
    }));
  }

  function selectChartType(nextType: ChartType) {
    updateDraft((current) => {
      const numericFields = fieldCandidates
        .filter(({ kind }) => kind === "numeric")
        .map(({ name }) => name);
      const dimensionFields = fieldCandidates
        .filter(({ kind }) => kind === "categorical" || kind === "temporal")
        .map(({ name }) => name);
      if (nextType === "scatter") {
        const previous = current.scatter_fields;
        const xField =
          previous?.x_field && numericFields.includes(previous.x_field)
            ? previous.x_field
            : numericFields.includes(current.dimension_field)
              ? current.dimension_field
              : (numericFields[0] ?? "");
        const yField =
          previous?.y_field &&
          numericFields.includes(previous.y_field) &&
          previous.y_field !== xField
            ? previous.y_field
            : (numericFields.find((field) => field !== xField) ?? "");
        const visualField =
          previous?.visual_field &&
          numericFields.includes(previous.visual_field) &&
          previous.visual_field !== xField &&
          previous.visual_field !== yField
            ? previous.visual_field
            : undefined;
        return {
          ...current,
          chart_type: nextType,
          dimension_field: xField,
          metric_fields: yField ? [yField, ...(visualField ? [visualField] : [])] : [],
          hidden_metric_fields: [],
          sort: { mode: "original" },
          scatter_fields: {
            x_field: xField,
            y_field: yField,
            ...(visualField
              ? {
                  visual_field: visualField,
                  visual_encoding: previous?.visual_encoding === "color" ? "color" : "size",
                }
              : {}),
          },
        };
      }
      if (nextType === "heatmap") {
        const previous = current.heatmap_fields;
        const xField =
          previous?.x_field && dimensionFields.includes(previous.x_field)
            ? previous.x_field
            : dimensionFields.includes(current.dimension_field)
              ? current.dimension_field
              : (dimensionFields[0] ?? "");
        const yField =
          previous?.y_field &&
          dimensionFields.includes(previous.y_field) &&
          previous.y_field !== xField
            ? previous.y_field
            : (dimensionFields.find((field) => field !== xField) ?? "");
        const valueField =
          previous?.value_field && numericFields.includes(previous.value_field)
            ? previous.value_field
            : (numericFields[0] ?? "");
        return {
          ...current,
          chart_type: nextType,
          dimension_field: xField,
          metric_fields: valueField ? [valueField] : [],
          hidden_metric_fields: [],
          sort: { mode: "original" },
          heatmap_fields: {
            x_field: xField,
            y_field: yField,
            value_field: valueField,
            aggregation: previous?.aggregation ?? "none",
          },
        };
      }
      const dimensionField = dimensionFields.includes(current.dimension_field)
        ? current.dimension_field
        : (dimensionFields[0] ?? "");
      const metricFields =
        nextType === "pie"
          ? current.metric_fields.filter((field) => numericFields.includes(field)).slice(0, 1)
          : current.metric_fields.filter((field) => numericFields.includes(field));
      const sort =
        nextType === "step_line" &&
        getArrowFieldKind(
          queryArtifact.columnTypes?.[queryArtifact.columns.indexOf(dimensionField)] ?? "",
        ) === "temporal"
          ? { mode: "dimension" as const, field: dimensionField, direction: "asc" as const }
          : nextType === "line" || nextType === "area"
            ? current.sort
            : { mode: "original" as const };
      return {
        ...current,
        chart_type: nextType,
        dimension_field: dimensionField,
        metric_fields: metricFields,
        hidden_metric_fields: current.hidden_metric_fields.filter((field) =>
          metricFields.includes(field),
        ),
        sort,
        ...(nextType === "bar" ? { bar_stack_mode: current.bar_stack_mode ?? "grouped" } : {}),
        ...(nextType === "step_line" ? { step_position: current.step_position ?? "end" } : {}),
      };
    });
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

  function selectMetricYAxis(field: string, side: "left" | "right") {
    updateDraft((current) => ({
      ...current,
      y_axis_by_metric: {
        ...current.y_axis_by_metric,
        [field]: side,
      },
    }));
  }

  function setYAxisName(side: ChartYAxisSide, patch: { visible?: boolean; text?: string }) {
    updateDraft((current) => ({
      ...current,
      y_axis_names: {
        ...current.y_axis_names,
        [side]: { ...current.y_axis_names?.[side], ...patch },
      },
    }));
  }

  function selectScatterField(role: "x_field" | "y_field" | "visual_field", field: string) {
    updateDraft((current) => {
      const numericFields = fieldCandidates
        .filter(({ kind }) => kind === "numeric")
        .map(({ name }) => name);
      const previous = current.scatter_fields ?? { x_field: "", y_field: "" };
      let xField = role === "x_field" ? field : previous.x_field;
      let yField = role === "y_field" ? field : previous.y_field;
      if (role === "x_field" && xField === yField) {
        yField = numericFields.find((candidate) => candidate !== xField) ?? "";
      }
      if (role === "y_field" && yField === xField) {
        xField = numericFields.find((candidate) => candidate !== yField) ?? "";
      }
      const visualField =
        role === "visual_field"
          ? field && field !== xField && field !== yField
            ? field
            : undefined
          : previous.visual_field &&
              previous.visual_field !== xField &&
              previous.visual_field !== yField
            ? previous.visual_field
            : undefined;
      return {
        ...current,
        dimension_field: xField,
        metric_fields: yField ? [yField, ...(visualField ? [visualField] : [])] : [],
        hidden_metric_fields: [],
        sort: { mode: "original" },
        scatter_fields: {
          x_field: xField,
          y_field: yField,
          ...(visualField
            ? {
                visual_field: visualField,
                visual_encoding: previous.visual_encoding ?? "size",
              }
            : {}),
        },
      };
    });
  }

  function selectHeatmapField(role: "x_field" | "y_field" | "value_field", field: string) {
    updateDraft((current) => {
      const dimensions = fieldCandidates
        .filter(({ kind }) => kind === "categorical" || kind === "temporal")
        .map(({ name }) => name);
      const previous = current.heatmap_fields ?? {
        x_field: "",
        y_field: "",
        value_field: "",
        aggregation: "none" as const,
      };
      let xField = role === "x_field" ? field : previous.x_field;
      let yField = role === "y_field" ? field : previous.y_field;
      if (role === "x_field" && xField === yField) {
        yField = dimensions.find((candidate) => candidate !== xField) ?? "";
      }
      if (role === "y_field" && yField === xField) {
        xField = dimensions.find((candidate) => candidate !== yField) ?? "";
      }
      const valueField = role === "value_field" ? field : previous.value_field;
      return {
        ...current,
        dimension_field: xField,
        metric_fields: valueField ? [valueField] : [],
        hidden_metric_fields: [],
        sort: { mode: "original" },
        heatmap_fields: { ...previous, x_field: xField, y_field: yField, value_field: valueField },
      };
    });
  }

  function updateMetricColor(field: string, color?: ChartColorSpec) {
    updateDraft((current) => {
      const colors = { ...(current.color_by_metric ?? {}) };
      if (color) colors[field] = color;
      else delete colors[field];
      return {
        ...current,
        ...(Object.keys(colors).length
          ? { color_by_metric: colors }
          : { color_by_metric: undefined }),
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
    const nextColor =
      mode === "solid"
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

  async function applyDraft() {
    const result = validateChartView(draft, queryArtifact);
    if (!result.view) {
      setSaveError("");
      return;
    }
    const summary = summarizeChartViewChange(savedView, result.view);
    if (await persistView(result.view, "manual", summary)) setDialogOpen(false);
  }

  async function restoreRecommendation() {
    if (await persistView(recommendedView, "restore_recommendation", "恢复 AI 推荐配置")) {
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

  function downloadChartPng() {
    const succeeded = chartCanvasRef.current?.downloadImage(
      `askdb-chart-${queryArtifact.rows.length}-rows.png`,
    );
    setDisplayNotice(
      succeeded ? "PNG 图表快照已下载。" : "PNG 导出暂不可用，图表实例尚未就绪或浏览器拒绝了下载。",
    );
  }

  function downloadChartCsv() {
    const result = buildChartCsv(queryArtifact);
    if (!result.ok) {
      setDisplayNotice(
        result.reason === "duplicate_columns"
          ? "CSV 导出不可用：查询结果含重复列名，无法无损生成表头。"
          : result.reason === "export_limit"
            ? "CSV 导出不可用：结果超出安全导出上限。"
            : "CSV 导出不可用：查询结果结构无效。",
      );
      return;
    }
    let url: string | undefined;
    try {
      const blob = new Blob([result.content], { type: "text/csv;charset=utf-8" });
      url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = getChartCsvFilename(
        result.rowCount,
        getChartCompletenessState(queryArtifact),
      );
      link.click();
      window.setTimeout(() => url && URL.revokeObjectURL(url), 0);
      setDisplayNotice(
        `CSV 已导出 ${result.rowCount} 行、${result.columnCount} 列；${chartCompletenessLabel(queryArtifact)}。`,
      );
    } catch {
      if (url) URL.revokeObjectURL(url);
      setDisplayNotice("CSV 文件无法下载，请重试。");
    }
  }

  function normalizeSelection(indices: number[]) {
    const displayPosition = new Map(
      savedRows.map(({ sourceRowIndex }, position) => [sourceRowIndex, position]),
    );
    return [...new Set(indices)].sort(
      (left, right) =>
        (displayPosition.get(left) ?? Number.MAX_SAFE_INTEGER) -
        (displayPosition.get(right) ?? Number.MAX_SAFE_INTEGER),
    );
  }

  function selectSourceRows(indices: number[]) {
    const normalized = normalizeSelection(indices);
    setSelectionSourceKey(savedCanvasSourceKey);
    setSelectedSourceRowIndices(normalized);
    if (normalized.length > 50) {
      setDisplayNotice(`已选 ${normalized.length} 行；追问最多引用 50 行，请缩小选区后再追问。`);
    } else {
      setDisplayNotice(`已选中 ${normalized.length} 行查询结果，可查看行值或填入追问。`);
    }
  }

  function toggleBrushSelection(enabled: boolean) {
    const axis =
      savedView.chart_type === "bar" && savedView.bar_orientation === "horizontal" ? "y" : "x";
    if (enabled) chartCanvasRef.current?.clearBrushSelection();
    const changed = chartCanvasRef.current?.setBrushMode(enabled, axis);
    if (!changed) {
      setDisplayNotice("图表尚未就绪，暂时无法切换框选模式。");
      return;
    }
    setBrushMode(enabled);
    setDisplayNotice(
      enabled
        ? "框选模式已开启；拖动选择同一维度上的连续分类，按 Escape 退出。"
        : "框选模式已关闭。",
    );
  }

  function clearSelection() {
    setSelectedSourceRowIndices([]);
    setSelectionSourceKey(savedCanvasSourceKey);
    chartCanvasRef.current?.clearBrushSelection();
    setDisplayNotice("已清除临时选区。");
  }

  async function saveSelectionAsFocusArea() {
    if (!canEdit || visibleSelection.length === 0 || visibleSelection.length > 1000) return;
    const annotations = savedView.annotations ?? { reference_lines: [], reference_areas: [] };
    if (annotations.reference_areas.length >= 32) {
      setDisplayNotice("已达到 32 个重点区间的保存上限。");
      return;
    }
    const nextView: ChartViewConfiguration = {
      ...savedView,
      annotations: {
        ...annotations,
        reference_areas: [
          ...annotations.reference_areas,
          {
            id: createChartAnnotationId("area"),
            source_row_indices: [...visibleSelection],
            label: `关注区间（${visibleSelection.length} 行）`,
          },
        ],
      },
    };
    if (await persistView(nextView)) setDisplayNotice("已将当前选区保存为图表重点区间。");
  }

  function fillComposerWithSelection() {
    const context = buildChartSelectionContext(
      queryArtifact,
      artifact.source_result_id,
      savedView,
      visibleSelection,
      chartCompletenessLabel(queryArtifact),
    );
    if (!context.ok) {
      const message =
        context.reason === "stale_result"
          ? "图表查询结果已变化，请重新选择数据。"
          : context.reason === "too_many_rows"
            ? "追问最多引用 50 行，请缩小选区。"
            : context.reason === "cell_too_long"
              ? "选中值过长，请缩小选区或改为点选需要的行。"
              : context.reason === "context_too_long"
                ? "选区上下文过长，请缩小选区。"
                : "当前选区无效，请重新选择。";
      setDisplayNotice(message);
      return;
    }
    const filled = fillComposerWithChartSelection(
      { setText: (text) => aui.thread.composer().setText(text) },
      composerText,
      context.text,
    );
    if (!filled.ok) {
      setDisplayNotice("现有聊天草稿与选区上下文过长，请先精简草稿或缩小选区。");
      return;
    }
    setDisplayNotice("已把选区摘要填入可编辑的聊天输入框；检查或修改后，请手动点击发送。");
  }

  const displayedTypeLabel = getChartTypeLabel(savedView.chart_type);
  const supportsAxisInteraction = new Set<string>(["line", "area", "step_line", "bar"]).has(
    savedView.chart_type,
  );
  const supportsReferenceAnnotations =
    new Set<string>(["line", "area", "step_line", "bar"]).has(draft.chart_type) &&
    !(draft.chart_type === "bar" && draft.bar_stack_mode === "percent");
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
            {supportsAxisInteraction && (
              <Button
                type="button"
                variant={brushMode ? "secondary" : "ghost"}
                size="sm"
                aria-label={brushMode ? "退出框选模式" : "进入框选模式"}
                aria-pressed={brushMode}
                onClick={() => toggleBrushSelection(!brushMode)}
              >
                {brushMode ? "退出框选" : "框选数据"}
              </Button>
            )}
            {supportsAxisInteraction && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label="重置图表缩放"
                onClick={() => chartCanvasRef.current?.resetZoom()}
              >
                <RotateCcwIcon aria-hidden="true" />
                <span>重置缩放</span>
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label="导出图表 PNG 图片"
              onClick={downloadChartPng}
            >
              导出 PNG
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label="导出查询结果 CSV"
              onClick={downloadChartCsv}
            >
              导出 CSV
            </Button>
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
        {savedView.chart_type === "bar" && savedView.bar_stack_mode === "percent" && (
          <p className="mb-2 text-[11px] text-muted-foreground">
            比例分母为每个维度项中当前可见指标的合计；仅在本轮已返回行内计算。
          </p>
        )}
        {savedView.chart_type === "heatmap" && savedView.heatmap_fields && (
          <p className="mb-2 text-[11px] text-muted-foreground">
            热力图
            {savedView.heatmap_fields.aggregation === "none"
              ? "按唯一维度组合显示单行值"
              : `按本轮已返回行${{ sum: "求和", avg: "求平均值", min: "取最小值", max: "取最大值", none: "" }[savedView.heatmap_fields.aggregation]}聚合`}
            。空值保持为空，不会转换为 0。
          </p>
        )}
        <p className="mb-2 text-[11px] text-muted-foreground">
          CSV 基于本轮已返回的 {queryArtifact.rows.length} 行 ·{" "}
          {chartCompletenessLabel(queryArtifact)}
        </p>
        <ChartCanvas
          ref={chartCanvasRef}
          option={savedOption}
          ariaLabel={`${savedView.title}，${displayedTypeLabel}`}
          className={`w-full min-w-0 ${fullScreen ? "h-[calc(100dvh-8rem)]" : "h-[280px]"}`}
          sourceKey={savedCanvasSourceKey}
          sourceRows={savedRows}
          interactive
          onPointClick={(sourceRowIndexes) => {
            selectSourceRows(sourceRowIndexes);
            setSelectionDialogOpen(true);
          }}
          onBrushSelection={selectSourceRows}
          onDataZoom={(nextViewport) =>
            setSavedViewportState((current) =>
              current.sourceKey === savedCanvasSourceKey &&
              current.start === nextViewport.start &&
              current.end === nextViewport.end
                ? current
                : { sourceKey: savedCanvasSourceKey, ...nextViewport },
            )
          }
        />
        <div className="mt-2 space-y-1 text-[11px] text-muted-foreground" aria-label="图表统计范围">
          <p>
            展示 {savedRows.length}
            {savedView.chart_type === "heatmap" ? " 个网格单元格" : " 行"} ·{" "}
            {chartCompletenessLabel(queryArtifact)}
          </p>
          {savedView.chart_type !== "pie" && (
            <>
              <p>
                已返回行统计：
                {returnedStatistics
                  .map(
                    (statistic) =>
                      `${savedView.field_labels[statistic.metric_field]?.trim() || statistic.metric_field} 均值 ${statistic.mean === undefined ? "无有效值" : formatChartValue(statistic.mean, savedView.format_by_field[statistic.metric_field])}，峰值 ${statistic.peak === undefined ? "无有效值" : formatChartValue(statistic.peak, savedView.format_by_field[statistic.metric_field])}（${statistic.validCount} 个有效值）`,
                  )
                  .join("；")}
              </p>
              <p>
                当前窗口 {Math.round(savedViewport.start)}%–{Math.round(savedViewport.end)}% 统计：
                {viewportStatistics
                  .map(
                    (statistic) =>
                      `${savedView.field_labels[statistic.metric_field]?.trim() || statistic.metric_field} 均值 ${statistic.mean === undefined ? "无有效值" : formatChartValue(statistic.mean, savedView.format_by_field[statistic.metric_field])}，峰值 ${statistic.peak === undefined ? "无有效值" : formatChartValue(statistic.peak, savedView.format_by_field[statistic.metric_field])}（${statistic.validCount} 个有效值）`,
                  )
                  .join("；")}
              </p>
            </>
          )}
        </div>
        <div className="mt-2 max-w-md">
          <label className="block">
            <span className={fieldLabelClass}>键盘选择查询结果行</span>
            <ComposerSelect
              id={`${controlIdPrefix}-keyboard-row`}
              ariaLabel="键盘选择查询结果行"
              value={keyboardRowIndex}
              options={savedRows.map(({ row, sourceRowIndex }) => ({
                value: String(sourceRowIndex),
                label: `${chartCategoryDisplayLabel(row[savedView.dimension_field])}${savedView.chart_type === "heatmap" && savedView.heatmap_fields ? ` × ${chartCategoryDisplayLabel(row[savedView.heatmap_fields.y_field])}` : ""} · 来源行 ${sourceRowIndex + 1}`,
              }))}
              placeholder={
                savedView.chart_type === "heatmap"
                  ? "选择网格单元并查看来源行"
                  : "选择图表点并查看查询结果行"
              }
              triggerClassName="max-w-md"
              onValueChange={(value) => {
                const sourceRowIndex = Number(value);
                if (!Number.isSafeInteger(sourceRowIndex)) return;
                setKeyboardRowIndex(value);
                const selectedRow = savedRows.find(
                  (entry) => entry.sourceRowIndex === sourceRowIndex,
                );
                selectSourceRows(selectedRow?.sourceRowIndexes ?? [sourceRowIndex]);
                setSelectionDialogOpen(true);
              }}
            />
          </label>
        </div>
        {visibleSelection.length > 0 && (
          <section
            className="mt-3 rounded-lg border border-primary/30 bg-primary/5 p-3"
            aria-label="图表临时选区"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-medium">
                已选中 {visibleSelection.length} 行查询结果
                {visibleSelection.length > 50
                  ? " · 追问上限为 50 行，请缩小选区"
                  : ` · 来自已返回行，${chartCompletenessLabel(queryArtifact)}`}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setSelectionDialogOpen(true)}
                >
                  查看查询结果行
                </Button>
                {canEdit &&
                  supportsAxisInteraction &&
                  !(savedView.chart_type === "bar" && savedView.bar_stack_mode === "percent") && (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={persisting || visibleSelection.length > 1000}
                      onClick={() => void saveSelectionAsFocusArea()}
                    >
                      保存为重点区间
                    </Button>
                  )}
                <Button
                  type="button"
                  size="sm"
                  disabled={visibleSelection.length > 50}
                  onClick={fillComposerWithSelection}
                >
                  填入聊天追问
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={clearSelection}>
                  清除选区
                </Button>
              </div>
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              图表维度范围：
              {String(
                queryArtifact.rows[visibleSelection[0]]?.[savedView.dimension_field] ?? "（空值）",
              )}{" "}
              至{" "}
              {String(
                queryArtifact.rows[visibleSelection[visibleSelection.length - 1]]?.[
                  savedView.dimension_field
                ] ?? "（空值）",
              )}
            </p>
          </section>
        )}
        {saveError && (
          <p role="alert" className="mt-2 text-xs leading-5 text-destructive">
            {saveError}
          </p>
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

      <Dialog open={selectionDialogOpen} onOpenChange={setSelectionDialogOpen}>
        <DialogContent className="max-h-[85dvh] max-w-4xl overflow-hidden">
          <DialogHeader>
            <DialogTitle>查询结果行</DialogTitle>
            <DialogDescription>
              以下是当前图表所绑定查询结果中的 {visibleSelection.length}{" "}
              行；它们是查询返回行，不代表数据库明细记录。
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[60dvh] overflow-auto rounded-md border border-border/70">
            <table className="w-full border-collapse text-xs">
              <thead className="sticky top-0 bg-muted">
                <tr>
                  <th className="border-b border-border/70 px-2 py-2 text-left">来源行</th>
                  {queryArtifact.columns.map((column, index) => (
                    <th
                      key={`${column}-${index}`}
                      className="border-b border-border/70 px-2 py-2 text-left"
                    >
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleSelection.map((sourceRowIndex) => (
                  <tr key={sourceRowIndex}>
                    <td className="border-b border-border/50 px-2 py-2">{sourceRowIndex + 1}</td>
                    {queryArtifact.columns.map((column, index) => {
                      const value = queryArtifact.rows[sourceRowIndex]?.[column];
                      const text =
                        value === null || value === undefined
                          ? "（空值）"
                          : typeof value === "string" ||
                              typeof value === "number" ||
                              typeof value === "boolean"
                            ? String(value)
                            : JSON.stringify(value);
                      return (
                        <td
                          key={`${column}-${index}`}
                          className="max-w-64 break-words border-b border-border/50 px-2 py-2"
                        >
                          {text}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <DialogFooter>
            <DialogClose render={<Button type="button" variant="outline" />}>关闭</DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
                    {(
                      [
                        "line",
                        "area",
                        "step_line",
                        "bar",
                        "pie",
                        "scatter",
                        "heatmap",
                      ] as ChartType[]
                    ).map((type) => {
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
                  {draft.chart_type === "scatter" ? (
                    <div className="space-y-3">
                      {(["x_field", "y_field"] as const).map((role) => (
                        <label key={role} className="block">
                          <span className={fieldLabelClass}>
                            {role === "x_field" ? "X 数值字段" : "Y 数值字段"}
                          </span>
                          <ComposerSelect
                            id={`${controlIdPrefix}-scatter-${role}`}
                            ariaLabel={
                              role === "x_field" ? "散点图 X 数值字段" : "散点图 Y 数值字段"
                            }
                            value={draft.scatter_fields?.[role] ?? ""}
                            options={fieldCandidates
                              .filter(({ kind }) => kind === "numeric")
                              .map(({ name }) => ({ value: name, label: name }))}
                            placeholder="选择数值字段"
                            triggerClassName={selectTriggerClass}
                            onValueChange={(field) => selectScatterField(role, field)}
                          />
                        </label>
                      ))}
                      <label className="block">
                        <span className={fieldLabelClass}>第三数值字段（可选）</span>
                        <ComposerSelect
                          id={`${controlIdPrefix}-scatter-visual-field`}
                          ariaLabel="散点图第三数值字段"
                          value={draft.scatter_fields?.visual_field ?? "none"}
                          options={[
                            { value: "none", label: "不映射" },
                            ...fieldCandidates
                              .filter(
                                ({ kind, name }) =>
                                  kind === "numeric" &&
                                  name !== draft.scatter_fields?.x_field &&
                                  name !== draft.scatter_fields?.y_field,
                              )
                              .map(({ name }) => ({ value: name, label: name })),
                          ]}
                          placeholder="选择映射字段"
                          triggerClassName={selectTriggerClass}
                          onValueChange={(field) =>
                            selectScatterField("visual_field", field === "none" ? "" : field)
                          }
                        />
                      </label>
                      {draft.scatter_fields?.visual_field && (
                        <label className="block">
                          <span className={fieldLabelClass}>第三字段映射方式</span>
                          <ComposerSelect
                            id={`${controlIdPrefix}-scatter-visual-encoding`}
                            ariaLabel="散点图第三字段映射方式"
                            value={draft.scatter_fields.visual_encoding ?? "size"}
                            options={[
                              { value: "size", label: "气泡大小" },
                              { value: "color", label: "颜色深浅" },
                            ]}
                            placeholder="选择映射方式"
                            triggerClassName={selectTriggerClass}
                            onValueChange={(value) =>
                              updateDraft((current) => ({
                                ...current,
                                scatter_fields: current.scatter_fields
                                  ? {
                                      ...current.scatter_fields,
                                      visual_encoding: value as "size" | "color",
                                    }
                                  : undefined,
                              }))
                            }
                          />
                        </label>
                      )}
                      <RecordFieldError
                        id={`${controlIdPrefix}-scatter-error`}
                        error={validation.errors.scatter_fields}
                      />
                      <RecordFieldError
                        id="chart-metrics-error"
                        error={validation.errors.metric_fields}
                      />
                      <p className="text-[11px] leading-4 text-muted-foreground">
                        每个点对应一条本轮已返回结果；无效的 X、Y 数值不绘制。
                      </p>
                    </div>
                  ) : draft.chart_type === "heatmap" ? (
                    <div className="space-y-3">
                      {(["x_field", "y_field"] as const).map((role) => (
                        <label key={role} className="block">
                          <span className={fieldLabelClass}>
                            {role === "x_field" ? "横轴维度" : "纵轴维度"}
                          </span>
                          <ComposerSelect
                            id={`${controlIdPrefix}-heatmap-${role}`}
                            ariaLabel={role === "x_field" ? "热力图横轴维度" : "热力图纵轴维度"}
                            value={draft.heatmap_fields?.[role] ?? ""}
                            options={fieldCandidates
                              .filter(
                                ({ kind, name }) =>
                                  (kind === "categorical" || kind === "temporal") &&
                                  (role === "x_field" || name !== draft.heatmap_fields?.x_field),
                              )
                              .map(({ name, kind }) => ({
                                value: name,
                                label: `${name}${kind === "temporal" ? " · 日期/时间" : " · 分类"}`,
                              }))}
                            placeholder="选择分类或时间字段"
                            triggerClassName={selectTriggerClass}
                            onValueChange={(field) => selectHeatmapField(role, field)}
                          />
                        </label>
                      ))}
                      <label className="block">
                        <span className={fieldLabelClass}>颜色数值</span>
                        <ComposerSelect
                          id={`${controlIdPrefix}-heatmap-value-field`}
                          ariaLabel="热力图数值指标"
                          value={draft.heatmap_fields?.value_field ?? ""}
                          options={fieldCandidates
                            .filter(({ kind }) => kind === "numeric")
                            .map(({ name }) => ({ value: name, label: name }))}
                          placeholder="选择数值指标"
                          triggerClassName={selectTriggerClass}
                          onValueChange={(field) => selectHeatmapField("value_field", field)}
                        />
                      </label>
                      <label className="block">
                        <span className={fieldLabelClass}>重复单元格处理</span>
                        <ComposerSelect
                          id={`${controlIdPrefix}-heatmap-aggregation`}
                          ariaLabel="热力图重复单元格处理"
                          value={draft.heatmap_fields?.aggregation ?? "none"}
                          options={[
                            { value: "none", label: "不聚合（要求维度组合唯一）" },
                            { value: "sum", label: "求和" },
                            { value: "avg", label: "平均值" },
                            { value: "min", label: "最小值" },
                            { value: "max", label: "最大值" },
                          ]}
                          placeholder="选择聚合方式"
                          triggerClassName={selectTriggerClass}
                          onValueChange={(aggregation) =>
                            updateDraft((current) => ({
                              ...current,
                              heatmap_fields: current.heatmap_fields
                                ? {
                                    ...current.heatmap_fields,
                                    aggregation: aggregation as NonNullable<
                                      ChartViewConfiguration["heatmap_fields"]
                                    >["aggregation"],
                                  }
                                : undefined,
                            }))
                          }
                        />
                      </label>
                      <RecordFieldError
                        id={`${controlIdPrefix}-heatmap-error`}
                        error={validation.errors.heatmap_fields}
                      />
                      <RecordFieldError
                        id="chart-metrics-error"
                        error={validation.errors.metric_fields}
                      />
                      <p className="text-[11px] leading-4 text-muted-foreground">
                        默认要求横纵维度组合唯一；聚合只使用本轮已返回行，空值不会改成 0。
                      </p>
                    </div>
                  ) : (
                    <>
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
                                <label
                                  key={name}
                                  className="flex min-h-8 items-center gap-2 text-xs"
                                >
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
                                <label
                                  key={field}
                                  className="inline-flex items-center gap-2 text-xs"
                                >
                                  <input
                                    type="checkbox"
                                    checked={visible}
                                    disabled={visible && visibleMetrics.length <= 1}
                                    onChange={(event) => {
                                      const checked = event.currentTarget.checked;
                                      updateDraft((current) => {
                                        const hiddenMetricFields = checked
                                          ? current.hidden_metric_fields.filter(
                                              (item) => item !== field,
                                            )
                                          : [...current.hidden_metric_fields, field];
                                        const yAxisByMetric = {
                                          ...current.y_axis_by_metric,
                                        };
                                        const nextVisibleMetrics = current.metric_fields.filter(
                                          (item) => !hiddenMetricFields.includes(item),
                                        );
                                        const hasVisibleRightMetric = nextVisibleMetrics.some(
                                          (item) => yAxisByMetric[item] === "right",
                                        );
                                        const hasVisibleLeftMetric = nextVisibleMetrics.some(
                                          (item) => yAxisByMetric[item] !== "right",
                                        );
                                        if (hasVisibleRightMetric && !hasVisibleLeftMetric) {
                                          for (const item of nextVisibleMetrics) {
                                            yAxisByMetric[item] = "left";
                                          }
                                        }
                                        return {
                                          ...current,
                                          hidden_metric_fields: hiddenMetricFields,
                                          y_axis_by_metric: yAxisByMetric,
                                        };
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
                      {supportsMultipleYAxis && selectedMetrics.length > 1 && (
                        <fieldset className="mt-3">
                          <legend className={fieldLabelClass}>Y 轴分配</legend>
                          <p className="mb-2 text-[11px] leading-4 text-muted-foreground">
                            每个指标可分配到左轴或右轴；两侧使用各自的刻度。
                          </p>
                          <div className="space-y-2">
                            {selectedMetrics.map((field) => {
                              const isHidden = draft.hidden_metric_fields.includes(field);
                              const canMoveToRight =
                                isHidden ||
                                visibleMetrics.some(
                                  (other) =>
                                    other !== field &&
                                    (draft.y_axis_by_metric?.[other] ?? "left") === "left",
                                );
                              return (
                                <label
                                  key={field}
                                  className="flex flex-wrap items-center justify-between gap-2 text-xs"
                                >
                                  <span>{field}</span>
                                  <ComposerSelect
                                    id={`${controlIdPrefix}-y-axis-${selectedMetrics.indexOf(field)}`}
                                    ariaLabel={`指标 ${field} 的 Y 轴`}
                                    value={draft.y_axis_by_metric?.[field] ?? "left"}
                                    options={[
                                      { value: "left", label: "左 Y 轴" },
                                      {
                                        value: "right",
                                        label: "右 Y 轴",
                                        disabled: !canMoveToRight,
                                      },
                                    ]}
                                    placeholder="选择 Y 轴"
                                    triggerClassName={selectTriggerClass}
                                    onValueChange={(value) =>
                                      selectMetricYAxis(field, value as "left" | "right")
                                    }
                                  />
                                </label>
                              );
                            })}
                          </div>
                          <RecordFieldError
                            id={`${controlIdPrefix}-y-axis-error`}
                            error={validation.errors.y_axis_by_metric}
                          />
                        </fieldset>
                      )}
                      {supportsYAxisNames && visibleMetrics.length > 0 && (
                        <fieldset className="mt-3">
                          <legend className={fieldLabelClass}>Y 轴名称</legend>
                          <p className="mb-2 text-[11px] leading-4 text-muted-foreground">
                            可单独隐藏或重命名每侧数值轴；名称留空时使用该轴上的指标名称。
                          </p>
                          {editableYAxisSides.map((side) => {
                            const axisLabel = side === "left" ? "左 Y 轴" : "右 Y 轴";
                            const config = draft.y_axis_names?.[side];
                            const visible =
                              config?.visible ??
                              (hasVisibleRightYAxis || Boolean(config?.text?.trim()));
                            const defaultName = visibleMetrics
                              .filter(
                                (field) => (draft.y_axis_by_metric?.[field] ?? "left") === side,
                              )
                              .map((field) => draft.field_labels[field]?.trim() || field)
                              .join(" / ");
                            const error = validation.errors[`y_axis_names.${side}.text`];
                            return (
                              <div
                                key={side}
                                className="mb-3 grid gap-2 sm:grid-cols-[minmax(9rem,auto)_minmax(0,1fr)] sm:items-center"
                              >
                                <label className="inline-flex items-center gap-2 text-xs">
                                  <input
                                    type="checkbox"
                                    checked={visible}
                                    onChange={(event) =>
                                      setYAxisName(side, { visible: event.currentTarget.checked })
                                    }
                                    aria-label={`显示${axisLabel}名称`}
                                  />
                                  显示{axisLabel}名称
                                </label>
                                <input
                                  className={textControlClass}
                                  value={config?.text ?? ""}
                                  maxLength={36}
                                  disabled={!visible}
                                  placeholder={`默认：${defaultName}`}
                                  aria-label={`${axisLabel}自定义名称`}
                                  aria-invalid={Boolean(error || validation.errors.y_axis_names)}
                                  aria-describedby={
                                    error
                                      ? `${controlIdPrefix}-y-axis-name-${side}-error`
                                      : undefined
                                  }
                                  onChange={(event) =>
                                    setYAxisName(side, { text: event.currentTarget.value })
                                  }
                                />
                                <RecordFieldError
                                  id={`${controlIdPrefix}-y-axis-name-${side}-error`}
                                  error={error}
                                />
                              </div>
                            );
                          })}
                          <RecordFieldError
                            id={`${controlIdPrefix}-y-axis-names-error`}
                            error={validation.errors.y_axis_names}
                          />
                        </fieldset>
                      )}
                    </>
                  )}

                  <section
                    className="mt-3 rounded-lg border border-border/70 p-3"
                    aria-label="配色方案"
                  >
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <h4 className={fieldLabelClass}>配色方案</h4>
                      {(Object.keys(draft.color_by_metric ?? {}).length > 0 ||
                        Object.keys(draft.pie_category_colors?.by_category_key ?? {}).length >
                          0) && (
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
                      {draft.chart_type === "scatter"
                        ? "色板为数值颜色映射提供连续渐变；气泡大小使用同一数值的线性尺度。"
                        : draft.chart_type === "heatmap"
                          ? "色板为单元格数值提供连续颜色渐变。"
                          : "自动配色跟随色板；单独设置的指标或类别颜色会保留为自定义颜色。"}
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
                          <span className="block truncate text-[11px] font-medium">
                            {paletteLabels[paletteId]}
                          </span>
                          <span className="mt-1.5 flex gap-1" aria-hidden="true">
                            {(paletteId === "system_default" && draft.chart_type === "pie"
                              ? CHART_COLOR_PALETTES.classic
                              : CHART_COLOR_PALETTES[paletteId]
                            ).map((color, index) => (
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

                  {!new Set<string>(["pie", "scatter", "heatmap"]).has(draft.chart_type) &&
                    visibleMetrics.length > 0 && (
                      <fieldset className="mt-3 space-y-2">
                        <legend className={fieldLabelClass}>指标颜色</legend>
                        {visibleMetrics.map((field) => {
                          const color = draft.color_by_metric?.[field];
                          const metricIndex = Math.max(0, visibleMetrics.indexOf(field));
                          const selectedPalette = draft.color_palette_id ?? "system_default";
                          const palette =
                            selectedPalette === "system_default"
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
                                    background:
                                      color?.mode === "linear_gradient"
                                        ? `linear-gradient(to right, ${color.start_hex}, ${color.end_hex})`
                                        : (color?.hex ?? fallbackColor),
                                    opacity: color?.opacity === undefined ? 1 : color.opacity / 100,
                                  }}
                                  aria-hidden="true"
                                />
                              </div>
                              <div
                                className="grid grid-cols-3 gap-1 rounded-lg bg-muted/60 p-1"
                                role="group"
                                aria-label={`指标 ${field} 的配色模式`}
                              >
                                {(
                                  [
                                    ["auto", "跟随色板"],
                                    ["solid", "纯色"],
                                    ["linear_gradient", "渐变"],
                                  ] as const
                                ).map(([mode, label]) => (
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
                        const palette =
                          CHART_COLOR_PALETTES[draft.color_palette_id ?? "system_default"];
                        return (
                          <div key={key} className="rounded-lg border border-border/70 p-3">
                            <div className="mb-2 flex min-w-0 items-center justify-between gap-2">
                              <span className="truncate text-xs font-medium">{label}</span>
                              <span
                                className="size-4 shrink-0 rounded-full border border-border/70"
                                style={{
                                  backgroundColor: color?.hex ?? palette[index % palette.length],
                                  opacity: color ? color.opacity / 100 : 1,
                                }}
                                aria-hidden="true"
                              />
                            </div>
                            <div
                              className="grid grid-cols-2 gap-1 rounded-lg bg-muted/60 p-1"
                              role="group"
                              aria-label={`类别 ${label} 的配色模式`}
                            >
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
                                onClick={() =>
                                  updatePieCategoryColor(
                                    key,
                                    color ?? {
                                      mode: "solid",
                                      hex: palette[index % palette.length],
                                      opacity: 100,
                                    },
                                  )
                                }
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
                                  if (nextColor.mode === "solid")
                                    updatePieCategoryColor(key, nextColor);
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
                  {draft.chart_type === "bar" && (
                    <label className="mt-3 block">
                      <span className={fieldLabelClass}>系列构成</span>
                      <ComposerSelect
                        id={`${controlIdPrefix}-bar-stack-mode`}
                        ariaLabel="柱状图系列构成"
                        value={draft.bar_stack_mode ?? "grouped"}
                        options={[
                          { value: "grouped", label: "分组柱状图" },
                          { value: "stacked", label: "堆叠柱状图" },
                          { value: "percent", label: "百分比堆叠" },
                        ]}
                        placeholder="选择柱状图系列构成"
                        triggerClassName={selectTriggerClass}
                        onValueChange={(value) => {
                          const barStackMode = value as "grouped" | "stacked" | "percent";
                          updateDraft((current) => ({
                            ...current,
                            bar_stack_mode: barStackMode,
                            ...(barStackMode === "percent"
                              ? { annotations: { reference_lines: [], reference_areas: [] } }
                              : {}),
                          }));
                        }}
                      />
                      {validation.errors.bar_stack_mode && (
                        <RecordFieldError
                          id={`${controlIdPrefix}-bar-stack-error`}
                          error={validation.errors.bar_stack_mode}
                        />
                      )}
                      {draft.bar_stack_mode === "percent" && (
                        <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                          每行以当前可见指标之和为分母；仅对本轮已返回行计算比例。
                        </p>
                      )}
                    </label>
                  )}
                  {draft.chart_type === "step_line" && (
                    <label className="mt-3 block">
                      <span className={fieldLabelClass}>阶梯变化位置</span>
                      <ComposerSelect
                        id={`${controlIdPrefix}-step-position`}
                        ariaLabel="阶梯线变化位置"
                        value={draft.step_position ?? "end"}
                        options={[
                          { value: "start", label: "开始" },
                          { value: "middle", label: "中间" },
                          { value: "end", label: "结束（默认）" },
                        ]}
                        placeholder="选择阶梯位置"
                        triggerClassName={selectTriggerClass}
                        onValueChange={(value) =>
                          updateDraft((current) => ({
                            ...current,
                            step_position: value as "start" | "middle" | "end",
                          }))
                        }
                      />
                    </label>
                  )}
                </section>

                {draft.chart_type !== "scatter" && draft.chart_type !== "heatmap" && (
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
                              disabled:
                                draft.chart_type === "pie" ||
                                (draft.chart_type === "step_line" && !temporalDimension),
                            },
                            ...(!new Set<string>(["line", "area", "step_line"]).has(
                              draft.chart_type,
                            )
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
                                sort: {
                                  ...current.sort,
                                  direction,
                                } as ChartViewConfiguration["sort"],
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
                    {temporalDimension &&
                      new Set<string>(["line", "area", "step_line"]).has(draft.chart_type) && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          时间维度按时间值排序；折线类图表不按指标重排。
                        </p>
                      )}
                    {draft.chart_type === "step_line" && !temporalDimension && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        分类阶梯线使用查询返回顺序；若需要特定阶段顺序，请在查询中明确排序。
                      </p>
                    )}
                  </section>
                )}

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
                      const formatErrorId = `${controlIdPrefix}-format-${encodeURIComponent(field)}-error`;
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
                                const nextPlaces = value === "auto" ? "auto" : Number(value);
                                updateFormat(field, { ...format, decimal_places: nextPlaces });
                              }}
                            />
                          </label>
                          <RecordFieldError id={formatErrorId} error={formatError} />
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

                <section aria-labelledby="chart-annotations-title">
                  <h4 id="chart-annotations-title" className={fieldLabelClass}>
                    参考标注和统计范围
                  </h4>
                  {!supportsReferenceAnnotations ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                      均值、峰值和目标线适用于折线、面积、阶梯线及普通柱状图；百分比堆叠以可见指标行内合计为分母。
                    </p>
                  ) : (
                    <>
                      <div className="space-y-3">
                        {visibleMetrics.map((field) => {
                          const annotations = draft.annotations?.reference_lines ?? [];
                          return (
                            <fieldset
                              key={field}
                              className="rounded-md border border-border/70 p-2"
                            >
                              <legend className="px-1 text-xs font-medium">
                                {draft.field_labels[field]?.trim() || field}
                              </legend>
                              {(["mean", "peak"] as const).map((kind) => {
                                const annotation = annotations.find(
                                  (line) => line.metric_field === field && line.kind === kind,
                                );
                                return (
                                  <div
                                    key={kind}
                                    className="flex flex-wrap items-center gap-2 py-1"
                                  >
                                    <label className="flex items-center gap-2 text-xs">
                                      <input
                                        type="checkbox"
                                        checked={Boolean(annotation)}
                                        onChange={(event) =>
                                          updateStatisticAnnotation(field, kind, {
                                            enabled: event.currentTarget.checked,
                                          })
                                        }
                                      />
                                      {kind === "mean" ? "均值参考线" : "峰值标注"}
                                    </label>
                                    {annotation && (
                                      <ComposerSelect
                                        id={`${controlIdPrefix}-annotation-${kind}-${encodeURIComponent(field)}`}
                                        ariaLabel={`${field} ${kind === "mean" ? "均值" : "峰值"}统计范围`}
                                        value={annotation.scope}
                                        options={[
                                          { value: "returned_rows", label: "已返回行" },
                                          { value: "viewport", label: "当前缩放窗口" },
                                        ]}
                                        placeholder="统计范围"
                                        triggerClassName="h-8 w-40 max-w-none"
                                        onValueChange={(value) =>
                                          updateStatisticAnnotation(field, kind, {
                                            scope: value as "returned_rows" | "viewport",
                                          })
                                        }
                                      />
                                    )}
                                  </div>
                                );
                              })}
                              {annotations.some(
                                (line) => line.metric_field === field && line.kind === "value",
                              ) && (
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="ghost"
                                  onClick={() =>
                                    updateDraft((current) => ({
                                      ...current,
                                      annotations: {
                                        ...(current.annotations ?? {
                                          reference_lines: [],
                                          reference_areas: [],
                                        }),
                                        reference_lines: (
                                          current.annotations?.reference_lines ?? []
                                        ).filter(
                                          (line) =>
                                            !(line.metric_field === field && line.kind === "value"),
                                        ),
                                      },
                                    }))
                                  }
                                >
                                  移除目标线
                                </Button>
                              )}
                            </fieldset>
                          );
                        })}
                      </div>
                      {visibleMetrics.length > 0 && (
                        <div className="mt-3 grid gap-2 rounded-md border border-border/70 p-2 sm:grid-cols-2">
                          <label>
                            <span className={fieldLabelClass}>目标线指标</span>
                            <ComposerSelect
                              id={`${controlIdPrefix}-annotation-target-metric`}
                              ariaLabel="目标线指标"
                              value={
                                visibleMetrics.includes(targetMetricField)
                                  ? targetMetricField
                                  : visibleMetrics[0]
                              }
                              options={visibleMetrics.map((field) => ({
                                value: field,
                                label: draft.field_labels[field]?.trim() || field,
                              }))}
                              placeholder="选择指标"
                              triggerClassName={selectTriggerClass}
                              onValueChange={setTargetMetricField}
                            />
                          </label>
                          <label>
                            <span className={fieldLabelClass}>目标值</span>
                            <input
                              className={textControlClass}
                              inputMode="decimal"
                              value={targetValue}
                              maxLength={256}
                              aria-label="目标线数值"
                              placeholder="例如 125.5"
                              onChange={(event) => setTargetValue(event.currentTarget.value)}
                            />
                          </label>
                          <label className="sm:col-span-2">
                            <span className={fieldLabelClass}>目标线名称</span>
                            <input
                              className={textControlClass}
                              value={targetLabel}
                              maxLength={80}
                              aria-label="目标线名称"
                              onChange={(event) => setTargetLabel(event.currentTarget.value)}
                            />
                          </label>
                          <Button
                            type="button"
                            size="sm"
                            className="sm:col-span-2 sm:justify-self-start"
                            disabled={
                              !/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/u.test(targetValue.trim()) ||
                              !targetLabel.trim()
                            }
                            onClick={saveTargetAnnotation}
                          >
                            添加或更新目标线
                          </Button>
                        </div>
                      )}
                    </>
                  )}
                  <p className="mt-2 text-[11px] leading-5 text-muted-foreground">
                    统计使用原始十进制值和有效数值；“已返回行”仅指本轮返回的图表行，不代表整个业务总体。
                  </p>
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
                  <ChartCanvas
                    option={previewOption}
                    ariaLabel={`${draft.title}，草稿预览`}
                    className="h-64 w-full min-w-0 rounded-lg border border-border/70 bg-background sm:h-80"
                    sourceKey={`${previewCanvasSourceKey}:preview`}
                    sourceRows={previewRows}
                    interactive={false}
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
