from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.routes.chat import router as chat_router
from api.routes.chat_options import router as chat_options_router
from api.routes.auth import router as auth_router
from api.routes.admin_users import router as admin_users_router
from api.routes.model_settings import router as model_settings_router
from api.routes.wren_settings import router as wren_settings_router
from api.routes.threads import router as threads_router
from api.routes.business_rules import router as business_rules_router
from api.routes.memories import router as memories_router
from api.dependencies import auth_http_exception
from api.thread_body_limit import ThreadRequestBodyLimitMiddleware
from domain.auth import AuthError
from application.runtime_manager import RuntimeManager
from application.auth import AuthApplication
from application.model_settings import ModelSettingsApplication
from application.wren_settings import WrenSettingsApplication
from application.conversation_memory_lifecycle import (
    initialize_memory_before_serving,
    release_sweeper_lease,
    run_memory_sweeper,
    stop_memory_sweeper,
    try_acquire_sweeper_lease,
)
from auth_store import AuthStore
from model_settings import ModelSettingsStore
from wren_settings import WrenSettingsStore
from integrations.conversation_store import ConversationMemoryStore
from integrations.deletion_journal import EncryptedDeletionJournal
from application.business_rule_memory import BusinessRuleMemoryApplication
from integrations.business_rule_store import BusinessRuleMemoryStore
from application.query_memory import QueryMemoryApplication
from integrations.query_memory_store import QueryMemoryStore


def _persisted_memory_requires_runtime(database_path: Any) -> bool:
    if not database_path.exists():
        return False
    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "agent_memory_journal_state" in tables:
            state_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(agent_memory_journal_state)"
                )
            }
            if "journal_initialized" in state_columns:
                state = connection.execute(
                    "SELECT journal_initialized FROM agent_memory_journal_state WHERE id=1"
                ).fetchone()
                if state and state[0]:
                    return True
        if "agent_conversation_threads" in tables:
            count = connection.execute(
                "SELECT COUNT(*) FROM agent_conversation_threads"
            ).fetchone()[0]
            if count > 0:
                return True
        if "business_rule_candidates" in tables:
            count = connection.execute(
                "SELECT COUNT(*) FROM business_rule_candidates"
            ).fetchone()[0]
            if count > 0:
                return True
        if "business_rule_origins" in tables:
            count = connection.execute(
                "SELECT COUNT(*) FROM business_rule_origins WHERE publication_status IN ('active','removal_pending')"
            ).fetchone()[0]
            if count > 0:
                return True
        if "query_example_candidates" in tables:
            count = connection.execute(
                """SELECT COUNT(*) FROM query_example_candidates
                   WHERE review_status IN ('pending','approved','needs_revalidation')
                      OR publication_status='active'"""
            ).fetchone()[0]
            if count > 0:
                return True
        if "query_corpus_revisions" in tables:
            count = connection.execute(
                "SELECT COUNT(*) FROM query_corpus_revisions WHERE status IN ('prepared','active')"
            ).fetchone()[0]
            return count > 0
        return False
    finally:
        connection.close()


def create_app(
    runtime: Any | None = None,
    *,
    model_settings_application: ModelSettingsApplication | None = None,
    model_store: ModelSettingsStore | None = None,
    wren_store: WrenSettingsStore | None = None,
    runtime_manager: RuntimeManager | None = None,
    wren_settings_application: WrenSettingsApplication | None = None,
    auth_store: AuthStore | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        memory_task: asyncio.Task[None] | None = None
        revision_retention_task: asyncio.Task[None] | None = None
        sweeper_lease: int | None = None
        try:
            await asyncio.to_thread(store.list_data_sources)
            await wren_settings.initialize()
            memory_enabled = os.environ.get("ASKDB_AGENT_MEMORY_ENABLED", "0").strip() == "1"
            recall_setting = os.environ.get("ASKDB_AGENT_RECALL_ENABLED")
            recall_requested = recall_setting is None or recall_setting.strip() == "1"
            recall_enabled = recall_requested and memory_enabled
            if recall_setting is not None and recall_setting.strip() == "1" and not memory_enabled:
                raise RuntimeError(
                    "online recall requires ASKDB_AGENT_MEMORY_ENABLED and its journal"
                )
            app.state.memory_recall_enabled = recall_enabled
            memory_required = await asyncio.to_thread(
                _persisted_memory_requires_runtime, store.database_path.resolve()
            )
            if memory_required and not memory_enabled:
                raise RuntimeError(
                    "persisted agent memory requires ASKDB_AGENT_MEMORY_ENABLED and its journal"
                )
            if memory_enabled:
                journal = EncryptedDeletionJournal.from_environment()
                database_path = store.database_path.resolve()
                if journal.path == database_path or journal.path.parent == database_path.parent:
                    raise RuntimeError(
                        "memory journal must use a separate directory from the settings database"
                    )
                interval_raw = os.environ.get("ASKDB_AGENT_MEMORY_SWEEP_INTERVAL_SECONDS", "300")
                try:
                    sweep_interval = int(interval_raw)
                except ValueError as exc:
                    raise RuntimeError("memory sweep interval must be an integer") from exc
                if not 30 <= sweep_interval <= 3600:
                    raise RuntimeError("memory sweep interval must be between 30 and 3600 seconds")
                sweeper_lease = await asyncio.to_thread(
                    try_acquire_sweeper_lease, database_path
                )
                if sweeper_lease is None:
                    raise RuntimeError(
                        "memory-enabled deployment requires one Agent process per SQLite store"
                    )
                business_rule_store = BusinessRuleMemoryStore(
                    database_path,
                    deletion_journal=journal,
                )
                query_memory_store = QueryMemoryStore(
                    database_path, deletion_journal=journal
                )
                corpus_root = query_memory_store.corpus_root
                journal_root = journal.path.parent
                if (
                    corpus_root == journal_root
                    or corpus_root.is_relative_to(journal_root)
                    or journal_root.is_relative_to(corpus_root)
                    or corpus_root == database_path
                ):
                    raise RuntimeError(
                        "query corpus, deletion journal, and settings database must use separate paths"
                    )
                await asyncio.to_thread(query_memory_store.initialize)
                wren_settings.attach_query_memory_store(query_memory_store)
                memory_store = await asyncio.to_thread(
                    lambda: ConversationMemoryStore(
                        database_path,
                        deletion_journal=journal,
                        deletion_participant=business_rule_store,
                        suppression_participants=(query_memory_store,),
                    )
                )
                # Recovery, expiry, and deletion replay complete before requests open.
                await asyncio.to_thread(initialize_memory_before_serving, memory_store)
                await asyncio.to_thread(query_memory_store.assert_corpus_files_consistent)
                while await asyncio.to_thread(query_memory_store.expire_pending_candidates, limit=500):
                    pass
                await asyncio.to_thread(query_memory_store.prune_expired_revisions, limit=500)
                app.state.conversation_memory = memory_store
                wren_settings.business_rule_store = business_rule_store
                app.state.business_rule_memory = BusinessRuleMemoryApplication(
                    business_rule_store,
                    store,
                    app.state.auth_application,
                )
                app.state.query_memory_store = query_memory_store
                app.state.query_memory = QueryMemoryApplication(
                    query_memory_store,
                    store,
                    app.state.auth_application,
                )
                await wren_settings.recover_source_operations(business_rule_store)
                await wren_settings.sweep_pending_rule_removals(business_rule_store)
                await wren_settings.prune_expired_wren_revisions()

                async def sweep_memory_publications() -> None:
                    await wren_settings.sweep_pending_rule_removals(business_rule_store)
                    await asyncio.to_thread(query_memory_store.expire_pending_candidates, limit=100)
                    retention_guard = (
                        wren_settings.runtime_manager.memory_revision_retention_guard()
                    )
                    async with retention_guard as in_flight_memory_revisions:
                        prune_task = asyncio.create_task(
                            asyncio.to_thread(
                                query_memory_store.prune_expired_revisions,
                                in_flight=lambda revision: (
                                    f"query-corpus:{revision.corpus_revision}:{revision.content_hash}"
                                    in in_flight_memory_revisions
                                ),
                                limit=100,
                            )
                        )
                        try:
                            await asyncio.shield(prune_task)
                        except asyncio.CancelledError as cancellation:
                            # Keep the lease snapshot stable until the worker stops unlinking files.
                            while not prune_task.done():
                                try:
                                    await asyncio.shield(prune_task)
                                except asyncio.CancelledError:
                                    continue
                                except BaseException:
                                    break
                            if prune_task.done() and not prune_task.cancelled():
                                prune_task.exception()
                            raise cancellation

                memory_task = asyncio.create_task(
                    run_memory_sweeper(
                        memory_store,
                        interval_seconds=sweep_interval,
                        publication_sweeper=sweep_memory_publications,
                    ),
                    name="askdb-agent-memory-sweeper",
                )
            else:
                wren_settings.attach_query_memory_store(None)
                await wren_settings.recover_source_operations()
                await wren_settings.prune_expired_wren_revisions()
            revision_retention_task = asyncio.create_task(
                wren_settings.run_revision_retention_sweeper(),
                name="askdb-wren-revision-retention",
            )
            yield
        finally:
            if memory_task is not None:
                await stop_memory_sweeper(memory_task)
            if revision_retention_task is not None:
                await stop_memory_sweeper(revision_retention_task)
            if sweeper_lease is not None:
                await asyncio.to_thread(release_sweeper_lease, sweeper_lease)

    app = FastAPI(title="AskDB Agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(ThreadRequestBodyLimitMiddleware)
    app.state.runtime = runtime
    model_settings = model_settings_application or ModelSettingsApplication(
        runtime=runtime,
        store=model_store,
    )
    store = wren_store or (
        wren_settings_application.store if wren_settings_application else WrenSettingsStore()
    )
    wren_settings = wren_settings_application or WrenSettingsApplication(
        store=store,
        model_store=model_settings.store,
        runtime_manager=runtime_manager,
    )
    model_settings.runtime_manager = wren_settings.runtime_manager
    model_settings.wren_store = store
    app.state.model_settings = model_settings
    app.state.wren_store = store
    app.state.runtime_manager = wren_settings.runtime_manager
    app.state.wren_settings = wren_settings
    auth_database = auth_store or AuthStore(database_path=store.database_path)
    app.state.auth_store = auth_database
    app.state.auth_application = AuthApplication(auth_database)
    app.state.auth_store_ready = False
    app.state.auth_store_lock = threading.Lock()
    app.include_router(auth_router)
    app.include_router(admin_users_router)
    app.include_router(chat_router)
    app.include_router(chat_options_router)
    app.include_router(model_settings_router)
    app.include_router(wren_settings_router)
    app.include_router(threads_router)
    app.include_router(business_rules_router)
    app.include_router(memories_router)

    @app.exception_handler(StarletteHTTPException)
    async def no_store_settings_errors(
        request: Request, exc: StarletteHTTPException
    ):
        if request.url.path.startswith((
            "/v1/auth", "/v1/admin", "/v1/chat", "/v1/settings/model",
            "/v1/settings/wren", "/v1/data-sources",
            "/v1/threads",
            "/v1/business-rules",
            "/v1/memories", "/v1/query-examples",
        )):
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": jsonable_encoder(exc.detail)},
                headers={**(exc.headers or {}), "Cache-Control": "no-store"},
            )
        return await http_exception_handler(request, exc)

    @app.exception_handler(AuthError)
    async def safe_auth_errors(request: Request, exc: AuthError) -> JSONResponse:
        http_error = auth_http_exception(exc)
        return JSONResponse(
            status_code=http_error.status_code,
            content={"detail": jsonable_encoder(http_error.detail)},
            headers={**(http_error.headers or {}), "Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def sanitize_settings_validation(
        request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        if request.url.path.startswith((
            "/v1/auth", "/v1/admin", "/v1/chat", "/v1/settings/model",
            "/v1/settings/wren", "/v1/data-sources",
            "/v1/threads",
            "/v1/business-rules",
        )):
            path = request.url.path
            if path.startswith("/v1/auth"):
                code, message = "AUTH_INPUT_INVALID", "登录或密码字段无效，请检查后重试。"
            elif path.startswith("/v1/admin"):
                code, message = "ACCOUNT_INPUT_INVALID", "账号字段无效，请检查后重试。"
            elif path.startswith("/v1/chat"):
                code, message = "CHAT_REQUEST_INVALID", "聊天请求格式无效，请检查后重试。"
            elif path.startswith("/v1/threads"):
                code, message = "THREAD_REQUEST_INVALID", "会话记忆请求格式无效，请检查后重试。"
            elif path.startswith("/v1/business-rules"):
                code, message = "BUSINESS_RULE_INPUT_INVALID", "业务规则候选字段无效，请检查后重试。"
            elif "/wren" in path or "/data-sources" in path:
                code, message = "WREN_CONFIGURATION_INVALID", "Wren 配置字段无效，请检查后重试。"
            else:
                code, message = "MODEL_CONFIGURATION_INVALID", "模型配置字段无效，请检查后重试。"
            return JSONResponse(
                status_code=422,
                content={"detail": {"code": code, "message": message}},
                headers={"Cache-Control": "no-store"},
            )
        safe_errors = [
            {
                key: error[key]
                for key in ("type", "loc", "msg")
                if key in error
            }
            for error in _exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"detail": jsonable_encoder(safe_errors)},
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok"}

    return app
