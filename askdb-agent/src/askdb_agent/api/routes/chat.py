from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from askdb_agent.api.schemas.chat import ChatRequest
from askdb_agent.api.dependencies import get_auth_application, require_current_user
from askdb_agent.api.streaming import encode_sse
from askdb_agent.application.auth import AuthApplication
from askdb_agent.application.chat import stream_chat_events
from askdb_agent.application.conversation_memory import sanitize_turn_text
from askdb_agent.application.memory_context import (
    ContextBudgetError,
    MemoryContextAssembler,
    MemoryContextBudget,
    recall_reference_payload,
    serialize_recall_references,
    serialize_tool_schemas,
)
from askdb_agent.agent.presentation import QUERY_GATE_SYSTEM_PROMPT
from askdb_agent.application.runtime_manager import (
    RuntimeDataSourceNotFound,
    RuntimeDataSourceUnavailable,
    RuntimeModelNotConfigured,
)
from askdb_agent.model_settings import ModelProfileNotFound
from askdb_agent.domain.auth import Principal
from askdb_agent.domain.conversation_memory import (
    ThreadDeletionJournalRequired,
    ThreadHistoryImportIncomplete,
    ThreadGrantRevoked,
    ThreadNotFound,
    TurnAlreadyRunning,
    TurnIdempotencyConflict,
    TurnSequenceConflict,
)
from askdb_agent.integrations.conversation_store import ConversationMemoryStore
from askdb_agent.integrations.deletion_journal import DeletionJournalUnavailable
from askdb_agent.integrations.tokenizer import TiktokenCounter
from askdb_agent.integrations.query_memory_store import QueryMemoryUnavailable
from askdb_agent.wren_settings import (
    ChatDataSourceForbidden,
    ChatDataSourceMismatch,
    ChatDataSourceUnavailable,
    ChatLegacyThreadRequiresNew,
    ChatThreadOwnerMismatch,
    WrenConfigurationError,
    WrenSettingsUnavailable,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    principal: Principal = Depends(require_current_user),
    auth: AuthApplication = Depends(get_auth_application),
) -> StreamingResponse:
    app = http_request.app
    lease = None
    memory_store: ConversationMemoryStore | None = None
    thread_context = None
    assembled_context = None
    recalled_document_keys: tuple[tuple[str, str], ...] = ()
    memory_references: tuple[dict[str, object], ...] = ()
    memory_reference_text = ""
    if request.message is None and not request.thread_id.startswith(f"{principal.user_id}:"):
        if ":" in request.thread_id:
            raise HTTPException(
                status_code=404,
                detail={"code": "CHAT_THREAD_NOT_FOUND", "message": "找不到此会话。"},
                headers={"Cache-Control": "no-store"},
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CHAT_THREAD_LEGACY_REQUIRES_NEW_THREAD",
                "message": "旧匿名会话不能继续使用，请新建会话。",
            },
            headers={"Cache-Control": "no-store"},
        )
    if request.message is not None:
        configured_memory = getattr(app.state, "conversation_memory", None)
        if configured_memory is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "THREAD_MEMORY_DISABLED",
                    "message": "服务端会话记忆尚未完成启动恢复。",
                },
                headers={"Cache-Control": "no-store"},
            )
        memory_store = (
            configured_memory.store
            if hasattr(configured_memory, "store")
            else configured_memory
        )
        try:
            thread_context = await run_in_threadpool(
                memory_store.load_context,
                request.thread_id,
                owner_user_id=principal.user_id,
                limit=30,
            )
        except (ThreadNotFound, ThreadGrantRevoked, LookupError):
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "CHAT_THREAD_NOT_FOUND",
                    "message": "找不到此会话或当前账号无权查看。",
                },
                headers={"Cache-Control": "no-store"},
            ) from None
        except (ThreadDeletionJournalRequired, DeletionJournalUnavailable):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "THREAD_MEMORY_UNAVAILABLE",
                    "message": "会话记忆正在恢复，请稍后重试。",
                },
                headers={"Cache-Control": "no-store"},
            ) from None
        except ThreadHistoryImportIncomplete:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "THREAD_HISTORY_IMPORT_INCOMPLETE",
                    "message": "旧会话仍在导入，请稍后重试。",
                },
                headers={"Cache-Control": "no-store"},
            ) from None
        if request.data_source_id and request.data_source_id != thread_context.source_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CHAT_DATA_SOURCE_MISMATCH",
                    "message": "会话已绑定到其他数据源。",
                },
                headers={"Cache-Control": "no-store"},
            )
    try:
        source_store = app.state.wren_store
        source_store.list_data_sources()  # initializes the shared SQLite catalog
        business_rule_memory = getattr(app.state, "business_rule_memory", None)
        business_rule_store = getattr(business_rule_memory, "store", None)
        if thread_context is not None:
            source_id = thread_context.source_id
        else:
            binding = source_store.get_thread_binding(request.thread_id)
            if binding:
                bound_source_id, owner_user_id = binding
                if owner_user_id is None:
                    raise ChatLegacyThreadRequiresNew(
                        "旧匿名会话不能继续使用，请新建会话。"
                    )
                if owner_user_id != principal.user_id:
                    raise ChatThreadOwnerMismatch("此会话不属于当前账号。")
                if request.data_source_id and request.data_source_id != bound_source_id:
                    raise ChatDataSourceMismatch("会话已绑定到其他数据源。")
                source_id = bound_source_id
            else:
                source_id = request.data_source_id
                if source_id is None:
                    default_source = source_store.get_default_source()
                    if default_source and auth.can_access_data_source(principal, default_source.id):
                        source_id = default_source.id
                if source_id is None:
                    message = (
                        "请选择当前账号可用的数据源。"
                        if principal.role == "member"
                        else "请先为此会话选择一个数据源。"
                    )
                    raise HTTPException(
                        status_code=422,
                        detail={"code": "DATA_SOURCE_REQUIRED", "message": message},
                        headers={"Cache-Control": "no-store"},
                    )

        try:
            selected_source = source_store.get_data_source(source_id)
        except LookupError:
            raise HTTPException(
                status_code=404,
                detail={"code": "DATA_SOURCE_NOT_FOUND", "message": "找不到所选数据源。"},
                headers={"Cache-Control": "no-store"},
            ) from None
        if not selected_source.enabled:
            raise ChatDataSourceUnavailable("所选数据源当前不可用。")
        if not auth.can_access_data_source(principal, source_id):
            raise HTTPException(
                status_code=404,
                detail={"code": "DATA_SOURCE_NOT_FOUND", "message": "找不到所选数据源。"},
                headers={"Cache-Control": "no-store"},
            )

        # Re-check the current identity and grant, and persist ownership before
        # runtime acquisition so a revoked permission cannot initialize a source.
        source_store.bind_thread_source(
            request.thread_id,
            principal.user_id,
            source_id,
            principal.role,
        )
        if business_rule_store is not None and await run_in_threadpool(
            business_rule_store.has_pending_removals, source_id
        ):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "BUSINESS_RULE_REMOVAL_PENDING",
                    "message": "该数据源正在移除已撤销的业务规则，请稍后重试。",
                },
                headers={"Cache-Control": "no-store"},
            )
        lease = await app.state.runtime_manager.acquire_runtime(
            source_id, request.model_profile_id
        )
        runtime_rule_ids = tuple(
            str(document.id)
            for document in lease.snapshot.memory_documents
            if str(getattr(document.kind, "value", document.kind)) == "business_rule"
            and len(str(document.id)) == 32
        ) if lease is not None else ()
        # Runtime initialization may take time. Re-check before exposing it to
        # the stream in case an administrator revoked access in the meantime.
        source_store.bind_thread_source(
            request.thread_id,
            principal.user_id,
            source_id,
            principal.role,
        )
        if business_rule_store is not None and await run_in_threadpool(
            business_rule_store.blocks_runtime_revision, source_id, runtime_rule_ids
        ):
            if lease is not None:
                await app.state.runtime_manager.release_runtime(lease)
            lease = None
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "BUSINESS_RULE_REMOVAL_PENDING",
                    "message": "该数据源正在移除已撤销的业务规则，请稍后重试。",
                },
                headers={"Cache-Control": "no-store"},
            )
    except ChatLegacyThreadRequiresNew:
        if lease is not None:
            await app.state.runtime_manager.release_runtime(lease)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CHAT_THREAD_LEGACY_REQUIRES_NEW_THREAD",
                "message": "旧匿名会话不能继续使用，请新建会话。",
            },
            headers={"Cache-Control": "no-store"},
        ) from None
    except ChatThreadOwnerMismatch:
        if lease is not None:
            await app.state.runtime_manager.release_runtime(lease)
        raise HTTPException(
            status_code=404,
            detail={"code": "CHAT_THREAD_NOT_FOUND", "message": "找不到此会话。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    except ChatDataSourceForbidden:
        if lease is not None:
            await app.state.runtime_manager.release_runtime(lease)
        raise HTTPException(
            status_code=404,
            detail={"code": "DATA_SOURCE_NOT_FOUND", "message": "找不到所选数据源。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    except ChatDataSourceUnavailable:
        if lease is not None:
            await app.state.runtime_manager.release_runtime(lease)
        raise HTTPException(
            status_code=409,
            detail={"code": "DATA_SOURCE_UNAVAILABLE", "message": "所选数据源当前不可用。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    except ChatDataSourceMismatch:
        if lease is not None:
            await app.state.runtime_manager.release_runtime(lease)
        raise HTTPException(
            status_code=409,
            detail={"code": "CHAT_DATA_SOURCE_MISMATCH", "message": "此会话已绑定其他数据源，请新建会话后切换。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    except RuntimeDataSourceNotFound:
        if lease is not None:
            await app.state.runtime_manager.release_runtime(lease)
        raise HTTPException(
            status_code=404,
            detail={"code": "DATA_SOURCE_NOT_FOUND", "message": "找不到所选数据源。"},
        ) from None
    except RuntimeDataSourceUnavailable:
        if lease is not None:
            await app.state.runtime_manager.release_runtime(lease)
        raise HTTPException(
            status_code=409,
            detail={"code": "DATA_SOURCE_UNAVAILABLE", "message": "所选数据源当前不可用，请检查设置。"},
        ) from None
    except RuntimeModelNotConfigured:
        if lease is not None:
            await app.state.runtime_manager.release_runtime(lease)
        raise HTTPException(
            status_code=503,
            detail={"code": "MODEL_NOT_CONFIGURED", "message": "请先完成模型设置。"},
        ) from None
    except ModelProfileNotFound:
        if lease is not None:
            await app.state.runtime_manager.release_runtime(lease)
        raise HTTPException(
            status_code=404,
            detail={"code": "MODEL_PROFILE_NOT_FOUND", "message": "找不到所选模型配置，请重新选择。"},
        ) from None
    except WrenSettingsUnavailable:
        if lease is not None:
            await app.state.runtime_manager.release_runtime(lease)
        raise HTTPException(
            status_code=503,
            detail={"code": "WREN_SETTINGS_UNAVAILABLE", "message": "Wren 设置存储当前不可用。"},
        ) from None
    except (QueryMemoryUnavailable, WrenConfigurationError) as exc:
        if lease is not None:
            await app.state.runtime_manager.release_runtime(lease)
        logger.error("AskDB runtime snapshot unavailable (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RUNTIME_SNAPSHOT_UNAVAILABLE",
                "message": "数据源语义或记忆版本当前无法准备，请稍后重试。",
            },
            headers={"Cache-Control": "no-store"},
        ) from None

    if lease is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "MODEL_SETTINGS_UNAVAILABLE", "message": "模型服务当前不可用。"},
            headers={"Cache-Control": "no-store"},
        )
    runtime = lease.snapshot.graph

    turn_start = None
    if request.message is not None:
        if memory_store is None or thread_context is None or request.turn_id is None:
            if lease is not None:
                await app.state.runtime_manager.release_runtime(lease)
            raise HTTPException(
                status_code=503,
                detail={"code": "THREAD_MEMORY_UNAVAILABLE", "message": "会话记忆服务当前不可用。"},
                headers={"Cache-Control": "no-store"},
            )
        try:
            turn_start = await run_in_threadpool(
                memory_store.begin_turn,
                thread_id=request.thread_id,
                owner_user_id=principal.user_id,
                turn_id=request.turn_id,
                user_content=request.message.content,
                expected_sequence=request.expected_sequence,
            )
        except (TurnAlreadyRunning, TurnIdempotencyConflict, TurnSequenceConflict):
            if lease is not None:
                await app.state.runtime_manager.release_runtime(lease)
            raise HTTPException(
                status_code=409,
                detail={"code": "THREAD_TURN_CONFLICT", "message": "会话状态已变化，请刷新后重试。"},
                headers={"Cache-Control": "no-store"},
            ) from None
        except Exception as exc:
            if lease is not None:
                await app.state.runtime_manager.release_runtime(lease)
            logger.error("AskDB turn reservation failed (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=503,
                detail={"code": "THREAD_MEMORY_UNAVAILABLE", "message": "会话记忆服务当前不可用。"},
                headers={"Cache-Control": "no-store"},
            ) from None

        if not turn_start.is_new:
            if lease is not None:
                await app.state.runtime_manager.release_runtime(lease)
            if turn_start.status == "completed":
                async def replay_completed_turn() -> AsyncIterator[str]:
                    envelope = {
                        "thread_id": request.thread_id,
                        "turn_id": request.turn_id,
                        "user_sequence": turn_start.user_sequence,
                        "assistant_sequence": turn_start.assistant_sequence,
                        "assistant_content": turn_start.assistant_content or "",
                    }
                    yield encode_sse("replay", envelope)
                    yield encode_sse("done", envelope)

                return StreamingResponse(
                    replay_completed_turn(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-store, no-cache", "X-Accel-Buffering": "no"},
                )
            code = "THREAD_TURN_RUNNING" if turn_start.status == "running" else "THREAD_TURN_FAILED"
            message = (
                "此轮回答仍在生成，请稍后重连获取结果。"
                if turn_start.status == "running"
                else "此轮回答未完成，请使用新的 turn_id 重试。"
            )
            raise HTTPException(
                status_code=409,
                detail={"code": code, "message": message},
                headers={"Cache-Control": "no-store"},
            )

        async def abandon_reserved_turn() -> None:
            if memory_store is None or request.turn_id is None:
                return
            try:
                await run_in_threadpool(
                    memory_store.fail_turn,
                    thread_id=request.thread_id,
                    owner_user_id=principal.user_id,
                    turn_id=request.turn_id,
                )
            except Exception as exc:
                logger.error("AskDB pre-stream turn cleanup failed (%s)", type(exc).__name__)

        snapshot = lease.snapshot if lease is not None else None
        if (
            snapshot is None
            or snapshot.context_window_tokens is None
            or snapshot.max_output_tokens is None
            or snapshot.tokenizer_id is None
            or snapshot.connector_type is None
        ):
            await abandon_reserved_turn()
            if lease is not None:
                await app.state.runtime_manager.release_runtime(lease)
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "MODEL_CONTEXT_BUDGET_REQUIRED",
                    "message": "此模型配置尚未设置会话上下文窗口、输出预留和 tokenizer。",
                },
                headers={"Cache-Control": "no-store"},
            )
        system_policy = "\n\n".join(
            (
                str(getattr(runtime, "query_context", "")),
                QUERY_GATE_SYSTEM_PROMPT,
            )
        )
        try:
            recall_enabled = bool(getattr(app.state, "memory_recall_enabled", False))
            query_memory_store = getattr(app.state, "query_memory_store", None)
            if recall_enabled and query_memory_store is None:
                raise ValueError("online recall is enabled without the query-memory store")
            suppressed_ids = (
                await run_in_threadpool(
                    query_memory_store.suppressed_ids,
                    data_source_id=thread_context.source_id,
                )
                if recall_enabled
                else frozenset()
            )
            toolkit = snapshot.toolkit
            get_tools = getattr(toolkit, "get_tools", None)
            if not callable(get_tools):
                raise ValueError("runtime toolkit does not expose tool schemas")
            tool_schema_text = serialize_tool_schemas(
                tuple(get_tools(include_memory_write=False, raise_on_error=True))
            )
            assembled_context = await run_in_threadpool(
                MemoryContextAssembler(TiktokenCounter()).assemble,
                data_source_id=thread_context.source_id,
                connector_type=snapshot.connector_type,
                wren_revision_id=snapshot.wren_revision_id,
                mdl_digest=snapshot.mdl_digest or "",
                system_policy=system_policy,
                current_question=request.message.content,
                recent_turns=thread_context.turns,
                summary=None,
                documents=snapshot.memory_documents if recall_enabled else (),
                suppressed_ids=suppressed_ids,
                budget=MemoryContextBudget(
                    snapshot.context_window_tokens,
                    snapshot.max_output_tokens,
                    snapshot.tokenizer_id,
                ),
                tool_schema_text=tool_schema_text,
            )
            if recall_enabled:
                memory_references = recall_reference_payload(assembled_context.evidence)
                memory_reference_text = serialize_recall_references(assembled_context.evidence)
                recalled_document_keys = tuple(
                    (hit.document.kind.value, hit.document.id)
                    for hit in assembled_context.evidence
                )
        except ContextBudgetError:
            await abandon_reserved_turn()
            if lease is not None:
                await app.state.runtime_manager.release_runtime(lease)
            raise HTTPException(
                status_code=413,
                detail={"code": "CHAT_CONTEXT_TOO_LARGE", "message": "当前问题和系统上下文超过此模型的输入预算。"},
                headers={"Cache-Control": "no-store"},
            ) from None
        except Exception as exc:
            await abandon_reserved_turn()
            if lease is not None:
                await app.state.runtime_manager.release_runtime(lease)
            logger.error("AskDB context assembly failed (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "CHAT_CONTEXT_ASSEMBLY_UNAVAILABLE",
                    "message": "模型上下文当前无法准备，请稍后重试。",
                },
                headers={"Cache-Control": "no-store"},
            ) from None
        except BaseException:
            await abandon_reserved_turn()
            if lease is not None:
                await app.state.runtime_manager.release_runtime(lease)
            raise
        chat_messages = [
            {"role": turn.role, "content": turn.content}
            for turn in assembled_context.recent_turns
        ]
        chat_messages.append({"role": "user", "content": request.message.content})
    else:
        chat_messages = []
        legacy_messages = request.messages or ()
        current_user_index = max(
            index for index, message in enumerate(legacy_messages) if message.role == "user"
        )
        for index, message in enumerate(legacy_messages):
            # Preserve the current question as entered. Earlier turns are only
            # compatibility context, so sanitize both roles before sending them
            # back through the model; result/SQL display content is not history.
            content = (
                message.content
                if index == current_user_index
                else sanitize_turn_text(message.content)
            )
            if content:
                chat_messages.append({"role": message.role, "content": content})

    async def stream() -> AsyncIterator[str]:
        turn_finalized = False
        stream_failed = False
        assistant_parts: list[str] = []

        async def mark_failed() -> None:
            nonlocal turn_finalized
            if turn_finalized or memory_store is None or request.turn_id is None:
                return
            try:
                await run_in_threadpool(
                    memory_store.fail_turn,
                    thread_id=request.thread_id,
                    owner_user_id=principal.user_id,
                    turn_id=request.turn_id,
                )
            except Exception as exc:
                logger.error("AskDB turn cleanup failed (%s)", type(exc).__name__)
            turn_finalized = True

        if request.message is not None:
            yield encode_sse(
                "status",
                {
                    "thread_id": request.thread_id,
                    "turn_id": request.turn_id,
                    "status": "running",
                    "user_sequence": turn_start.user_sequence if turn_start else None,
                },
            )
        try:
            async for event, payload in stream_chat_events(
                runtime,
                chat_messages,
                request.thread_id,
                memory_references=memory_references,
                memory_reference_text=memory_reference_text,
            ):
                if business_rule_store is not None:
                    while not business_rule_store.try_acquire_online_emission(source_id):
                        await asyncio.sleep(0.01)
                blocked_by_suppression = False
                blocked_by_recalled_memory = False
                is_error_event = event == "error"
                try:
                    blocked_by_suppression = (
                        business_rule_store is not None
                        and business_rule_store.blocks_runtime_revision(
                            source_id, runtime_rule_ids
                        )
                    )
                    blocked_by_recalled_memory = (
                        business_rule_store is not None
                        and bool(recalled_document_keys)
                        and business_rule_store.blocks_suppressed_memory_documents(
                            source_id, recalled_document_keys
                        )
                    )
                    blocked_by_suppression = (
                        blocked_by_suppression or blocked_by_recalled_memory
                    )
                    if not blocked_by_suppression:
                        if request.message is not None:
                            if event == "token" and isinstance(payload, dict):
                                assistant_parts.append(str(payload.get("text", "")))
                            if is_error_event:
                                stream_failed = True
                            metadata = {
                                "thread_id": request.thread_id,
                                "turn_id": request.turn_id,
                                "user_sequence": turn_start.user_sequence if turn_start else None,
                            }
                            payload = (
                                {**payload, **metadata}
                                if isinstance(payload, dict)
                                else {**metadata, "payload": payload}
                            )
                        yield encode_sse(event, payload)
                finally:
                    if business_rule_store is not None:
                        business_rule_store.release_online_emission(source_id)
                if blocked_by_suppression:
                    stream_failed = True
                    await mark_failed()
                    suppression_code = (
                        "MEMORY_REFERENCE_REVOKED"
                        if blocked_by_recalled_memory
                        else "BUSINESS_RULE_REMOVAL_PENDING"
                    )
                    suppression_message = (
                        "此回答引用的记忆已撤销，本次回答已停止。"
                        if blocked_by_recalled_memory
                        else "该数据源正在移除已撤销的业务规则，本次回答已停止。"
                    )
                    yield encode_sse(
                        "error",
                        {
                            "code": suppression_code,
                            "message": suppression_message,
                            "thread_id": request.thread_id,
                            "turn_id": request.turn_id,
                            "user_sequence": turn_start.user_sequence if turn_start else None,
                        },
                    )
                    break
                if is_error_event:
                    await mark_failed()
            if request.message is not None and not turn_finalized and memory_store is not None:
                await run_in_threadpool(
                    memory_store.complete_turn,
                    thread_id=request.thread_id,
                    owner_user_id=principal.user_id,
                    turn_id=request.turn_id,
                    assistant_content="".join(assistant_parts),
                )
                turn_finalized = True
            yield encode_sse(
                "done",
                {
                    "thread_id": request.thread_id,
                    "turn_id": request.turn_id,
                    "user_sequence": turn_start.user_sequence if turn_start else None,
                    "assistant_sequence": (
                        turn_start.user_sequence + 1
                        if (
                            not stream_failed
                            and turn_start
                            and turn_start.user_sequence is not None
                        )
                        else None
                    ),
                    "status": "failed" if stream_failed else "completed",
                }
                if request.message is not None
                else {},
            )
        except Exception as exc:
            stream_failed = True
            await mark_failed()
            logger.error("AskDB chat request failed (%s)", type(exc).__name__)
            yield encode_sse(
                "error",
                {
                    "code": "AGENT_ERROR",
                    "message": "问数暂时失败，请稍后重试。",
                    **(
                        {
                            "thread_id": request.thread_id,
                            "turn_id": request.turn_id,
                            "user_sequence": turn_start.user_sequence if turn_start else None,
                        }
                        if request.message is not None
                        else {}
                    ),
                },
            )
            yield encode_sse(
                "done",
                {
                    "thread_id": request.thread_id,
                    "turn_id": request.turn_id,
                    "status": "failed",
                }
                if request.message is not None
                else {},
            )
        except BaseException:
            await mark_failed()
            raise
        finally:
            await mark_failed()
            if lease is not None:
                await app.state.runtime_manager.release_runtime(lease)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store, no-cache", "X-Accel-Buffering": "no"},
    )
