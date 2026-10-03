# Wren Multi-Data-Source UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users configure mixed Wren database/warehouse sources in AskDB, select one for each new conversation, and see each conversation's source in the sidebar.

**Architecture:** The Agent owns an encrypted SQLite catalog of data sources, revisions, operations, and immutable thread-to-source bindings. Each data source gets an isolated Wren profile/project; `RuntimeManager` caches graphs by source revision and the conversation's existing model profile. The Web app uses same-origin BFF routes, a Wren settings page, a composer selector, and local thread metadata for display.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, Fernet, PyYAML, Wren CLI 0.15 connector extras, Wren LangChain Toolkit, LangGraph, Next.js 16, React 19, TypeScript, Vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-02-wren-settings-ui-design.md`

## Global Constraints

- Python requires `>=3.11`; install the Wren 0.15 extras for Athena, BigQuery, ClickHouse, Databricks, SQL Server, MySQL, Oracle, PostgreSQL, Redshift, Snowflake, Spark, and Trino. DuckDB is included in core; Doris uses Wren's MySQL connector extra.
- V1 supports PostgreSQL, MySQL/MariaDB, BigQuery, Snowflake, ClickHouse, Trino, SQL Server, Databricks, Redshift, Oracle, Athena, Spark, DuckDB, and Doris. It excludes file connectors and Wren internal service connectors. Each source has its own Wren project, profile, semantic model, revision history, and runtime; V1 does not execute cross-source joins.
- Connection forms and validation are generated from the pinned Wren connection schema; secrets are extracted using its sensitive-field metadata. Connection tests and query policy are connector/dialect aware. Schema readers are registered per database and return metadata only.
- A conversation binds to one `data_source_id` on first send. Changing the source requires a new conversation. The existing per-conversation model profile selection remains supported.
- Runtime cache identity is `(data_source_id, wren_revision_id, model_profile_id, model_profile_revision)`.
- Preserve `POST /v1/chat`, request compatibility for clients omitting `data_source_id`, existing SSE event names and payloads, and the SQL `dry_plan → dry_run → query` gate.
- Keep credentials server-side in encrypted SQLite fields. Never return them in catalog/settings responses, BFF errors, logs, CLI output, or generated Wren project files.
- A failed source operation must preserve that source's active revision and runtime; other sources remain usable. An in-flight request keeps its captured runtime snapshot.
- V1 assumes one Agent process. Runtime broadcasts across multiple workers are outside scope.
- Before editing Web code, follow `askdb-web/AGENTS.md` and read the applicable Next.js 16 guide from `askdb-web/node_modules/next/dist/docs/`.
- The inspected workspace and service subdirectories do not have Git metadata; this plan has no commit commands.

---

## File and Module Map

### Agent

- Create `askdb-agent/src/askdb_agent/wren_settings.py` for source/revision/operation persistence, encrypted Wren credentials, defaults, and thread-source bindings.
- Create `askdb-agent/src/askdb_agent/application/wren_settings.py` for create/edit/test/introspect/apply/poll/rollback/deactivate workflows.
- Create `askdb-agent/src/askdb_agent/integrations/wren_cli.py` for profile creation and fixed CLI operations behind an injectable command runner.
- Create `askdb-agent/src/askdb_agent/integrations/wren_connectors.py` for Wren schema-driven connector definitions, variants, validation, secret mapping, and profile serialization.
- Create `askdb-agent/src/askdb_agent/integrations/schema_readers/` for connector-backed `SELECT 1` and metadata-only introspection.
- Create `askdb-agent/src/askdb_agent/integrations/wren_project.py` for safely generating immutable Wren project files from validated form data.
- Modify `askdb-agent/src/askdb_agent/integrations/wren.py` to construct toolkits from explicit source project/profile bindings and defer importing Wren until `WREN_HOME` is set.
- Create `askdb-agent/src/askdb_agent/application/runtime_manager.py` for source/model-profile graph snapshots and atomic activation.
- Create `askdb-agent/src/askdb_agent/api/schemas/wren_settings.py` and `api/routes/wren_settings.py` for public catalog and management APIs.
- Modify `askdb-agent/src/askdb_agent/api/app.py`, `api/routes/chat.py`, `api/schemas/chat.py`, `application/model_settings.py`, `config.py`, and `runtime.py` to wire source resolution and preserve existing model-profile behavior.
- Add `askdb-agent/tests/test_wren_settings.py`, `test_wren_settings_application.py`, and `test_runtime_manager.py`; extend `test_api.py` for routes and chat binding.

### Web

- Create `askdb-web/lib/wren-settings-api.ts` for bounded, no-store, sanitized same-origin proxy requests.
- Create `askdb-web/lib/data-sources.ts` for safe catalog types and client fetch/management functions.
- Create `askdb-web/app/api/data-sources/route.ts` and `app/api/settings/wren/[...segments]/route.ts` for BFF routing.
- Create `askdb-web/app/settings/wren/page.tsx` and focused components under `askdb-web/components/wren-settings/` for the source catalog, source form, schema/model editor, operation progress, and revision preview.
- Modify `askdb-web/lib/local-thread-adapter.tsx`, `lib/agent-chat-adapter.ts`, `components/assistant-ui/elements/thread.aui.tsx`, and `thread-list-sidebar.aui.tsx` for source metadata, composer selection, request payloads, and sidebar badges.
- Create `askdb-web/components/assistant-ui/elements/data-source-selector.tsx` to isolate and test the accessible composer selector.
- Add a Vitest configuration and focused tests under `askdb-web/tests/`; keep Next build/lint as integration checks.

## Implementation Tasks

### Task 1: Add the encrypted source catalog and thread binding store

**Files:**
- Create: `askdb-agent/src/askdb_agent/wren_settings.py`
- Create: `askdb-agent/tests/test_wren_settings.py`
- Reference: `askdb-agent/src/askdb_agent/model_settings.py` for SQLite permissions, Fernet handling, timestamps, and error mapping.

**Interfaces:**
- `WrenDataSource`: immutable `id`, `display_name`, `connector_type`, `enabled`, `active_revision_id`, `draft_revision_id`, `runtime_status`, `created_at`, and `updated_at` fields.
- `WrenRevision`: immutable `id`, `source_id`, `status`, `config`, `project_dir`, `profile_name`, `mdl_digest`, `error_code`, and timestamps.
- `WrenSettingsStore(database_path: Path | None = None, encryption_key: str | None = None)` exposes `create_data_source`, `list_data_sources`, `get_data_source`, `get_default_source`, `save_draft`, `set_default`, `set_enabled`, `create_revision`, `update_operation`, `bind_thread_source`, and `source_is_referenced`.
- `bind_thread_source(thread_id: str, source_id: str) -> str` inserts once and returns the bound source; an existing different binding raises `ChatDataSourceMismatch`.

- [ ] **Step 1: Write store tests for isolation, encryption, and bindings**

Add `test_creates_two_independent_sources`, `test_secret_is_encrypted_at_rest`, `test_default_must_be_enabled_and_active`, and `test_thread_source_binding_is_immutable`. Use `tmp_path` for SQLite and `Fernet.generate_key().decode()` for the test key. For the binding test, bind `thread-1` to `source-a`, assert a second bind to `source-a` is idempotent, and assert binding to `source-b` raises `ChatDataSourceMismatch`.

```python
def test_thread_source_binding_is_immutable(tmp_path):
    store = WrenSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    source_a = store.create_data_source("A", "mysql", {}, {})
    source_b = store.create_data_source("B", "mysql", {}, {})

    assert store.bind_thread_source("thread-1", source_a.id) == source_a.id
    assert store.bind_thread_source("thread-1", source_a.id) == source_a.id
    with pytest.raises(ChatDataSourceMismatch):
        store.bind_thread_source("thread-1", source_b.id)
```

- [ ] **Step 2: Run the focused tests and confirm the store is missing**

Run: `cd askdb-agent && uv run pytest tests/test_wren_settings.py -q`
Expected: collection or import failure because `WrenSettingsStore` is not implemented.

- [ ] **Step 3: Implement SQLite tables and transactional store methods**

Create tables `wren_state`, `wren_data_sources`, `wren_revisions`, `wren_secrets`, `wren_operations`, and `chat_thread_data_sources`. Reuse `ASKDB_SETTINGS_DB_PATH` and `ASKDB_SETTINGS_ENCRYPTION_KEY`. Store each credential/CA secret as Fernet ciphertext keyed by source and revision. Use a unique constraint on `thread_id` and a transaction for insert-if-absent; do not use a read-then-write sequence without a uniqueness constraint.

```sql
CREATE TABLE IF NOT EXISTS chat_thread_data_sources (
    thread_id TEXT PRIMARY KEY,
    data_source_id TEXT NOT NULL REFERENCES wren_data_sources(id),
    created_at TEXT NOT NULL
);
```

- [ ] **Step 4: Add one-source legacy migration**

When the Wren catalog is empty and `WREN_PROJECT_DIR` plus `WREN_PROFILE` are configured, read the connector declared by the existing project/profile and seed one default source with a stable generated ID. Extract schema-marked sensitive values into encrypted storage. If migration cannot decrypt or resolve a referenced secret, raise the store's unavailable/migration error and leave the existing environment configuration usable.

- [ ] **Step 5: Run the focused tests**

Run: `cd askdb-agent && uv run pytest tests/test_wren_settings.py -q`
Expected: all catalog, ciphertext, default-source, legacy-migration, and immutable-binding tests pass.

### Task 2: Build isolated Wren profiles and projects per source

**Files:**
- Create: `askdb-agent/src/askdb_agent/integrations/wren_cli.py`
- Create: `askdb-agent/src/askdb_agent/integrations/wren_connectors.py`
- Create: `askdb-agent/src/askdb_agent/integrations/schema_readers/`
- Create: `askdb-agent/src/askdb_agent/integrations/wren_project.py`
- Modify: `askdb-agent/src/askdb_agent/integrations/wren.py`
- Create: `askdb-agent/src/askdb_agent/application/wren_settings.py`
- Create: `askdb-agent/tests/test_wren_settings_application.py`
- Modify: `askdb-agent/src/askdb_agent/config.py` and `askdb-agent/pyproject.toml`.

**Interfaces:**
- `WrenCli(command_runner, wren_home: Path)` exposes `add_profile(profile_name, connection_fields)`, `validate(project_dir)`, and `build(project_dir)`; it invokes fixed argument arrays with an explicit working directory and never accepts shell text or arbitrary paths from HTTP input.
- `WrenConnectorRegistry` exposes the supported connector catalog and connection form schema from Wren 0.15. Its validation path returns non-secret config, encrypted-secret fields, and the selected authentication variant.
- `SchemaReaderRegistry` exposes `test_connection() -> None` and `introspect() -> list[TableSchema]` for each supported database/warehouse connector; `TableSchema` contains qualified table identity, typed columns, and optional key metadata, never data rows.
- `WrenProjectBuilder(data_root: Path)` exposes `build(source_id, revision_id, profile_name, connector_type, config, schema) -> Path` and writes only beneath `sources/{source_id}/revisions/{revision_id}/`.
- `WrenSettingsApplication` exposes `create_source`, `update_draft`, `test_connection`, `refresh_schema`, `start_apply`, `get_operation`, `rollback`, and `deactivate`.
- `Settings` keeps `WREN_PROJECT_DIR`/`WREN_PROFILE` as optional one-time migration inputs and adds `ASKDB_WREN_DATA_DIR` plus dedicated `WREN_HOME` resolution; active runtime construction no longer requires the legacy environment pair.

- [ ] **Step 1: Add adapter tests with injected fakes**

In `test_wren_settings_application.py`, add `test_cli_uses_argument_array_and_revision_working_directory`, `test_failed_cli_output_is_redacted`, `test_schema_reader_only_returns_metadata`, and `test_project_builder_rejects_path_escape`. Use a fake command runner and fake connector; assert that neither captured calls nor generated project files include the raw password.

```python
def test_cli_uses_argument_array_and_revision_working_directory(tmp_path):
    runner = FakeCommandRunner(stdout="", returncode=0)
    project = tmp_path / "sources" / "ds-a" / "revisions" / "rev-1"
    project.mkdir(parents=True)

    WrenCli(runner, tmp_path / "wren-home").build(project)

    assert runner.calls == [( ["wren", "context", "build"], project )]
```

- [ ] **Step 2: Run the new adapter tests and confirm they fail**

Run: `cd askdb-agent && uv run pytest tests/test_wren_settings_application.py -q`
Expected: import failure for the new adapter/application types.

- [ ] **Step 3: Resolve WREN_HOME before importing Wren**

Add persistent `WREN_HOME` and `ASKDB_WREN_DATA_DIR` settings. Change `build_wren_toolkit` to accept `(project_dir: Path, profile_name: str)`, import `WrenToolkit` lazily after setting `WREN_HOME`, and never read a browser-supplied path. Keep the environment project/profile optional for migration only.

- [ ] **Step 4: Implement profile/CLI adapter**

Use `subprocess.run([...], cwd=project_dir, env=..., shell=False, timeout=...)`. Create one profile per source/revision. Keep password values in encrypted storage and pass Wren only generated `${ASKDB_WREN_{SOURCE_TOKEN}_{REV_TOKEN}_PASSWORD}` references. Redact stdout/stderr before persistence or returning errors.

```python
def _run(self, args: list[str], *, cwd: Path) -> CompletedProcess[str]:
    return self.command_runner(
        args, cwd=cwd, env=self._profile_environment(),
        shell=False, capture_output=True, text=True, timeout=self.timeout_seconds,
    )
```

- [ ] **Step 5: Implement connector test and metadata adapters**

Run a fixed `SELECT 1` through the selected Wren connector for connection verification. Register metadata-only readers for all in-scope database/warehouse connectors. Query catalog/schema/table/column/key metadata only; do not expose a generic SQL endpoint or fetch business rows. If a connector cannot report foreign keys, allow manual relationship configuration.

- [ ] **Step 6: Import the existing project as the initial source**

On first catalog initialization, copy the configured existing Wren project into `ASKDB_WREN_DATA_DIR/sources/{source_id}/revisions/{revision_id}/`, replace its connection binding with the generated per-revision profile, and build the runtime from the imported MDL. Do not edit the configured project in place. If the import fails, keep the old environment-backed runtime available and leave migration status visible.

- [ ] **Step 7: Implement safe project generation and lifecycle operations**

Declare `PyYAML>=6,<7` as a direct runtime dependency. Generate `wren_project.yml`, model/field metadata, relationships, business rules, and any reviewed advanced views under the validated revision directory. Run `wren context validate` then `wren context build`. Persist operation phases and stable error codes. On process startup, mark unfinished operations `WREN_OPERATION_INTERRUPTED`. Keep failed drafts and never change the active revision until a candidate graph is ready.

```python
project_dir = data_root / "sources" / source_id / "revisions" / revision_id
project_dir = project_dir.resolve()
if data_root.resolve() not in project_dir.parents:
    raise WrenConfigurationError("项目路径无效。")
project_dir.mkdir(parents=True, exist_ok=False)
project_manifest = {
    "schema_version": 5,
    "name": source_id,
    "data_source": connector_type,
    "profile": profile_name,
}
(project_dir / "wren_project.yml").write_text(
    yaml.safe_dump(project_manifest, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
```

- [ ] **Step 8: Run adapter and lifecycle tests**

Run: `cd askdb-agent && uv run pytest tests/test_wren_settings_application.py tests/test_wren_settings.py -q`
Expected: all faked CLI, metadata-only introspection, secret-redaction, revision-build, failure-retention, and rollback cases pass.

### Task 3: Introduce a shared source/model-profile runtime registry

**Files:**
- Create: `askdb-agent/src/askdb_agent/application/runtime_manager.py`
- Modify: `askdb-agent/src/askdb_agent/application/model_settings.py`
- Modify: `askdb-agent/src/askdb_agent/runtime.py`
- Create: `askdb-agent/tests/test_runtime_manager.py`
- Modify: `askdb-agent/tests/test_runtime.py`

**Interfaces:**
- `RuntimeSnapshot`: immutable `data_source_id`, `wren_revision_id`, `model_profile_id`, `model_profile_revision`, `toolkit`, and `graph`.
- `RuntimeKey = tuple[str, str, str, str]` in the order `(data_source_id, wren_revision_id, model_profile_id, model_profile_revision)`.
- `RuntimeLease` owns a `RuntimeSnapshot` and increments/decrements its in-flight use count.
- `RuntimeManager(source_store, model_store, snapshot_builder)` receives an injectable `snapshot_builder(source, revision, model_profile) -> RuntimeSnapshot` so registry behavior is independent of database or model network calls.
- `async def RuntimeManager.acquire_runtime(data_source_id: str, model_profile_id: str | None) -> RuntimeLease` resolves the source and model profile and leases a cached graph for the full four-part cache key.
- `async def RuntimeManager.release_runtime(lease: RuntimeLease) -> None` idempotently decrements the use count; retired project/profile resources become eligible for deletion only at zero.
- `async def RuntimeManager.current_runtime(data_source_id: str, model_profile_id: str | None) -> RuntimeSnapshot` is the non-streaming lookup used by model-settings compatibility callers.
- `ModelSettingsApplication` receives the shared `RuntimeManager` and `WrenSettingsStore`; its existing `current_runtime(profile_id=None)` returns the default source snapshot's `.graph` to preserve old callers.
- `async def RuntimeManager.prepare_source_revision(source_id: str, revision_id: str) -> dict[RuntimeKey, RuntimeSnapshot]` builds candidate snapshots without changing active registry state.
- `async def RuntimeManager.activate_source_revision(source_id: str, revision_id: str, candidates: dict[RuntimeKey, RuntimeSnapshot]) -> None` swaps that source's entries atomically after persistence succeeds.
- Model profile updates invalidate/rebuild only that profile's entries across sources; data source updates touch only that source's entries.

- [ ] **Step 1: Write runtime cache tests**

Add `test_runtime_key_includes_source_and_both_revisions`, `test_source_swap_preserves_other_sources`, `test_model_profile_swap_preserves_other_profiles`, and `test_candidate_build_failure_keeps_active_snapshot`. Use injected fake model/toolkit/graph builders and assert call counts plus object identity of snapshots.

The `runtime_manager` test fixture creates two in-memory source records, two model-profile records, and a builder that returns a distinct `RuntimeSnapshot` for each four-part key. `source_a` and `source_b` in the example below are those fixture records.

```python
async def test_source_swap_preserves_other_sources(runtime_manager, source_a, source_b):
    old_b = await runtime_manager.current_runtime(source_b.id, "model-1")
    candidate_a = await runtime_manager.prepare_source_revision(source_a.id, "rev-a2")
    await runtime_manager.activate_source_revision(source_a.id, "rev-a2", candidate_a)

    assert (await runtime_manager.current_runtime(source_b.id, "model-1")) is old_b
```

- [ ] **Step 2: Run focused tests and confirm missing manager**

Run: `cd askdb-agent && uv run pytest tests/test_runtime_manager.py -q`
Expected: import failure for `RuntimeManager` and `RuntimeSnapshot`.

- [ ] **Step 3: Implement immutable keyed snapshots, leases, and atomic swaps**

Key the registry by `RuntimeKey`. Build candidates off-registry, persist active revision first, and then replace only that source's registry entries under a short swap lock. `acquire_runtime` increments an in-flight count; `release_runtime` decrements it in a `finally` path. Keep retired project/profile files until their source revision has no active leases.

```python
async with self._swap_lock:
    old = self._registry
    self._registry = {
        key: snapshot for key, snapshot in old.items() if key[0] != source_id
    } | candidates
    self._retire_unleased_revisions(old, self._registry)
```

- [ ] **Step 4: Route existing model settings through the shared manager**

Refactor `ModelSettingsApplication.current_runtime` and its candidate builder to supply the currently selected source and model profile to `RuntimeManager`. Preserve current default-model behavior and existing profile CRUD APIs. Ensure a failed model-profile update leaves all old snapshots active.

```python
async def current_runtime(self, profile_id: str | None = None) -> Any:
    if self._provided_runtime:
        return self._provided_runtime_value
    source = self.wren_store.get_default_source()
    snapshot = await self.runtime_manager.current_runtime(source.id, profile_id)
    return snapshot.graph
```

- [ ] **Step 5: Run runtime tests**

Run: `cd askdb-agent && uv run pytest tests/test_runtime_manager.py tests/test_runtime.py -q`
Expected: new source/model cache cases and existing model construction tests pass.

### Task 4: Expose safe data-source catalog and management APIs

**Files:**
- Create: `askdb-agent/src/askdb_agent/api/schemas/wren_settings.py`
- Create: `askdb-agent/src/askdb_agent/api/routes/wren_settings.py`
- Modify: `askdb-agent/src/askdb_agent/api/app.py`
- Modify: `askdb-agent/tests/test_api.py`

**Interfaces:**
- Request models use Pydantic `ConfigDict(extra="forbid")`; connection forms accept only fields/variants generated by the pinned Wren schema for the selected connector, semantic forms accept validated tables/models/relationships/rules, and no model accepts project paths or CLI arguments.
- `GET /v1/settings/wren/connectors` returns the supported database/warehouse connector catalog and safe field definitions; it excludes secret values and file/internal service connector types.
- `GET /v1/data-sources` returns `{"default_data_source_id": string | null, "data_sources": [...]}` with safe summaries for enabled and disabled sources; each summary is `{id, display_name, connector_type, enabled, runtime_status, is_default}` and never returns host, database, username, password, CA bytes, or encrypted values.
- Management routes implement `GET/POST /v1/settings/wren/data-sources`, `GET/PUT /v1/settings/wren/data-sources/{source_id}`, source-scoped connection test/schema refresh/apply/rollback/deactivate, operation status, and default-source update.

- [ ] **Step 1: Add API tests for catalog filtering and credential projection**

In `test_api.py`, add `test_data_source_catalog_exposes_only_safe_fields`, `test_wren_settings_rejects_unknown_fields`, and `test_source_crud_and_default_source_routes`. Use a temporary SQLite store injected into `create_app`; the public catalog test asserts it contains no connection endpoint fields, while source detail tests permit non-secret host/database/user values but reject password and ciphertext.

```python
def test_data_source_catalog_exposes_only_safe_fields(client_with_two_sources):
    response = client_with_two_sources.get("/v1/data-sources")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert {item["id"] for item in response.json()["data_sources"]} == {"source-a", "source-b"}
    assert "connection" not in response.text
    assert "password" not in response.text
```

The `client_with_two_sources` fixture creates a `WrenSettingsStore` under `tmp_path`, inserts two enabled sources, injects the store into `create_app`, and returns `TestClient(app)`.

- [ ] **Step 2: Run route tests and confirm the new paths are absent**

Run: `cd askdb-agent && uv run pytest tests/test_api.py -q`
Expected: new route tests fail with 404 or missing route behavior.

- [ ] **Step 3: Add route schemas and application error mapping**

Map unknown source to `DATA_SOURCE_NOT_FOUND`/404; disabled or unready source to `DATA_SOURCE_UNAVAILABLE`/409; invalid configuration to `WREN_CONFIGURATION_INVALID`/422; storage/encryption failure to `WREN_SETTINGS_UNAVAILABLE`/503. Return `Cache-Control: no-store` on catalog and settings responses/errors.

```python
@router.get("/v1/data-sources")
async def list_data_sources(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.wren_settings.public_catalog()
```

- [ ] **Step 4: Wire dependency-injected services into `create_app`**

Extend `create_app` with optional store/application/runtime-manager parameters while retaining the current `runtime=` test override. Register the Wren settings router; extend no-store validation/error handling to `/v1/settings/wren`; add FastAPI lifespan initialization that imports/migrates the legacy source, restores each active source, and marks only failed sources unavailable. Preserve existing chat/model-settings routers and health response fields.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await app.state.wren_settings.initialize_and_restore()
    yield

app = FastAPI(title="AskDB Agent", version="0.1.0", lifespan=lifespan)
```

- [ ] **Step 5: Run API tests**

Run: `cd askdb-agent && uv run pytest tests/test_api.py -q`
Expected: source catalog, CRUD, validation, error mapping, and all pre-existing API tests pass.

### Task 5: Bind chat requests to a source before opening SSE

**Files:**
- Modify: `askdb-agent/src/askdb_agent/api/schemas/chat.py`
- Modify: `askdb-agent/src/askdb_agent/api/routes/chat.py`
- Modify: `askdb-agent/tests/test_api.py`
- Reference: `askdb-agent/src/askdb_agent/application/chat.py` and `tests/test_query.py`.

**Interfaces:**
- `ChatRequest.data_source_id: str | None` is optional for old clients; `thread_id` and message contracts remain unchanged.
- The route resolves an existing immutable thread binding first. For a new thread, it uses the explicit ID or, only when omitted, the active default source. It acquires a `RuntimeLease` before constructing `StreamingResponse`.
- Existing `encode_sse` event types and `stream_chat_events(runtime, messages, thread_id)` contract do not change.

- [ ] **Step 1: Add chat routing tests**

Add `test_chat_uses_requested_source_runtime`, `test_existing_thread_rejects_source_change_before_sse`, `test_missing_source_uses_default_only_for_legacy_request`, `test_missing_default_returns_data_source_required`, and `test_unavailable_source_does_not_fall_back`. Use fake store/runtime-manager implementations and assert mismatch responses are JSON errors before any stream event or graph call.

```python
response = client.post("/v1/chat", json={
    "thread_id": "thread-1",
    "data_source_id": "source-b",
    "messages": [{"role": "user", "content": "订单数"}],
})

assert response.status_code == 409
assert response.json()["detail"]["code"] == "CHAT_DATA_SOURCE_MISMATCH"
assert runtime_manager.acquired_keys == []
```

- [ ] **Step 2: Run the API tests and confirm the new behaviors fail**

Run: `cd askdb-agent && uv run pytest tests/test_api.py -q`
Expected: newly added source-aware routing cases fail before implementation.

- [ ] **Step 3: Resolve and persist the immutable binding**

Resolve the source and call `bind_thread_source` before obtaining the runtime. If an existing binding conflicts with an explicit request ID, return HTTP 409 with `CHAT_DATA_SOURCE_MISMATCH` before yielding SSE. If the thread is new, the request omits the ID, and no active default exists, return HTTP 422 with `DATA_SOURCE_REQUIRED`.

- [ ] **Step 4: Capture the correct graph snapshot and preserve SSE**

Acquire a `RuntimeLease` for the resolved source plus `model_profile_id`; pass only `lease.snapshot.graph` to the unchanged `stream_chat_events`. Release the lease in the generator's `finally` block. Map source-not-found/unavailable errors to stable JSON responses and do not retry against another source.

```python
lease = await app.state.runtime_manager.acquire_runtime(source_id, request.model_profile_id)

async def stream():
    try:
        async for event, payload in stream_chat_events(
            lease.snapshot.graph, messages, request.thread_id
        ):
            yield encode_sse(event, payload)
        yield encode_sse("done", {})
    finally:
        await app.state.runtime_manager.release_runtime(lease)
```

- [ ] **Step 5: Run chat and query-gate regression tests**

Run: `cd askdb-agent && uv run pytest tests/test_api.py tests/test_query.py -q`
Expected: all new binding/source selection tests pass and the existing `dry_plan → dry_run → query` assertions remain unchanged.

### Task 6: Add same-origin Web proxy routes and source client API

**Files:**
- Create: `askdb-web/lib/wren-settings-api.ts`
- Create: `askdb-web/lib/data-sources.ts`
- Create: `askdb-web/app/api/data-sources/route.ts`
- Create: `askdb-web/app/api/settings/wren/[...segments]/route.ts`
- Add: `askdb-web/vitest.config.ts`, `askdb-web/tests/lib/data-sources.test.ts`
- Modify: `askdb-web/package.json` and `askdb-web/pnpm-lock.yaml`.

**Interfaces:**
- `DataSourceSummary` includes `id`, `display_name`, `connector_type`, `enabled`, `runtime_status`, and `is_default`; it excludes every connection/credential field.
- `fetchDataSourceCatalog(): Promise<DataSourceCatalog>` validates unknown JSON before returning it and uses `cache: "no-store"`.
- `proxyWrenSettings(request, agentPath)` limits request bodies, proxies same-origin requests to `ASKDB_AGENT_URL`, uses endpoint-specific allowlists, maps stable errors to Chinese messages, and uses no-store headers. The public catalog omits connection fields; source settings may return non-secret host/database/user fields and `credential_configured`, but never password or ciphertext.

- [ ] **Step 1: Set up Web unit tests**

Run `cd askdb-web && pnpm add -D vitest jsdom @testing-library/react`; add `"test": "vitest run"` to `package.json`; configure the `@` alias to the Web project root in `vitest.config.ts`.

- [ ] **Step 2: Write catalog parser and proxy tests**

Add `test_fetches_catalog_without_cache`, `test_rejects_invalid_catalog_payload`, `test_catalog_projection_drops_connection_fields`, and `test_settings_projection_keeps_connection_fields_but_drops_secrets`. Mock `fetch`; assert the catalog omits `host`, `database`, `user`, password, and ciphertext, while settings detail keeps only host/port/database/user and `credential_configured` from connection data.

```ts
it("fetches the catalog without caching", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({
    default_data_source_id: "source-a",
    data_sources: [{
      id: "source-a", display_name: "分析库", connector_type: "mysql",
      enabled: true, runtime_status: "ready", is_default: true,
    }],
  }));
  vi.stubGlobal("fetch", fetchMock);

  await fetchDataSourceCatalog();

  expect(fetchMock).toHaveBeenCalledWith("/api/data-sources", { cache: "no-store" });
});
```

- [ ] **Step 3: Run focused Web tests before implementation**

Run: `cd askdb-web && pnpm run test -- tests/lib/data-sources.test.ts`
Expected: module import/test failures for the missing source API helpers.

- [ ] **Step 4: Implement BFF and safe client helpers**

Forward `/api/data-sources` to `/v1/data-sources` and catch-all `/api/settings/wren/*` to `/v1/settings/wren/*`. Encode dynamic path segments, stream-limit JSON bodies at 1 MiB, forward cancellation signals, and sanitize upstream error bodies.

```ts
const upstream = await fetch(`${agentUrl}/v1/data-sources`, {
  method: request.method,
  cache: "no-store",
  signal: request.signal,
});
return Response.json(publicCatalogProjection(await upstream.json()), {
  status: upstream.status,
  headers: { "cache-control": "no-store, max-age=0" },
});
```

- [ ] **Step 5: Run Web unit and lint checks**

Run: `cd askdb-web && pnpm run test -- tests/lib/data-sources.test.ts && pnpm run lint`
Expected: catalog validation, safe projection, and formatting/lint checks pass.

### Task 7: Persist source choice in conversations and show it in the sidebar

**Files:**
- Modify: `askdb-web/lib/local-thread-adapter.tsx`
- Modify: `askdb-web/lib/agent-chat-adapter.ts`
- Modify: `askdb-web/components/assistant-ui/elements/thread.aui.tsx`
- Create: `askdb-web/components/assistant-ui/elements/data-source-selector.tsx`
- Modify: `askdb-web/components/assistant-ui/elements/thread-list-sidebar.aui.tsx`
- Add: `askdb-web/tests/lib/local-thread-adapter.test.ts` and focused selector tests.

**Interfaces:**
- Extend `StoredThread` with `dataSourceId?: string` and `dataSourceNameSnapshot?: string`; malformed or absent legacy fields remain readable.
- Export `getThreadDataSourceId(threadId)`, `getThreadDataSourceName(threadId)`, `setThreadDataSource(threadId, sourceId, sourceName)`, and `migrateLegacyThreadSources(defaultSourceId, defaultSourceName)` from `local-thread-adapter.tsx`.
- `agentChatAdapter.run` sends `data_source_id` from the active thread metadata while keeping `thread_id`, `model_profile_id`, messages, and SSE parsing unchanged.

- [ ] **Step 1: Write local metadata migration tests**

Add `test_reads_existing_threads_without_source_metadata`, `test_saves_and_reads_thread_source`, `test_migrates_old_threads_to_legacy_default`, and `test_keeps_source_name_snapshot_when_catalog_source_is_disabled`. Mock `window.localStorage` with a fresh map per test.

```ts
it("stores the chosen source on the thread record", () => {
  setThreadDataSource("thread-1", "source-a", "分析库");

  expect(getThreadDataSourceId("thread-1")).toBe("source-a");
  expect(getThreadDataSourceName("thread-1")).toBe("分析库");
});
```

- [ ] **Step 2: Run thread adapter tests and confirm the new APIs are absent**

Run: `cd askdb-web && pnpm run test -- tests/lib/local-thread-adapter.test.ts`
Expected: missing helper exports or assertions fail.

- [ ] **Step 3: Implement source metadata and legacy migration**

Persist source ID and display-name snapshot in the existing `askdb:chat:threads` record. When the catalog loads, resolve current display names while retaining snapshots for disabled/unavailable sources. Do not store database connection or credential fields in local storage.

- [ ] **Step 4: Add the composer selector and request payload**

Add a native accessible `<select>` beside `ModelProfileSelector`. For a blank thread, initialize to the deployment default and allow changing the source; lock it after the first user message. With no available source, disable sending and show a link/action to open Wren settings. Store the selected source before send so the adapter can include it in the first request.

```tsx
<label htmlFor="askdb-data-source">数据源</label>
<select
  id="askdb-data-source"
  aria-label="选择当前会话的数据源"
  value={selectedSourceId}
  disabled={hasUserMessages || availableSources.length === 0}
  onChange={(event) => void changeSource(event.target.value)}
>
  {availableSources.map((source) => (
    <option key={source.id} value={source.id}>{source.display_name}</option>
  ))}
</select>
```

The selector initializes a local thread ID before persisting a default/changed source. The chat adapter then includes that stored value in the existing request body:

```ts
body: JSON.stringify({
  thread_id: unstable_threadId,
  data_source_id: getThreadDataSourceId(unstable_threadId),
  model_profile_id: getThreadModelProfileId(unstable_threadId),
  messages: formattedMessages,
}),
```

- [ ] **Step 5: Render the source badge in every sidebar thread item**

Pass the thread's `remoteId` into the sidebar item renderer, resolve its source from local metadata/catalog, and render the name beside the title. Render `不可用` for disabled/missing sources and retain the saved name snapshot. Keep title search and mobile navigation behavior intact.

```tsx
<span className="min-w-0 flex-1 truncate">
  <ThreadListItemPrimitive.Title fallback="新对话" />
</span>
{sourceName && <span className="max-w-24 truncate rounded bg-[#e8e3d8] px-1.5 py-0.5 text-[10px]">{sourceName}</span>}
{sourceUnavailable && <span className="text-[10px] text-[#9c6046]">不可用</span>}
```

- [ ] **Step 6: Run source UI unit tests and Web lint**

Run: `cd askdb-web && pnpm run test -- tests/lib/local-thread-adapter.test.ts tests/components/data-source-selector.test.tsx && pnpm run lint`
Expected: legacy migration, source locking, no-source disabled state, and sidebar badge assertions pass; lint is clean.

### Task 8: Build the Wren data-source settings page

**Files:**
- Create: `askdb-web/app/settings/wren/page.tsx`
- Create: `askdb-web/components/wren-settings/data-sources-page.tsx`
- Create: `askdb-web/components/wren-settings/data-source-form.tsx`
- Create: `askdb-web/components/wren-settings/schema-model-editor.tsx`
- Create: `askdb-web/components/wren-settings/operation-progress.tsx`
- Modify: `askdb-web/components/assistant-ui/elements/thread-list-sidebar.aui.tsx`
- Modify: `askdb-web/tests` with page/form tests.

**Interfaces:**
- The catalog page lists sources, enabled/runtime status, default marker, last operation, and actions to create, edit, set default, apply, rollback, or deactivate.
- The per-source form edits display name, connector-specific fields/variants, selected tables/fields, models, relationships, rules, and advanced view SQL. Blank sensitive fields mean retain the stored credential; the page never hydrates a saved secret.
- `operation-progress.tsx` polls the source operation endpoint until `active` or `failed` and displays phase plus stable sanitized message.

- [ ] **Step 1: Write page/form behavior tests**

Add `test_lists_sources_and_marks_default`, `test_password_is_never_prefilled`, `test_apply_polls_until_active`, and `test_failed_apply_preserves_editable_draft`. Mock `fetchDataSourceCatalog` and management API calls.

```tsx
it("never pre-fills the saved database password", async () => {
  render(<DataSourceForm sourceId="source-a" />);
  const password = await screen.findByLabelText("密码");

  expect(password).toHaveValue("");
  expect(password).toHaveAttribute("placeholder", "已配置；留空则保留当前密码");
});
```

- [ ] **Step 2: Build the source directory and per-source editing flow**

Reuse the existing form and dialog conventions from `components/model-settings/model-settings-dialog.tsx`, but present a dedicated settings page at `/settings/wren`. Submit structured JSON only. Connect-test and schema-refresh actions update the current form without applying it.

- [ ] **Step 3: Add default, apply, rollback, and deactivate actions**

Only allow default selection for enabled sources with an active runtime. Poll operation IDs returned from apply and rollback. Disable hard deletion for sources referenced by conversations; expose deactivation and retain historical source labels. Include a “返回对话” link from the standalone settings page to `/`.

```ts
async function waitForOperation(sourceId: string, operationId: string) {
  while (true) {
    const operation = await fetchOperation(sourceId, operationId);
    setOperation(operation);
    if (operation.status === "active" || operation.status === "failed") return operation;
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
}
```

- [ ] **Step 4: Add the sidebar navigation item**

Add “Wren 数据源” to the existing settings menu and navigate to `/settings/wren`; keep “设置模型” behavior unchanged.

- [ ] **Step 5: Run page tests and lint**

Run: `cd askdb-web && pnpm run test -- tests/components/wren-settings && pnpm run lint`
Expected: catalog rendering, secret blank-field semantics, operation polling, and navigation tests pass.

### Task 9: Verify the complete two-source flow

**Files:**
- Modify docs only if implementation changes the accepted contract: `docs/superpowers/specs/2026-10-02-wren-settings-ui-design.md`.
- No new application files unless a failing acceptance scenario identifies a concrete gap.

- [ ] **Step 1: Run the complete Agent suite**

Run: `cd askdb-agent && uv run pytest -q`
Expected: all source store, Wren lifecycle, runtime manager, API, chat, and query-gate tests pass.

- [ ] **Step 2: Run Web unit, lint, and production build**

Run: `cd askdb-web && pnpm run test && pnpm run lint && pnpm run build`
Expected: unit tests pass, `oxlint`/`oxfmt --check` report no issues, and Next production build completes.

- [ ] **Step 3: Exercise at least two different non-production database/warehouse connectors**

Create two named sources of different types with read-only test accounts; test both, introspect metadata, configure one model in each, apply each revision, and confirm both appear as ready in the composer catalog. Verify the settings response and generated project directories contain no plaintext secret.

- [ ] **Step 4: Verify conversation selection and sidebar identity**

Create one conversation under each source, send a question, and confirm each request reaches only its selected source. Confirm the left sidebar shows the matching source name. Attempt to alter a thread's source and confirm Agent returns `CHAT_DATA_SOURCE_MISMATCH` before SSE/tool execution. Reload and confirm both source labels persist.

- [ ] **Step 5: Verify failure isolation and in-flight snapshot behavior**

Force a build failure for one source and confirm its active runtime plus the other source remain queryable. Start a streamed request, apply a successful new revision during the stream, and confirm the in-flight request finishes on its captured snapshot while the next request uses the new revision.

## Spec Coverage Self-Review

- Mixed database/warehouse sources, isolated profiles/projects/revisions, and per-source CRUD: Tasks 1, 2, 4, and 8.
- Connection testing, schema metadata, model/relationship/rule editing, build/apply/poll/rollback, secret handling: Tasks 2, 4, and 8.
- Source-aware runtime and existing per-thread model profiles: Task 3.
- Chat dropdown, immutable conversation binding, legacy thread migration, sidebar badge, unavailable-source behavior: Tasks 1, 5, and 7.
- BFF catalog/settings routes and credential-safe projection: Task 6.
- Failure isolation, process restart recovery, SSE and SQL gate compatibility: Tasks 3, 5, and 9.
- The only new Agent runtime dependency is `PyYAML` for safe project YAML generation. Web-only validation dependencies are Vitest, jsdom, and Testing Library.
