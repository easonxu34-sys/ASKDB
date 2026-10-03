# ASKDB Agent Memory System Implementation Plan

**Required sub-skill:** `superpowers:executing-plans` for implementation; use `superpowers:using-git-worktrees` before isolated parallel implementation and `superpowers:verification-before-completion` before reporting completion.

## Goal

Implement the approved design in [Agent记忆体系设计.md](../../Agent记忆体系设计.md) without replacing the FastAPI + LangChain/LangGraph + WrenToolkit stack. Deliver durable thread continuity, source-scoped read-only recall of approved query examples, an auditable review/publish path, and explicit gates for identity, deletion, SQL safety, and runtime activation.

This is a development plan only. No product code or tests are changed by this document.

## Architecture

- The existing `chat_thread_data_sources` binding remains the authoritative thread-to-source registry. Evolve its migration/schema as needed; do not create a second table that can disagree about thread owner or source.
- The server becomes authoritative for natural-language turns and summaries. The browser keeps a display cache, including result artifacts, but browser-submitted history is not trusted as server memory.
- Wren MDL and reviewed rules remain the business-semantics authority. Query examples are source-scoped evidence, never authorization or a replacement for current schema.
- A query example can be recalled only when approved and compatible with the active semantic digest. Candidate content is never recalled.
- Keep the current deterministic SQL checks, Wren `dry_plan`, `dry_run`, and query execution order. Memory must not create a bypass.
- Publish a new memory generation only after corpus validation/index construction succeeds. Existing runtime leases continue to use their original immutable runtime snapshot.
- Do not enable shared server-side threads for unauthenticated multi-user access. The current authenticated principal and data-source grants must be checked on every thread operation.

## Tech Stack

- Python 3.11+ service, FastAPI, Pydantic, SQLite for single-instance development, PostgreSQL for shared/multi-worker production, LangChain/LangGraph, WrenToolkit, SQLGlot.
- Next.js web client and existing BFF routes, assistant-ui adapters, browser storage for display cache.
- Current locked Wren packages are `wren-langchain 0.2.0` and `wrenai 0.15.0`. Treat Wren memory extras and embedding backends as optional until a compatibility/evaluation task proves they are needed.
- Planned verification commands (to run during implementation, not during planning):
  - `cd askdb-agent && uv run pytest -q`
  - `cd askdb-web && node --test tests/*.test.mjs`
  - `cd askdb-web && npm run lint`

## Spec

Use [Agent记忆体系设计.md](../../Agent记忆体系设计.md) as the behavioral source. The detailed task breakdown below resolves code ownership and sequencing; the following protocol choices must be frozen before implementation starts:

1. **Thread and source ownership:** server-issued opaque thread IDs; persisted owner field/migration for existing bindings; loss-of-grant behavior. Chat, turn/history/export, creation, and edits require current source grant. After grant revocation, the owner can still list a minimal management row, preview deletion impact, and delete; these endpoints expose no conversation content.
2. **Turn and SSE idempotency:** uniqueness of `(thread_id, turn_id)`, behavior for a repeated key with a different request body, and reconnect behavior while a turn is running or after it completes. Persist only sanitized natural-language history. Define whether a replay includes a final text/status event while SQL and result rows remain only in the browser display cache.
3. **Legacy history migration:** import at most the newest 500 complete turns / 2 MiB sanitized UTF-8 per thread, in requests no larger than 64 KiB; only eligible user/assistant natural language is imported. Older cache remains display-only. SQL/result blocks are excluded.
4. **Retention and multi-instance mode:** all times are UTC. Thread TTL is 30 days since the last accepted user turn (`expires_at <= now_utc` expires); manual deletion and automatic expiry both delete linked business rules, while published query examples remain source-shared. Pending candidates have a 90-day maximum and expire sooner with the source thread. Superseded Wren and query-corpus revisions are retained 30 days from supersession; backups 30 days from creation; active runtime leases may delay physical cleanup until release. Query-example revocation suppresses online recall immediately, and neither rollback nor restore may reactivate a revoked entry. Retain minimal suppression records until no retained revision/backup can resurrect content. A rolling snapshot alone is insufficient: deletion/revocation tombstones need an independently durable append-only journal with a snapshot high-water mark, replayed before restored service accepts requests; this journal is mandatory for production. Initial memory-enabled deployment is one Agent process. OIDC/SSO is not required for the existing local principal.
5. **Canonical stores:** Wren revision semantic config/project files are authoritative for business rules; source-scoped immutable JSON revisions are authoritative for NL→SQL examples. Candidate DB rows and audit events are workflow metadata only. Neither rule text nor query-example content may have a second mutable authority.
6. **Reviewer identity:** source-granted member may submit; existing global `admin` approves, rejects, clarifies, revokes, and publishes. Existing `AuthStore.can_access_data_source` grants an active global admin access to every enabled source; memory routes still check source existence/enabled state and actor role. Thread deletion by its owner cascades to linked business-rule memories, with explicit UI confirmation; automatic TTL expiry performs the same cascade without a prompt. Published query examples are not thread-owned after approval and survive thread deletion/expiry.
7. **Semantic digest:** compute it from canonical compiled `target/mdl.json`, sorted rule paths and bytes, and connector type; exclude credentials and generated unstable metadata. Keep Wren semantic revision and memory corpus revision separate.
8. **Recall and query template contract:** define normalized question, parameter placeholders, allowed SQL dialect/connector, deterministic lexical ranking (including Chinese tokenization), tie-breaking, and SQLGlot AST-based parameter binding. Never interpolate parameters with regex/string replacement.
9. **Runtime publication:** initial single-process guarantee; serialize Wren apply and rule publication per source; bind jobs to explicit target revisions; durable operation recovery and startup reconciliation. Multi-worker generation acknowledgement is deferred.
10. **Context budget:** every memory-enabled model profile must explicitly configure `context_window_tokens`, `max_output_tokens`, and a provider-compatible `tokenizer_id`; do not infer an unknown context window or tokenizer from model names. Reserve budget for system policy, current question, recent turns, summary, and at most three examples.
11. **Deletion contract:** `GET /v1/threads/{id}/deletion-impact` returns counts, up to 20 rule-term labels, and a five-minute `impact_version`. `DELETE` supplies that version plus explicit confirmation and an idempotency key; the server recomputes impact transactionally and returns 409 for reconfirmation if it changed. Deletion operation status distinguishes online suppression/removal from historical artifact expiry.
12. **Rule identity and deletion lineage:** persist a stable 32-hex `business_rule_id` in a Wren-safe rule name (`askdb_br_<id>`); the current builder uses rule name as the generated Markdown filename. After Wren activation, clear candidate body/source-turn fields and retain only a minimal `(data_source_id, business_rule_id, source_thread_id)` origin row so thread expiry/deletion can cascade without preserving conversation text. On cascade, write suppression first, then clear the plain thread link and retain only a keyed non-reversible audit hash. Recall and suppression map by this stable ID, never by fuzzy term matching.
13. **Deletion retry and recall evaluation:** deletion transitions through `QUEUED → SUPPRESSED → REMOVING_REVISION → COMPLETED_ONLINE`; build/activation errors remain suppressed and retry with 1-minute exponential backoff capped at 1 hour, alert admin after 5 consecutive failures, and do not auto-abandon. A pending Wren draft is `BLOCKED` until resolved. Initial lexical release requires at least 50 gold questions, schema/rule Hit@5 ≥80%, query-example Hit@3 ≥80%, and zero cross-source/pending/revoked/stale-digest leakage. Vector retrieval must improve relevant Hit@k by at least 5 percentage points with zero leakage.
14. **Backup/restore consistency:** snapshot SQLite with its online backup API and copy only immutable Wren/corpus artifacts referenced by the captured generation; verify checksums and write the final backup manifest last. The encrypted, append-only tombstone/revocation journal is durable and stored independently of the rolling snapshot set, so restoring an older snapshot cannot roll it back. Events contain a strictly increasing global sequence/event ID, type, source and stable rule/example IDs, timestamp, and integrity data, never memory bodies. Append and durably flush the journal event first; then apply events in sequence order and advance `journal_applied_seq` only across a contiguous applied prefix in the primary DB transaction. Return success only after both are durable. If journal append fails, return a retryable error; if append succeeds but DB application fails, close that source's recall until startup/runtime reconciliation catches it up. A backup manifest takes its high-water mark from `journal_applied_seq` inside the captured DB snapshot, not from the journal tail. Retain journal records for at least 60 days and until every restorable artifact that can contain the deleted/revoked content has expired. Restore verifies the snapshot and manifest, replays journal events with sequence greater than the captured watermark idempotently, rebuilds indexes and active pointers, verifies suppression, and only then enables chat; missing, corrupt, or discontinuous journal fails closed. This independent journal is a production launch requirement.

The initial release should choose deterministic lexical recall and a single service instance. Add vector recall or multi-worker publication only after their compatibility and operational contracts are tested.

## Global Constraints

- Before any web code edit, follow `askdb-web/AGENTS.md` and read the installed Next.js documentation for the repository's current version.
- Before parallel code changes, establish usable Git metadata and clean, reviewable branch/worktree boundaries. At planning time, `git rev-parse --show-toplevel` failed at the workspace root, `askdb-agent`, and `askdb-web`; managed worktrees cannot isolate this checkout as it stands. Do not have multiple workers edit the same unversioned working tree concurrently.
- Keep `askdb-agent` and `askdb-web` changes independently reviewable. Only the integration owner edits shared protocol definitions, `api/app.py` router registration, the migration registry, `api/routes/chat.py` / `api/schemas/chat.py`, `agent/graph.py`, `runtime_manager.py`, `application/wren_settings.py`, `integrations/wren_project.py`, and publication coordination. Feature lanes add their own router/schema/migration modules and hand the registration/ordering patch to the integration owner.
- Keep database transactions short. Never hold a write transaction while calling a model, Wren, or streaming SSE.
- Scope every persistent conversation operation by trusted principal and every query-memory lookup/write by `data_source_id` in the storage query itself. A `thread_id` is not an authentication credential.
- Persist sanitized user/assistant natural-language turns only. Exclude tool payloads, SQL display blocks, result rows, secrets, raw exceptions, and chain-of-thought. Keep the frontend result artifact separate from server conversation memory.
- A replayed or recalled SQL statement always passes the existing current-source, parser, read-only, `dry_plan`, `dry_run`, and query gates.
- Do not add a memory write tool to the model's available tools. Application services create candidates and publish only after review.
- Every lifecycle transition must be auditable and idempotent where network retries can occur. Deletion, expiry, revocation, and source removal must invalidate recall before physical cleanup finishes.
- Do not install Wren memory extras or an embedding database in M1/M2a. First measure lexical recall against a fixed evaluation set.
- Do not change API/SSE contracts silently. Retain a documented legacy window and tests until the Web client migration is complete.

## Dependency Map and Parallelism

```text
T0 contracts + version-control boundary
  ├── Lane A: Agent thread store/API ──┐
  ├── Lane B: Web BFF/thread adapter ──┴── T1 server-backed thread continuity integration
  └── Lane C: recall core/evaluation ─────┐
                    T1 integration ───────┴── T2 runtime recall wiring (same graph.py owner)
                         ↓
      ┌──────────────────┴──────────────────┐
 T3a query-example review      T3b SQL AST     T3c business-rule review
      └───────────────────────┴──────────────────────────┘
                         ↓
       T4 corpus publication/runtime activation (one owner)
                         ↓
           T5 system integration + acceptance
```

**Can the work be parallelized?** Yes, by module and after T0 freezes the wire/data contracts. Three initial lanes are practical: Python conversation persistence/API, Web BFF/UI migration, and pure lexical recall/evaluation. T1 integration follows the thread API and Web contract; the same named Agent integrator retains ownership of `agent/graph.py` through T2 so the file is never concurrently edited. Later, candidate/review API and SQL-template validation can proceed in parallel after their record and state-transition contracts are fixed. T3C owns business-rule records, APIs, origin mapping and suppression participant; T4 alone owns Wren revision building, `application/wren_settings.py`, active generation pointers and runtime activation. The absence of Git metadata means this is logical parallelism only today; safe concurrent edits require restoring/creating the real repository boundaries first. If that setup is unavailable, execute lanes sequentially or assign strictly disjoint files and integrate through reviewed patches.

Use `gpt-6-luna` with high reasoning for contract/security reviews, runtime integration, and acceptance analysis. The available delegation interface exposes model and reasoning effort but no separate “fast” control; use Luna for bounded implementation/review tasks without claiming a distinct fast setting.

## Tasks

### T0 — Establish implementation boundary and freeze contracts

**Owner:** lead/integrator. **Parallelism:** none; prerequisite for all lanes.

1. Identify or restore the real Git repository boundaries for the workspace, `askdb-agent`, and `askdb-web`; record clean starting revisions and confirm no user changes would be overwritten. Use managed worktrees once the repository is available.
2. Review `docs/Agent记忆体系设计.md`, `docs/Agent架构骨架与开发规范.md`, `askdb-agent/AGENTS.md` if present, and `askdb-web/AGENTS.md`; read current Next.js local docs before Web implementation.
3. Freeze Pydantic request/response models, SSE event envelope, numbered migration contract/owner, UTC clock/boundary semantics, source/thread ownership, idempotency/replay, 500-turn/2-MiB local-history import cap, canonical stores, semantic digest, global-admin source access, deletion-impact preview/CAS, manual and TTL deletion cascade, published rule-origin mapping, candidate states/TTLs/quotas, stable Wren rule rendering/IDs, token counter, recall quality thresholds, deletion retry policy, restore journal/high-water/applied-sequence protocol, query-corpus supersession metadata and retention, and single-process topology in a short decision record. Feature migrations live in separate modules; one integrator owns ordering/registry changes.
4. Publish task ownership and acceptance commands. Keep shared schema/protocol edits with the integrator.

**Acceptance:** an API/event example and migration note exist; each implementation lane has an immutable interface and disjoint file ownership; clean Git starting points exist for concurrent work.

### T1A — Agent conversation persistence and thread lifecycle

**Owner:** Agent backend lane. **Parallelism:** parallel with T1B and T1C after T0.

1. Add a serialized, re-entrant `schema_migrations` runner before adding turn/summary/idempotency/lifecycle/deletion/expiry tables. The current `WrenSettingsStore._connect()` uses inline `CREATE TABLE IF NOT EXISTS` plus ad-hoc column checks; do not extend it with more unversioned DDL. Evolve `chat_thread_data_sources` as the single thread/source/owner registry; do not create a competing registry.
2. Implement `integrations/conversation_store.py` and `application/conversation_memory.py`: owner-scoped load/append, sequence checks, duplicate `turn_id` handling, summary compare-and-set, TTL/tombstone, delete, and restore behavior.
3. Keep all writes short; use a clear in-progress/completed/failed turn state. Duplicate IDs with a different normalized request hash fail deterministically. Model and Wren calls occur outside transactions.
4. Add authenticated thread create/list/delete-impact/delete routes and recheck principal, source grant, and source binding. After source revocation, list only minimal owner metadata and allow owner deletion; block turn/history/chat access.
5. Implement one idempotent deletion coordinator and expiration sweeper. Manual delete requires a five-minute impact version and explicit confirmation; the transaction recomputes impact and returns 409 if it changed. A single-process five-minute sweeper processes 30-day inactivity expiries before chat startup; both paths tombstone thread content and invoke linked-memory deletion participants. T3C supplies the business-rule participant before shared-rule release. Retain pending candidate linkage until source-thread deletion; after publication move only the minimal source-thread/rule-ID mapping to `business_rule_origins`, then suppress and redact it atomically on cascade. Append and durably flush the deletion event to the independent journal before applying suppression; serialize event application in global sequence order and persist the sequence with suppression in the DB, advancing only over a contiguous applied prefix. Do not return 202 until both are durable. If journal append fails, return a retryable error; if the event is appended but DB application fails, keep the affected source closed to recall until reconciliation catches up. Startup must replay/reconcile the journal against `journal_applied_seq` before opening chat.
6. Add migrations and tests for legacy thread bindings, concurrent append, retry idempotency, owner mismatch, grant revocation, impact-token expiry/change, tombstone-before-cleanup, expiry cascade, crash after journal append but before DB apply, snapshot watermark taken from the captured DB sequence, backup restore/replay-before-chat, and cleanup status.

**Primary files:** `askdb-agent/src/askdb_agent/api/routes/threads.py` (new), `api/schemas/threads.py` (new), `application/conversation_memory.py` (new), `integrations/conversation_store.py` (new), a feature-owned migration module, and focused tests. Do not edit `api/app.py`, the chat route, or chat schema; the integration owner registers feature routers and owns the shared chat contract. A single T0 integrator owns migration registry/ordering.

### T1B — Web BFF and server-authoritative thread UX

**Owner:** Web lane. **Parallelism:** parallel with T1A/T1C after T0; code against the frozen contract or a mock.

1. Add BFF routes for thread creation/list/delete-impact/delete and preserve existing session, Bearer forwarding, Origin, and CSRF checks on every mutation. Before delete, fetch server impact counts/labels; show pending/published business rules will be removed and may affect other conversations, while published query examples remain.
2. Update `local-thread-adapter` to use server-issued opaque IDs and server metadata. Keep local display history as a cache, scoped by authenticated account and thread; clear or partition it on logout/account switch and respect server tombstones/TTL. Require confirmation tied to `impact_version`; on 409 refresh the preview and ask again. Show online removal versus historical revision/backup retention status.
3. Update `agent-chat-adapter`/`chat-request` to send only the current user turn, `thread_id`, `turn_id`, selected source/profile as allowed by the frozen API. Never let client history choose the bound source.
4. Define SSE reconnect/replay handling. Correlate token/status/result/done/error/replay events with `(thread_id, turn_id)` and sequence; update one assistant message on retry instead of duplicating it. Keep displayed SQL/rows in a separate local result artifact and out of future model history.
5. Implement bounded legacy import according to T0. Do not blindly promote every browser assistant bubble or result payload to trusted server memory.
6. Add tests for CSRF on all write routes, request allowlist/size limits, import filtering, cache isolation/logout, SSE replay/retry, result artifact separation, and tombstone behavior.

**Primary files:** `askdb-web/app/api/chat/route.ts`, new BFF thread routes, `lib/local-thread-adapter.tsx`, `lib/agent-chat-adapter.ts`, `lib/chat-request.ts`, `app/assistant.tsx`, assistant-ui thread/list components, and Node tests.

### T1C — Query-memory data contract, lexical recall, and evaluation set

**Owner:** recall lane. **Parallelism:** parallel with T1A/T1B after T0; no runtime graph edits.

1. Define a typed source-scoped `QueryExample` and corpus manifest including normalized question, parameterized SQL, connector, `wren_revision_id`, `mdl_digest`, review state, separate publication status/operation ID, source provenance, timestamps, and content hash.
2. Implement the deterministic lexical scorer as a pure adapter over compiled MDL/rules and corpus fixtures. Use Unicode NFKC + casefold, split ASCII/SQL identifiers while retaining the whole identifier, adjacent CJK bigrams, BM25 `k1=1.2/b=0.75`, `2×` title/term plus `1×` body/definition score, stable document-ID tie-breaking, source/digest/status filters, and return only positive-score documents. Apply top-5 to schema/rules and top-3 to query examples; T3A/T3C wire active stores and the suppression ledger into the adapter.
3. Add corpus and recall fixtures for same-name metrics across sources, no-match, stale digest, Chinese/English paraphrases, revoked/pending records, and ranking ties.
4. Build a fixed set of at least 50 questions: ≥20 schema/rule, ≥20 query-example, and ≥10 no-match/isolation questions, with expected and prohibited document IDs. Define Hit@k as the share of applicable questions with ≥1 expected ID in the top k. Release lexical recall only at schema/rule Hit@5 ≥80%, query-example Hit@3 ≥80%, zero no-match false recall, and zero source/status/digest leakage. Record per-category Hit@k/precision@k; do not hide category failures in an aggregate. Use the same set to compare any Wren extra/embedding candidate, which must improve relevant Hit@k by ≥5 percentage points with zero leakage.
5. Keep retrieval as an application-level read-only adapter at first. Do not mutate `.wren/memory`, invoke write tools, or edit shared graph/runtime files in this lane.

**Primary files:** `askdb-agent/src/askdb_agent/application/memory_context.py` (new), a pure `application/lexical_recall.py` (new), domain recall records, and recall tests/eval fixtures. Keep query governance/corpus persistence in T3A-owned modules; avoid shared `query_memory.py`, `agent/graph.py`, and `application/runtime_manager.py` in this lane.

### T1 integration — Server-backed chat and history assembly

**Owner:** Agent integrator. **Parallelism:** sequential integration after T1A/T1B contracts pass.

1. Change `/v1/chat` from client-authoritative full-history behavior to a current-turn request while retaining an explicit, bounded legacy protocol window.
2. Assemble system policy, current source/revision context, recent turns, low-trust summary, and bounded recall within configured token budget. Remove SQL/result blocks and tool payloads from assistant display events before persistence.
3. Persist user and sanitized assistant turns with idempotency and stable event correlation. Verify failures/cancellations do not leave a completed turn with partial content.
4. Keep existing runtime lease, current-source grant check, and SQL gate order intact.
5. Add API/streaming tests for follow-up continuity, empty/new thread, same-thread isolation, source changes/revocation, truncated stream, duplicate request, and old-client compatibility.

**Primary files:** `askdb-agent/src/askdb_agent/api/app.py`, `api/routes/chat.py`, `api/schemas/chat.py`, `api/streaming.py`, `application/chat.py`, `agent/graph.py` (single Agent integrator only), and related tests. This owner registers thread and later memory routers, and retains shared chat files plus `agent/graph.py` through T2; no feature lane edits those files.

### T2 — Read-only recall wiring and runtime snapshot identity

**Owner:** Agent runtime integrator. **Parallelism:** sequential after T1C and T1 integration.

1. Add the source-context and approved-query recall output to the application-level context assembler. Return typed, bounded, provenance-bearing references, not system-policy text.
2. Compute canonical semantic digest from compiled MDL, sorted rules, and connector type. Keep credentials out of the digest and all logs.
3. Extend `RuntimeSnapshot`/cache identity to include `memory_revision` (or corpus digest) independently from Wren revision and model profile revision.
4. Prepare the complete candidate runtime/index before activation. On failure, leave the old DB pointer and old runtime serving; add reconciliation for crash windows.
5. Preserve old runtime leases while requests are running. Verify changes to corpus, MDL, and model profile invalidate only the appropriate cache key.
6. Run offline recall eval and compare current no-memory baseline to top-3 lexical recall.

**Primary files:** `application/runtime_manager.py`, `application/wren_settings.py`, `integrations/wren_project.py`, `integrations/wren_memory.py`, `tests/test_runtime_manager.py`, and new source-context/recall tests. T2 and T4 are sequential tasks owned by the same runtime integrator for these shared files; T2 uses the T1 integrator as the sole `agent/graph.py` owner, with no second editor for that shared file.

### T3A — Candidate review workflow and source corpus

**Owner:** memory governance lane. **Parallelism:** parallel with T3B after T0 contract and T2 recall interfaces stabilize.

1. Add candidate create/list/review/revoke API using the authenticated admin principal. Explicit-save and correction flows create `PENDING` candidates; pending/rejected/revoked items never enter retrieval. Approval and active corpus publication are separately visible; failed/unpublished approved records are not searchable.
2. Store reviewer, time, reason, expected previous status, source/revision/digest, source turn reference, and canonical content hash for every transition. Enforce compare-and-set state transitions.
3. Implement canonical corpus revisions per source, with deterministic JSON serialization, atomic publication, diff/export, and rebuild from canonical records. Revision JSON/manifests remain immutable; keep `activated_at`, `superseded_at`, and `delete_after` in database lifecycle metadata so the 30-day TTL starts at supersession without rewriting a revision. Choose exactly one mutable authority as specified in T0; candidate DB rows must not silently disagree with approved corpus files.
4. On MDL digest change, mark examples for revalidation and make them ineligible before the new runtime is exposed.
5. After activation, replace source thread/turn identifiers with keyed journal hashes; do not retain raw provenance IDs in the active candidate row or canonical corpus.
6. Include linked pending query-candidate IDs/count in thread deletion preview and the five-minute impact hash. Thread deletion and expiry clear their content with the thread; active/published query examples survive and have already had raw source identifiers redacted.
7. Enforce at most 20 unresolved candidates per submitter/source/type, 20 per thread/type, and 10 combined business-rule/query-example submissions per submitter/hour. Return HTTP 429 with `Retry-After`; duplicate idempotent retries do not consume quota.
8. List candidates with stable keyset pagination ordered by `(created_at DESC, id ASC)` and bind cursors to source, review filter, and caller scope.
9. Add tests for admin-only review, submitter withdraw, 90-day expiry, source-thread deletion/expiry cleanup of pending candidates, cross-source access denial, double approval, stale digest, immediate suppression on revoke before index refresh, crash during publication, rollback non-resurrection, supersession-based 30-day cleanup after the last runtime lease, and reproducible corpus digest.

**Primary files:** `application/query_memory.py`, new `api/routes/memories.py`, `api/schemas/memories.py`, candidate/review persistence, canonical corpus adapter, and governance tests.

### T3B — SQL template validation and safe parameter binding

**Owner:** SQL safety lane. **Parallelism:** parallel with T3A after the template contract is frozen; no runtime publication edits.

1. Define a small placeholder grammar and typed parameter contract. Reject missing, extra, malformed, and unsupported parameter types.
2. Parse templates with SQLGlot for the declared connector dialect. Bind values through AST nodes and serialize to the same dialect; never use regex replacement or concatenate user-controlled values.
3. Require exactly the expected single read-only statement and re-run the current parser/policy, Wren `dry_plan`, and `dry_run` every time the example is used.
4. Test quote/comment payloads, date and numeric types, nulls, repeated parameters, dialect edge cases, multiple statements, DDL/DML, and unsupported functions.

**Primary files:** a dedicated `application/sql_template.py` or `integrations/sql_template.py` and focused tests. Do not weaken or duplicate the existing `tools/wren_query.py` security gate.

### T3C — Cross-session business-rule candidates and Wren publication

**Owner:** business-semantics lane. **Parallelism:** candidate schema/API/review can proceed with T3A/T3B after T0; Wren revision publication integrates sequentially with T4.

1. Add a `business_rule_candidates` table and append-only events in the current thread/source transaction database. Store normalized term/definition, optional MDL model/field references, source/thread provenance, base Wren revision/digest, content hash, submitter/reviewer, and separate review/publication states. Do not store full conversation text.
2. Let a source-granted member submit and view their own candidates. V1 uses the existing global `admin` for review. Add idempotent submit, admin clarification request/submitter response, approve, reject, withdraw, revoke, and operation-status contracts; candidates expire after 90 days and the submitter can withdraw before publication.
3. Validate references against the candidate's base MDL. Conflicting existing terms require admin clarification or rejection; V1 does not silently merge/overwrite active rules. If the active revision/digest changes, require revalidation.
4. On approval, derive a new immutable Wren revision from the exact active semantic config and copy secrets through the existing encrypted revision mechanism. Set rule `name` to `askdb_br_<32-hex business_rule_id>` because the current builder uses `name` as the generated Markdown file stem; keep the human term in content. Never edit the active project directory or query-example corpus.
5. Serialize Wren draft create/edit, apply, rollback, rule publication/removal, and source deletion through one durable per-source operation lock/generation CAS. A pending settings draft blocks publication with a visible reason. Bind every publish operation to explicit base/target revision IDs; do not resolve mutable `draft_revision_id` later in a background task. Recover unfinished durable operations on startup; do not rely on the current Wren apply's process-local `asyncio.create_task` pattern.
6. On manual thread deletion or automatic TTL expiry, participate in the shared deletion coordinator: remove candidate content, suppression-tombstone linked published `business_rule_id`s immediately, and queue one Wren revision removing all linked rules. Honor suppression through rollback/reindex and historical artifact retention; online removal is complete only after active revision change, but recall is blocked immediately.
7. Add tests for member/admin authorization, repeated submit key, clarification hash/version, 90-day candidate upper bound and earlier thread expiry, origin-row creation and cascade after publication, stale digest, conflicting terms, stable rule-file ID mapping, Wren draft conflict, failed publish retry, source deletion, deletion impact re-confirmation, suppression-before-rebuild, rollback non-resurrection, and async status.

**Primary files:** new `domain/business_rules.py`, `application/business_rule_memory.py`, a feature-owned migration/repository, new `api/routes/business_rules.py`, `business_rule_origins` persistence and deletion-coordinator participant. T3C does not edit `application/wren_settings.py`, `integrations/wren_project.py`, `runtime_manager.py`, `agent/graph.py`, or Web files; it exposes a frozen publication/suppression interface for T4. T1B owns the Web BFF and delete UX.

### T3 Web UX — Chat submission and memory governance

**Owner:** Web lane, after the T3 API contracts stabilize. This section defines the user and administrator interaction; it does not change the chat or memory APIs' authority boundaries.

1. Put **“提交为查询示例”** and **“提交为业务规则”** in the assistant answer's `…` menu. Do not add a persistent memory button beside the composer: submission belongs to the answer that provides its context. Show “提交为查询示例” only when that answer contains a completed, successful database query.
2. For a query example, open a review form tied to the current data source. Pre-fill the user's question and the validated, parameterized read-only SQL template with its typed parameter schema. Never include concrete parameter values, query result rows, or the full conversation. Reject hard-coded literals inside WHERE/HAVING through the server-side SQL AST check before persisting a candidate. Show the exact content to be submitted and require an explicit submit action.
3. For a business rule, collect the term and definition, with optional MDL model/field references. Tie the candidate to the selected data source and originating turn; do not persist the surrounding conversation. Validate references and conflicts on the server against the candidate's base MDL.
4. Before submission, explain in plain language: **“提交后不会立即影响回答；管理员审核，并完成发布/激活后才会生效。”** A successful submission confirms receipt and links to **“我的提交”**; it must not imply that the content is already in use.
5. Add **“记忆管理”** to the existing settings/admin navigation for administrators. Provide separate **“查询示例”** and **“业务规则”** lists, source and status filters, a candidate detail view, and review actions allowed by the corresponding API. Candidate details show only submitted fields, submitter/time, source, base revision/digest, validation/conflict status, and transition history—not the full source conversation.
6. Show review state and effective/publication state separately. Query examples progress through pending review, approved/prepared, then explicit corpus activation before showing active. Business rules progress through pending review, clarification or approval, publication, and active. Rejected, withdrawn, stale/revalidation-required, revoked, publishing, failed, and removal-pending states need distinct labels and an actionable reason where available. “Approved” alone must never be displayed as “active”.
7. Keep authorization in the server: members may submit and view their own candidates; only administrators may review/publish/revoke; the UI calls the server through the authenticated Next BFF. Show loading, empty, permission, validation, conflict, retryable failure, and expired states without losing the form's unsent content.
8. The personal-memory exchange in the screenshot (“你记得我叫什么”) is outside T3 query-example/business-rule submission. Do not offer either T3 action for that turn; personal memory requires its separately scoped identity, consent, and controls.

**Web implementation scope:** add authenticated BFF routes for query-example and business-rule candidate workflows, answer-menu actions and forms, “我的提交” status view, and the admin governance page. Keep API responses and status transitions server-authoritative. Implementation is tracked in the dated progress ledger and focused T3 prompt.

### T4 — Corpus/Wren publication coordination and activation recovery

**Owner:** one runtime/persistence integrator. **Parallelism:** sequential after T3A/T3B/T3C; consumes all query-example, SQL-template, and business-rule publication paths.

1. Connect approved query corpus generation and approved business-rule Wren revisions to deterministic index/runtime preparation. A successful review must not imply that runtime activation succeeded; deletion/expiry/revocation suppression must become effective before asynchronous rebuild and fence concurrent publish and rollback.
2. Use prepare → validate → persist generation → activate/swap → retire-old lifecycle. Define recovery when the process stops between each stage.
3. Keep old snapshots available to in-flight leases; make the active generation discoverable after process restart.
4. For a single process, test atomic in-process swap plus durable active generation. If more than one worker is required, implement durable generation notification/acknowledgement or explicitly block that deployment mode; do not rely on process-local cache invalidation.
5. Test corpus/Wren build failure, activation failure, concurrent approvals, stale publish racing thread deletion or query-example revoke, un-applied Wren draft conflict, process restart, crash between journal append and DB apply, journal gap/corruption fail-closed behavior, rollback non-resurrection, old lease completion, thread deletion with linked rules, and source deletion. Cleanup must honor 30 days since supersession for both Wren and corpus revisions and wait for the final old-runtime lease before unlinking revision files.

### T5 — End-to-end acceptance, operations, and staged rollout

**Owner:** lead with lane owners. **Parallelism:** integrated verification only.

1. Run Agent and Web suites and linters listed in Tech Stack, then manual end-to-end checks for: refresh/restart follow-up, new thread isolation, source grant revocation, SQL/result exclusion from model history, duplicate SSE retry, deletion impact preview/reconfirmation, manual and automatic deletion cascade, published query-example survival, pending-candidate expiry, approved-only recall, MDL change invalidation, candidate review, and failed publish recovery. Restore a backup captured before a business-rule deletion or query-example revocation; prove its watermark comes from the captured DB `journal_applied_seq`, the independent journal is replayed idempotently first, and no content is resurrected before chat is enabled.
2. Verify one same-name metric question against two data sources and confirm no cross-source recall. Confirm a remembered unsafe SQL example still fails all existing gates.
3. Inspect logs/traces for accidental conversation, SQL parameter, result row, credential, raw exception, or memory text capture.
4. Document backup/restore for SQLite/PostgreSQL, generation-consistent backup manifests, independent tombstone journal/high-water replay, corpus export/rebuild, 30-day TTL cleanup for superseded Wren/query-corpus revisions and backups, suppression ledger retention, rollback procedure, deployment topology, reviewer role, and model-provider retention configuration.
5. Roll out in stages: server thread continuity; lexical read-only recall behind a flag; review/publish behind an admin-only route; embeddings only if eval demonstrates a material gain; personal preferences only after trusted identity and user controls ship.

## Verification and Acceptance Gates

- **Thread gate:** same thread continues after browser refresh and service restart; another thread/principal cannot load it; owner/source/grant is checked server-side; deleted/tombstoned threads are immediately unavailable.
- **Payload gate:** server history contains only filtered user/assistant text and summary; no prior SQL, rows, tool payloads, secrets, or raw exceptions; UI may separately retain result artifacts.
- **Retry gate:** same `(thread_id, turn_id)` and same request does not append duplicate turns or duplicate assistant bubbles; changed content under the same ID is rejected; SSE state is deterministic across reconnect.
- **Recall gate:** retrieval is source-scoped in the storage query, limited to approved records with the active digest, deterministic, and capped at three. Cross-source leakage eval is zero.
- **SQL gate:** every generated or recalled SQL goes through the existing parser/read-only policy, `dry_plan`, `dry_run`, and execution identity. No memory path changes data authorization.
- **Governance gate:** candidate approval/rejection/revocation is authenticated, audited, diffable, reversible, and corpus/index rebuild is reproducible.
- **Business-rule gate:** candidate is not recalled before publication; admin approval and runtime activation are separate; active source revision drift blocks stale rules; explicit thread deletion warns the owner, automatic 30-day expiry does not prompt, and both suppress linked published rules immediately before eventually activating a Wren revision without them. Published query examples survive thread deletion/expiry.
- **Deletion gate:** impact preview is owner-scoped; changed/expired `impact_version` requires fresh confirmation; owner deletion works after source grant revocation; operation status says `completed_online` only after content reads are blocked, linked rules are suppressed, and active revision removal is done. Historical immutable revisions and backups follow their own retention.
- **Publication gate:** candidate generation failure leaves the last good generation active; a restart recovers the durable active generation; no in-flight lease switches snapshots midway.
- **Quality gate:** evaluation includes references, topic changes, empty history, same-name cross-source metrics, correction, stale MDL, deletion/expiry, recall ranking, retry/replay, and lexical-vs-vector comparison if vector retrieval is proposed.
- **Compatibility gate:** API and SSE legacy behavior remains covered for the documented migration window; write BFF routes retain session and CSRF protections.

## Out of Scope for Initial Release

- Personal preference memory before trusted OIDC/SSO identity, ownership checks, consent, and user-facing view/edit/delete controls exist.
- Model-autonomous writes to Wren memory or automatic approval based on successful execution, user silence, or answer display.
- Cross-thread conversational similarity search, result-row retention, arbitrary embeddings infrastructure, and multi-agent rewrites.
- Multi-worker SQLite or a claim of cross-worker runtime consistency without a shared database and generation coordination.

## Open Operational Decisions

| Decision | Recommended initial value | Must be resolved by |
|---|---|---|
| Thread TTL | 30 days since last accepted user turn; manual delete and automatic expiry remove linked business rules; only manual deletion prompts | User confirmed |
| Pending candidate TTL | 90-day maximum; expires earlier with its source thread; submitter can withdraw; expired body removed while hash-only audit remains | V1 default |
| Deletion impact | Preview counts and up to 20 rule labels; 5-minute impact token; recompute under transaction and require reconfirmation on change | V1 contract |
| Backup TTL | 30 days; restore checks DB/corpus/Wren generation hashes, reapplies suppression ledger, and rebuilds indexes before serving | User confirmed default; recovery procedure required |
| Historical Wren revision TTL | Retain superseded revision artifacts for 30 days, then clean; rollback must honor suppression and cannot target expired revisions | User confirmed |
| Historical query-corpus revision TTL | Retain immutable superseded revisions for 30 days from supersession, then clean after runtime leases release; revoke suppresses active recall immediately, and rollback/restore cannot reactivate revoked entries | User confirmed |
| Tombstone journal for restore | Independent encrypted append-only durable journal; snapshot records high-water mark; restore replays later deletions/revocations and verifies suppression before serving | User confirmed; mandatory before production memory enablement |
| Initial persistence topology | One Agent process on current WrenSettingsStore SQLite; future multi-worker PostgreSQL migration includes Wren settings/catalog, thread bindings, and memory metadata together | V1 architecture constraint |
| Thread deletion | Manual delete has impact preview and explicit confirmation; automatic expiry uses same cascade without prompt. Published examples survive. Suppression is immediate; Wren cleanup is async. | User confirmed |
| Reviewer | Existing global `admin`; member with source grant may submit business-rule candidates | User confirmed |
| Canonical approved corpus | One versioned JSON authority per data source for query examples; business rules remain in Wren revisions | T0 contract |
| Recall backend | Deterministic lexical/CJK-aware ranking first | T1C/T2 evaluation |
| Vector index | Deferred; add only if evaluation shows lexical recall is insufficient | After T2 |
| Model context budget | `context_window_tokens`, `max_output_tokens`, and an explicit provider-compatible token counter per enabled model profile; no inferred window | T0 contract |
| Recall quality and delete retries | ≥50 questions including ≥10 no-match; schema/rule Hit@5 ≥80%, query-example Hit@3 ≥80%, zero no-match false recall/leakage; capped exponential retry and alert after 5 failures while suppression remains active | Recommended implementation default; freeze at T0 |
| Candidate submission quota | 20 open candidates per submitter/source/type; 10 new candidates per submitter/hour across types; idempotent retries excluded | Recommended anti-abuse default; freeze at T0 |
