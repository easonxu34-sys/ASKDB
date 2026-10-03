# User Login Implementation Plan

> **For agentic workers:** Implement this plan inline, task by task. Preserve the reviewed login design and the no-test authorization boundary.

**Goal:** Add local accounts, Agent-authoritative sessions and data-source authorization, plus a same-origin Web login and account-management experience.

**Architecture:** Keep FastAPI, LangGraph, WrenToolkit, Next.js BFF, and the existing settings SQLite database. The Agent validates every principal and permission; the Web BFF handles the HttpOnly session cookie, CSRF checks, and request forwarding. Existing thread/source rows gain nullable owner identity so legacy rows remain unclaimed.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, Argon2id, Next.js 16 Route Handlers, React, existing assistant-ui and localStorage adapter.

**Spec:** `docs/superpowers/specs/2026-10-02-user-login-design.md`

## Global Constraints

- One organization, local accounts, one Agent instance, and the existing `ASKDB_SETTINGS_DB_PATH` SQLite file.
- Agent is authoritative for identity, role, sessions, thread ownership, and data-source grants.
- Admin manages accounts, models, and data sources and may chat against enabled sources; member chats only against enabled assigned sources.
- Data-source assignment choices share the chat selector rule: sources must be enabled and have `runtime_status=ready`; Agent rejects grants to other sources.
- The first admin is provisioned once from the Agent host; no default credentials, public bootstrap endpoint, or self-registration.
- Passwords use Argon2id; temporary passwords are shown once and hashed before persistence; first login requires password change.
- Newly set passwords require at least 8 characters, with no composition rules or common-password blocklist.
- Login and password-change fields default to masked input and provide per-field show/hide controls, except the first-login temporary-password field, which stays masked.
- Session token hashes only are stored. Enforce 30-minute idle and 8-hour absolute expiry; revoke sessions on logout, disable, reset, or role change.
- State-changing BFF calls validate same-origin `Origin` and a CSRF token. Session cookie is `HttpOnly; Secure; SameSite=Lax; Path=/` with no `Domain`.
- Add nullable `owner_user_id`; preserve existing rows with NULL and reject their reuse. Namespace new local browser data by stable `user_id`; leave old anonymous keys in place and unread.
- Namespace new remote thread IDs with the authenticated user ID so legacy runtime checkpoints without a source-binding row cannot be adopted.
- Do not implement MFA, OIDC, email recovery, server-side conversation memory, self-registration, row/table permissions, or multi-instance operation.
- Do not read, print, copy, or modify real `.env` or secret files. Do not modify `askdb-admin/src/main/resources/application.yml`.
- No Git metadata exists at the workspace or either subproject. Do not stage, commit, clean, or claim a Git diff.
- This authorization excludes adding/running tests, builds, and lint. Keep a concrete verification checklist for separate authorization.

---

### Task 1: Agent identity and SQLite persistence

**Files:**
- Create: `askdb-agent/src/askdb_agent/domain/auth.py`
- Create: `askdb-agent/src/askdb_agent/auth_store.py`
- Create: `askdb-agent/src/askdb_agent/application/auth.py`
- Modify: `askdb-agent/pyproject.toml`, `askdb-agent/uv.lock`
- Modify: `askdb-agent/src/askdb_agent/wren_settings.py`

**Interfaces:**
- `Principal(user_id: str, username: str, role: Literal["admin", "member"], must_change_password: bool)` is built only from a persisted session and active user row.
- `AuthStore` owns additive schema initialization and transactional account/session/grant operations in `ASKDB_SETTINGS_DB_PATH`.
- `AuthApplication` owns login, logout, password change, session lookup, account management, and source-grant use cases.
- `WrenSettingsStore.bind_thread_source(thread_id, owner_user_id, source_id)` atomically checks source existence and stores owner plus source.

- [x] Add the auth tables (`auth_users`, `auth_sessions`, `auth_user_data_sources`, `auth_login_throttles`, `auth_audit_events`) using an idempotent migration on the existing SQLite file.
- [x] Hash passwords using Argon2id and opaque session tokens using SHA-256; never store plaintext credentials.
- [x] Enforce normalized unique usernames, active-state checks, one-time bootstrap, password reset, session revocation, grants, login throttling, and safe audit summaries transactionally.
- [x] Add a nullable `owner_user_id` to `chat_thread_data_sources` without backfilling legacy records.
- [x] Keep SQLite directory/file permissions consistent with the existing settings store.

### Task 2: Agent authentication API and bootstrap command

**Files:**
- Create: `askdb-agent/src/askdb_agent/api/dependencies.py`
- Create: `askdb-agent/src/askdb_agent/api/routes/auth.py`
- Create: `askdb-agent/src/askdb_agent/api/routes/admin_users.py`
- Create: `askdb-agent/src/askdb_agent/api/schemas/auth.py`
- Create: `askdb-agent/src/askdb_agent/cli.py`
- Modify: `askdb-agent/src/askdb_agent/api/app.py`

**Interfaces:**
- `get_current_principal(request)` validates the bearer session against SQLite on every protected request.
- `require_admin(principal)` rejects member access with stable 403.
- Auth API returns the raw token only to the trusted BFF login response; all other responses omit it and set `Cache-Control: no-store`.

- [x] Initialize the auth store before every protected request through the FastAPI dependency; fail closed when the database cannot be opened or migrated.
- [x] Add login, logout, `/me`, and password-change endpoints; use one generic invalid-credentials response and account-based throttling.
- [x] Restrict first-login sessions to `/me`, password change, and logout until the password changes; rotate the session on successful change.
- [x] Add admin list/create/update/disable/reset-password and source-grant/revoke endpoints; return generated temporary passwords once only.
- [x] Protect the last active admin from disable/demotion and ensure bootstrap never reopens after an account has existed.
- [x] Add the local one-time `auth init-admin` command; interactively collect the username, generate the temporary password, and print it once.
- [x] Keep `/healthz` minimal and public; do not add any public bootstrap endpoint.

### Task 3: Route authorization and thread/source ownership

**Files:**
- Modify: `askdb-agent/src/askdb_agent/api/routes/chat.py`
- Modify: `askdb-agent/src/askdb_agent/api/routes/model_settings.py`
- Modify: `askdb-agent/src/askdb_agent/api/routes/wren_settings.py`
- Modify: `askdb-agent/src/askdb_agent/wren_settings.py`
- Create: `askdb-agent/src/askdb_agent/api/routes/chat_options.py` (or add the endpoint to the existing chat router)

- [x] Require admin on every plural and legacy singular model-settings route, including credential deletion and model tests.
- [x] Require admin on every `/v1/settings/wren/**` route, including operation polling and every data-source mutation.
- [x] Require login on chat and `/v1/data-sources`; return only enabled sources allowed to the principal.
- [x] Add `/v1/chat/model-options` with the minimum fields needed for chat selection and no API keys or credential-presence details.
- [x] In chat, resolve and authorize the source and thread before acquiring a runtime. Members cannot fall back to an unassigned global default.
- [x] On first thread use, bind `thread_id`, principal `user_id`, and authorized `data_source_id` in one `BEGIN IMMEDIATE` transaction.
- [x] Reject NULL-owner legacy bindings with `CHAT_THREAD_LEGACY_REQUIRES_NEW_THREAD`; return a non-enumerating 404 for another user's thread.
- [x] Preserve request schema, SSE event ordering/termination, cancellation, and the existing SQL/Wren safety gate.

### Task 4: Next.js BFF session and CSRF layer

**Files:**
- Create: `askdb-web/lib/auth-api.ts`
- Create: `askdb-web/lib/agent-proxy.ts`
- Create: `askdb-web/app/api/auth/login/route.ts`
- Create: `askdb-web/app/api/auth/csrf/route.ts`
- Create: `askdb-web/app/api/auth/logout/route.ts`
- Create: `askdb-web/app/api/auth/me/route.ts`
- Create: `askdb-web/app/api/auth/change-password/route.ts`
- Create: `askdb-web/app/api/admin/users/route.ts`
- Create: `askdb-web/app/api/admin/users/[userId]/route.ts`
- Create: `askdb-web/app/api/admin/users/[userId]/reset-password/route.ts`
- Create: `askdb-web/app/api/admin/users/[userId]/data-sources/[sourceId]/route.ts`
- Modify: `askdb-web/app/api/chat/route.ts`, `askdb-web/app/api/data-sources/route.ts`
- Modify: model-setting Route Handlers and `app/api/settings/wren/[...segments]/route.ts`

- [x] Use Next 16 async `cookies()` APIs in Route Handlers; keep the Agent token only in the `__Host-askdb_session` cookie and server-side Authorization forwarding.
- [x] Issue the CSRF cookie through `GET /api/auth/csrf`, check a separate `__Host-askdb_csrf` token, and verify exact same-origin `Origin` on login and every cookie-authenticated mutation.
- [x] Add explicit same-origin auth/admin proxies with strict body/path allowlists, no-store responses, bounded bodies, and safe errors.
- [x] Forward the session for chat and preserve SSE streaming; do not forward browser-supplied identity or role headers.
- [x] Ensure neither session token nor upstream authorization header enters JSON, localStorage, URLs, SSE, or logs.
- [x] Keep Agent authorization authoritative even when BFF helpers or page guards reject early.

### Task 5: Login, password-change, user-management UI, and per-user browser data

**Files:**
- Create: `askdb-web/app/login/page.tsx`, `askdb-web/app/login/login-form.tsx`
- Create: `askdb-web/app/change-password/page.tsx`, `askdb-web/app/change-password/password-form.tsx`
- Create: `askdb-web/app/admin/users/page.tsx`
- Create: focused user-management components under `askdb-web/components/user-management/`
- Modify: `askdb-web/app/page.tsx`, `askdb-web/app/assistant.tsx`
- Modify: `askdb-web/lib/local-thread-adapter.tsx`, `askdb-web/lib/model-profiles.ts`, `askdb-web/lib/model-selection.ts`
- Modify: relevant navigation and settings entry components.

- [x] Gate the chat UI using `/api/auth/me`; redirect signed-out users to login and `must_change_password` users to forced password change.
- [x] Provide admin-only account creation, username/role editing, enable/disable, reset, and per-user data-source grants.
- [x] Display each created/reset temporary password once in transient UI state; never persist it in browser storage.
- [x] Scope threads, messages, model choices, and source choices by stable `user_id`; leave legacy unnamespaced keys untouched and unread.
- [x] On account switch/logout, unmount the prior user's runtime and clear transient in-memory state without deleting that user's namespaced conversation history.
- [x] Keep the current AskDB assistant/sidebar visual language; use plain Chinese copy, keyboard focus states, responsive layout, and reduced-motion support.
- [x] Read the installed Next.js local authentication, BFF, cookies, and Route Handler docs before editing Web source.

### Task 6: Migration and operator documentation

**Files:**
- Modify: `askdb-agent/README.md`
- Modify: deployment/configuration docs only if a new non-secret setting is required.

- [x] Document additive SQLite migration, single-instance constraint, backup/restore implications, one-time admin bootstrap, initial password change, and HTTPS requirement.
- [x] Document loopback-only HTTP development behavior and never trust forwarded client IP headers without explicit trusted-proxy configuration.
- [x] Document account reset and last-admin recovery through the Agent host; keep secrets out of docs and examples.
- [x] Manually review all route decorators, BFF handlers, chat ordering, and error projections against the approved design.
- [x] Keep tests/build/lint unexecuted until the user separately authorizes them; report exact deferred commands and behavior that remains unverified.

## Deferred verification checklist (requires separate authorization)

- Agent unit/API tests for bootstrap, hashing, generic errors, throttling, expiry, revocation, forced password change, role matrix, grants, thread ownership, legacy thread handling, and SSE compatibility.
- Web tests for cookie flags, CSRF/Origin checks, proxy forwarding, token non-leakage, one-time temporary-password display, page gating, and cross-user localStorage isolation.
- Suggested later commands: `uv run pytest -q` from `askdb-agent/`; existing Web Node tests via `node --test askdb-web/tests/*.test.mjs`; Web lint/build only if separately authorized.
