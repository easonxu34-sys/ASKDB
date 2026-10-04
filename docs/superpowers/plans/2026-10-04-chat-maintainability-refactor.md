# Chat Maintainability Refactor Plan

## Goal

Reduce the responsibility concentration in `application/chat.py` while preserving the current chat API and SSE contract.

## Scope and boundaries

- Replace tool-name `if/elif` output handling with a small explicit presentation registry. Keep unknown internal tools hidden behind a generic progress label.
- Keep tool lifecycle state local to one `stream_chat_events()` invocation; do not introduce a global plugin framework or mutable process-wide registration.
- Move message normalization and response-safety decisions into cohesive application helpers. Keep the existing `stream_chat_events()` entry point and preserve its yielded event names and payload shapes.
- Do not change query authorization, SQL execution, memory recall, HTTP behavior, or Web rendering.
- Leave the large settings and Web adapter modules for a separate state-boundary review; no line-count-only extraction in this change.

## Implementation steps

1. [x] Add a typed per-turn tool-event state and presentation registry for the existing `wren_query` and `render_chart` tools.
2. [x] Route tool-start progress labels and tool-end presentation through the registry; retain the existing chart fallback and source-result matching behavior.
3. [x] Extract message parsing and response safety helpers from `application/chat.py` into cohesive modules, keeping orchestration imports explicit.
4. [x] Review the diff for unchanged SSE names/payload shapes, request/query boundaries, and compatibility imports.

## Validation constraints

Do not add or run tests in this task. Static AST parsing succeeded for 81 Python source files, `git diff --check` is clean, and the event contract was compared against the committed implementation. Report that behavioral test suites were not run.
