const MAX_CHART_EDIT_HISTORY = 20;
const DEFAULT_MAX_RESULT_ARTIFACT_BYTES = 2 * 1024 * 1024;
const VALID_ORIGINS = new Set(["manual", "natural_language", "restore_recommendation"]);

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readIndex(storage, key) {
  try {
    const parsed = JSON.parse(storage.getItem(key) ?? "{}");
    if (!isRecord(parsed) || Object.values(parsed).some((items) => !Array.isArray(items))) {
      return undefined;
    }
    return Object.fromEntries(Object.entries(parsed).map(([turn, items]) => [turn, [...items]]));
  } catch {
    return undefined;
  }
}

function artifactOf(item) {
  if (!isRecord(item)) return undefined;
  return isRecord(item.artifact) ? item.artifact : item;
}

function matchingOverrides(items, sourceResultId) {
  return items.flatMap((item, index) => {
    const artifact = artifactOf(item);
    return artifact?.kind === "chart_view_override" &&
      artifact.source_result_id === sourceResultId
      ? [{ index, artifact }]
      : [];
  });
}

function validatedView(candidate, validateView) {
  try {
    return validateView(candidate);
  } catch {
    return undefined;
  }
}

function readHistory(override, validateView) {
  if (!override || override.schema_version === 1) return [];
  if (override.schema_version !== 2 && override.schema_version !== 3) return undefined;
  if (override.undo_history === undefined) return [];
  if (!Array.isArray(override.undo_history) || override.undo_history.length > MAX_CHART_EDIT_HISTORY) {
    return undefined;
  }
  const result = [];
  for (const entry of override.undo_history) {
    if (
      !isRecord(entry) ||
      Object.keys(entry).some((key) => !["view", "summary", "origin"].includes(key)) ||
      typeof entry.summary !== "string" ||
      !entry.summary.trim() ||
      entry.summary.length > 512 ||
      !VALID_ORIGINS.has(entry.origin)
    ) {
      return undefined;
    }
    const view = validatedView(entry.view, validateView);
    if (!view) return undefined;
    result.push({ view, summary: entry.summary, origin: entry.origin });
  }
  return result;
}

function writeIndex(storage, key, index, turnId, maxBytes) {
  try {
    let serialized = JSON.stringify(index);
    while (new TextEncoder().encode(serialized).byteLength > maxBytes) {
      const oldestOtherTurn = Object.keys(index).find((id) => id !== turnId);
      if (!oldestOtherTurn) return false;
      delete index[oldestOtherTurn];
      serialized = JSON.stringify(index);
    }
    storage.setItem(key, serialized);
    return true;
  } catch {
    return false;
  }
}

function hasUsableIdentity(options) {
  return Boolean(
    options.storage &&
      typeof options.key === "string" && options.key &&
      typeof options.turnId === "string" && options.turnId &&
      typeof options.sourceResultId === "string" && options.sourceResultId &&
      typeof options.hasSourceArtifacts === "function" &&
      typeof options.validateView === "function",
  );
}

function areViewsEqual(left, right, equalViews) {
  try {
    return equalViews ? equalViews(left, right) : JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

function replaceOverride(index, turnId, sourceResultId, override) {
  const existing = index[turnId] ?? [];
  index[turnId] = [
    ...existing.filter((item) => {
      const artifact = artifactOf(item);
      return !(artifact?.kind === "chart_view_override" && artifact.source_result_id === sourceResultId);
    }),
    override,
  ];
}

function getWritableHistory(options) {
  const {
    storage,
    key,
    turnId,
    sourceResultId,
    recommendedView,
    hasSourceArtifacts,
    validateView,
  } = options;
  const index = readIndex(storage, key);
  if (!index) return undefined;
  const turnArtifacts = index[turnId];
  if (!turnArtifacts) return undefined;
  try {
    if (!hasSourceArtifacts(turnArtifacts, sourceResultId)) return undefined;
  } catch {
    return undefined;
  }
  const matches = matchingOverrides(turnArtifacts, sourceResultId);
  if (matches.length > 1) return undefined;
  const override = matches[0]?.artifact;
  if (
    override &&
    (override.schema_version !== 1 && override.schema_version !== 2 && override.schema_version !== 3 || !isRecord(override.view))
  ) {
    return undefined;
  }
  const currentView = validatedView(override?.view ?? recommendedView, validateView);
  const parsedHistory = readHistory(override, validateView);
  if (!currentView) return undefined;
  return {
    index,
    turnArtifacts,
    override,
    currentView,
    undoHistory: parsedHistory ?? [],
    historyValid: parsedHistory !== undefined,
  };
}

/**
 * Commit a chart view and its undo record in one write to the existing artifact index.
 * The host supplies chart-specific validation and source-result checks.
 */
export function commitChartViewChange(options) {
  if (!hasUsableIdentity(options)) return undefined;
  const {
    key,
    turnId,
    sourceResultId,
    before,
    after,
    summary,
    origin,
    validateView,
    equalViews,
    maxBytes = DEFAULT_MAX_RESULT_ARTIFACT_BYTES,
  } = options;
  if (
    typeof summary !== "string" ||
    !summary.trim() ||
    summary.length > 512 ||
    !VALID_ORIGINS.has(origin) ||
    !Number.isSafeInteger(maxBytes) ||
    maxBytes < 0
  ) {
    return undefined;
  }
  const state = getWritableHistory(options);
  if (!state) return undefined;
  const validBefore = validatedView(before, validateView);
  const validAfter = validatedView(after, validateView);
  if (
    !validBefore ||
    !validAfter ||
    !areViewsEqual(state.currentView, validBefore, equalViews)
  ) {
    return undefined;
  }
  const undoHistory = [
    ...state.undoHistory,
    { view: validBefore, summary: summary.trim(), origin },
  ].slice(-MAX_CHART_EDIT_HISTORY);
  const override = {
    kind: "chart_view_override",
    schema_version: 3,
    source_result_id: sourceResultId,
    view: validAfter,
    undo_history: undoHistory,
  };
  replaceOverride(state.index, turnId, sourceResultId, override);
  if (!writeIndex(options.storage, key, state.index, turnId, maxBytes)) return undefined;
  return { view: validAfter, undoHistory };
}

/**
 * Restore the previous view only if the current persisted view still matches the caller.
 */
export function undoChartViewChange(options) {
  if (!hasUsableIdentity(options)) return undefined;
  const {
    key,
    turnId,
    sourceResultId,
    expectedView,
    validateView,
    equalViews,
    maxBytes = DEFAULT_MAX_RESULT_ARTIFACT_BYTES,
  } = options;
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 0) return undefined;
  const state = getWritableHistory(options);
  if (!state?.override || state.undoHistory.length === 0) return undefined;
  if (!state.historyValid) return undefined;
  const validExpected = validatedView(expectedView, validateView);
  if (!validExpected || !areViewsEqual(state.currentView, validExpected, equalViews)) {
    return undefined;
  }
  const previous = state.undoHistory.at(-1);
  const remainingHistory = state.undoHistory.slice(0, -1);
  const override = {
    kind: "chart_view_override",
    schema_version: 3,
    source_result_id: sourceResultId,
    view: previous.view,
    undo_history: remainingHistory,
  };
  replaceOverride(state.index, turnId, sourceResultId, override);
  if (!writeIndex(options.storage, key, state.index, turnId, maxBytes)) return undefined;
  return { view: previous.view, undoHistory: remainingHistory, undone: previous };
}

export const MAX_CHART_VIEW_UNDO_HISTORY = MAX_CHART_EDIT_HISTORY;
