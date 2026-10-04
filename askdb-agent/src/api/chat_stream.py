"""SSE delivery and per-turn lifecycle for prepared chat requests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from starlette.concurrency import run_in_threadpool

from api.schemas.chat import ChatRequest
from api.streaming import encode_sse
from application.chat import stream_chat_events
from domain.auth import Principal
from integrations.conversation_store import ConversationMemoryStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatStreamContext:
    """Dependencies and prepared state needed to stream one chat turn."""

    app: Any
    request: ChatRequest
    principal: Principal
    lease: Any
    runtime: Any
    source_id: str
    runtime_rule_ids: tuple[str, ...]
    business_rule_store: Any | None
    recalled_document_keys: tuple[tuple[str, str], ...]
    memory_references: tuple[dict[str, object], ...]
    memory_reference_text: str
    turn_start: Any
    memory_store: ConversationMemoryStore | None
    chat_messages: list[Mapping[str, str]]


async def stream_chat_response(context: ChatStreamContext) -> AsyncIterator[str]:
    """Deliver application events through SSE and finalize the reserved turn."""
    app = context.app
    request = context.request
    principal = context.principal
    lease = context.lease
    runtime = context.runtime
    source_id = context.source_id
    runtime_rule_ids = context.runtime_rule_ids
    business_rule_store = context.business_rule_store
    recalled_document_keys = context.recalled_document_keys
    memory_references = context.memory_references
    memory_reference_text = context.memory_reference_text
    turn_start = context.turn_start
    memory_store = context.memory_store
    chat_messages = context.chat_messages

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
                        payload = _attach_turn_metadata(
                            payload,
                            thread_id=request.thread_id,
                            turn_id=request.turn_id,
                            user_sequence=turn_start.user_sequence if turn_start else None,
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


def _attach_turn_metadata(
    payload: Any,
    *,
    thread_id: str,
    turn_id: str | None,
    user_sequence: int | None,
) -> dict[str, Any]:
    metadata = {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "user_sequence": user_sequence,
    }
    return {**payload, **metadata} if isinstance(payload, dict) else {**metadata, "payload": payload}
