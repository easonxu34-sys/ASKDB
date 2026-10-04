# Repository Maintainability Slices

## Completed in this worktree

- [x] Register chat tool presentation handlers and isolate message/safety helpers.
- [x] Move chat SSE delivery and per-turn cleanup into an API streaming module.
- [x] Move pure Wren source-model transforms out of the large data-source page.
- [x] Move legacy chat-history sanitization and pairing logic out of the thread adapter.
- [x] Isolate the one-time legacy Wren project migration workflow.
- [x] Extract runtime snapshot assembly behind an explicit builder while keeping the application method as the runtime-manager callback.

## Boundaries

- Public API routes, SSE event names and payloads, SQL authorization, and Wren revision semantics remain owned by their current entry points.
- UI state and browser storage ownership remain unchanged.
- The remaining large coordinators must be split only where a stable dependency boundary exists; this work does not rename or rewire them mechanically.
- No tests are added or run in this task. Static checks and source review are the validation available here.

## Validation

- Parsed all 96 Python source files with `ast.parse`; `git diff --check` passed.
- `oxlint` and `oxfmt --check` passed for the changed Web modules. The adapter retains seven pre-existing unused-history-import warnings; the baseline had those warnings plus two unused imports that were removed here.
- TypeScript checking still reports the same seven baseline diagnostics, including two existing `DataSourceConnection` type errors in the data-source page.
- No tests were added or run.
