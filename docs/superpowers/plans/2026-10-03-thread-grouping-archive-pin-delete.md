# Data-Source Grouped Threads, Archive, Pin, and Delete Implementation Plan

> **For agentic workers:** Use the executing-plans workflow to implement this plan task by task. The feature is authorized for inline execution in this session.

**Goal:** Make the Agent the authority for thread metadata and lifecycle, then expose data-source-grouped recent threads and archived-thread management in AskDB Web.

**Architecture:** Extend the existing SQLite conversation store with additive metadata, owner-scoped list revisions, CAS mutations, search, pagination, and archive-aware retention. Keep the Next.js BFF as the only browser-to-Agent path. Treat browser storage as a short-lived display/result cache, and use the existing assistant-ui remote thread adapter for navigation and thread lifecycle actions.

**Tech Stack:** FastAPI, Pydantic, SQLite, Next.js 16 App Router, React 19, assistant-ui `RemoteThreadListAdapter`, Tailwind CSS.

**Spec:** `docs/superpowers/specs/2026-10-03-thread-grouping-archive-pin-delete-design.md`

## Global Constraints

- Keep `agent_conversation_threads.status` meanings `active`, `deleted`, and `expired`; archive state is orthogonal.
- Scope all metadata and list operations by the authenticated thread owner; require current data-source authorization only for history reads and query execution.
- Keep the browser behind the Next.js BFF and set `Cache-Control: no-store` on thread metadata responses.
- Require `expected_metadata_revision` for title, pin, archive, and restore writes; reject stale cursors with `409 THREAD_CURSOR_STALE`.
- Search all matching records before pagination; order first by data-source name and ID, then the view-specific thread order.
- Do not import old localStorage-only sessions or treat localStorage as metadata authority; clear old thread indexes, histories, query-result artifacts, and unfinished imports once on upgrade.
- Keep the existing deletion impact preview, confirmation version, idempotency, tombstone, journal, and memory-suppression behavior.
- Archived active threads pause their remaining retention; a question while archived resets the frozen remainder to 30 days; restoring resumes from the frozen remainder.

---

### Task 1: Add server metadata schema and projection types

**Files:**
- Modify: `askdb-agent/src/integrations/memory_migrations.py`
- Modify: `askdb-agent/src/domain/conversation_memory.py`

**Interfaces:**
- Add nullable `title`, `archived_at`, and `retention_remaining_seconds`; add `is_pinned INTEGER NOT NULL DEFAULT 0` and `metadata_revision INTEGER NOT NULL DEFAULT 1` to `agent_conversation_threads` in a new migration.
- Add `agent_thread_list_revisions(owner_user_id PRIMARY KEY, revision)` and initialize existing owners to revision 1.
- Add a data-source display-name update trigger that increments revisions for owners with threads bound to the changed source.
- Extend thread projections with title, pin state, archive time, retention pause state, frozen seconds, current source name, and metadata revision; define explicit page and state projection dataclasses and conflict types.

- [x] Add the next numbered migration without changing any prior migration checksum or existing status semantics.
- [x] Initialize existing thread owners and create the source-name revision trigger.
- [x] Define the immutable domain projections needed by list pages, metadata CAS, archive/restore, and local-cache state reconciliation.

### Task 2: Implement owner-scoped thread listing and lifecycle in the store

**Files:**
- Modify: `askdb-agent/src/integrations/conversation_store.py`
- Modify: `askdb-agent/src/domain/conversation_memory.py`

**Interfaces:**
- `list_thread_page(owner_user_id, view, q, limit, cursor) -> ThreadPage` validates the cursor owner, view, normalized query, page size, and owner snapshot revision; it filters the full matching owner view before offset pagination. The legacy `list_threads(owner_user_id)` wrapper remains for in-process callers.
- `update_thread_metadata(owner_user_id, thread_id, expected_metadata_revision, title, is_pinned) -> ThreadMetadata` performs owner-scoped compare-and-swap and returns the current projection on conflict.
- `archive_thread(owner_user_id, thread_id, expected_metadata_revision) -> ThreadMetadata` and `restore_thread(...) -> ThreadMetadata` apply their lifecycle transition in one SQLite transaction.
- `get_thread_states(owner_user_id, thread_ids) -> tuple[ThreadState, ...]` returns only `active`, `archived`, `deleted`, `expired`, or `unavailable` projections for supplied IDs.

- [x] Add one helper for effective expiry that treats archived threads as valid while their frozen remainder is positive.
- [x] Apply archive-aware expiry to `_owned_thread`, `begin_turn`, and `expire_inactive_threads`; skip archived rows in the sweeper.
- [x] Bump the owner list revision atomically for create, title/pin changes, archive, restore, user turns, delete, and expire.
- [x] On an archived user turn preserve `archived_at`, reset frozen seconds to the store retention duration, update the last-user-turn timestamp, and keep the thread out of recent results.
- [x] Implement search on title and current source display name before pagination; sort recent and archived pages exactly as specified.
- [x] Implement cursor snapshot validation and the owner-scoped batch status lookup without returning conversation content.
- [x] Keep deletion impact and deletion journal code paths intact while ensuring successful terminal transitions bump the owner list revision.

### Task 3: Expose FastAPI schemas and thread endpoints

**Files:**
- Modify: `askdb-agent/src/api/schemas/threads.py`
- Modify: `askdb-agent/src/api/routes/threads.py`

**Interfaces:**
- `GET /v1/threads?view=recent|archived&q=&limit=50&cursor=` returns metadata-only pages and `next_cursor`.
- `PATCH /v1/threads/{thread_id}` accepts only `title`, `is_pinned`, and required `expected_metadata_revision`.
- `POST /v1/threads/{thread_id}/archive` and `/restore` accept required `expected_metadata_revision`.
- `POST /v1/threads/states` accepts a bounded list of thread IDs and returns owner-scoped state projections.

- [x] Validate view, query length, maximum page size, cursor size, title size, batch size, and extra request fields.
- [x] Map cursor and metadata conflicts to stable 409 codes; return only a minimal current metadata projection on CAS conflict.
- [x] Preserve no-store headers, safe 404 projections, authentication, existing deletion routes, and request-body limits.
- [x] Return server metadata/revision in thread creation responses so the Web adapter can initialize its cache from the authoritative projection.

### Task 4: Add BFF routes and a typed Web thread API

**Files:**
- Modify: `askdb-web/app/api/threads/route.ts`
- Modify: `askdb-web/app/api/threads/[threadId]/route.ts`
- Create: `askdb-web/app/api/threads/[threadId]/archive/route.ts`
- Create: `askdb-web/app/api/threads/[threadId]/restore/route.ts`
- Create: `askdb-web/app/api/threads/states/route.ts`
- Create: `askdb-web/lib/thread-api.ts`

**Interfaces:**
- The BFF forwards only `view`, `q`, `limit`, and `cursor` for list requests and whitelists metadata fields for mutations.
- `thread-api.ts` exports typed page, metadata, state, list, patch, archive, restore, and deletion helpers; all mutations use the existing CSRF-aware `authMutation` path.

- [x] Preserve dynamic route `params` as promises and await them before constructing upstream paths.
- [x] Keep upstream requests on `proxyAgentJson`; never expose the Agent URL or token to the browser.
- [x] Ensure list and state URLs are no-store and forward normalized user search rather than filtering only the currently loaded page.

### Task 5: Make the remote adapter server-authoritative and reset legacy caches once

**Files:**
- Modify: `askdb-web/lib/local-thread-adapter.tsx`
- Modify: `askdb-web/lib/agent-chat-adapter.ts`
- Modify: `askdb-web/app/assistant.tsx`

**Interfaces:**
- Implement `RemoteThreadListAdapter.list({ after })` by mapping the service cursor to the Agent cursor and mapping server metadata into assistant-ui `custom` fields.
- Keep an adapter-scoped search query that the sidebar updates before calling `aui.threads.reload()`; the current open thread remains selected.
- Persist rename, archive, restore, and pin only through server calls using the cached `metadata_revision`, then update cache after success.

- [x] On the first authenticated load of the new storage version, remove the old thread index, per-thread message history, result artifacts, and unfinished history-import state without clearing other preference keys.
- [x] Remove localStorage-only history import and fallback listing; create new sessions directly on the server and preserve the selected draft data source.
- [x] Merge only authoritative server metadata into display cache; never write cached title/archive/pin state back to the server during list reconciliation.
- [x] On missing IDs, call the batch states endpoint and purge per-thread content only for explicit `deleted` or `expired`; do not infer deletion from a page omission or `unavailable` result.
- [x] On a stale cursor, surface a reload path that clears pagination and reloads page one; on CAS conflict, fetch current metadata and ask the user to retry the action.
- [x] Retain local query-result artifacts only for sessions that remain server-owned and preserve existing server history as the transcript authority.

### Task 6: Group the sidebar and add thread actions

**Files:**
- Modify: `askdb-web/components/assistant-ui/elements/thread-list-sidebar.aui.tsx`
- Modify: `askdb-web/lib/local-thread-adapter.tsx`

- [x] Render server-sorted recent pages under data-source headings, including source names from server metadata when the source is disabled or no longer granted.
- [x] Send search text through the adapter query and reload all matching pages from the server while retaining group context.
- [x] Add pin/unpin, archive, and delete menu actions; keep active-view navigation unchanged when the open thread is archived.
- [x] Keep deletion impact confirmation, stale-impact refresh, idempotency, existing success notice, and local cleanup after success.
- [x] Show retryable errors without replacing authoritative metadata with cached values.

### Task 7: Add archived-thread management in personal settings

**Files:**
- Modify: `askdb-web/components/settings/settings-shell.tsx`
- Create: `askdb-web/app/settings/archived-threads/page.tsx`
- Create: `askdb-web/components/settings/archived-threads-page.tsx`
- Reuse: `askdb-web/lib/thread-api.ts`

- [x] Add one shared personal navigation item for desktop and mobile at `/settings/archived-threads`.
- [x] Fetch archived pages from the server, grouped by source, with server-side title/source search and a load-more control.
- [x] Show title, source, archive time, and paused retention state without loading conversation bodies.
- [x] Restore through the metadata CAS API and return the session to its original source group without changing its pin state or granting source access.
- [x] Delete through the existing deletion-impact confirmation flow and refresh the archived list after successful mutations.

### Task 8: Coordinate release and record the implementation boundary

**Files:**
- Modify documentation only if implementation behavior requires a spec clarification.

- [x] Ensure the Agent, BFF, and Web changes use the same view, cursor, metadata revision, and error-code contracts.
- [x] Confirm no service path expires an archived thread and no client treats paginated absence as deletion.
- [x] Keep deployment as a coordinated Agent/BFF/Web release; do not publish or deploy as part of this change.
- [x] Record any check that could not be run because the installed dependencies or runtime services are unavailable.

## Verification record

- `PYTHONPYCACHEPREFIX=/private/tmp/askdb-agent-pycache python3 -m compileall -q askdb-agent/src` passed.
- `git diff --check` passed after the final edits.
- The Web TypeScript check could not run: the `askdb-web/node_modules` tree contains pnpm metadata links but no TypeScript compiler entry point. No tests or builds were run.
