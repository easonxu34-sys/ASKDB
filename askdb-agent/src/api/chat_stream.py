"""SSE delivery and per-turn lifecycle for prepared chat requests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
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


def short_log_reference(value: str | None) -> str | None:
    """Hash opaque identifiers so related logs can be correlated safely."""
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


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
        wren_diagnostic = _wren_error_diagnostic(
            exc,
            thread_id=request.thread_id,
            turn_id=request.turn_id,
        )
        if wren_diagnostic is None:
            logger.error("AskDB chat request failed (%s)", type(exc).__name__)
        else:
            logger.error(
                "AskDB chat request failed diagnostic=%s",
                json.dumps(wren_diagnostic, ensure_ascii=True, separators=(",", ":")),
            )
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


def _wren_error_diagnostic(
    exc: Exception,
    *,
    thread_id: str,
    turn_id: str | None,
) -> dict[str, Any] | None:
    """Return bounded exception diagnostics without messages, SQL, or locals."""
    if not any(base.__name__ == "WrenError" for base in type(exc).__mro__):
        return None

    def safe_label(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        name = value
        if (
            len(name) <= 64
            and name.isascii()
            and name.replace("_", "").isalnum()
        ):
            return name
        return None

    def safe_type_name(value: object) -> str:
        return safe_label(type(value).__name__) or "UNKNOWN"

    def safe_module_root(value: object) -> str:
        module = type(value).__module__.split(".", maxsplit=1)[0]
        return safe_label(module) or "UNKNOWN"

    def enum_name(value: object) -> str:
        return safe_label(getattr(value, "name", None)) or "UNKNOWN"

    def safe_integer(value: object) -> int | None:
        if type(value) is int and 0 <= value <= 2_147_483_647:
            return value
        return None

    def driver_fields(error: BaseException) -> dict[str, object]:
        fields: dict[str, object] = {"argument_count": 0, "argument_types": []}
        try:
            error_args = error.args
        except Exception:
            error_args = ()
        if isinstance(error_args, tuple):
            fields["argument_count"] = len(error_args)
            fields["argument_types"] = [
                safe_type_name(value) for value in error_args[:8]
            ]
            if len(error_args) > 8:
                fields["argument_types_truncated"] = True

        for attribute in ("errno", "code"):
            try:
                numeric_code = safe_integer(getattr(error, attribute, None))
            except Exception:
                numeric_code = None
            if numeric_code is not None:
                fields["driver_code"] = numeric_code
                fields["driver_code_source"] = attribute
                break
        if "driver_code" not in fields and isinstance(error_args, tuple) and error_args:
            numeric_code = safe_integer(error_args[0])
            if numeric_code is not None:
                fields["driver_code"] = numeric_code
                fields["driver_code_source"] = "args[0]"

        for attribute in ("sqlstate", "sql_state", "pgcode"):
            try:
                state = getattr(error, attribute, None)
            except Exception:
                state = None
            if (
                isinstance(state, str)
                and len(state) == 5
                and state.isascii()
                and state.isalnum()
            ):
                fields["sqlstate"] = state.upper()
                fields["sqlstate_source"] = attribute
                break
        return fields

    def safe_frames(error: BaseException) -> tuple[list[dict[str, object]], bool]:
        frames: list[dict[str, object]] = []
        tb = error.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append(
                {
                    "file": os.path.basename(code.co_filename),
                    "line": tb.tb_lineno,
                    "function": safe_label(code.co_name) or "UNKNOWN",
                }
            )
            tb = tb.tb_next
        return frames[-32:], len(frames) > 32

    exception_chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    exception_chain_truncated = False
    while current is not None and id(current) not in seen:
        if len(exception_chain) >= 8:
            exception_chain_truncated = True
            break
        seen.add(id(current))
        exception_chain.append(current)
        current = current.__cause__ or current.__context__

    chain_diagnostics: list[dict[str, object]] = []
    for error in exception_chain:
        frames, frames_truncated = safe_frames(error)
        node: dict[str, object] = {
            "type": safe_type_name(error),
            "module": safe_module_root(error),
            **driver_fields(error),
            "stack": frames,
        }
        if frames_truncated:
            node["stack_truncated"] = True
        chain_diagnostics.append(node)

    metadata = getattr(exc, "metadata", None)
    metadata_keys: list[str] = []
    metadata_keys_truncated = False
    if isinstance(metadata, Mapping):
        keys = list(metadata.keys())
        metadata_keys_truncated = len(keys) > 16
        for key in keys[:16]:
            metadata_keys.append(
                safe_label(getattr(key, "name", None))
                or safe_label(key)
                or safe_type_name(key)
            )

    return {
        "code": enum_name(getattr(exc, "error_code", None)),
        "phase": enum_name(getattr(exc, "phase", None)),
        "thread_ref": short_log_reference(thread_id),
        "turn_ref": short_log_reference(turn_id),
        "metadata_keys": metadata_keys,
        "metadata_keys_truncated": metadata_keys_truncated,
        "exception_chain": chain_diagnostics,
        "exception_chain_truncated": exception_chain_truncated,
    }


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
