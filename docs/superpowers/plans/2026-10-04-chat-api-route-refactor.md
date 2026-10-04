# Chat API Route Refactor Plan

## Goal

Separate prepared-turn setup from SSE delivery in the chat endpoint while preserving the route, authorization checks, event names, payloads, and cleanup behavior.

## Scope

- Move the nested SSE generator and its per-turn lifecycle into api/chat_stream.py.
- Pass request-local values through a typed ChatStreamContext.
- Keep request validation, source authorization, runtime acquisition, turn reservation, and context assembly in the route for this phase.
- Do not change query policy, memory recall, HTTP status mapping, or the Web contract.
- Do not add or run tests in this task; use static review and diff checks only.

## Steps

1. [x] Extract the SSE generator into api/chat_stream.py.
2. [x] Construct ChatStreamContext after request setup and return the same StreamingResponse.
3. [x] Compare event order, turn completion/failure, suppression checks, and runtime release against the prior implementation.
4. [x] Run static syntax and whitespace checks; behavioral tests were not run.

## Validation

- Parsed all 96 Python source files with `ast.parse`.
- `git diff --check` passed.
- TypeScript checking reports the same seven pre-existing diagnostics as the baseline checkout; no new diagnostics were introduced.
- Behavioral test suites were not run.
