# Agent Memory Implementation Prompt Packet

**Source of truth:** [`Agent记忆体系设计.md`](../../Agent记忆体系设计.md) and [`2026-10-02-agent-memory-system.md`](../plans/2026-10-02-agent-memory-system.md). If a prompt and either source disagree, stop and report the conflict; do not silently change the contract.

**Current continuation:** T3 Agent governance already exists in the checkout; continue with the focused [T3 Web implementation prompt](2026-10-03-t3-web-implementation-prompt.md), first reconciling its query-turn reference contract. Preserve current uncommitted workspace changes.

**Requested model:** `gpt-6-luna`, high reasoning. The available dispatch interface has no separate fast setting.

## Shared instructions for every implementation lane

Copy this block before the lane-specific prompt:

> You are implementing one bounded task from the ASKDB Agent memory plan. Read the referenced task and relevant design sections before changing files. Preserve FastAPI + LangChain/LangGraph + WrenToolkit, existing source authorization, SQL gate order, API compatibility windows, and user changes. Do not read, print, modify, or stage `askdb-agent/.env` or any credential file. Do not broaden the assigned file ownership. Do not spawn another agent.
>
> Do not add or run tests unless the user explicitly asks for testing or verification in the active task. When testing is not requested, use only the permitted static checks and state clearly that behavior remains unverified. Never claim a check passed without its output.
>
> The checkout-state note in older lane prompts is historical. At the T3 continuation, the live workspace is a Git checkout on `main` with three unrelated user changes already present. Continue serially in this checkout to preserve them; do not reset, stage, commit, or rebase. Do not start another implementation lane while one is editing. Avoid edits outside the current T3 scope. Before editing an existing file, inspect its current contents and preserve surrounding behavior. Report every changed path and any concern.
>
> Use the user-confirmed decisions: thread deletion and 30-day automatic expiry cascade to linked business-rule memories; published query examples survive thread deletion/expiry. Superseded query-corpus revisions remain for 30 days from supersession and wait for the last runtime lease before cleanup. Revocation suppresses online recall immediately. Production requires a separate encrypted append-only deletion/revocation journal; restore replays after the captured DB `journal_applied_seq`, validates suppression, rebuilds indexes, and only then serves chat. A missing, corrupt, or discontinuous journal fails closed.

## Dispatch sequence

The plan permits three initial parallel lanes, but previous work ran serially and the current T3 continuation remains serial. Do not interpret the prompts as permission to launch lanes concurrently:

1. T1A — Agent thread store/API/lifecycle
2. T1C — lexical recall core and evaluation fixtures
3. T1B — Web BFF and thread UX (read `askdb-web/AGENTS.md` and current local Next.js docs first)
4. T1 integration — server-backed chat and history assembly
5. T2 — recall wiring and runtime snapshot identity
6. T3A — query-example review and corpus governance
7. T3B — safe SQL template binding
8. T3C — business-rule candidate/review lifecycle
9. T4 — publication, suppression, and activation recovery
10. T5 — integrated acceptance and operations

The integration owner alone edits `api/app.py`, the migration registry, shared chat schemas/routes, `agent/graph.py`, and the runtime/publication files named in the implementation plan. Feature lanes add their own route/schema/migration modules and hand registration/ordering work to that owner.

## Lane prompts

### T1A — Agent thread store/API/lifecycle

> Implement T1A from the plan. Own only `api/routes/threads.py`, `api/schemas/threads.py`, conversation-memory/store modules, feature migration module(s), and focused Agent tests. Do not edit `api/app.py`, `api/routes/chat.py`, or `api/schemas/chat.py`; the integration owner will register routers and own the shared chat contract. Reuse the existing `chat_thread_data_sources` owner/source registry and Principal/source grants. Implement owner-scoped persistence, expected sequence and idempotency primitives, thread create/list/delete-impact/delete, retention/expiry, deletion coordinator hooks, and journal ordering exactly as the design specifies. Keep writes short and never hold a DB transaction across model, Wren, or SSE work. Stop if the current database boundary cannot safely support the designed owner/source transaction and report the concrete mismatch.

### T1C — Lexical recall core/evaluation

> Implement T1C from the plan as a pure recall/evaluation lane. Own the pure scorer, recall domain records, fixtures, and evaluation tests only. Implement NFKC + casefold, full ASCII/SQL identifiers plus tokens, adjacent CJK bigrams, BM25 `k1=1.2,b=0.75`, title/term weight 2, body/definition weight 1, stable document-ID tie-breaks, positive-score-only results, and source/status/digest filtering. Do not edit corpus persistence, `agent/graph.py`, `runtime_manager.py`, or runtime snapshots. Include the fixed ≥50-question evaluation shape and prohibited IDs; do not claim answer-quality evidence from retrieval metrics.

### T1B — Web BFF/thread UX

> Implement T1B from the plan only after reading `askdb-web/AGENTS.md` and the installed Next.js local documentation. Own the thread BFF routes and client thread/chat adapter/UI files listed under T1B. Preserve session, Bearer forwarding, Origin, CSRF, request allowlist/size limits, SSE contracts, and local result-artifact separation. Do not let browser history choose the server-bound source or become trusted memory. Coordinate against the frozen T1A request/response shapes; do not invent parallel API fields.

### T1 integration — Server-backed chat/history

> Implement the T1 integration section after T1A/T1B/T1C contracts are reviewed. You are the sole owner of `api/app.py` router registration, `api/routes/chat.py`, `api/schemas/chat.py`, `api/streaming.py`, `application/chat.py`, and `agent/graph.py`. Change `/v1/chat` to the current-turn protocol while retaining the bounded legacy window. Assemble only sanitized natural-language history and bounded typed recall; persist idempotent turns and preserve SSE replay semantics. Keep current source authorization and SQL gate ordering. Do not persist SQL, result rows, tool payloads, raw exceptions, or model reasoning.

### T2 — Recall wiring/runtime identity

> Implement T2 after T1 integration and T1C. Wire typed, bounded source-context and approved-query recall; compute the canonical semantic digest; add memory revision to cache identity; prepare before activation; retain old runtime leases. You are the runtime integration owner and must remain the same owner for T4. Do not modify the shared graph except through the T1 integration owner. Do not add Wren memory extras or embeddings in M2a.

### T3A — Query-example review/corpus

> Implement T3A from the plan. Resolve `data_source_id` from the authenticated submitter's active server thread; never accept it as authority from the request body. Use the frozen typed `:name` SQL-template contract and keep parameter values out of the shared corpus. Only global `admin` reviews in V1. Approval creates an immutable `prepared` per-source corpus revision and remains distinct from activation; pending/rejected/revoked/stale-digest records never recall. Store deterministic JSON snapshots and SHA-256 digests outside Wren revisions, with 30-day superseded retention. On activation, replace raw source-thread/turn IDs with keyed hashes from the independent encrypted journal. Thread deletion and expiry remove linked non-active candidates; deletion impact preview and its five-minute compare-and-set hash include affected query-candidate IDs/count. Published examples remain source-shared after thread deletion/expiry. Admin revoke is journaled and suppresses online recall immediately, independent of rebuild. Enforce at most 20 unresolved candidates per submitter/source/type, 20 per thread/type, and 10 new rule-plus-query candidates per submitter/hour with HTTP 429 `Retry-After`. Use keyset pagination ordered by `(created_at DESC, id ASC)`, with cursors bound to source, filters, and caller scope. Do not weaken the current SQL gate. The integration owner may register the route and additive migration in this serial no-Git checkout; otherwise keep feature files separate and hand off exact registration edits.

### T3B — Safe SQL template binding

> Implement T3B as an isolated SQL-template module. Parse using SQLGlot for the declared connector dialect; accept only the frozen named `:name` placeholder grammar; bind typed values through AST nodes; reject missing, extra, positional, malformed, nullable/type-invalid parameters and non-single/read-write statements; then re-run the current read-only policy, Wren `dry_plan`, and `dry_run` both during approval and each use. Never use regex substitution or string interpolation. Do not weaken or duplicate the existing Wren query gate and do not edit corpus publication.

### T3C — Business-rule candidates/review

> Implement T3C from the plan. Own business-rule domain/application/API/repository modules, `business_rule_origins`, and a feature-owned migration. Reuse current Principal/source grant; only global `admin` reviews in V1. Store structured, bounded rules, not full conversation text. On thread deletion/expiry, invoke the shared deletion participant and suppress stable rule IDs before Wren rebuild. Do not edit `api/app.py`, migration registry, Wren builder/settings, runtime manager, graph, or Web files; the integration owner registers the route/migration, and T4 owns Wren publication.

### T4 — Publication/runtime recovery

> Implement T4 as the sole runtime/publication owner after T3A/T3B/T3C. Coordinate query-corpus and Wren-rule generations, durable operations, immediate suppression fences, per-source serialization, restore reconciliation, active pointers, and lease-aware 30-day cleanup. The production journal is authoritative for post-snapshot deletion/revocation events. Test crashes between journal append and DB apply, watermark correctness, fail-closed recovery, old-lease behavior, and rollback non-resurrection. Do not expose chat until journal replay, index rebuild, and suppression verification finish.

### T5 — Integrated acceptance/operations

> Execute T5 only after all implementation tasks are reviewed. Run tests or verification commands only when the user explicitly requests them in the active task. Cover authorization, thread, SSE, recall, SQL-gate, deletion, retention, restore, and operational behavior; restore a snapshot predating deletion/revocation and prove journal replay prevents resurrection before chat opens. Report each gate separately; do not infer business accuracy from SQL execution or recall Hit@k.
