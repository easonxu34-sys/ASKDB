# Wren Core AskDB Integration Implementation Plan

> **For agentic workers:** Implement inline task by task from this plan. Keep each step independently reviewable.

**Goal:** Deliver AskDB's first real natural-language-to-data flow through assistant-ui, a Python LangGraph service, WrenToolkit, Wren Core, and a read-only database.

**Architecture:** Keep the Next.js UI under `askdb-web/`. Add a Python FastAPI service under `askdb-agent/`, where LangGraph owns tool routing and WrenToolkit binds one prebuilt Wren project. Keep model and database credentials on the server. Add MCP tool configuration and Feishu channel adapters after the web-to-Wren query path is accepted.

**Tech Stack:** Next.js 16, assistant-ui, Python 3.11+, FastAPI, LangChain, LangGraph, wren-langchain, Wren Core.

**Spec:** `docs/开发文档.md`

## Global Constraints

- Python runtime must be 3.11 or newer.
- Every Wren project must have a pinned profile and a built `target/mdl.json` before the Agent starts.
- Database credentials must use read-only permissions and remain server-side.
- The Agent must validate the Wren plan and database dry-run before query execution.
- The Wren memory write tool remains disabled until an answer is explicitly confirmed.
- UI and Agent communicate through an HTTP streaming contract; the browser never connects to Wren directly.
- MCP connectors and Feishu channel handling remain separate extension boundaries.

---

### Task 1: Split the workspace into web and agent projects

**Files:**
- Move: `app/`, `components/`, `hooks/`, `lib/`, `components.json`, `next-env.d.ts`, `next.config.ts`, `package.json`, `pnpm-lock.yaml`, `postcss.config.mjs`, `tsconfig.json` to `askdb-web/`
- Modify: `.gitignore`, `README.md`
- Create: `askdb-web/README.md`, `askdb-agent/README.md`, `askdb-agent/pyproject.toml`, `askdb-agent/.env.example`

**Verification:**
- From `askdb-web/`, run `npx tsc --noEmit`.
- From `askdb-web/`, run `npm run build -- --webpack`.
- Confirm no UI application source files remain in the workspace root.

### Task 2: Prepare and verify one Wren project

**Files:**
- Create: a separate Wren project directory configured outside committed secrets
- Modify: Wren `wren_project.yml`, `models/`, profile and knowledge files as required by the selected database schema

**Verification:**
- Run `wren context validate`, `wren context build`, and `wren profile debug`.
- Run one known SQL query with the Wren CLI using the read-only profile and compare the result to the database.

### Task 3: Implement and verify the Python Agent API

**Files:**
- Create: `askdb-agent/src/askdb_agent/config.py`, `runtime.py`, `graph.py`, `api.py`
- Create: `askdb-agent/tests/test_api.py`, `askdb-agent/tests/test_query_flow.py`
- Modify: `askdb-agent/pyproject.toml`, `askdb-agent/.env.example`

**Interfaces:**
- `POST /v1/chat` accepts `thread_id` and ordered role/content messages.
- The response is SSE with `status`, `token`, `sql`, `result`, `error`, and `done` events.
- `build_graph(settings, toolkit)` returns a LangGraph runnable; `toolkit` is constructed from exactly one Wren project and selected profile.

**Verification:**
- Add tests first for invalid requests, tool write disabled, plan/dry-run/query order, query failure, and row cap.
- Verify each test fails for the missing behavior before adding its implementation.
- Run the focused Python tests and API schema checks.

### Task 4: Connect assistant-ui to the Agent API

**Files:**
- Modify: `askdb-web/app/assistant.tsx`
- Create: `askdb-web/app/api/chat/route.ts`, `askdb-web/lib/agent-events.ts`
- Modify: `askdb-web/.env.example`, `askdb-web/README.md`

**Verification:**
- Verify the mock adapter is replaced with the same-origin chat route.
- Verify token, SQL, result, error, and completion events map to assistant-ui messages.
- Run TypeScript checks and a production build.

### Task 5: Prove the complete business flow

**Files:**
- Create: `askdb-agent/tests/integration/test_wren_query.py`
- Create: `askdb-agent/tests/integration/fixtures/` with non-sensitive deterministic test data

**Verification:**
- Start the Agent with a test Wren project and read-only database profile.
- Ask one aggregate question and confirm displayed answer, SQL, and rows against an independent database query.
- Ask a follow-up in the same thread and confirm the session does not leak across thread IDs.
- Confirm invalid, read/write-incompatible, and over-limit queries fail safely.
