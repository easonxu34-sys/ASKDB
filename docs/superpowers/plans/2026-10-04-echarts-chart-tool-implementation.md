# Deterministic ECharts Chart Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a fixed `render_chart` tool that creates deterministic ECharts chart artifacts from the current turn's successful Wren query results and displays them in chat.

**Architecture:** Create a request-scoped query artifact context and bind query/chart tools to a per-turn Agent invocation so cached runtime objects cannot share mutable result state. Stream chart artifacts alongside query results, store them with existing thread result artifacts, and render validated `data-chart` message parts with a client-loaded ECharts component.

**Tech Stack:** Python 3, LangChain/LangGraph, FastAPI SSE, React 19, Next.js, TypeScript, ECharts, pnpm, pytest, Node test runner.

**Spec:** `docs/superpowers/specs/2026-10-04-echarts-chart-tool-design.md`

## Global Constraints

- Preserve read-only SQL validation and execution order: `validate_read_query → dry_plan → dry_run → query`.
- `render_chart` accepts only a query result ID; never accept SQL, rows, raw ECharts options, or JavaScript from the model.
- Keep query result state scoped to one user turn; do not add module-global or runtime-snapshot mutable result registries.
- Chart selection supports only `line`, `bar`, and `pie`, with deterministic rules defined in the spec.
- ECharts options are constructed in Web code from validated artifacts and query rows.
- Preserve all existing working-tree edits; stage only files created or changed for this feature.

---

### Task 1: Add per-turn query artifacts and deterministic chart tool

**Files:**
- Create: `askdb-agent/src/domain/chart_artifact.py`
- Create: `askdb-agent/src/application/chart_context.py`
- Create: `askdb-agent/src/tools/chart.py`
- Modify: `askdb-agent/src/tools/wren_query.py`
- Modify: `askdb-agent/src/agent/graph.py`
- Modify: `askdb-agent/src/agent/prompts.py`
- Test: `askdb-agent/tests/test_query.py`
- Create: `askdb-agent/tests/test_chart_tool.py`

**Interfaces:**
- `QueryResultArtifact`: immutable result ID, SQL, ordered columns, ordered column types, rows, row count, and truncation flag.
- `ChartArtifact`: immutable schema version 1 with source result ID, chart type, x field, series fields, and title.
- `ChartRequest`: immutable latest-user-message text plus `requested_chart_type: Literal["line", "bar", "pie"] | None`, derived by `parse_requested_chart_type(text)` using the fixed aliases in the spec.
- `QueryArtifactContext.store_query(table, sql, limit) -> QueryResultArtifact`, `.get_query(result_id) -> QueryResultArtifact | None`, and `.clear() -> None`.
- `create_guarded_query_tool(toolkit, context, dialect="mysql")` stores successful query results in the supplied context and returns the existing result fields plus `result_id` and `column_types`.
- `create_chart_tool(context, chart_request)` returns a LangChain tool named `render_chart`; its only model-visible input is `result_id: str`.
- `AgentRuntime.create_agent_for_turn(context, chart_request)` builds an Agent with query and chart tools bound to that context. Reuse the snapshot's model and toolkit, but do not share the turn-bound Agent between requests.

- [ ] Add focused tests for registry isolation, missing IDs, and deterministic chart selection: temporal plus numeric chooses line; categorical plus numeric chooses bar; the aliases `折线图`/`line chart`, `柱状图`/`bar chart`, and `饼图`/`pie chart` parse to the corresponding type; `不要饼图` does not parse as a pie request; a pie request requires one numeric series and at most eight categories; unsupported shapes return `chart_unavailable`.
- [ ] Run `pytest askdb-agent/tests/test_chart_tool.py askdb-agent/tests/test_query.py -q` and confirm the new contract tests fail before implementation.
- [ ] Implement the immutable artifact types and in-memory `QueryArtifactContext`; make `clear()` remove every stored result and never return an artifact for an unknown ID.
- [ ] Extend the guarded query tool to derive `column_types` from `table.schema`, store the exact table result in the context, and preserve SQL validation and query call order.
- [ ] Implement `render_chart(result_id)` as a pure selector over the stored result. Return only the typed chart artifact or typed unavailable result; do not access the toolkit or issue SQL.
- [ ] Make `AgentRuntime` construct turn-bound tools and an Agent from the shared model/toolkit. Update `build_graph()` to retain the inputs/factory needed for per-turn construction.
- [ ] Update the system prompt so the Agent calls `render_chart` after a successful query when the user requests a chart, using only the `result_id` returned by `wren_query`.
- [ ] Run the focused Agent tests and confirm the query gate remains before Agent execution and the query tool still executes `validate_read_query → dry_plan → dry_run → query`.

### Task 2: Stream chart artifacts from the Agent turn

**Files:**
- Modify: `askdb-agent/src/application/chat.py`
- Test: `askdb-agent/tests/test_chat.py`
- Test: `askdb-agent/tests/test_api.py`

**Interfaces:**
- `stream_chat_events(...)` creates a new `QueryArtifactContext` for each invocation and calls `runtime.create_agent_for_turn(context, chart_request)`.
- Successful `wren_query` calls continue to produce the existing `result` event.
- Successful `render_chart` calls produce `chart` events carrying `{ "artifact": ChartArtifact }`; unavailable outcomes carry a typed reason without failing the turn.
- Context cleanup runs in a `finally` block after normal completion, tool failure, cancellation, or stream closure.

- [ ] Add event-stream tests asserting query result precedes chart result, both refer to the same result ID, and the assistant's final text remains a `token` event.
- [ ] Add a concurrent-turn test using distinct contexts and prove that a chart tool in one turn cannot resolve the other turn's result ID.
- [ ] Add an API streaming test asserting a `chart` SSE frame retains `thread_id`, `turn_id`, and `user_sequence` metadata.
- [ ] Run `pytest askdb-agent/tests/test_chat.py -q` and confirm the new stream assertions fail before wiring.
- [ ] Build the per-turn Agent and artifact context inside `stream_chat_events`; clear the context in `finally`.
- [ ] Handle `on_tool_end` for `render_chart` and emit the typed `chart` event. Preserve existing `wren_query` result handling and final-answer safety review.
- [ ] Verify `askdb-agent/src/api/routes/chat.py` forwards `chart` events with the same thread/turn/sequence metadata used for existing events.
- [ ] Run the focused chat/API tests and inspect serialized SSE frames for `result`, `chart`, `token`, and `done` order.

### Task 3: Persist and replay chart artifacts in Web chat

**Files:**
- Modify: `askdb-web/lib/agent-chat-adapter.ts`
- Modify: `askdb-web/lib/local-thread-adapter.tsx`
- Modify: `askdb-web/lib/chat-output.ts`
- Test: `askdb-web/tests/chat-output.test.mjs`
- Test: `askdb-web/tests/chat-request.test.mjs`

**Interfaces:**
- Add TypeScript guards for query result IDs, column types, and version-1 chart artifacts.
- `saveThreadResultArtifact()` stores query and chart artifacts in event order under the existing user/thread/turn key.
- `agent-chat-adapter` parses `chart` SSE events and yields a typed `data-chart` content part associated with the current assistant turn.
- Replay handling loads saved chart artifacts with query artifacts and emits the same `data-chart` part without another database query.

- [ ] Add Node tests for valid/invalid chart artifact parsing, unknown schema versions, missing query-result references, and live event ordering.
- [ ] Run `node --experimental-strip-types --test askdb-web/tests/chat-output.test.mjs askdb-web/tests/chat-request.test.mjs` and confirm new chart assertions fail before adapter changes.
- [ ] Extend the existing result artifact readers/writers to retain chart artifacts while preserving the existing storage byte cap and query artifact compatibility.
- [ ] Parse `chart` SSE events, persist each artifact, and yield a `data-chart` part only after its referenced query result is available.
- [ ] Update replay/hydration to restore chart parts from the same turn artifact index; skip a chart whose source query result is absent or invalid.
- [ ] Run the focused Node tests and confirm repeated replay produces the same chart part and does not trigger a new chat request.

### Task 4: Render validated chart parts with ECharts

**Files:**
- Modify: `askdb-web/package.json`
- Modify: `askdb-web/pnpm-lock.yaml`
- Create: `askdb-web/lib/chart-output.ts`
- Create: `askdb-web/components/assistant-ui/elements/chart-result.tsx`
- Modify: `askdb-web/components/assistant-ui/elements/thread.aui.tsx`
- Test: `askdb-web/tests/chart-output.test.mjs`

**Interfaces:**
- `buildEChartsOption(chartArtifact, queryArtifact)` validates source ID and referenced fields and returns an ECharts option for only the supported chart types.
- `ChartResult` is a client component that receives validated artifact data and shows either the ECharts card or a compact unavailable state.
- `AssistantMessage` renders `data-chart` parts through `ChartResult`; each part carries the versioned chart artifact and the matching query result artifact resolved by the adapter. Text, tool-call rendering, and result table formatting remain available.

- [ ] Add pure option-builder tests for line, bar, explicit pie, chronological ordering, invalid source IDs, unknown fields, and malformed values.
- [ ] Run `node --experimental-strip-types --test askdb-web/tests/chart-output.test.mjs` and confirm the option-builder tests fail before implementation.
- [ ] Add pinned compatible `echarts` and React wrapper dependencies using pnpm; update only `package.json` and `pnpm-lock.yaml` for dependency resolution.
- [ ] Implement the option builder with a fixed field whitelist, at most four series and 1,000 points, a field-derived title, default tooltip/legend, and no custom HTML or executable option input.
- [ ] Implement the client-only ECharts chart card with responsive sizing, chart type labels, and safe fallback on import/render errors.
- [ ] Render the `data-chart` message part in `AssistantMessage` and verify that invalid chart data cannot break the surrounding assistant message.
- [ ] Run focused Web tests and inspect the chart card at narrow and wide message widths.

### Task 5: Document the behavior and verify the vertical slice

**Files:**
- Modify: `askdb-agent/README.md`
- Modify: `askdb-web/README.md`
- Verify: all feature files listed above

- [ ] Document supported chart types, automatic selection rules, explicit pie constraints, and table fallback in the Agent/Web usage docs.
- [ ] Run focused Agent checks: `pytest askdb-agent/tests/test_chart_tool.py askdb-agent/tests/test_query.py askdb-agent/tests/test_chat.py -q`.
- [ ] Run focused Web checks: `node --experimental-strip-types --test askdb-web/tests/chart-output.test.mjs askdb-web/tests/chat-output.test.mjs askdb-web/tests/chat-request.test.mjs`.
- [ ] Run `pnpm run build` from `askdb-web` and record any pre-existing diagnostics separately from chart changes.
- [ ] Run `git diff --check`; inspect changed paths and confirm unrelated user edits remain untouched.

## Completion criteria

- The same successful query artifact always yields the same chart type, fields, title, and plotted data.
- Concurrent turns cannot read one another's query artifacts.
- Agent SSE, live Web display, local artifact persistence, and history replay all carry the same versioned chart artifact.
- Unsupported data or a rendering failure preserves the successful query result and assistant answer.
- The existing SQL safety sequence remains unchanged and still gates every database query.
