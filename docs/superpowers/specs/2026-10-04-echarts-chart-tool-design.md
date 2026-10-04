# Deterministic ECharts Chart Tool Design

## Goal

Add a fixed chart tool that turns a successful Wren query result into a stable, typed chart artifact and renders that artifact with ECharts in the chat. The chart must use the exact result produced by `wren_query`; the model must not supply chart rows or executable chart code.

## Current flow

`build_graph()` creates the Wren tools once for a runtime snapshot. `wren_query` validates SQL, plans it, dry-runs it, and returns SQL plus columns and rows. `stream_chat_events()` forwards successful query results as SSE `result` events. The Web adapter stores those results in local thread artifacts and formats them as Markdown tables. Assistant messages currently render text and tool-call parts.

Relevant boundaries:

- Agent tool creation: `askdb-agent/src/agent/graph.py`
- Guarded query: `askdb-agent/src/tools/wren_query.py`
- Agent event stream: `askdb-agent/src/application/chat.py`
- Web SSE adapter and local result artifacts: `askdb-web/lib/agent-chat-adapter.ts`, `askdb-web/lib/local-thread-adapter.tsx`
- Query result formatting and message rendering: `askdb-web/lib/chat-output.ts`, `askdb-web/components/assistant-ui/elements/thread.aui.tsx`

## Decisions

1. Add an Agent tool named `render_chart`. It accepts only a `result_id`; it does not accept SQL, row data, a raw ECharts option, or JavaScript.
2. `wren_query` assigns an opaque ID to each successful result and records the exact rows and column types in a per-turn artifact context. The `render_chart` tool reads only from that same context.
3. Tool and registry instances are turn-scoped. The runtime snapshot is shared by chats, so query artifacts must never live in a module global or in mutable state shared by the cached Agent.
4. Chart selection is deterministic. An explicit chart type is parsed from the latest user message by a fixed alias list: `折线图`/`line chart`, `柱状图`/`bar chart`, or `饼图`/`pie chart`. Negated requests such as `不要饼图` suppress that override. If no explicit type is found, a time dimension plus numeric metric selects a line chart; otherwise, a categorical dimension plus numeric metric selects a bar chart. A pie chart requires one categorical dimension, one numeric metric, and at most eight categories. Unsupported result shapes return a typed `chart_unavailable` result and leave the query table intact.
5. The supported initial types are `line`, `bar`, and `pie`. An explicit supported type overrides the default only when its required fields are available.
6. The Agent returns a versioned chart artifact containing the source result ID, chart type, x field, series fields, and deterministic title. The front end constructs ECharts options from this artifact and the source query result; the model never constructs ECharts options.
7. Charts travel as a typed `data-chart` message part containing the chart artifact and its matching query result artifact. It is rendered by an ECharts client component. Query result tables and normal assistant text remain available.
8. Query and chart artifacts are saved in the existing per-user/per-thread/per-turn local artifact cache. History hydration restores them in original order. A chart whose source query artifact is missing or invalid is not rendered.
9. Chart creation is display-only and uses no additional SQL query. Failures in chart generation or rendering do not invalidate the successful query or assistant answer.

## Data contracts

### Query result artifact

`wren_query` adds the following fields to its existing result envelope:

```json
{
  "result_id": "opaque-turn-scoped-id",
  "column_types": ["date", "int64", "float64"]
}
```

Existing `sql`, `columns`, `rows`, `row_count`, and `truncated` fields remain available. `column_types` follows the order of `columns` and is derived from the returned Arrow schema, not guessed from sample values.

### Chart artifact

```json
{
  "kind": "echarts_chart",
  "schema_version": 1,
  "source_result_id": "opaque-turn-scoped-id",
  "chart_type": "line",
  "x_field": "month",
  "series_fields": ["revenue"],
  "title": "revenue by month"
}
```

The artifact contains field references, not copied rows. The front end resolves `source_result_id` against the query artifacts saved for the same turn before rendering.

### Per-turn context

Introduce a request-scoped `QueryArtifactContext` with these operations:

```python
store_query(table, sql, limit) -> QueryResultArtifact
get_query(result_id: str) -> QueryResultArtifact | None
clear() -> None
```

It retains typed columns and materialized rows only for the active Agent invocation. The immutable turn chart request also carries the latest user text and a deterministically parsed `requested_chart_type: Literal["line", "bar", "pie"] | None`. The request context is cleared after the stream completes, fails, or is cancelled.

## Deterministic chart selection

1. Resolve the result ID. If it is absent, expired, or belongs to another turn, return `chart_unavailable`.
2. Use Arrow column types to classify temporal, categorical, and numeric fields. Do not infer numeric types by parsing arbitrary text values.
3. Honor an explicit supported chart request only if its required fields exist. For a pie chart, require one numeric metric and no more than eight category values.
4. Without an explicit type, choose the first temporal field and the numeric metric fields in query column order for a line chart. If there is no temporal field, choose the first categorical field and numeric metric fields in query column order for a bar chart.
5. Keep the query result order for categories. Sort valid temporal values ascending for line charts. Do not aggregate, drop, or invent rows.
6. Limit series to four and points to the 1,000-row cap already enforced by `wren_query`. The chart tool never fetches or aggregates additional rows. Return `chart_unavailable` if the result has no valid x field or numeric series.
7. Build chart titles from field names with a fixed template. No model-generated title or formatter is included in the initial version.

## ECharts rendering

Add the `echarts` package and a React wrapper compatible with the existing Next.js and React versions, pinned in `askdb-web/pnpm-lock.yaml`. Load the chart component on the client only. A small pure Web helper validates chart artifacts, resolves the referenced query result, and builds a whitelist-based ECharts option for the three supported types. The component handles resize, empty values, labels, default tooltip, and bounded legend display. It does not enable HTML tooltip formatters, custom JavaScript, or arbitrary option merging.

## Error behavior

- Invalid, missing, or cross-turn result ID: return a typed unavailable result; do not query again.
- Unsupported types or missing dimensions/metrics: return a user-safe reason and keep the query table.
- Invalid chart artifact in Web: omit the chart card and keep the text/table.
- ECharts import or rendering error: display a compact chart-unavailable status and preserve the rest of the message.
- Query, SQL authorization, and safety-gate behavior remain the existing source of truth.

## Acceptance criteria

- Repeated chart generation from the same query artifact produces the same chart type and field selection.
- A chart can only reference a query artifact from the active turn.
- Line, bar, and explicitly requested valid pie charts render from exact query rows.
- Unsupported data keeps the table and reports chart unavailability without failing the chat turn.
- Live streams and replayed thread history show the same chart artifact.
- Existing query result presentation and read-only SQL safety sequence continue to work.
