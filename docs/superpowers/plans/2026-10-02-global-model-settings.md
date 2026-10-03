# AskDB Global Model Settings Implementation Plan

> **For agentic workers:** Execute this plan inline, task by task. The approved design is `docs/superpowers/specs/2026-10-02-global-model-settings-design.md`.

Implementation status: source and documentation changes are complete; automated verification has not been run.

**Goal:** Let trusted AskDB users configure one deployment-wide OpenAI-compatible model, validate connectivity, persist its credential encrypted, and switch new Agent requests without restarting.

**Architecture:** The Agent owns a SQLite settings repository, Fernet credential encryption, connectivity probes, and an atomically replaceable runtime snapshot. The Next.js app exposes same-origin BFF routes and a sidebar dialog; the browser never receives the API key except while submitting a user-entered key over HTTPS.

**Tech Stack:** FastAPI, Pydantic, SQLite, `cryptography`, LangChain OpenAI-compatible models, Next.js route handlers, React, Base UI Dialog.

**Spec:** `docs/superpowers/specs/2026-10-02-global-model-settings-design.md`

## Global Constraints

- Model settings are shared by the deployment and writable by anyone who can reach the trusted internal deployment.
- Support `openai`, `deepseek`, and `custom` over the OpenAI-compatible API only.
- Persist settings in SQLite; encrypt API keys with `ASKDB_SETTINGS_ENCRYPTION_KEY` using Fernet.
- Never return, log, or persist API keys in browser storage, responses, errors, or traces.
- Do not save or switch runtime until a short connectivity probe succeeds.
- Requests already in progress retain their captured runtime; later requests use the new runtime.
- Seed settings from existing model environment variables only when SQLite has no setting; SQLite takes precedence thereafter.
- The browser calls same-origin Web routes; chat requests cannot override provider, model, endpoint, or key.
- Preserve the existing assistant-ui thread sidebar and Base UI Dialog conventions.

---

### Task 1: [x] Add encrypted SQLite settings domain and environment initialization

**Files:**
- Create: `askdb-agent/src/askdb_agent/model_settings.py`
- Modify: `askdb-agent/src/askdb_agent/config.py`
- Modify: `askdb-agent/pyproject.toml`
- Modify: `askdb-agent/.env.example`

**Deliverable:** A focused settings service validates provider/model/absolute HTTP(S) base URL, creates the single-row SQLite schema, encrypts/decrypts credentials with Fernet, and seeds one record from current environment variables only on first initialization. It returns a public settings projection that contains only `api_key_configured`, never key material. Missing/invalid encryption configuration fails closed for settings operations; existing legacy environment-based chat remains usable only when no database record exists.

**Verification to run when testing is authorized:** Import the new service; initialize a temporary SQLite database; confirm seed, encrypted-at-rest value, key preservation, replacement, missing-key, bad-key, invalid URL, and unavailable-key behavior.

### Task 2: [x] Add model runtime manager and safe connectivity probe

**Files:**
- Modify: `askdb-agent/src/askdb_agent/integrations/models.py`
- Modify: `askdb-agent/src/askdb_agent/runtime.py`
- Modify: `askdb-agent/src/askdb_agent/api/app.py`
- Create: `askdb-agent/src/askdb_agent/application/model_settings.py`

**Deliverable:** Build fixed-model Agent runtimes from explicit settings, probe candidates with a temporary OpenAI-compatible client (16 output tokens, 15-second timeout, no user messages), and maintain the active runtime through an async lock and immutable per-request snapshots. Failed validation/build/persistence leaves the prior runtime active. When SQLite configuration is removed, legacy environment runtime may be used only if the database has never stored settings; a configured-but-unreadable or credential-cleared record fails closed.

**Verification to run when testing is authorized:** Use fake model/runtime builders to check probe outcomes, save ordering, persistence failures, runtime snapshot behavior, and stable upstream error mapping without contacting a real provider.

### Task 3: [x] Add Agent settings API and bind chat requests to runtime snapshots

**Files:**
- Create: `askdb-agent/src/askdb_agent/api/schemas/model_settings.py`
- Create: `askdb-agent/src/askdb_agent/api/routes/model_settings.py`
- Modify: `askdb-agent/src/askdb_agent/api/app.py`
- Modify: `askdb-agent/src/askdb_agent/api/routes/chat.py`

**Interfaces:**
- `GET /v1/settings/model` returns provider, model, base URL, and `api_key_configured` only.
- `POST /v1/settings/model/test` accepts provider, model, base URL, and optional replacement key; it does not persist or switch runtime.
- `PUT /v1/settings/model` accepts the same configuration; an empty key preserves the stored key, and an initial empty key leaves the model unready.
- `DELETE /v1/settings/model/credential` clears only the saved credential and makes future chat requests return `MODEL_NOT_CONFIGURED`.
- Settings responses use `Cache-Control: no-store`; errors use stable codes and sanitized Chinese messages.
- `POST /v1/chat` captures one runtime before opening its SSE iterator and retains it through stream completion.

**Deliverable:** All settings routes serialize operations through the application settings service; chat bodies cannot select model endpoints or credentials. Invalid fields return 422, unavailable encryption/storage returns 503, and provider failures never include upstream response content.

**Verification to run when testing is authorized:** Exercise routes through FastAPI with fakes and assert response schemas, status/error codes, no-store headers, secret absence, and request snapshot behavior.

### Task 4: [x] Add same-origin Web BFF routes

**Files:**
- Create: `askdb-web/app/api/settings/model/route.ts`
- Create: `askdb-web/app/api/settings/model/test/route.ts`
- Create: `askdb-web/app/api/settings/model/credential/route.ts`
- Modify: `askdb-web/app/api/chat/route.ts`

**Deliverable:** Web handlers proxy GET, POST test, PUT, and DELETE to the corresponding Agent endpoints with `cache: "no-store"`, preserve safe status/error payloads, and set no-store response headers. They do not log request bodies or retain key material. Existing chat proxy behavior remains unchanged apart from a stable, sanitized 503 response for an unconfigured model.

**Verification to run when testing is authorized:** Type-check route signatures and exercise upstream success, unavailable Agent, and sanitized error forwarding with mocked `fetch`.

### Task 5: [x] Add sidebar settings entry and model configuration dialog

**Files:**
- Modify: `askdb-web/components/assistant-ui/elements/thread-list-sidebar.aui.tsx`
- Create: `askdb-web/components/model-settings/model-settings-dialog.tsx`

**Deliverable:** Add an accessible gear button at the bottom of the existing sidebar. It opens a compact upward menu with only “设置模型”; selecting it opens the existing Base UI Dialog. The form loads non-secret settings with a retry state, supports OpenAI/DeepSeek/custom endpoint selection, invalidates successful connection tests whenever inputs change, preserves form values after failures, and only enables save after a successful test. Blank key preserves the saved key. Clearing a saved key is a separately confirmed action. Loading/testing/saving states are accessible and disable conflicting actions; successful save closes the dialog and shows confirmation.

**Verification to run when testing is authorized:** Run Web type checking and production build; manually inspect desktop/mobile keyboard flow, dialog focus, menu placement, test invalidation, and the clear-key confirmation.

### Task 6: [x] Document operations, security boundary, and recovery

**Files:**
- Modify: `askdb-agent/.env.example`
- Modify: `askdb-agent/README.md`
- Modify: `docs/开发文档.md`

**Deliverable:** Document generating and deploying a Fernet key, the SQLite path and persistent-volume permissions, first-start environment seeding, trusted-network/HTTPS requirements, encrypted database backup/restore, and key rotation/recovery. Add `ASKDB_SETTINGS_DB_PATH` and `ASKDB_SETTINGS_ENCRYPTION_KEY` examples without real secrets.

**Verification to run when testing is authorized:** Cross-check docs against the implemented environment names, routes, first-start behavior, and failure codes.

## Acceptance Review

When verification is authorized, run focused Python checks and Web type/build checks, then review the implementation against all eight acceptance conditions in the approved spec. Do not claim end-to-end provider success without a configured provider and an actual successful probe.
