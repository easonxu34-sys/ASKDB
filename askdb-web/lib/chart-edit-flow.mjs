export const CHART_EDIT_TURN_COMPLETION_EVENT = "askdb:chart-edit-turn-completion";
export const CHART_EDIT_TURN_START_EVENT = "askdb:chart-edit-turn-start";

export function isChartEditContextCurrent(expected, current, viewsEqual) {
  return Boolean(expected && current &&
    expected.sourceResultId === current.sourceResultId &&
    expected.sourceMessageId === current.sourceMessageId &&
    expected.threadId === current.threadId &&
    viewsEqual(expected.view, current.view));
}

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function buildChartEditRequest({
  threadId,
  modelProfileId,
  instruction,
  chartArtifact,
  queryArtifact,
  view,
}) {
  const truncated = Boolean(
    queryArtifact.truncated ||
      queryArtifact.rows.length > 1000 ||
      (typeof queryArtifact.rowCount === "number" &&
        queryArtifact.rowCount > queryArtifact.rows.length),
  );
  const safeView = { ...view };
  delete safeView.pie_category_colors;
  return {
    thread_id: threadId,
    ...(modelProfileId ? { model_profile_id: modelProfileId } : {}),
    instruction,
    source_result_id: chartArtifact.source_result_id,
    view: safeView,
    columns: [...queryArtifact.columns],
    column_types: [...(queryArtifact.columnTypes ?? [])],
    row_count: Math.min(queryArtifact.rows.length, 1000),
    truncated,
  };
}

export async function interpretChartEdit(intent, handlers) {
  if (!isRecord(intent) || !handlers) throw new Error("CHART_EDIT_OUTPUT_INVALID");
  if (intent.status === "apply") {
    await handlers.preview(intent);
    return { status: "preview" };
  }
  if (intent.status === "clarify") {
    await handlers.clarify(intent.clarification?.code);
    return { status: "clarify" };
  }
  if (intent.status === "query_required") {
    await handlers.showQueryProposal(intent);
    return { status: "query_required" };
  }
  throw new Error("CHART_EDIT_OUTPUT_INVALID");
}

export async function confirmChartEditQuery(_proposal, originalInstruction, sendMessage) {
  if (typeof originalInstruction !== "string" || !originalInstruction.trim()) {
    throw new Error("CHART_EDIT_INPUT_INVALID");
  }
  const message = `${originalInstruction}\n\n请根据以上原始指令查询数据，并生成图表。`;
  return sendMessage(message);
}

export function publishChartEditTurnCompletion(report, dispatch) {
  const waiters = [];
  const detail = {
    report: {
      thread_id: report.thread_id,
      turn_id: report.turn_id,
      history_turn_id: report.history_turn_id,
      status: report.status,
      query_result_ids: [...report.query_result_ids],
      chart_source_result_ids: [...report.chart_source_result_ids],
    },
    waitUntil(value) {
      waiters.push(Promise.resolve(value));
    },
  };
  const event = typeof CustomEvent === "function"
    ? new CustomEvent(CHART_EDIT_TURN_COMPLETION_EVENT, { detail })
    : { type: CHART_EDIT_TURN_COMPLETION_EVENT, detail };
  if (dispatch) dispatch(event);
  else if (typeof window !== "undefined") window.dispatchEvent(event);
  return Promise.allSettled(waiters);
}

export function publishChartEditTurnStart(report, dispatch) {
  const detail = {
    thread_id: report.thread_id,
    turn_id: report.turn_id,
    history_turn_id: report.history_turn_id,
    confirmed_chart_query: report.confirmed_chart_query === true,
  };
  const event = typeof CustomEvent === "function"
    ? new CustomEvent(CHART_EDIT_TURN_START_EVENT, { detail })
    : { type: CHART_EDIT_TURN_START_EVENT, detail };
  if (dispatch) dispatch(event);
  else if (typeof window !== "undefined") window.dispatchEvent(event);
}

export async function completeStagedChartEdit(report, staged, handlers) {
  if (
    !staged ||
    report.thread_id !== staged.threadId ||
    report.turn_id !== staged.confirmedTurnId ||
    report.history_turn_id !== staged.confirmedHistoryTurnId
  ) return { status: "ignored" };
  if (report.status !== "completed") return { status: "discarded", reason: report.status };

  const chartSources = new Set(report.chart_source_result_ids);
  const matchingResultIds = [...new Set(report.query_result_ids.filter((id) => chartSources.has(id)))];
  if (matchingResultIds.length !== 1) return { status: "discarded", reason: "result_mismatch" };
  const intent = staged.intent;
  const hasDisplayChanges =
    (isRecord(intent?.patch) && Object.keys(intent.patch).length > 0) ||
    Boolean(intent?.current_result_operation) ||
    (Array.isArray(intent?.category_color_operations) && intent.category_color_operations.length > 0);
  if (!hasDisplayChanges) return { status: "no_display_changes" };

  let target;
  try {
    target = await handlers.resolveTarget(matchingResultIds[0], report);
  } catch {
    return { status: "discarded", reason: "result_unavailable" };
  }
  if (!target || target.source_result_id !== matchingResultIds[0]) {
    return { status: "discarded", reason: "result_unavailable" };
  }

  let applied;
  try {
    applied = handlers.apply(target, staged.intent);
  } catch {
    return { status: "discarded", reason: "incompatible" };
  }
  if (!applied || applied.status !== "apply") {
    return {
      status: "discarded",
      reason: "incompatible",
      code: applied?.code,
    };
  }

  let saved;
  try {
    saved = await handlers.commit(target, applied.view);
  } catch {
    return { status: "discarded", reason: "cache_failure" };
  }
  if (!saved) return { status: "discarded", reason: "cache_failure" };
  return { status: "applied", target, view: applied.view };
}
