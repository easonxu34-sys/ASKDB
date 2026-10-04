# Interactive Chart Editor Hardening Implementation Plan

> **For agentic workers:** Execute inline in this task using the TDD cycle. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close the interactive chart editor's JSON transport, numeric precision, override schema, and cache-persistence gaps without changing SQL execution or adding server-side chart storage.

**Architecture:** Keep query results and chart recommendations as the source of truth. Normalize Arrow values to JSON-safe wire values at the Agent artifact boundary; preserve decimal text for exact display, sorting, formatting, and scaling in Web code, while passing finite JavaScript numbers to ECharts for geometry. Propagate local artifact write failures so a chart remains viewable but cannot offer an unsaveable editor.

**Tech Stack:** Python 3, FastAPI SSE, TypeScript, Node's built-in test runner, React 19, Next.js 16.

**Spec:** `docs/superpowers/specs/2026-10-04-interactive-chart-editor-design.md`

## Global Constraints

- Keep SQL validation and execution order `validate_read_query → dry_plan → dry_run → query` unchanged.
- Keep chart edits display-only; do not start Agent turns, emit query requests, or modify query rows.
- Keep chart field and ECharts option inputs allowlisted; never accept arbitrary options, HTML, or scripts from users or the Agent.
- Preserve Decimal text for user-visible formatting and metric sorting; send ECharts only finite numeric plot values.
- Reject chart recommendations with duplicate output field names and retain the result table.
- Keep per-user/thread/turn local cache scope and its existing 2 MiB cap.

---

### Task 1: Normalize Arrow result rows for JSON transport

**Files:**
- Modify: `askdb-agent/src/domain/chart_artifact.py`
- Test: `askdb-agent/tests/test_chart_serialization.py`

**Interfaces:**
- `QueryResultArtifact.to_dict()` returns JSON-safe row values while the in-memory artifact retains original Python values for deterministic chart selection.
- Dates, times, and datetimes become ISO-8601 strings; `Decimal` becomes fixed-point decimal text; integers outside JavaScript's safe integer range become decimal text; non-finite floats become `None`.

- [x] Add a test with `date`, `time`, `datetime`, `Decimal`, an integer larger than `2**53 - 1`, and `NaN`; assert the serialized values are strings or null as specified.
- [x] Pass the serialized result through `api.streaming.encode_sse()` and parse its `data:` JSON to prove the complete result event is serializable.
- [x] Run a standard-library-only regression test and confirm it fails because raw Python values remain in `to_dict()`.
- [x] Add a recursive private wire-value converter in `chart_artifact.py`; do not mutate `QueryResultArtifact.rows`.
- [x] Run the focused Agent test and confirm exact Decimal text, ISO temporal values, large integer text, and null non-finite values.

### Task 2: Preserve decimal semantics in Web chart construction

**Files:**
- Modify: `askdb-web/lib/chart-output.ts`
- Create: `askdb-web/lib/chart-decimal.ts`
- Test: `askdb-web/tests/chart-output.test.mjs`

**Interfaces:**
- `formatChartValue(value, format)` accepts finite JavaScript numbers or canonical decimal strings and applies raw, suffix, CNY scale, and percent formatting without converting decimal strings through `Number`.
- Metric sorting and pie validation compare and sum numeric decimal strings exactly. ECharts series and pie slice geometry receive finite JavaScript numbers; invalid or unrepresentable plot values become gaps or make pie unavailable.
- `unitSignature()` distinguishes CNY source/display scales and percent encodings. A shared axis uses consistent automatic precision; series labels and tooltips retain each metric's selected format.

- [x] Add tests for long Decimal strings, automatic precision below six decimal places, explicit rounding, CNY and percent scaling, exact metric sort order, decimal pie validity/share, and mixed percent/CNY source scales.
- [x] Run `node --experimental-strip-types --test askdb-web/tests/chart-output.test.mjs` and confirm the new decimal and unit-scale cases fail.
- [x] Implement decimal parsing, comparison, summation, power-of-ten scaling, and rounding with `BigInt` in the pure chart helper; add no runtime dependency.
- [x] Route numeric Arrow metric values through decimal parsing; leave malformed values as gaps.
- [x] Convert validated values to finite JavaScript numbers only when building ECharts data arrays.
- [x] Run focused Web chart tests and confirm output labels preserve source precision while plotted values remain finite.

### Task 3: Align override schema and document field constraints

**Files:**
- Modify: `docs/superpowers/specs/2026-10-04-interactive-chart-editor-design.md`

- [x] Add `hidden_metric_fields` to the JSON example and state that duplicate query field names make charts unavailable with the query table retained.
- [x] Define automatic precision, shared-axis precision, and canonical JSON representations for temporal, Decimal, large integer, and non-finite values in the design doc.
- [x] Keep the existing duplicate-column fallback test as the regression guard; do not duplicate already-covered code behavior.

### Task 4: Propagate result-cache persistence state to the editor

**Files:**
- Create: `askdb-web/lib/thread-result-artifacts.mjs`
- Modify: `askdb-web/lib/local-thread-adapter.tsx`
- Modify: `askdb-web/lib/agent-chat-adapter.ts`
- Modify: `askdb-web/lib/chat-output.ts`
- Modify: `askdb-web/components/assistant-ui/elements/chart-result.tsx`
- Modify: `askdb-web/components/assistant-ui/elements/thread.aui.tsx`
- Test: `askdb-web/tests/thread-result-artifacts.test.mjs`
- Test: `askdb-web/tests/chat-output.test.mjs`

**Interfaces:**
- `saveThreadResultArtifact()` returns whether the current artifact was persisted within the existing 2 MiB cap and localStorage quota.
- `getChartMessageParts(outputs, { persistenceAvailable })` exposes persistence readiness on the chart part.
- `ChartResult` hides editing when the backing query/chart artifacts were not persisted and displays a concise explanation; persisted live and replayed charts remain editable.

- [x] Add pure storage tests for successful writes, old-turn eviction, current-turn oversize rejection, and `setItem` quota failure.
- [x] Add a chart-part test proving persistence readiness is carried into the message part.
- [x] Run the new storage and chat-output tests and confirm failure was caused by the absent return/status contract.
- [x] Extract the cache write operation into `thread-result-artifacts.mjs` with injected storage/key/limit arguments; preserve the existing key format and byte cap.
- [x] Return the write result from `saveThreadResultArtifact()` and record query and chart write outcomes in the live adapter.
- [x] Pass persistence readiness through `getChartMessageParts()` and hide the edit action with an explanatory status when false.
- [x] Keep history replay editable when its query and chart artifacts are present in the cache.
- [x] Run the focused storage, chat-output, and chat-request Node tests; update the stale chat-request test fixture to the existing current request contract without changing request handling.

### Task 5: Verify the hardened vertical slice

**Files:**
- Verify: `askdb-agent/tests/test_chart_tool.py`
- Verify: `askdb-web/tests/chart-output.test.mjs`
- Verify: `askdb-web/tests/chat-output.test.mjs`
- Verify: `askdb-web/tests/thread-result-artifacts.test.mjs`
- Verify: `askdb-web/tests/chat-request.test.mjs`

- [ ] Run `askdb-agent/tests/test_chart_tool.py`; the new standard-library serialization test passes, but full chart-tool pytest is unavailable because pytest and the Wren dev wheel are not installed/cached in this offline environment.
- [x] Run the focused Web chart, chat-output, cache, and chat-request Node tests; decimal values, cache failure handling, replay status, and request validation pass.
- [x] Run `git diff --check` and inspect the final diff for unrelated paths and accidental query/SQL changes.
- [x] Report unavailable Next build verification separately because this worktree has no installed `askdb-web/node_modules`.
